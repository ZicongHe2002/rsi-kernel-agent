"""Local account, browser approval and note permissions for the knowledge site."""

import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time


OWNER_ID = "fl"
DEFAULT_CATEGORY = "未分类"
SESSION_SECONDS = 30 * 24 * 60 * 60
PENDING_SECONDS = 10 * 60
REQUEST_WINDOW = 10 * 60
REQUESTS_PER_IP = 10
REQUESTS_TOTAL = 100
MAX_PENDING = 200
ID_RE = re.compile(r"[a-zA-Z0-9_-]{1,40}\Z")
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
INVALID_PATH = re.compile(r'[<>:"\\|?*\x00-\x1f\x7f]')
RESERVED_NAMES = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


class AuthError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def normalized_id(value):
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise AuthError(400, "ID 只能包含 1–40 个英文字母、数字、下划线或短横线。")
    return value.casefold()


def permission_path(value, scope):
    if not isinstance(value, str) or len(value) > 1800:
        raise AuthError(400, "权限路径无效。")
    parts = value.split("/")
    if len(parts) > (13 if scope == "note" else 12):
        raise AuthError(400, "权限路径层级过多。")
    for part in parts:
        if (not part or part != part.strip() or part.startswith(".")
                or part.endswith(".") or len(part) > 150
                or INVALID_PATH.search(part) or RESERVED_NAMES.match(part)):
            raise AuthError(400, "权限路径不能包含隐藏名称、特殊字符或相对路径。")
    if scope == "note" and not parts[-1].casefold().endswith(".md"):
        raise AuthError(400, "文档权限必须指向 .md 文档。")
    return "/".join(parts)


def normalize_grants(grants):
    if not isinstance(grants, list) or len(grants) > 200:
        raise AuthError(400, "权限列表无效，最多支持 200 条。")
    result = {}
    for grant in grants:
        if not isinstance(grant, dict):
            raise AuthError(400, "权限项无效。")
        scope, access = grant.get("scope"), grant.get("access")
        if scope not in ("all", "category", "note") or access not in ("read", "edit"):
            raise AuthError(400, "请选择全部文档、分类或文档，以及阅读或编辑权限。")
        path = "" if scope == "all" else permission_path(grant.get("path"), scope)
        key = (scope, path.casefold())
        previous = result.get(key)
        if not previous or access == "edit":
            result[key] = {"scope": scope, "path": path, "access": access}
    return list(result.values())


def _allowed(user, path, access, create=False):
    if not user or not user.get("enabled"):
        return False
    try:
        if create:
            target_category = permission_path(path, "category").casefold()
            target_note = None
        else:
            target_note = permission_path(path, "note").casefold()
            # NoteStore presents a root-level Markdown file as the virtual
            # “未分类” category. Keep the note's original ID for note grants.
            parent = target_note.rsplit("/", 1)[0] if "/" in target_note else DEFAULT_CATEGORY.casefold()
            target_category = parent
    except AuthError:
        return False
    if user.get("owner") and user.get("id") == OWNER_ID:
        return True
    for grant in user.get("grants", []):
        if access == "edit" and grant["access"] != "edit":
            continue
        if grant["scope"] == "all":
            return True
        allowed = grant["path"].casefold()
        if grant["scope"] == "category":
            if target_category == allowed or target_category.startswith(allowed + "/"):
                return True
        elif not create and target_note == allowed:
            return True
    return False


def can_read(user, note_id):
    return _allowed(user, note_id, "read")


def can_edit(user, note_id):
    return _allowed(user, note_id, "edit")


def can_create(user, category):
    return _allowed(user, category, "edit", create=True)


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_hash(token):
    return _hash(token) if isinstance(token, str) and TOKEN_RE.fullmatch(token) else ""


def _display_name(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 80
            or any(ord(character) < 32 for character in value)):
        raise AuthError(400, "显示名称应为 1–80 个字符。")
    return value.strip()


