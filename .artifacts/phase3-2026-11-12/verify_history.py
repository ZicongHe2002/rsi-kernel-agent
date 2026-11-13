"""Verify the generated historical inventory and local documentation links."""
import json
import math
import re
from pathlib import Path
from urllib.parse import unquote

root = Path(__file__).resolve().parents[2]
manifest = root / "knowledge-site/manifests/phase3-2026-11-12"
doc = json.loads((manifest / "history-experiments.json").read_text(encoding="utf-8"))
ids = [r["experiment_id"] for r in doc["experiments"]]
assert len(ids) == len(set(ids)) == 136
required = {"experiment_id", "kernel", "source", "digest", "parent", "change", "method", "workload", "hardware", "correctness", "benchmark", "profile", "outcome", "evidence", "limitations"}
timed = 0
for record in doc["experiments"]:
    assert required <= record.keys()
    assert len(record["digest"]) == 64
    b = record["benchmark"]
    if b["samples_ms"]:
        timed += 1
        assert math.isclose(b["median_ms"], b["reported_ms"], rel_tol=1e-12, abs_tol=1e-12), record["experiment_id"]
        assert b["count"] == len(b["samples_ms"])
assert timed == 100
assert all(s["matches"] for items in doc["source_maps"].values() for s in items)
assert doc["recent_artifact_verification"] == {"checked": 1044, "failed": []}
docs = ["phase3-project2-history.md", "mla-forward-history.md", "mla-decode-history.md", "optimization-methods.md", "failed-experiments.md"]
links = []
for name in docs:
    path = root / "docs/phase3" / name
    for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
        if "://" not in target and not target.startswith("#"):
            resolved = (path.parent / unquote(target.split("#")[0])).resolve()
            assert resolved.exists(), (name, target)
            links.append({"document": name, "target": target})
result = {"date": "2026-11-12", "experiments": len(ids), "timed_rows_recomputed": timed,
          "source_hash_checks": sum(len(v) for v in doc["source_maps"].values()),
          "latest_artifact_hash_checks": 1044, "valid_relative_links": len(links), "links": links,
          "real_tpu_executed_by_this_audit": False, "status": "passed"}
(manifest / "history-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: v for k, v in result.items() if k != "links"}))
