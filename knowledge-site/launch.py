"""Start the local knowledge site in the background."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import build_opener, ProxyHandler, Request
import webbrowser

from local_access import local_headers
from owner_admin import owner_url


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = (BASE_DIR / "content").resolve()
LOG_DIR = BASE_DIR / "logs"
OPENER = build_opener(ProxyHandler({}))


def get_health(port):
    try:
        request = Request(f"http://127.0.0.1:{port}/api/health", headers=local_headers(DATA_DIR))
        with OPENER.open(request, timeout=1) as response:
            health = json.load(response)
    except (URLError, OSError, ValueError):
        return None
    if not isinstance(health, dict):
        return None
    return health


def matches_site(health):
    return (health.get("app") == "zhixu"
            and isinstance(health.get("data_dir"), str)
            and Path(health["data_dir"]).resolve() == DATA_DIR)


def state_path(port):
    return LOG_DIR / f"server-{port}.json"


def parse_args(description, browser=False):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--port", type=int, default=8765)
    if browser:
        parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    return args


def main():
    args = parse_args(__doc__, browser=True)
    url = f"http://127.0.0.1:{args.port}"
    health = get_health(args.port)
    if health is not None:
        if not matches_site(health):
            raise RuntimeError(f"Port {args.port} belongs to another site. Use --port 8766.")
        print(f"Already running: {url}")
    else:
        LOG_DIR.mkdir(exist_ok=True)
        token = secrets.token_urlsafe(32)
        environment = dict(os.environ, PYTHONUTF8="1", ZHIXU_SHUTDOWN_TOKEN=token)
        stdout_path = LOG_DIR / f"server-{args.port}.log"
        stderr_path = LOG_DIR / f"server-{args.port}-error.log"
        with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
            child = subprocess.Popen(
                [sys.executable, "-u", str(BASE_DIR / "server.py"), "--port", str(args.port), "--data-dir", str(DATA_DIR)],
                cwd=BASE_DIR, env=environment, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            health = get_health(args.port)
            if health is not None:
                if health.get("app") == "zhixu" and "data_dir" not in health:
                    # The socket can accept health checks before the server has
                    # created the local service credential. Retry with the key.
                    time.sleep(0.2)
                    continue
                if not matches_site(health):
                    raise RuntimeError(f"Port {args.port} belongs to another site.")
                if health.get("instance") != hashlib.sha256(token.encode()).hexdigest():
                    raise RuntimeError("Another launcher started this site at the same time. Run the launcher again.")
                if isinstance(health.get("pid"), int):
                    break
            if child.poll() is not None:
                raise RuntimeError(f"Server could not start. See {stderr_path}")
            time.sleep(0.2)
        else:
            raise RuntimeError(f"Startup not confirmed. Check {stderr_path} and try again.")
        # Health reports the actual Python server PID, even through a uv wrapper.
        state = {"port": args.port, "pid": health["pid"], "data_dir": str(DATA_DIR), "token": token}
        temporary = state_path(args.port).with_suffix(".json.tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(state_path(args.port))
        print(f"Started: {url}")
    if not args.no_browser:
        # The fragment is consumed by the browser and never reaches HTTP logs.
        webbrowser.open(owner_url(DATA_DIR, args.port))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
