"""Recover structured knowledge from named historical result files, without executing them."""
from __future__ import annotations

import collections
import csv
import hashlib
import json
import statistics
import zipfile
from pathlib import Path

ROOT = Path(r"\\?\C:\Users\fengluo\Desktop\project2")
CURRENT = Path(r"C:\Users\fengluo\Desktop\training\PallasKernels")
OUT = Path(__file__).resolve().parents[2] / "knowledge-site/manifests/phase3-2026-11-12"
evidence = {}
experiments = []

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def ref(path):
    path = Path(path)
    rel = path.relative_to(ROOT).as_posix()
    if rel not in evidence:
        evidence[rel] = {"path": rel, "bytes": path.stat().st_size, "sha256": digest(path)}
    return evidence[rel]

def load(path):
    ref(path)
    return json.loads(path.read_text(encoding="utf-8-sig"))

def rows(path):
    ref(path)
    return list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))

def source_map(root, declared):
    result = []
    for rel, sha in sorted(declared.items()):
        path = root / rel
        item = {"path": path.relative_to(ROOT).as_posix(), "declared_sha256": sha}
        if path.is_file():
            item.update(sha256=digest(path), bytes=path.stat().st_size)
            item["matches"] = item["sha256"] == sha
        else:
            item["matches"] = False
            item["reason"] = "missing"
        result.append(item)
    return result

def stats(values):
    return {"samples_ms": values, "count": len(values),
            "median_ms": statistics.median(values) if values else None,
            "mean_ms": statistics.mean(values) if values else None,
            "stdev_ms": statistics.stdev(values) if len(values) > 1 else None}

fwd = ROOT / "result/imported/result14"
fm = load(fwd / "manifest.json")
fsm = load(fwd / "source_manifest.json")
fwd_sources = source_map(fwd / "source_snapshot", fsm["files"])
for candidate in fm["candidates"]:
    phase, name = candidate["phase"], candidate["implementation"]
    directory = candidate["directory"].split("/results/", 1)[1]
    base = fwd / directory
    raw = rows(base / "device_event_raw_samples.csv") if (base / "device_event_raw_samples.csv").exists() else []
    samples = [float(x["device_duration_ms"]) for x in raw if x.get("event_scope") == "pipeline"]
    accuracy = rows(base / "accuracy.csv") if (base / "accuracy.csv").exists() else []
    profiles = [ref(p) for p in base.rglob("*.xplane.pb")]
    if not profiles:
        profiles = [ref(p) for p in base.rglob("perfetto_trace.json.gz")]
    stage = next((s for s in candidate.get("stages", []) if s["stage"] == "pipeline"), {})
    recipe = next(r for r in fm["recipes"] if r.get("name") == name)
    source = "pallas_kernels/kernels/" + {"research_layout": "mla_fwd_research_layout.py", "research_state": "mla_fwd_research_state.py", "research_persistent": "mla_fwd_research_persistent.py", "bdu01_square": "mla_fwd_bdu01_square.py", "pallas_paired": "mla_fwd_paired_experimental.py"}[recipe["backend"]]
    experiments.append({"experiment_id": f"fwd-result14:{phase}:{name}", "kernel": "MLA Forward",
        "source": (fwd / "source_snapshot" / source).relative_to(ROOT).as_posix(),
        "digest": next((s.get("sha256") for s in fwd_sources if s["path"].endswith("/" + source)), None),
        "source_manifest": "fwd-result14", "parent": "unknown", "parent_basis": "No per-candidate commit ancestry; same source set with recipe selection.",
        "comparison_baseline": recipe["control"], "recipe_parent_label": recipe["parent"],
        "change": recipe["hypothesis"], "method": name, "recipe": recipe,
        "workload": fm["shape"], "hardware": {k: fm["environment"].get(k) for k in ("jax", "jaxlib", "libtpu", "device_kinds", "timed_chip_count")},
        "correctness": {"scope": candidate.get("accuracy_scope"), "rows": accuracy},
        "benchmark": dict(stats(samples), phase=phase, reported_ms=stage.get("latency_ms", candidate.get("pipeline_ms")), boundary="seven inputs to O including projections/layouts", kind="historical trace-derived device event"),
        "profile": profiles, "outcome": candidate["result"], "evidence": [ref(fwd / "manifest.json"), ref(base / "candidate.json")] if (base / "candidate.json").exists() else [ref(fwd / "manifest.json")],
        "evidence_grade": "historical_real_tpu_raw_artifacts" if profiles and samples else "historical_compile_failure_artifacts",
        "limitations": ["Not a current RSI Run; no current Best eligibility", "JAX 0.7.2; loaded libtpu binary not independently re-attested", "No valid LLO/spill/hardware memory counters in this round", "One session fixed-order comparison, not cross-session reproducibility"]})