class AuthStore:
    """One SQLite connection guarded by a lock for ThreadingHTTPServer."""

    can_read = staticmethod(can_read)
    can_edit = staticmethod(can_edit)
    can_create = staticmethod(can_create)

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.db:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                    enabled INTEGER NOT NULL, grants TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS devices (
                    token_hash TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                    user_id TEXT NOT NULL, device_name TEXT NOT NULL,
                    requested_at REAL NOT NULL, expires_at REAL NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS devices_status ON devices(status, expires_at);
                CREATE TABLE IF NOT EXISTS requests (ip_hash TEXT NOT NULL, requested_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS requests_time ON requests(requested_at);
                CREATE TABLE IF NOT EXISTS owner_tokens (token_hash TEXT PRIMARY KEY, expires_at REAL NOT NULL);
            """)
            self.db.execute("INSERT OR IGNORE INTO users VALUES (?, ?, 1, '[]')", (OWNER_ID, "风落"))
            self.db.execute("UPDATE users SET display_name = ?, enabled = 1, grants = '[]' WHERE id = ?", ("风落", OWNER_ID))

    def close(self):
        with self.lock:
            self.db.close()

    def _user(self, row):
        if row is None:
            return None
        return {"id": row["id"], "display_name": row["display_name"],
                "owner": row["id"] == OWNER_ID, "enabled": bool(row["enabled"]),
                "grants": json.loads(row["grants"])}

    def list_users(self):
        with self.lock:
            return [self._user(row) for row in self.db.execute("SELECT * FROM users ORDER BY id")]

    def create_user(self, user_id, display_name=None, grants=None, enabled=True):
        user_id = normalized_id(user_id)
        display_name = _display_name(display_name if display_name is not None else user_id)
        grants = normalize_grants(grants if grants is not None else [])
        if not isinstance(enabled, bool):
            raise AuthError(400, "账号启用状态必须是布尔值。")
        with self.lock, self.db:
            if self.db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
                raise AuthError(409, "这个 ID 已存在。")
            self.db.execute("INSERT INTO users VALUES (?, ?, ?, ?)",
                            (user_id, display_name, int(enabled), json.dumps(grants, ensure_ascii=False)))
            return self._user(self.db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())

    def update_user(self, user_id, display_name=None, grants=None, enabled=None):
        user_id = normalized_id(user_id)
        if user_id == OWNER_ID:
            raise AuthError(403, "不能修改 owner 账号。")
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise AuthError(404, "账号不存在。")
            name = _display_name(display_name) if display_name is not None else row["display_name"]
            normalized = normalize_grants(grants) if grants is not None else json.loads(row["grants"])
            if enabled is not None and not isinstance(enabled, bool):
                raise AuthError(400, "账号启用状态必须是布尔值。")
            active = row["enabled"] if enabled is None else int(enabled)
            self.db.execute("UPDATE users SET display_name = ?, enabled = ?, grants = ? WHERE id = ?",
                            (name, active, json.dumps(normalized, ensure_ascii=False), user_id))
            # Disabling an account revokes remembered browsers, even if later re-enabled.
            if not active:
                self.db.execute("DELETE FROM devices WHERE user_id = ?", (user_id,))
            return self._user(self.db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())

    def delete_user(self, user_id):
        user_id = normalized_id(user_id)
        if user_id == OWNER_ID:
            raise AuthError(403, "不能删除 owner 账号。")
        with self.lock, self.db:
            if not self.db.execute("DELETE FROM users WHERE id = ?", (user_id,)).rowcount:
                raise AuthError(404, "账号不存在。")
            self.db.execute("DELETE FROM devices WHERE user_id = ?", (user_id,))

    def _prune(self, now):
        self.db.execute("DELETE FROM requests WHERE requested_at <= ?", (now - REQUEST_WINDOW,))
        self.db.execute("DELETE FROM devices WHERE expires_at <= ?", (now - 86400,))
        self.db.execute("DELETE FROM owner_tokens WHERE expires_at <= ?", (now,))

    def request_device(self, user_id, ip, device_name=""):
        user_id = normalized_id(user_id)
        if user_id == OWNER_ID:
            raise AuthError(403, "owner 请使用本机生成的一次性登录链接。")
        if not isinstance(device_name, str) or len(device_name) > 160:
            raise AuthError(400, "设备名称最长为 160 个字符。")
        device_name = "".join(char for char in device_name if ord(char) >= 32).strip()
        now = time.time()
        with self.lock, self.db:
            self._prune(now)
            per_ip = self.db.execute("SELECT COUNT(*) FROM requests WHERE ip_hash = ?", (_hash(str(ip)),)).fetchone()[0]
            total = self.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
            pending = self.db.execute("SELECT COUNT(*) FROM devices WHERE status = 'pending' AND expires_at > ?", (now,)).fetchone()[0]
            if per_ip >= REQUESTS_PER_IP or total >= REQUESTS_TOTAL or pending >= MAX_PENDING:
                raise AuthError(429, "登录申请过于频繁，请稍后再试。")
            token = secrets.token_urlsafe(32)
            self.db.execute("INSERT INTO requests VALUES (?, ?)", (_hash(str(ip)), now))
            self.db.execute("INSERT INTO devices VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                            (_hash(token), secrets.token_hex(16), user_id, device_name, now, now + PENDING_SECONDS))
            # Identical response for an unknown, disabled or existing ordinary ID.
            return {"token": token, "status": "pending", "expires_at": now + PENDING_SECONDS}

    def device_status(self, token):
        with self.lock:
            row = self.db.execute("SELECT * FROM devices WHERE token_hash = ?", (_token_hash(token),)).fetchone()
            if row is None:
                return {"status": "anonymous"}
            if row["expires_at"] <= time.time():
                return {"status": "expired"}
            if row["status"] == "approved":
                user = self._user(self.db.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone())
                if user is None or not user["enabled"]:
                    return {"status": "anonymous"}
                return {"status": "authenticated", "user": user, "expires_at": row["expires_at"]}
            # This is the ID submitted by this browser, not an assertion that
            # the account exists. It lets a mistyped pending ID be corrected.
            return {"status": row["status"], "user_id": row["user_id"], "expires_at": row["expires_at"]}

    def get_user(self, token):
        return self.device_status(token).get("user")

    def get_session(self, token):
        user = self.get_user(token)
        if user is None:
            return None
        return {"user": user, "csrf_token": _hash("knowledge-site:csrf:" + token)}

    def logout(self, token):
        with self.lock, self.db:
            self.db.execute("DELETE FROM devices WHERE token_hash = ?", (_token_hash(token),))

    def list_requests(self):
        with self.lock:
            rows = self.db.execute("SELECT request_id, user_id, device_name, requested_at, expires_at FROM devices WHERE status = 'pending' AND expires_at > ? ORDER BY requested_at", (time.time(),))
            return [{"id": row["request_id"], "user_id": row["user_id"], "device_name": row["device_name"],
                     "requested_at": row["requested_at"], "expires_at": row["expires_at"]} for row in rows]

    def approve_device(self, request_id):
        now = time.time()
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM devices WHERE request_id = ? AND status = 'pending' AND expires_at > ?", (request_id, now)).fetchone()
            if row is None:
                raise AuthError(404, "申请不存在或已经过期。")
            user = self.db.execute("SELECT * FROM users WHERE id = ? AND enabled = 1", (row["user_id"],)).fetchone()
            if user is None or row["user_id"] == OWNER_ID:
                raise AuthError(400, "请先创建并启用这个普通账号。")
            self.db.execute("UPDATE devices SET status = 'approved', expires_at = ? WHERE request_id = ?", (now + SESSION_SECONDS, request_id))

    def reject_device(self, request_id):
        with self.lock, self.db:
            if not self.db.execute("UPDATE devices SET status = 'rejected' WHERE request_id = ? AND status = 'pending' AND expires_at > ?", (request_id, time.time())).rowcount:
                raise AuthError(404, "申请不存在或已经过期。")

    def create_owner_token(self, ttl=600):
        if not isinstance(ttl, int) or ttl < 1 or ttl > 600:
            raise AuthError(400, "owner 登录链接有效期最多为 10 分钟。")
        token = secrets.token_urlsafe(32)
        with self.lock, self.db:
            self.db.execute("DELETE FROM owner_tokens")
            self.db.execute("INSERT INTO owner_tokens VALUES (?, ?)", (_hash(token), time.time() + ttl))
        return token

    def consume_owner_token(self, token):
        now = time.time()
        with self.lock, self.db:
            row = self.db.execute("DELETE FROM owner_tokens WHERE token_hash = ? AND expires_at > ? RETURNING token_hash", (_token_hash(token), now)).fetchone()
            if row is None:
                raise AuthError(403, "owner 登录链接无效、已使用或已过期，请在本机重新生成。")
            session = secrets.token_urlsafe(32)
            self.db.execute("INSERT INTO devices VALUES (?, ?, ?, ?, ?, ?, 'approved')",
                            (_hash(session), secrets.token_hex(16), OWNER_ID, "Owner browser", now, now + SESSION_SECONDS))
            return session
