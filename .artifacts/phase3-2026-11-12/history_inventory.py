"""Read-only historical evidence indexing; never imports or executes project2 code."""
from __future__ import annotations

import collections
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(r"\\?\C:\Users\fengluo\Desktop\project2")
OUT = Path(__file__).resolve().parents[2] / "knowledge-site/manifests/phase3-2026-11-12"
OUT.mkdir(parents=True, exist_ok=True)
EXCLUDED = {".git", ".venv", "venv", "node_modules", "site-packages", "__pycache__",
            ".pytest_cache", ".ruff_cache", ".uv-cache", ".uv-cache-colab", ".auth",
            ".aws", ".config", "qa-env", "lib", "scripts"}
EXTENSIONS = {".py", ".ipynb", ".md", ".txt", ".json", ".csv", ".yaml", ".yml",
              ".log", ".pb", ".hlo", ".mlir", ".zip", ".xml", ".patch", ".diff",
              ".docx", ".html", ".gz"}
TOP_EXCLUDE = {"ProtonFramework", "sglang-jax", "tpu-inference", "tpu-llo-viewer",
               "OrgGithub-coverage-comment-fix", "PallasKernels-embedding-bwd-fix",
               ".tmp_extract", ".tmp_case"}
entries, errors = [], []

def reject_name(name):
    s = name.lower()
    return (s in EXCLUDED or s.startswith((".venv", "pytest", "uv-cache"))
            or any(part in s for part in ("credential", "oauth", "cookie", "secret", "token"))
            or s in {".env", "id_rsa", "id_ed25519"})

def onerror(exc):
    errors.append({"path": str(exc.filename), "error": type(exc).__name__, "errno": exc.errno})

for folder, dirs, files in os.walk(ROOT, onerror=onerror, followlinks=False):
    base = Path(folder)
    dirs[:] = sorted(d for d in dirs if not reject_name(d)
                     and not (base == ROOT and d in TOP_EXCLUDE)
                     and not (base / d).is_symlink()
                     and not (hasattr(base / d, "is_junction") and (base / d).is_junction()))
    for name in sorted(files):
        path = base / name
        if reject_name(name) or path.suffix.lower() not in EXTENSIONS or path.is_symlink():
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            onerror(exc)
            continue
        rel = path.relative_to(ROOT).as_posix()
        category = ("xplane" if name.endswith(".xplane.pb") else
                    "source" if path.suffix == ".py" else
                    "notebook" if path.suffix == ".ipynb" else
                    "profile_or_ir" if path.suffix in {".hlo", ".mlir", ".pb"} else
                    "archive" if path.suffix == ".zip" else
                    "documentation" if path.suffix in {".md", ".docx", ".html"} else "data_or_log")
        entries.append({"path": rel, "bytes": size, "category": category})

doc = {"schema": "project2-history-index.v1", "audited_on": "2026-11-12",
       "root": str(ROOT).removeprefix("\\\\?\\"), "read_only": True,
       "scope": "Relevant top-level MLA repositories and result/docs/tools trees; inventory is path/size, not a claim every file was semantically read.",
       "excluded_top_level": sorted(TOP_EXCLUDE), "excluded_directory_names": sorted(EXCLUDED),
       "sensitive_name_filter": "credential/oauth/cookie/secret/token/.env/private-key names omitted without reading",
       "lineage_uses_mtime": False, "counts": dict(collections.Counter(x["category"] for x in entries)),
       "entries": entries, "unreadable": errors}
(OUT / "project2-inventory.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"entries": len(entries), "counts": doc["counts"], "unreadable": len(errors)}))
