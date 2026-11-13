"""Create a short-lived owner sign-in link from the local computer."""

import argparse
from pathlib import Path
import subprocess
import sys
import webbrowser

from auth import AuthStore
from local_access import auth_state_dir


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = (BASE_DIR / "content").resolve()


def owner_url(data_dir, port):
    """Mint a one-use local URL; never send the token to application logs."""
    store = AuthStore(auth_state_dir(Path(data_dir).resolve()) / "auth.sqlite3")
    try:
        token = store.create_owner_token(ttl=600)
    finally:
        store.close()
    return f"http://127.0.0.1:{port}/#owner-token={token}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap", help="sign in locally as owner fl")
    bootstrap.add_argument("--port", type=int, default=8765)
    bootstrap.add_argument("--open", action="store_true", help="open the browser without printing the sign-in link")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    subprocess.run(
        [sys.executable, str(BASE_DIR / "launch.py"), "--port", str(args.port), "--no-browser"],
        cwd=BASE_DIR, check=True,
    )
    url = owner_url(DATA_DIR, args.port)
    if args.open:
        if not webbrowser.open(url):
            raise RuntimeError("Browser did not open. Run bootstrap without --open to obtain a local sign-in link.")
        print("Opened local owner sign-in (fl). Link is single-use and expires in 10 minutes.")
    else:
        print("Local owner sign-in (fl). Open only on this computer; do not share this link.")
        print("Single-use, expires in 10 minutes. Creating a new link invalidates the previous unused link.")
        print(url)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
