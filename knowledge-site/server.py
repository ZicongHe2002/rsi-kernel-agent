#!/usr/bin/env python3
"""Local Markdown notebook. Run: python server.py --port 8765."""

import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
from http.cookies import SimpleCookie
import ipaddress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
from urllib.parse import parse_qs, quote, unquote, urlsplit

from auth import AuthError, AuthStore, can_read, can_edit, can_create
from local_access import auth_state_dir, get_service_token
from task_rollover import carry_content


BASE_DIR = Path(__file__).resolve().parent
MAX_BODY = 4 * 1024 * 1024
DEFAULT_CATEGORY = "未分类"
SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")
INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
RESERVED_NAMES = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


class ApiError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


def component(value):
    if (not isinstance(value, str) or not value or value != value.strip()
            or value.startswith(".") or value.endswith(".")
            or INVALID_CHARS.search(value) or RESERVED_NAMES.match(value)
            or len(value) > 150):
        raise ApiError(400, "名称不能为空，也不能包含特殊路径字符、隐藏名称或系统保留名称。")
    return value


def category_parts(value):
    if not isinstance(value, str):
        raise ApiError(400, "分类必须是文字。")
    value = value.strip() or DEFAULT_CATEGORY
    parts = [component(part) for part in value.split("/")]
    if len(parts) > 12:
        raise ApiError(400, "分类层级最多为 12 层。")
    return parts


def current_week_start():
    today = datetime.now(SHANGHAI).date()
    return today - timedelta(days=today.weekday())


def accepts_gzip(value):
    qualities = {}
    for item in value.lower().split(","):
        coding, *parameters = item.strip().split(";")
        quality = 1.0
        for parameter in parameters:
            name, separator, weight = parameter.strip().partition("=")
            if name.strip() == "q" and separator:
                try:
                    quality = float(weight)
                except ValueError:
                    quality = 0
                if not 0 <= quality <= 1:
                    quality = 0
        qualities[coding.strip()] = quality
    return qualities.get("gzip", qualities.get("*", 0)) > 0


class NoteStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def path(self, relative):
        if not isinstance(relative, str):
            raise ApiError(400, "文档路径无效。")
        parts = [component(part) for part in relative.split("/")]
        path = self.root.joinpath(*parts)
        cursor = self.root
        for part in parts:
            cursor /= part
            if cursor.is_symlink() or getattr(cursor, "is_junction", lambda: False)():
                raise ApiError(400, "不能访问链接目录或链接文件。")
        if not path.resolve().is_relative_to(self.root):
            raise ApiError(400, "文档路径超出了知识库目录。")
        return path

    def note_path(self, note_id):
        path = self.path(note_id)
        if path.suffix.lower() != ".md":
            raise ApiError(400, "仅支持 Markdown（.md）文件。")
        return path

    def read(self, note_id):
        path = self.note_path(note_id)
        if not path.is_file():
            raise ApiError(404, "文档不存在，可能已被移动。")
        raw = path.read_bytes()
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ApiError(400, "文档不是 UTF-8 编码，请转换后再打开。")
        relative = path.relative_to(self.root)
        return {
            "id": relative.as_posix(), "title": path.stem,
            "category": relative.parent.as_posix() if len(relative.parts) > 1 else DEFAULT_CATEGORY,
            "content": content,
            "updated": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            "revision": hashlib.sha256(raw).hexdigest(),
        }

    def listing(self, query=""):
        notes, categories = [], {DEFAULT_CATEGORY}
        query = query.casefold().strip()
        with self.lock:
            for parent, dirs, files in os.walk(self.root, followlinks=False):
                valid_dirs = []
                for name in dirs:
                    relative = (Path(parent) / name).relative_to(self.root).as_posix()
                    try:
                        self.path(relative)
                    except ApiError:
                        continue
                    categories.add(relative)
                    valid_dirs.append(name)
                dirs[:] = valid_dirs
                for name in files:
                    if Path(name).suffix.lower() != ".md":
                        continue
                    note_id = (Path(parent) / name).relative_to(self.root).as_posix()
                    try:
                        note = self.read(note_id)
                    except (ApiError, OSError):
                        continue
                    if query and query not in (note["title"] + "\n" + note["category"] + "\n" + note["content"]).casefold():
                        continue
                    preview = re.sub(r"\A#{1,6} [^\n]*\n+", "", note.pop("content"), count=1)
                    note["excerpt"] = re.sub(r"\s+", " ", preview).strip()[:180]
                    notes.append(note)
        notes.sort(key=lambda n: (n["updated"], n["id"]), reverse=True)
        return {"notes": notes, "categories": sorted(categories, key=str.casefold)}

    def save(self, payload, create=False):
        title = component(payload.get("title", "").strip()) if isinstance(payload.get("title"), str) else component(None)
        category = "/".join(category_parts(payload.get("category", DEFAULT_CATEGORY)))
        content = payload.get("content")
        if not isinstance(content, str):
            raise ApiError(400, "Markdown 正文必须是文字。")
        target = self.path(f"{category}/{title}.md")
        with self.lock:
            source = None
            if not create:
                previous = self.read(payload.get("id"))
                if payload.get("revision") != previous["revision"]:
                    raise ApiError(409, "文档已在其他窗口或文件中更新。请先保留当前正文，再重新打开文档。")
                source = self.note_path(previous["id"])
                if title == previous["title"] and category == previous["category"]:
                    target = source
            if target != source and target.exists():
                raise ApiError(409, "该分类中已有同名文档，请换一个标题。")
            target.parent.mkdir(parents=True, exist_ok=True)
            # A temporary file in the same directory makes replacement atomic.
            fd, temporary_name = tempfile.mkstemp(prefix=".save-", dir=target.parent)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content.encode("utf-8"))
                    stream.flush()
                    os.fsync(stream.fileno())
                if target == source:
                    os.replace(temporary, target)
                else:
                    # An exclusive hard-link publish also protects against collisions
                    # from external editors between the existence check and save.
                    try:
                        os.link(temporary, target)
                    except FileExistsError:
                        raise ApiError(409, "该分类中已有同名文档，请换一个标题。")
                    if source:
                        try:
                            source.unlink()
                        except OSError:
                            target.unlink()
                            raise
            finally:
                temporary.unlink(missing_ok=True)
            return self.read(target.relative_to(self.root).as_posix())

    def add_category(self, name):
        category = "/".join(category_parts(name))
        with self.lock:
            self.path(category).mkdir(parents=True, exist_ok=True)
        return {"name": category}

    def ensure_task_lists(self):
        with self.lock:
            monday = current_week_start()
            title = f"一周待办 · {monday.isoformat()}"
            templates = {
                "weekly": (title, f"# {title}\n\n## 本周待办\n\n## 完成记录\n"),
                "general": ("长期待办", "# 长期待办\n\n## 待办事项\n\n## 完成记录\n"),
            }
            result = {"week_start": monday.isoformat(), "created": []}
            missing = {}
            for key, (name, content) in templates.items():
                note_id = f"待办清单/{name}.md"
                if self.note_path(note_id).exists():
                    result[key] = self.read(note_id)
                else:
                    missing[note_id] = (key, name, content)

            # Deleted lists stay deleted. Unlike the browsing index, this check
            # fails on damaged snapshots instead of silently recreating a list.
            deleted = {}
            trash = self.trash_path()
            if missing and trash.exists():
                for entry in trash.iterdir():
                    if re.fullmatch(r"[0-9a-f]{32}", entry.name):
                        note = self.read_trash(entry.name)
                        previous = deleted.get(note["id"])
                        if previous is None or (note["deleted_at"], note["trash_id"]) > (previous["deleted_at"], previous["trash_id"]):
                            deleted[note["id"]] = note
            for note_id, (key, name, content) in missing.items():
                if note_id in deleted:
                    result[key] = deleted[note_id]
                else:
                    result[key] = self.save({"title": name, "category": "待办清单", "content": content}, create=True)
                    result["created"].append(note_id)
            weekly = result['weekly']
            if not weekly.get('trash_id') and f'<!-- zhixu-weekly-carry:{monday.isoformat()} -->' not in weekly['content']:
                candidates = []
                for path in self.path('待办清单').glob('一周待办 · ????-??-??.md'):
                    day = path.stem.removeprefix('一周待办 · ')
                    if day < monday.isoformat():
                        candidates.append((day, path.relative_to(self.root).as_posix()))
                if candidates:
                    day, previous_id = max(candidates)
                    # A newer deleted week must not resurrect tasks from an older one.
                    newer_deleted = False
                    if trash.exists():
                        for entry in trash.iterdir():
                            if re.fullmatch(r'[0-9a-f]{32}', entry.name):
                                removed = self.read_trash(entry.name)
                                match = re.fullmatch(r'待办清单/一周待办 · (\d{4}-\d{2}-\d{2})\.md', removed['id'])
                                if match and day < match[1] < monday.isoformat():
                                    newer_deleted = True
                    previous_content = '' if newer_deleted else self.read(previous_id)['content']
                    content = carry_content(weekly['content'], previous_content, monday.isoformat())
                    if content != weekly['content']:
                        result['weekly'] = self.save(dict(weekly, content=content))
            return result

    def trash_path(self, trash_id=None, filename=None):
        parts = [".trash"]
        if trash_id is not None or filename is not None:
            if not isinstance(trash_id, str) or not re.fullmatch(r"[0-9a-f]{32}", trash_id):
                raise ApiError(400, "已删除文档的标识无效。")
            parts.append(trash_id)
        if filename is not None:
            parts.append(filename)
        path = self.root
        for part in parts:
            path /= part
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise ApiError(400, "不能访问链接目录或链接文件。")
        if not path.resolve().is_relative_to(self.root):
            raise ApiError(400, "已删除文档的路径超出了知识库目录。")
        return path

    def read_trash(self, trash_id):
        metadata_path = self.trash_path(trash_id, "metadata.json")
        content_path = self.trash_path(trash_id, "note.md")
        if not metadata_path.is_file() or not content_path.is_file():
            raise ApiError(404, "已删除文档不存在，可能已恢复。")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict) or not all(
                isinstance(metadata.get(key), str) for key in ("id", "updated", "revision", "deleted_at")
            ):
                raise ValueError
            parts = [component(part) for part in metadata["id"].split("/")]
            if Path(parts[-1]).suffix.lower() != ".md":
                raise ValueError
            datetime.fromisoformat(metadata["updated"])
            datetime.fromisoformat(metadata["deleted_at"])
            raw = content_path.read_bytes()
            content = raw.decode("utf-8-sig")
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "已删除文档的记录无效，原文件仍保留在本地。")
        revision = hashlib.sha256(raw).hexdigest()
        if revision != metadata["revision"]:
            raise ApiError(409, "已删除文档的原文件已被修改，暂时无法恢复。")
        return {
            "id": "/".join(parts), "trash_id": trash_id, "title": Path(parts[-1]).stem,
            "category": "/".join(parts[:-1]) if len(parts) > 1 else DEFAULT_CATEGORY,
            "content": content, "revision": revision, "updated": metadata["updated"],
            "deleted_at": metadata["deleted_at"],
        }

    def trash_listing(self, query=""):
        notes = []
        query = query.casefold().strip()
        with self.lock:
            root = self.trash_path()
            if not root.exists():
                return {"notes": notes}
            for entry in root.iterdir():
                if not re.fullmatch(r"[0-9a-f]{32}", entry.name):
                    continue
                try:
                    note = self.read_trash(entry.name)
                except (ApiError, OSError):
                    continue
                if query and query not in (note["title"] + "\n" + note["category"] + "\n" + note["content"]).casefold():
                    continue
                note["excerpt"] = re.sub(r"\s+", " ", note.pop("content")).strip()[:180]
                notes.append(note)
        notes.sort(key=lambda note: (note["deleted_at"], note["trash_id"]), reverse=True)
        return {"notes": notes}

    def delete(self, payload):
        with self.lock:
            note = self.read(payload.get("id"))
            if payload.get("revision") != note["revision"]:
                raise ApiError(409, "文档已更新，请重新打开后再删除。")
            source = self.note_path(note["id"])
            raw = source.read_bytes()
            if hashlib.sha256(raw).hexdigest() != note["revision"]:
                raise ApiError(409, "文档已更新，请重新打开后再删除。")
            trash_id = secrets.token_hex(16)
            entry = self.trash_path(trash_id)
            self.trash_path().mkdir(exist_ok=True)
            entry.mkdir()
            metadata_path = self.trash_path(trash_id, "metadata.json")
            content_path = self.trash_path(trash_id, "note.md")
            deleted_at = datetime.now(timezone.utc).isoformat()
            metadata = {key: note[key] for key in ("id", "updated", "revision")}
            metadata["deleted_at"] = deleted_at
            try:
                # Finish the durable snapshot before removing the active file.
                for path, data in ((content_path, raw), (metadata_path, json.dumps(metadata, ensure_ascii=False).encode("utf-8"))):
                    with path.open("xb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                if hashlib.sha256(source.read_bytes()).hexdigest() != note["revision"]:
                    raise ApiError(409, "文档已更新，请重新打开后再删除。")
                source.unlink()
            except (ApiError, OSError):
                # If an external editor removed the source, retain the snapshot.
                if source.exists():
                    content_path.unlink(missing_ok=True)
                    metadata_path.unlink(missing_ok=True)
                    entry.rmdir()
                raise
            return dict(note, trash_id=trash_id, deleted_at=deleted_at)

    def restore(self, payload):
        with self.lock:
            note = self.read_trash(payload.get("trash_id"))
            target = self.note_path(note["id"])
            if target.exists():
                raise ApiError(409, "原位置已有同名文档。请先移动或重命名该文档，再恢复。")
            target.parent.mkdir(parents=True, exist_ok=True)
            content_path = self.trash_path(note["trash_id"], "note.md")
            raw = content_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != note["revision"]:
                raise ApiError(409, "已删除文档的原文件已被修改，暂时无法恢复。")
            fd, temporary_name = tempfile.mkstemp(prefix=".restore-", dir=target.parent)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                updated = datetime.fromisoformat(note["updated"]).timestamp()
                os.utime(temporary, (updated, updated))
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    raise ApiError(409, "原位置已有同名文档。请先移动或重命名该文档，再恢复。")
            finally:
                temporary.unlink(missing_ok=True)
            # Hide the completed entry atomically before removing its two files.
            entry = self.trash_path(note["trash_id"])
            retired = self.trash_path() / (".restored-" + note["trash_id"])
            os.rename(entry, retired)
            try:
                (retired / "metadata.json").unlink()
                (retired / "note.md").unlink()
                retired.rmdir()
            except OSError:
                pass  # The active file is already durable; a hidden cleanup remnant is harmless.
            return self.read(note["id"])


class Handler(BaseHTTPRequestHandler):
    server_version = "KnowledgeSite/1.0"

    def reply(self, status, body, content_type="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if status != 304:
            self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", (extra or {}).get("Cache-Control", "no-store"))
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for key, value in (extra or {}).items():
            if key != "Cache-Control":
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def check_origin(self):
        port = self.server.server_port
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            allowed_hosts.update({"127.0.0.1", "localhost"})
        host = self.headers.get("Host", "").lower()
        self.local_host = host in allowed_hosts
        public_origin = self.server.public_origin
        if not public_origin:
            config = auth_state_dir(self.server.store.root) / "public-origin.json"
            if config.is_file():
                try:
                    setting = json.loads(config.read_text(encoding="utf-8"))
                    public_origin = setting.get("origin", "") if isinstance(setting, dict) else ""
                except (ValueError, OSError):
                    public_origin = ""
        try:
            public = urlsplit(public_origin if isinstance(public_origin, str) else "")
        except ValueError:
            public = urlsplit("")
        self.public_request = bool(public.scheme == "https" and public.netloc.lower() == host and not public.path.strip("/"))
        if not self.local_host and not self.public_request:
            raise ApiError(403, "此网站地址尚未配置，请使用 owner 提供的链接。")
        # External links may open the public login shell. Document data still
        # loads through authenticated, same-origin API requests after navigation.
        if (self.command == "GET" and urlsplit(self.path).path in {"/", "/index.html"}
                and self.headers.get("Sec-Fetch-Mode") == "navigate"
                and self.headers.get("Sec-Fetch-Dest") == "document"):
            return
        expected_origin = f"https://{host}" if self.public_request else f"http://{host}"
        origin = self.headers.get("Origin")
        if origin is not None and origin.lower() != expected_origin:
            raise ApiError(403, "不允许从其他网站修改知识库。")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise ApiError(403, "不允许跨网站访问知识库。")

    def local_service(self):
        # A reverse proxy is also loopback: peer IP alone never grants ownership.
        if not self.local_host or self.public_request or any(self.headers.get(key) for key in ("Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "CF-Connecting-IP")):
            return False
        expected = "Bearer " + self.server.local_token
        return secrets.compare_digest(self.headers.get("Authorization", ""), expected)

    def cookie_token(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:
            return ""
        item = cookies.get("zhixu_session")
        return item.value if item else ""

    def session_cookie(self, token, clear=False):
        secure = "; Secure" if self.public_request else ""
        return {"Set-Cookie": f"zhixu_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={0 if clear else 2592000}{secure}"}

    def require_user(self):
        if self.local_service():
            return {"id": "fl", "display_name": "风落", "owner": True, "enabled": True, "grants": []}
        session = self.server.auth.get_session(self.cookie_token())
        if not session:
            raise ApiError(401, "请先输入 ID，等待 owner 批准这台设备。")
        if self.command != "GET":
            csrf = self.headers.get("X-CSRF-Token", "")
            if not csrf or not secrets.compare_digest(csrf, session["csrf_token"]):
                raise ApiError(403, "登录状态已更新，请刷新页面后重试。")
        return session["user"]

    @staticmethod
    def require_owner(user):
        if not user.get("owner"):
            raise ApiError(403, "只有 owner 可以执行此操作。")

    def visible_note(self, note, user):
        if user.get("owner"):
            return note
        return dict(note, permissions={"read": True, "edit": can_edit(user, note["id"])})

    def visible_listing(self, listing, user):
        notes = [self.visible_note(note, user) for note in listing["notes"] if can_read(user, note["id"])]
        if user.get("owner"):
            categories = listing["categories"]
        else:
            categories = set()
            for note in notes:
                parts = note["category"].split("/")
                categories.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
            for grant in user.get("grants", []):
                if grant["scope"] == "category":
                    categories.add(grant["path"])
            categories = sorted(categories, key=str.casefold)
        return {"notes": notes, "categories": categories}

    def payload(self):
        if self.headers.get_content_type() != "application/json":
            raise ApiError(415, "请使用 JSON 请求。")
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(400, "不支持分块请求。")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError(400, "请求长度无效。")
        if not 0 < length <= MAX_BODY:
            raise ApiError(413, "文档请求不能超过 4 MB。")
        try:
            result = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "JSON 内容无效。")
        if not isinstance(result, dict):
            raise ApiError(400, "JSON 必须是对象。")
        return result

    def handle_request(self):
        try:
            self.check_origin()
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            store = self.server.store
            auth = self.server.auth
            if self.command == "GET":
                if url.path == "/api/health":
                    if not self.local_service():
                        return self.reply(200, {"app": "zhixu"})
                    instance = hashlib.sha256(self.server.shutdown_token.encode()).hexdigest() if self.server.shutdown_token else None
                    return self.reply(200, {"app": "zhixu", "data_dir": str(store.root), "pid": os.getpid(), "instance": instance})
                if url.path == "/api/auth/session":
                    session = auth.get_session(self.cookie_token())
                    if session:
                        user = dict(session["user"], role="owner" if session["user"]["owner"] else "member")
                        library_id = hashlib.sha256(str(store.root).encode()).hexdigest()[:24]
                        result = {"authenticated": True, "user": user, "csrf_token": session["csrf_token"], "library_id": library_id}
                        if user["owner"]:
                            result["legacy_storage_prefix"] = "zhixu.local." + str(store.root) + "."
                        return self.reply(200, result)
                    status = auth.device_status(self.cookie_token())
                    return self.reply(200, {"authenticated": False, "user": None, "request_status": status.get("status")})
                if not url.path.startswith("/api/"):
                    return self.serve_static(unquote(url.path))
                user = self.require_user()
                if url.path == "/api/admin/users":
                    self.require_owner(user)
                    return self.reply(200, {"users": auth.list_users()})
                if url.path == "/api/admin/requests":
                    self.require_owner(user)
                    requests = [dict(item, device_label=item.get("device_name", ""), created_at=item.get("requested_at", "")) for item in auth.list_requests()]
                    return self.reply(200, {"requests": requests})
                if url.path == "/api/notes":
                    return self.reply(200, self.visible_listing(store.listing(query.get("q", [""])[0]), user))
                if url.path == "/api/trash":
                    self.require_owner(user)
                    return self.reply(200, store.trash_listing(query.get("q", [""])[0]))
                if url.path == "/api/trash/note":
                    self.require_owner(user)
                    with store.lock:
                        return self.reply(200, self.visible_note(store.read_trash(query.get("id", [None])[0]), user))
                if url.path in {"/api/note", "/api/export"}:
                    with store.lock:
                        note_id = query.get("id", [None])[0]
                        store.note_path(note_id)
                        if not can_read(user, note_id):
                            raise ApiError(404, "文档不存在或没有访问权限。")
                        note = store.read(note_id)
                        if url.path == "/api/export":
                            raw = store.note_path(note["id"]).read_bytes()
                            disposition = "attachment; filename*=UTF-8''" + quote(note["title"] + ".md", safe="")
                            return self.reply(200, raw, "text/markdown; charset=utf-8", {"Content-Disposition": disposition})
                        return self.reply(200, self.visible_note(note, user))
                raise ApiError(404, "接口不存在。")
            data = self.payload()
            if self.command == "POST" and url.path == "/api/auth/request":
                current = auth.device_status(self.cookie_token())
                requested_id = data.get("id")
                if current.get("status") == "pending" and isinstance(requested_id, str) and current.get("user_id") == requested_id.casefold():
                    return self.reply(200, {"request_status": "pending"})
                client_ip = self.client_address[0]
                if self.public_request and self.headers.get("CF-Connecting-IP"):
                    try:
                        client_ip = str(ipaddress.ip_address(self.headers["CF-Connecting-IP"]))
                    except ValueError:
                        pass
                result = auth.request_device(data.get("id"), client_ip, self.headers.get("User-Agent", "")[:160])
                auth.logout(self.cookie_token())
                return self.reply(200, {"request_status": "pending"}, extra=self.session_cookie(result["token"]))
            if self.command == "POST" and url.path == "/api/auth/bootstrap":
                token = auth.consume_owner_token(data.get("token", ""))
                return self.reply(200, {"ok": True}, extra=self.session_cookie(token))
            user = self.require_user()
            if self.command == "POST" and url.path == "/api/auth/logout":
                auth.logout(self.cookie_token())
                return self.reply(200, {"ok": True}, extra=self.session_cookie("", clear=True))
            if url.path.startswith("/api/admin/"):
                self.require_owner(user)
                if url.path == "/api/admin/users":
                    if self.command == "POST":
                        account = auth.create_user(data.get("id"), data.get("display_name"), data.get("grants", []), enabled=data.get("enabled", True))
                        return self.reply(201, account)
                    if self.command == "PUT":
                        account = auth.update_user(data.get("id"), display_name=data.get("display_name"), grants=data.get("grants"), enabled=data.get("enabled"))
                        return self.reply(200, account)
                if self.command == "POST" and url.path == "/api/admin/requests/approve":
                    auth.approve_device(data.get("id"))
                    return self.reply(200, {"ok": True})
                if self.command == "POST" and url.path == "/api/admin/requests/reject":
                    auth.reject_device(data.get("id"))
                    return self.reply(200, {"ok": True})
                raise ApiError(404, "接口不存在。")
            if self.command == "POST" and url.path == "/api/shutdown":
                if not self.local_service():
                    raise ApiError(403, "此操作仅限本机启动器。")
                token = data.get("token")
                expected = self.server.shutdown_token
                if not expected or not isinstance(token, str) or not secrets.compare_digest(token, expected):
                    raise ApiError(403, "此启动器无权停止该服务。")
                self.reply(200, {"stopping": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if self.command == "POST" and url.path == "/api/notes":
                category = "/".join(category_parts(data.get("category", DEFAULT_CATEGORY)))
                if not can_create(user, category):
                    raise ApiError(403, "没有在此分类新建文档的权限。")
                return self.reply(201, self.visible_note(store.save(data, create=True), user))
            if self.command == "POST" and url.path == "/api/task-lists/ensure":
                self.require_owner(user)
                lists = store.ensure_task_lists()
                for key in ("weekly", "general"):
                    lists[key] = self.visible_note(lists[key], user)
                return self.reply(200, lists)
            if self.command == "PUT" and url.path == "/api/note":
                with store.lock:
                    note_id = data.get("id")
                    store.note_path(note_id)
                    if not can_edit(user, note_id):
                        raise ApiError(403, "没有编辑此文档的权限。")
                    previous = store.read(note_id)
                    if not user.get("owner") and (data.get("title") != previous["title"] or data.get("category") != previous["category"]):
                        raise ApiError(403, "成员可以编辑正文；移动或重命名文档请联系 owner。")
                    return self.reply(200, self.visible_note(store.save(data), user))
            if self.command == "DELETE" and url.path == "/api/note":
                self.require_owner(user)
                return self.reply(200, self.visible_note(store.delete(data), user))
            if self.command == "POST" and url.path == "/api/trash/restore":
                self.require_owner(user)
                return self.reply(200, self.visible_note(store.restore(data), user))
            if self.command == "POST" and url.path == "/api/categories":
                self.require_owner(user)
                return self.reply(201, store.add_category(data.get("name")))
            raise ApiError(404, "接口不存在。")
        except (ApiError, AuthError) as error:
            self.reply(error.status, {"error": error.message})
        except OSError:
            self.reply(500, {"error": "文件读写失败，请检查目录权限和可用空间。"})

    def serve_static(self, url_path):
        allowed = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/auth.js": "auth.js", "/style.css": "style.css", "/note-links.js": "note-links.js",
                   "/vendor/marked.js": "vendor/marked.js", "/vendor/purify.js": "vendor/purify.js",
                   "/vendor/mermaid.js": "vendor/mermaid.js", "/vendor/mermaid-full.js": "vendor/mermaid-full.js"}
        allowed.update(self.server.mermaid_assets)
        if url_path not in allowed:
            raise ApiError(404, "页面不存在。")
        relative = Path(allowed[url_path])
        path = self.server.web_root / relative
        cursor = self.server.web_root
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink() or getattr(cursor, "is_junction", lambda: False)():
                raise ApiError(404, "页面不存在。")
        if not path.is_file() or not path.resolve().is_relative_to(self.server.web_root):
            raise ApiError(404, "页面不存在。")
        content_type = "text/javascript" if path.suffix == ".mjs" else mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with self.server.static_lock:
            stat = path.stat()
            version = (stat.st_mtime_ns, stat.st_size)
            cached = self.server.static_cache.get(relative)
            if cached is None or cached[0] != version:
                raw = path.read_bytes()
                compressed = gzip.compress(raw, compresslevel=6, mtime=0) if len(raw) >= 1024 else raw
                digest = hashlib.sha256(raw).hexdigest()
                cached = (version, raw, compressed, digest)
                self.server.static_cache[relative] = cached
        _, raw, compressed, digest = cached
        use_gzip = len(compressed) < len(raw) and accepts_gzip(self.headers.get("Accept-Encoding", ""))
        body = compressed if use_gzip else raw
        etag = '"' + digest + ("-gzip" if use_gzip else "") + '"'
        # Asset names are not versioned: revalidate even HTML and app.js so
        # updates appear immediately, while unchanged files transfer no body.
        headers = {"Cache-Control": "private, no-cache", "ETag": etag, "Vary": "Accept-Encoding"}
        if url_path in self.server.mermaid_assets:
            headers["Cache-Control"] = "private, max-age=31536000, immutable"
        if use_gzip:
            headers["Content-Encoding"] = "gzip"
        candidates = [value.strip().removeprefix("W/") for value in self.headers.get("If-None-Match", "").split(",")]
        status = 304 if etag in candidates or "*" in candidates else 200
        self.reply(status, b"" if status == 304 else body, content_type + "; charset=utf-8", headers)

    do_GET = handle_request
    do_POST = handle_request
    do_PUT = handle_request
    do_DELETE = handle_request


class KnowledgeServer(ThreadingHTTPServer):
    daemon_threads = False
    request_queue_size = 64

    def server_close(self):
        super().server_close()
        if getattr(self, "auth", None):
            self.auth.close()
            self.auth = None


def make_server(data_dir=None, port=8765, web_root=None, public_origin=""):
    server = KnowledgeServer(("127.0.0.1", port), Handler)
    server.store = NoteStore(data_dir or BASE_DIR / "content")
    server.web_root = Path(web_root or BASE_DIR / "web").resolve()
    server.shutdown_token = None
    server.auth = AuthStore(auth_state_dir(server.store.root) / "auth.sqlite3")
    server.local_token = get_service_token(server.store.root, create=True)
    server.public_origin = public_origin
    server.static_cache = {}
    server.static_lock = threading.Lock()
    server.mermaid_assets = {}
    vendor = server.web_root / "vendor" / "mermaid-11.17.2"
    manifest = vendor / "manifest.json"
    if manifest.is_file():
        for item in json.loads(manifest.read_text(encoding="utf-8"))["files"]:
            relative = item["path"]
            if not re.fullmatch(r"(?:mermaid\.esm\.min\.mjs|chunks/mermaid\.esm\.min/[A-Za-z0-9_-]+\.mjs)", relative):
                raise ValueError("Unexpected Mermaid module path")
            asset = "vendor/mermaid-11.17.2/" + relative
            server.mermaid_assets["/" + asset] = asset
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=BASE_DIR / "content")
    args = parser.parse_args()
    server = make_server(args.data_dir, args.port)
    server.shutdown_token = os.environ.get("ZHIXU_SHUTDOWN_TOKEN")
    print(f"Knowledge site: http://127.0.0.1:{server.server_port}", flush=True)
    print(f"Markdown directory: {server.store.root}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
