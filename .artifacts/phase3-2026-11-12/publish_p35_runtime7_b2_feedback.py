"""Publish one idempotent, evidence-bound B2 feedback association note.

The note makes verified A2/diagnostic feedback visible to the B strategy's
next Analyzer history. It does not create a Run, change budget, or contact a
Runtime. Existing records and the final Memory report are checked before the
single note is appended.
"""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src")]

from rsi_kernel_agent.contracts import ArtifactRef, Decision, canonical_json
from rsi_kernel_agent.store import ExperimentStore, StoreError
from rsi_kernel_agent.workflow.controller import stable
from rsi_kernel_agent.workflow.journal import WorkflowJournal

D = Path(__file__).resolve().parent
TASK = "mla-decode-v1"
ROUND = "real-runtime7-b2-continuation-round:2"
B = "mla-strategy:ad3416851dfcc72142373541d6dc1f47121b390cce460b7fa479e9436b97e3cd"
REPORT = D / "p35-final-memory-20261113T190339063283Z.json"
RECOVERY = D / "real-round-2-runtime7" / "completed.json"
DIAGNOSTIC = D / "single-tile-diagnostic-e18e6e9bf4a98216" / "completed.json"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def ref(store, raw):
    saved = store.artifacts.put(raw)
    return asdict(saved)


def compact_result(row):
    return {
        k: row[k] for k in
        ("run_id", "classification", "correctness", "timing_validity", "confirmed", "latency_ms")
    }


def main():
    store = ExperimentStore(D / "experiments.sqlite3", D / "artifacts")
    report_raw = REPORT.read_bytes()
    report = json.loads(report_raw)
    recovery = read(RECOVERY)
    diagnostic = read(DIAGNOSTIC)
    state = WorkflowJournal(store, TASK).load()
    if state is None or state["cancelled"]:
        raise RuntimeError("Task is absent or cancelled")
    if state["limits"]["executions"] != 18 or state["limits"]["time_s"] != 32400:
        raise RuntimeError("B2 grant has not been applied")
    if state["spent"] != {"executions": 15, "tokens": 0, "time_s": 27000}:
        raise RuntimeError("Unexpected spending before B2")
    jobs = [j for j in state["jobs"].values() if j["round_id"] == ROUND]
    if jobs:
        raise RuntimeError("B2 jobs already exist; association must precede prepare")
    if report.get("status") != "verified" or report.get("runtime_generation") != "runtime7":
        raise RuntimeError("Final Memory is not the verified Runtime7 report")
    if report.get("missing_verified_runs") or report.get("missing_verification_reports"):
        raise RuntimeError("Final Memory has missing verification evidence")
    if len(report.get("verified_runs", [])) != 4:
        raise RuntimeError("Expected four verified milestone runs")
    if recovery["round_id"] != "real-runtime7-recovery-round:2" or diagnostic["round_id"] != "p35-runtime7-single-tile-diagnostic:1":
        raise RuntimeError("Completion round identity changed")

    # Keep only the result facts needed by the next Analyzer. The original
    # completion JSONs remain the authoritative full records.
    a2 = [compact_result(row) for row in recovery["results"].values()]
    diag = [compact_result(row) | {"context_id": diagnostic["context_id"]}
            for row in diagnostic["results"].values()]
    details = {
        "type": "p35_b2_feedback_association.v1",
        "runtime_generation": "runtime7",
        "runtime_id": "tpu-v6e1-s-euw4a0-3qldb90w629mv",
        "context_id": "runtime-context:b60862eb713e457a38d5a704bb3d7f1ceae68a118e919a326b0da13f50689b62",
        "recovery": {"path": str(RECOVERY.resolve()), "sha256": sha256(RECOVERY.read_bytes()).hexdigest(),
                     "snapshot": recovery["snapshot"]},
        "diagnostic": {"path": str(DIAGNOSTIC.resolve()), "sha256": sha256(DIAGNOSTIC.read_bytes()).hexdigest(),
                       "snapshot": diagnostic["snapshot"]},
        "a2_results": sorted(a2, key=lambda r: r["run_id"]),
        "diagnostic_results": sorted(diag, key=lambda r: r["run_id"]),
        "final_memory": {
            "status": report["status"],
            "report_ref": ref(store, report_raw),
            "report_sha256": sha256(report_raw).hexdigest(),
            "original_second_round_scope_complete": report["original_second_round_scope_complete"],
            "pending_recovery_candidates": report["pending_recovery_candidates"],
        },
        "grant": {"key": "p35-runtime7-b2-after-a2-diagnostic-v1", "applied_revision": state["revision"],
                  "limits": state["limits"], "spent": state["spent"]},
    }
    identity = stable("p35-b2-feedback-association", TASK + ":" + ROUND)
    note = Decision(identity, TASK, "note", B,
                    "Verified Runtime7 A2 and independent diagnostic feedback for B2 Analyzer",
                    details)
    with WorkflowJournal(store, TASK).lock():
        prior = None
        try:
            prior = store.get(identity)
        except StoreError:
            pass
        if prior is not None:
            if prior != note:
                raise RuntimeError("Existing B2 feedback association differs")
            result = {"status": "already_present", "id": identity}
        else:
            store.append(note, identity)
            result = {"status": "appended", "id": identity}
    result.update({"sha256": sha256(canonical_json(details).encode("utf-8")).hexdigest(),
                   "journal_revision": WorkflowJournal(store, TASK).load()["revision"],
                   "state_spent": WorkflowJournal(store, TASK).load()["spent"]})
    print(canonical_json(result))


if __name__ == "__main__":
    main()