decode_sources = {}
for round_no in (2, 3):
    root = ROOT / f"result/decode/result-decode-{round_no}"
    ref(root / "ANALYSIS.md")
    for runfile in sorted(root.glob("run_*/results.json")):
        run = load(runfile)
        run_dir = runfile.parent
        key = f"decode-r{round_no}:{run_dir.name}"
        decode_sources[key] = source_map(run_dir / "source_snapshot", run["source_sha256"])
        for candidate in run["candidates"]:
            name = candidate["name"]
            base = run_dir / name
            profiles = [ref(p) for p in base.rglob("*.xplane.pb")]
            source = ("aligned_kernels.py" if name.startswith("A_") else "round2_kernels.py" if name.startswith("R2_") else "upstream_adapters.py" if name.startswith(("G_", "SGL_", "M_")) else "decode_kernels.py")
            samples = candidate.get("device_samples_ms", [])
            experiments.append({"experiment_id": f"{key}:{name}", "kernel": "MLA Decode",
                "source": (run_dir / "source_snapshot" / source).relative_to(ROOT).as_posix(),
                "digest": run["source_sha256"].get(source), "source_manifest": key,
                "parent": "unknown", "parent_basis": "No per-candidate commit ancestry; explicit configuration and same-round comparator preserved.",
                "comparison_baseline": run.get("baseline_name"), "change": candidate.get("extension_metadata", candidate.get("config")),
                "method": name, "workload": run["configuration"], "hardware": run["environment"],
                "correctness": {"status": candidate.get("correctness_status"), "gates": candidate.get("accuracy"), "numerical_category": candidate.get("numerical_category")},
                "benchmark": dict(stats(samples), reported_ms=candidate.get("device_median_ms"), boundary=run["timing_scope"], kind="historical trace-derived device event"),
                "profile": profiles, "outcome": candidate["status"], "failure_reason": candidate.get("failure_reason"),
                "evidence": [ref(runfile)], "evidence_grade": "historical_real_tpu_raw_artifacts" if profiles and samples else "historical_tpu_failure_artifacts",
                "limitations": ["Dense and resident-cache native boundaries differ", "Logical effective bandwidth is not measured HBM utilization", "No current RSI Run/Best", "Fixed-order single-session measurements; per-candidate Git parent unknown"]})

recent = ROOT / "result/mla_vmem_rebase_20261111/downloaded"
recent_manifest = load(recent / "manifest.json")
payload_path = recent.parent / "payload.zip"
ref(payload_path)
payload = zipfile.ZipFile(payload_path)
recent_sources = []
for rel, declared in sorted(recent_manifest["files"].items()):
    data = payload.read("source/" + rel)
    actual = hashlib.sha256(data).hexdigest()
    recent_sources.append({"path": payload_path.relative_to(ROOT).as_posix() + "!source/" + rel,
                           "declared_sha256": declared, "sha256": actual,
                           "bytes": len(data), "matches": actual == declared})
artifact_checks = []
for item in load(recent / "artifact_manifest.json"):
    path = recent / item["path"]
    if path.is_file():
        actual = digest(path)
        artifact_checks.append({"path": item["path"], "bytes_match": path.stat().st_size == item["bytes"], "sha256_match": actual == item["sha256"]})
    else:
        artifact_checks.append({"path": item["path"], "missing": True})
