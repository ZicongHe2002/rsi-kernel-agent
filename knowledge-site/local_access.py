"""Credentials for local launchers and the owner's private stdio MCP only."""

import os
from pathlib import Path
import secrets


def auth_state_dir(data_dir=None):
    root = Path(data_dir or Path(__file__).resolve().parent / "content").resolve()
    return root.parent / ".auth"


def get_service_token(data_dir=None, create=False):
    path = auth_state_dir(data_dir) / "local-service.key"
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(secrets.token_urlsafe(48))
    if not path.is_file():
        return None
    value = path.read_text(encoding="ascii").strip()
    if len(value) < 40:
        raise RuntimeError("Invalid local service credential; inspect local configuration.")
    return value


def local_headers(data_dir=None):
    token = get_service_token(data_dir)
    return {"Authorization": "Bearer " + token} if token else {}
