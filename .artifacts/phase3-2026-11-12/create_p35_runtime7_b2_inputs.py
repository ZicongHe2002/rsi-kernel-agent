"""Create and validate the bounded Runtime 7 B2 grant/spec inputs.

This is deliberately a one-shot preflight. It archives the exact user
authorization bytes, builds the explicit grant and spec from the live Journal
and verified final Memory report, and runs the dedicated read-only milestone
validator. It does not apply the budget grant or contact a Runtime.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rsi_kernel_agent.contracts import ArtifactRef, canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from p35_b2_milestone import validate_p35_milestone
from real_round_aux import read_json, require_gates, write_once


D = Path(__file__).resolve().parent
DB = D / "experiments.sqlite3"
ARTIFACTS = D / "artifacts"
TASK = "mla-decode-v1"
GENERATION = "runtime7"
REPORT = D / "p35-final-memory-20261113T190339063283Z.json"
AUTH = D.parent.parent / "knowledge-site" / "manifests" / "phase3-2026-11-12" / "sources" / "p35-continuation-request-2026-11-13.txt"
ROUND = "real-runtime7-b2-continuation-round:2"
GRANT_KEY = "p35-runtime7-b2-after-a2-diagnostic-v1"
QUOTE = "同时如果第二轮ok的话，你就一直自己迭代，如果tpuv6-e分配不到，就一直申请不要停"


def digest(value):
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def main():
    store = ExperimentStore(DB, ARTIFACTS)
    raw_auth = AUTH.read_bytes()
    auth_ref = {"digest": sha256(raw_auth).hexdigest(), "size": len(raw_auth)}
    require_quote = QUOTE in raw_auth.decode("utf-8")
    if not require_quote or auth_ref != {
        "digest": "3b222c97f346ff54e414eafa743317419c52748c823e3a42ba45c669d37c79fd",
        "size": 1786,
    }:
        raise RuntimeError("Authorization source bytes differ")
    # Archive the exact source bytes through the canonical ArtifactStore API.
    saved = store.artifacts.put(raw_auth)
    if {"digest": saved.digest, "size": saved.size} != auth_ref:
        raise RuntimeError("Authorization artifact identity changed")

    report_raw = REPORT.read_bytes()
    report = json.loads(report_raw)
    if report.get("status") != "verified" or report.get("runtime_generation") != GENERATION:
        raise RuntimeError("Final Memory report is not the verified Runtime 7 report")
    state = WorkflowJournal(store, TASK).load()
    if state is None or state["cancelled"]:
        raise RuntimeError("Task is absent or cancelled")
    if state["limits"]["executions"] != 16 or state["limits"]["time_s"] != 28800:
        raise RuntimeError("Pre-grant limits changed")
    if state["spent"] != {"executions": 15, "tokens": 0, "time_s": 27000}:
        raise RuntimeError("Pre-grant spending changed")
    if any(job["state"] not in ("completed", "failed", "cancelled") for job in state["jobs"].values()):
        raise RuntimeError("An unfinished job remains before B2")
    if any(key.startswith("budget:") for key in state["commands"]):
        raise RuntimeError("A prior budget grant already exists")
    gates, _ = require_gates(store, D, {"generation": GENERATION})
    b = gates["baseline-b"]
    rows = []
    for round_id in ("real-runtime7-recovery-round:2", "p35-runtime7-single-tile-diagnostic:1"):
        jobs = [j for j in state["jobs"].values() if j["round_id"] == round_id]
        if len(jobs) != 2 or len({j["context_id"] for j in jobs}) != 1:
            raise RuntimeError("Milestone round does not contain exactly two jobs")
        rows.append({"round_id": round_id, "context_id": jobs[0]["context_id"],
                     "run_ids": sorted(j["run_id"] for j in jobs)})
    milestone = {"snapshot": deepcopy(report["final_snapshot"]), "rounds": rows,
                 "successful_round_id": "real-runtime7-recovery-round:2"}
    grant = {
        "schema_version": 1,
        "task_id": TASK,
        "expected_revision": state["revision"],
        "expected_state_digest": digest(state),
        "old_limits": deepcopy(state["limits"]),
        "new_limits": {**state["limits"], "executions": 18, "time_s": 32400},
        "spent": deepcopy(state["spent"]),
        "next_batch": {"round_id": ROUND,
                        "purpose": "Complete the two remaining B routes after verified Runtime 7 A2 and independent single-tile feedback",
                        "cost": {"executions": 2, "tokens": 0, "time_s": 3600}},
        "authorization": {"source": "knowledge-site/manifests/phase3-2026-11-12/sources/p35-continuation-request-2026-11-13.txt:18; original continuing user request",
                           "quote": QUOTE, "artifact_ref": auth_ref},
        "milestone": milestone,
    }
    spec = {
        "schema_version": 1, "task_id": TASK, "round_id": ROUND,
        "context_id": b["context_id"], "runtime_generation": GENERATION,
        "runtime_id": b["contract"]["context"]["environment"]["runtime_session_id"],
        "strategies": [b["strategy_id"]], "grant_key": GRANT_KEY,
        "grant_digest": digest(grant), "milestone": deepcopy(milestone),
        "knowledge": deepcopy(read_json(D / "p35-diagnostic-selection-runtime7.json")["knowledge"]),
        "milestone_validation": {"final_memory_report": {
            "path": str(REPORT.resolve()), "sha256": sha256(report_raw).hexdigest()}},
    }
    validation = validate_p35_milestone(store, D, spec, grant, gates)
    if validation.get("status") != "verified":
        raise RuntimeError("Dedicated milestone validator did not verify")
    write_once(D / "p35-runtime7-b2-grant.json", grant)
    write_once(D / "p35-runtime7-b2-spec.json", spec)
    write_once(D / "p35-runtime7-b2-preflight.json", {
        "status": "verified_pregrant", "grant_key": GRANT_KEY,
        "grant_digest": digest(grant), "spec_digest": digest(spec),
        "authorization_artifact_ref": auth_ref, "validation": validation,
        "report": str(REPORT.resolve()), "report_sha256": sha256(report_raw).hexdigest(),
        "local_json_files_written": 3, "authorization_artifact_put_calls": 1,
        "experiment_records_appended": 0, "journal_records_appended": 0,
        "budget_applied": False, "remote_calls": 0,
    })
    print(canonical_json({"status": "verified_pregrant", "grant": str(D / "p35-runtime7-b2-grant.json"),
                          "spec": str(D / "p35-runtime7-b2-spec.json"),
                          "preflight": str(D / "p35-runtime7-b2-preflight.json"),
                          "grant_digest": digest(grant), "spec_digest": digest(spec),
                          "journal_revision": state["revision"], "spent": state["spent"],
                          "validation": validation}))


if __name__ == "__main__":
    main()
