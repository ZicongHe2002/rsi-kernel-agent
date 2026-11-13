"""Share the local knowledge site through a temporary HTTPS Cloudflare tunnel."""

import argparse
import json
import os
from pathlib import Path
import queue
import re
import secrets
import socket
import subprocess
import sys
import threading
import time

from local_access import auth_state_dir


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = (BASE_DIR / "content").resolve()
CLOUDFLARED = BASE_DIR / "tools" / ("cloudflared.exe" if os.name == "nt" else "cloudflared")
ORIGIN_RE = re.compile(r"https://([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com)(?=[\s/|]|$)", re.I)


class ShareLock:
    """Hold an OS lock so stale files never block a new sharing process."""

    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        if self.stream.seek(0, os.SEEK_END) == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.stream.close()
            self.stream = None
            origin = read_state(self.path.parent / "share-state.json").get("origin")
            address = f" Current URL: {origin}." if isinstance(origin, str) and tunnel_origin(origin) == origin else ""
            raise RuntimeError("Sharing is already running." + address + " Use 'python share.py stop' to stop it.") from error
        return self

    def __exit__(self, *_):
        if self.stream is not None:
            # Closing the handle releases the OS lock, including after a crash.
            self.stream.close()
            self.stream = None


def tunnel_origin(line):
    match = ORIGIN_RE.search(line)
    return "https://" + match.group(1).lower() if match else None


def atomic_json(path, payload):
    path = Path(path)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def publish_origin(path, origin):
    atomic_json(path, {"origin": origin})


def read_state(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def clear_origin(path, origin):
    path = Path(path)
    try:
        if read_state(path).get("origin") == origin:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def metrics_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def tunnel_command(port):
    return [str(CLOUDFLARED), "tunnel", "--url", f"http://127.0.0.1:{port}",
            "--protocol", "http2", "--no-autoupdate", "--loglevel", "info",
            "--metrics", f"127.0.0.1:{metrics_port()}"]


def stop_child(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def run_share(port):
    if not CLOUDFLARED.is_file():
        raise RuntimeError(f"Cloudflared is missing: {CLOUDFLARED}. Restore the official tunnel tool first.")
    state_dir = auth_state_dir(DATA_DIR)
    with ShareLock(state_dir / "share.lock"):
        subprocess.run([sys.executable, str(BASE_DIR / "launch.py"), "--port", str(port), "--no-browser"],
                       cwd=BASE_DIR, check=True)
        log_path = BASE_DIR / "logs" / "share-cloudflared.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        origin_path = state_dir / "public-origin.json"
        state_path = state_dir / "share-state.json"
        stop_path = state_dir / "share-stop.request"
        run_id = secrets.token_hex(16)
        stop_path.unlink(missing_ok=True)
        state = {"run_id": run_id, "pid": os.getpid(), "port": port, "origin": None}
        origin = None
        child = subprocess.Popen(
            tunnel_command(port), cwd=BASE_DIR, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        messages = queue.Queue()

        def read_logs():
            try:
                with log_path.open("a", encoding="utf-8") as log:
                    log.write("\n--- Starting HTTPS share ---\n")
                    for line in child.stdout:
                        log.write(line)
                        log.flush()
                        address = tunnel_origin(line)
                        if address:
                            messages.put(address)
            finally:
                messages.put(None)

        reader = threading.Thread(target=read_logs, name="cloudflared-log", daemon=True)
        try:
            reader.start()
            print("Connecting HTTPS share. Keep this window open; Ctrl+C stops sharing.", flush=True)
            deadline = time.monotonic() + 120
            atomic_json(state_path, state)
            while True:
                if read_state(stop_path).get("run_id") == run_id:
                    print("Sharing stop requested. The local knowledge site stays running.", flush=True)
                    break
                try:
                    address = messages.get(timeout=0.5)
                except queue.Empty:
                    address = None
                if address and origin is None:
                    origin = address
                    publish_origin(origin_path, origin)
                    state["origin"] = origin
                    atomic_json(state_path, state)
                    print(f"Share URL: {origin}", flush=True)
                    print("Send this URL to colleagues. They enter their ID and wait for your browser approval.", flush=True)
                    print("The address changes after restarting sharing. Your computer must stay on.", flush=True)
                if child.poll() is not None:
                    raise RuntimeError(f"The tunnel stopped (exit {child.returncode}). See {log_path}")
                if origin is None and time.monotonic() > deadline:
                    raise RuntimeError(f"No public address was returned within 120 seconds. See {log_path}")
        finally:
            stop_child(child)
            if reader.ident is not None:
                reader.join(timeout=5)
            child.stdout.close()
            if origin:
                clear_origin(origin_path, origin)
            if read_state(state_path).get("run_id") == run_id:
                state_path.unlink(missing_ok=True)
            if read_state(stop_path).get("run_id") == run_id:
                stop_path.unlink(missing_ok=True)


def request_stop():
    state_dir = auth_state_dir(DATA_DIR)
    state_path = state_dir / "share-state.json"
    state = read_state(state_path)
    run_id = state.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
        print("No active share state found. The local knowledge site was not stopped.")
        return
    atomic_json(state_dir / "share-stop.request", {"run_id": run_id})
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if read_state(state_path).get("run_id") != run_id:
            print("Sharing stopped. The local knowledge site is still running.")
            return
        time.sleep(0.2)
    raise RuntimeError("No response from the sharing process. It may already have exited; start sharing again to replace stale state.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "stop"), nargs="?", default="start")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if args.command == "stop":
        request_stop()
    else:
        run_share(args.port)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSharing stopped. The local knowledge site is still running.")
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
