"""Stop only the knowledge-site process owned by this launcher."""

import json
from pathlib import Path
import sys
import time
from urllib.error import URLError
from urllib.request import Request

from launch import DATA_DIR, OPENER, get_health, matches_site, parse_args, state_path
from local_access import local_headers


def main():
    args = parse_args(__doc__)
    saved_path = state_path(args.port)
    health = get_health(args.port)
    if health is None:
        print("No responding knowledge site on this port. No process was stopped.")
        return
    if not matches_site(health):
        raise RuntimeError("This port belongs to another site; it was not stopped.")
    if not saved_path.is_file():
        raise RuntimeError("This server was not started by launch.py. Stop it from its original terminal.")
    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    if (saved.get("pid") != health.get("pid") or saved.get("port") != args.port
            or Path(saved.get("data_dir", "")).resolve() != DATA_DIR
            or not isinstance(saved.get("token"), str)):
        raise RuntimeError("Saved launch information does not match this process; it was not stopped.")
    request = Request(f"http://127.0.0.1:{args.port}/api/shutdown",
                      data=json.dumps({"token": saved["token"]}).encode("utf-8"),
                      headers={"Content-Type": "application/json", **local_headers(DATA_DIR)}, method="POST")
    with OPENER.open(request, timeout=3) as response:
        result = json.load(response)
    if result.get("stopping") is not True:
        raise RuntimeError("The server did not confirm shutdown.")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = get_health(args.port)
        if current is None or current.get("pid") != saved["pid"]:
            saved_path.unlink(missing_ok=True)
            print("Knowledge site stopped.")
            return
        time.sleep(0.2)
    raise RuntimeError("Shutdown was requested but is not complete yet. Try again shortly.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, URLError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