current_comparison = []
for kernel in ("mla_fwd", "mla_decode"):
    resultfile = recent / f"results/{kernel}/result.json"
    run = load(resultfile)
    source = f"pallas_kernels/kernels/{kernel}.py"
    archived = payload.read("source/" + source)
    current = CURRENT / source
    current_comparison.append({"kernel": kernel, "current_path": str(current), "historical_imported_sha256": run["imported_source_sha256"][source],
        "current_sha256": digest(current), "raw_bytes_match": current.read_bytes() == archived,
        "lf_normalized_match": current.read_bytes().replace(b"\r\n", b"\n") == archived.replace(b"\r\n", b"\n")})
    experiments.append({"experiment_id": f"vmem-rebase-20261111:{kernel}", "kernel": "MLA Forward" if kernel == "mla_fwd" else "MLA Decode",
        "source": payload_path.relative_to(ROOT).as_posix() + "!source/" + source, "digest": hashlib.sha256(archived).hexdigest(), "source_manifest": "vmem-rebase-20261111",
        "parent": "unknown", "source_commit": run["source_commit"], "change": "Unmodified registry default at recorded rebased commit; VMEM characterization",
        "method": "Source-bound full-call measurement and XProf compiler-memory analysis", "workload": run["config"],
        "hardware": {k: run[k] for k in ("python", "versions", "device_kind", "platform_version")},
        "correctness": run["comparisons"], "benchmark": dict(stats(run["device_profile"]["samples_ms"]), kind="historical profiling run, not unprofiled benchmark", boundary="full registry call", reported_ms=run["device_profile"]["median_ms"]),
        "profile": [ref(p) for p in (recent / f"results/{kernel}/profile").rglob("*.xplane.pb")],
        "outcome": run["status"], "evidence": [ref(resultfile), ref(recent / "measure.py")], "evidence_grade": "historical_real_tpu_raw_artifacts",
        "limitations": ["Profiling run latency is not separate formal benchmark", "XProf peak VMEM is compiler accounting, not physical runtime high water", "Not a current RSI Run/Best; imported hash does not attest loaded native library"]})

reports = ["result/INDEX.md", "result/mla_fwd_v6e_optimization_trajectory.md", "result/mla_fwd_v6e_result_5_report.md", "result/mla_fwd_v6e_result_6_report.md", "result/reports/ALL_METHODS_RESULTS_AND_CONCLUSIONS.md", "result/mla_main_check_20261104/STATUS.md", "result/mla_decode_fix_20261104/README.md", "result/mla_decode_fix_20261104/commit.json", "result/mla_vmem_rebase_20261111/README.md"]
for report in reports:
    ref(ROOT / report)

git = []
for name in ("PallasKernels", "PallasKernels_mla_fwd_v6e_clean", "PallasKernels_mla_decode_full_pr_20260924", "PallasKernels-mla-decode-fix"):
    gd = ROOT / name / ".git"
    if gd.is_file():
        pointer = gd.read_text().strip().removeprefix("gitdir: ")
        gd = Path(pointer)
    head = (gd / "HEAD").read_text().strip()
    common = gd
    if (gd / "commondir").exists():
        common = (gd / (gd / "commondir").read_text().strip()).resolve()
    sha = head
    if head.startswith("ref: "):
        name_ref = head[5:]
        if (common / name_ref).exists():
            sha = (common / name_ref).read_text().strip()
        else:
            sha = next((line.split()[0] for line in (common / "packed-refs").read_text().splitlines() if line.endswith(" " + name_ref)), "unknown")
    git.append({"repository": name, "head": head, "resolved_commit": sha, "verification": "Git metadata only; no status or ancestry claim"})

output = {"schema": "phase3-historical-experiments.v1", "audited_on": "2026-11-12", "root": str(ROOT).removeprefix("\\\\?\\"),
    "warning": "Historical knowledge, not imported RSI Runs or current Best. No historical script/kernel executed.",
    "experiments": experiments, "counts": dict(collections.Counter(e["outcome"] for e in experiments)),
    "source_maps": {"fwd-result14": fwd_sources, **decode_sources, "vmem-rebase-20261111": recent_sources},
    "recent_artifact_verification": {"checked": len(artifact_checks), "failed": [c for c in artifact_checks if not c.get("bytes_match") or not c.get("sha256_match")]},
    "current_source_comparison": current_comparison, "git_metadata": git,
    "lineage": [{"child": "0f552ae6cedad2d758816c24878fdb785cecf8af", "parent": "d336a54a9fa82560c3a7403f54c4479a8872332c", "basis": "Explicit commit.json declaration and saved patch; Git ancestry not independently traversed"}, {"child": "1912b9947708725c92928cbaf8164c85b1e3cac6", "base": "9979d4949ebc673fd7e81555e4af2ebfd1619d20", "basis": "20261111 README rebase declaration and source manifest; direct parent not asserted"}],
    "evidence_files": list(evidence.values())}
(OUT / "history-experiments.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"experiments": len(experiments), "counts": output["counts"], "evidence_files": len(evidence),
 "source_mismatches": {k: sum(not f["matches"] for f in v) for k, v in output["source_maps"].items()},
 "recent_artifacts": output["recent_artifact_verification"], "current_source_comparison": current_comparison, "git": git}, ensure_ascii=False))
