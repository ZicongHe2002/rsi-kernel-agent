"""Phase 2A acceptance: local synthetic records, never TPU measurements."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rsi_kernel_agent.store import ExperimentStore, ConflictError, StoreError
from rsi_kernel_agent.artifacts import ArtifactStore
from rsi_kernel_agent.contracts import (
    ArtifactRef, Candidate, CandidateIdentity, EvaluationBundle, EvaluationContext, Evidence,
    ParentCandidate, Plan, Run, RunEvent, Strategy, Task, Workload,
)


def identity(label):
    return CandidateIdentity(*(
        hashlib.sha256((label + suffix).encode()).hexdigest()
        for suffix in ("-source", "-config", "-build")
    ))


class FoundationAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "experiments.sqlite3"
        self.artifact_root = self.root / "artifacts"
        self.store = ExperimentStore(self.db, self.artifact_root)
        self.artifacts = ArtifactStore(self.artifact_root)
        self.expected_ids = set()
        self.add(Task("task", "Synthetic CPU infrastructure fixture", "contract-v1"))
        self.add(Workload("workload", "task", {"shape": [4, 8], "dtype": "fixture"}))
        self.add(EvaluationContext(
            "context", "task", "workload", {"device": "synthetic"},
            {"clock": "fixture", "boundary": "fixture", "unit": "ms"}, "synthetic",
        ))
        self.add(ParentCandidate("baseline", "task", identity("baseline")))
        for strategy in ("A", "B"):
            self.add(Strategy(strategy, "task", "Strategy " + strategy))
            for plan in ("1", "2"):
                plan_id = strategy + "plan" + plan
                self.add(Plan(plan_id, strategy, "Fixture plan " + plan))
                for candidate in ("1", "2"):
                    candidate_id = strategy + plan + candidate
                    self.add(Candidate(candidate_id, plan_id, "baseline", identity(candidate_id)))

    def add(self, record):
        self.store.append(record, "append:" + record.id)
        self.expected_ids.add(record.id)
        return record

    def event(self, run, execution, scheduling, previous=None):
        event = RunEvent(run + ":" + execution + ":" + scheduling, run,
                         execution, scheduling, "synthetic fixture state")
        self.store.transition(event, previous, "event:" + event.id)
        self.expected_ids.add(event.id)
        return event.id

    def evaluation(self, suffix, candidate, latency, context="context"):
        run = "run:" + suffix
        self.add(Run(run, candidate, context))
        active = self.event(run, "running", "active")
        self.event(run, "succeeded", "finished", active)
        ref = self.artifacts.put(json.dumps({"synthetic_latency_ms": latency}).encode())
        evidence = self.add(Evidence(
            "timing:" + suffix, run, "measured_timing", "available", "completed",
            "supported", value=latency, unit="ms", artifacts=(ref,),
            source={"provenance": "synthetic", "clock": "fixture", "boundary": "fixture"},
        ))
        return self.add(EvaluationBundle(
            "evaluation:" + suffix, run, "passed", "valid", latency, True, (evidence.id,),
        ))

    def child(self, code, *args):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", code, str(self.db), str(self.artifact_root), *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment,
        )
        self.addCleanup(self.reap, process)
        return process

    @staticmethod
    def reap(process):
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)

    def finish(self, process, expected_returncode=0):
        output, errors = process.communicate(timeout=30)
        self.assertEqual(process.returncode, expected_returncode, errors)
        return output.strip()

    def test_many_branches_plans_candidates_and_repair_keep_exact_lineage(self):
        self.add(ParentCandidate("parent:A11", "task", identity("A11"), "A11"))
        self.add(Candidate("A11repair", "Aplan1", "parent:A11", identity("repair"), "repair"))
        history = {record.id: record for record in self.store.history("task")}
        self.assertEqual(set(history), self.expected_ids)
        for strategy in ("A", "B"):
            plans = [r.id for r in history.values() if isinstance(r, Plan) and r.strategy_id == strategy]
            self.assertEqual(set(plans), {strategy + "plan1", strategy + "plan2"})
            for plan in plans:
                self.assertGreaterEqual(len([r for r in history.values()
                                             if isinstance(r, Candidate) and r.plan_id == plan]), 2)
        self.assertEqual(history["A11repair"].parent_id, "parent:A11")
        self.assertEqual(history["parent:A11"].candidate_id, "A11")
        self.assertEqual(history["parent:A11"].identity, identity("A11"))
        self.assertIsNone(history["baseline"].candidate_id)
        with self.assertRaises((StoreError, ValueError)):
            self.add(ParentCandidate("wrong-parent", "task", identity("B11"), "A11"))
        self.assertNotIn("wrong-parent", {r.id for r in self.store.history("task")})

    def test_multiple_attempts_preserve_fail_invalid_defer_incomplete_and_retry(self):
        for terminal in ("failed", "invalid", "incomplete"):
            run = "attempt:" + terminal
            self.add(Run(run, "A11", "context"))
            active = self.event(run, "running", "active")
            final = self.event(run, terminal, "finished", active)
            self.assertEqual(self.store.run_state(run)["execution_status"], terminal)
            with self.assertRaises((StoreError, ValueError)):
                self.event(run, "running", "active", final)
        self.add(Run("attempt:deferred", "A11", "context"))
        self.event("attempt:deferred", "not_run", "deferred")
        self.add(Run("attempt:unfinished", "A11", "context"))
        self.event("attempt:unfinished", "running", "active")
        self.evaluation("retry", "A11", 10.0)
        states = {
            r.id: self.store.run_state(r.id)
            for r in self.store.history("task") if isinstance(r, Run)
        }
        self.assertEqual(len(states), 6)
        self.assertEqual(states["attempt:deferred"]["execution_status"], "not_run")
        self.assertEqual(states["attempt:deferred"]["scheduling_status"], "deferred")
        self.assertEqual(states["attempt:unfinished"]["execution_status"], "running")
        self.assertEqual(states["run:retry"]["execution_status"], "succeeded")

    def test_best_per_branch_task_and_frontier_are_independent_and_monotonic(self):
        a = self.evaluation("a", "A11", 10.0)
        b = self.evaluation("b", "B11", 8.0)
        slower = self.evaluation("slower", "A12", 12.0)
        for evaluation in (a, b, slower):
            decision = self.store.consider(evaluation.id, "consider:" + evaluation.id)
            self.expected_ids.add(decision.id)
        self.assertEqual(self.store.best("task", "context", "A").id, a.id)
        self.assertEqual(self.store.best("task", "context", "B").id, b.id)
        self.assertEqual(self.store.best("task", "context").id, b.id)
        self.assertFalse(decision.details["branch_promoted"])
        self.assertFalse(decision.details["task_promoted"])
        self.store.set_frontier("A12", "context", True, "Explore a distinct approach", "frontier:add")
        self.assertEqual(self.store.frontier("task", "context"), ["A12"])
        self.assertEqual(self.store.best("task", "context").id, b.id)
        self.store.set_frontier("A12", "context", False, "Exploration complete", "frontier:remove")
        self.assertEqual(self.store.frontier("task", "context"), [])
        self.assertIn(slower.id, {r.id for r in self.store.history("task")})

    def test_comparison_contexts_cannot_replace_each_others_best(self):
        original = self.evaluation("original", "A11", 10.0)
        self.store.consider(original.id, "consider:original")
        self.add(Workload("other-workload", "task", {"shape": [1], "dtype": "fixture"}))
        self.add(EvaluationContext(
            "other-context", "task", "other-workload", {"device": "synthetic"},
            {"clock": "fixture", "boundary": "fixture", "unit": "ms"}, "synthetic",
        ))
        faster_elsewhere = self.evaluation("elsewhere", "B11", 0.01, "other-context")
        self.store.consider(faster_elsewhere.id, "consider:elsewhere")
        self.assertEqual(self.store.best("task", "context").id, original.id)
        self.assertEqual(self.store.best("task", "other-context").id, faster_elsewhere.id)
        self.add(EvaluationContext(
            "real-context", "task", "workload", {"device": "unverified"},
            {"clock": "future", "boundary": "future"}, "real",
        ))
        self.add(Run("run:real-declared", "A11", "real-context"))
        artifact = self.artifacts.put(b"explicitly synthetic, cannot be bound as real")
        with self.assertRaises((StoreError, ValueError)):
            self.add(Evidence(
                "synthetic-as-real", "run:real-declared", "measured_timing", "available",
                "completed", "supported", 1.0, "ms", (artifact,),
                source={"provenance": "synthetic", "clock": "fixture", "boundary": "fixture"},
            ))
        self.assertIsNone(self.store.best("task", "real-context"))

    def test_snapshot_coverage_is_explicit_frozen_and_survives_new_records(self):
        snapshot = self.store.create_snapshot("task", "snapshot:first")
        self.assertEqual(snapshot["version"], 1)
        self.assertEqual(snapshot["record_count"], 18)
        self.assertEqual(snapshot["strategy_counts"], {"A": 7, "B": 7})
        first = self.store.snapshot_page(snapshot["id"], 0, 3)
        self.assertFalse(first["complete"])
        self.assertEqual(first["total"], 18)
        self.assertEqual(len(first["records"]), 3)
        seen = {r.id for r in first["records"]}
        offset = first["next_offset"]
        while offset is not None:
            page = self.store.snapshot_page(snapshot["id"], offset, 3)
            self.assertFalse(page["complete"])
            seen.update(r.id for r in page["records"])
            offset = page["next_offset"]
        self.assertEqual(seen, self.expected_ids)
        full = self.store.snapshot_page(snapshot["id"], 0, 100)
        self.assertTrue(full["complete"])
        self.assertEqual({r.id for r in full["records"]}, self.expected_ids)
        self.add(Plan("later-plan", "B", "After snapshot"))
        after = self.store.create_snapshot("task", "snapshot:second")
        self.assertEqual(after["version"], 2)
        self.assertEqual(after["record_count"], 19)
        self.assertGreater(after["high_watermark"], snapshot["high_watermark"])
        self.assertEqual(self.store.create_snapshot("task", "snapshot:first"), snapshot)
        unchanged = self.store.snapshot_page(snapshot["id"], 0, 100)
        self.assertEqual(unchanged, full)
        self.assertNotEqual(after["digest"], snapshot["digest"])

    def test_concurrent_process_retries_commit_once_and_conflicts_are_explicit(self):
        code = """
import sys
from rsi_kernel_agent.store import ExperimentStore, ConflictError
from rsi_kernel_agent.contracts import Plan
s = ExperimentStore(sys.argv[1], sys.argv[2])
try:
    print(s.append(Plan('concurrent-plan', 'A', sys.argv[3]), 'shared-key'))
except ConflictError:
    print('conflict')
"""
        processes = [self.child(code, "same request") for _ in range(4)]
        self.assertEqual([self.finish(p) for p in processes], ["concurrent-plan"] * 4)
        self.assertEqual(len([r for r in self.store.history("task") if r.id == "concurrent-plan"]), 1)
        self.assertEqual(self.finish(self.child(code, "changed request")), "conflict")
        self.assertEqual(self.store.get("concurrent-plan").description, "same request")

    def test_concurrent_conflicting_requests_cannot_silently_overwrite_the_winner(self):
        code = """
import sys
from rsi_kernel_agent.store import ExperimentStore, ConflictError
from rsi_kernel_agent.contracts import Plan
s = ExperimentStore(sys.argv[1], sys.argv[2])
try:
    s.append(Plan('racing-plan', 'B', sys.argv[3]), 'racing-key')
    print(sys.argv[3])
except ConflictError:
    print('conflict')
"""
        processes = [self.child(code, label) for label in ("first contender", "second contender")]
        results = [self.finish(p) for p in processes]
        self.assertEqual(results.count("conflict"), 1)
        winner = next(value for value in results if value != "conflict")
        self.assertEqual(self.store.get("racing-plan").description, winner)
        self.assertEqual(len([r for r in self.store.history("task") if r.id == "racing-plan"]), 1)

    def test_concurrent_state_transitions_accept_only_one_expected_previous_state(self):
        self.add(Run("state-race", "A11", "context"))
        code = """
import sys
from rsi_kernel_agent.store import ExperimentStore, ConflictError
from rsi_kernel_agent.contracts import RunEvent
s = ExperimentStore(sys.argv[1], sys.argv[2])
event = RunEvent('state-event:' + sys.argv[3], 'state-race', 'running', 'active',
                 'Competing local synthetic state updates')
try:
    print(s.transition(event, None, 'state-key:' + sys.argv[3]))
except ConflictError:
    print('conflict')
"""
        processes = [self.child(code, label) for label in ("one", "two")]
        results = [self.finish(p) for p in processes]
        self.assertEqual(results.count("conflict"), 1)
        winner = next(value for value in results if value != "conflict")
        state = self.store.run_state("state-race")
        self.assertEqual(state["event_id"], winner)
        self.assertEqual(state["execution_status"], "running")
        events = [r for r in self.store.history("task")
                  if isinstance(r, RunEvent) and r.run_id == "state-race"]
        self.assertEqual(len(events), 1)

    def test_reusing_old_event_id_cannot_report_a_new_transition_without_history(self):
        self.add(Run("event-reuse", "A11", "context"))
        deferred = RunEvent("old-defer", "event-reuse", "not_run", "deferred", "Wait for budget")
        self.store.transition(deferred, None, "first-deferral")
        ready = RunEvent("ready-again", "event-reuse", "not_run", "ready", "Budget available")
        self.store.transition(ready, deferred.id, "resume-ready")
        with self.assertRaises(ConflictError):
            self.store.transition(deferred, ready.id, "second-deferral-needs-new-event-id")
        self.assertEqual(self.store.run_state("event-reuse")["event_id"], "ready-again")
        events = [r for r in self.store.history("task")
                  if isinstance(r, RunEvent) and r.run_id == "event-reuse"]
        self.assertEqual([r.id for r in events], ["old-defer", "ready-again"])

    def test_artifact_binding_rejects_missing_or_corrupt_raw_bytes_without_partial_records(self):
        self.add(Run("artifact-run", "A11", "context"))
        corrupted = self.artifacts.put(b"valid synthetic fixture")
        self.artifacts.path_for(corrupted).write_bytes(b"damaged fixture")
        missing = ArtifactRef(hashlib.sha256(b"not published").hexdigest(), len(b"not published"))
        for label, reference in (("corrupt", corrupted), ("missing", missing)):
            evidence = Evidence(
                "evidence:" + label, "artifact-run", "measured_timing", "available",
                "completed", "supported", 2.0, "ms", (reference,),
                source={"provenance": "synthetic", "clock": "fixture", "boundary": "fixture"},
            )
            with self.assertRaises((StoreError, ValueError, FileNotFoundError)):
                self.store.append(evidence, "append:" + evidence.id)
        ids = {r.id for r in self.store.history("task")}
        self.assertIn("artifact-run", ids)
        self.assertNotIn("evidence:corrupt", ids)
        self.assertNotIn("evidence:missing", ids)
        valid = self.artifacts.put(b"another intact synthetic fixture")
        self.add(Evidence(
            "evidence:intact", "artifact-run", "measured_timing", "available", "completed",
            "supported", 2.0, "ms", (valid,),
            source={"provenance": "synthetic", "clock": "fixture", "boundary": "fixture"},
        ))
        self.assertEqual(self.artifacts.read(self.store.get("evidence:intact").artifacts[0]),
                         b"another intact synthetic fixture")

    def test_snapshot_keeps_best_and_frontier_as_of_its_own_version(self):
        old = self.evaluation("old-best", "A11", 10.0)
        self.store.consider(old.id, "consider:old-best")
        self.store.set_frontier("A12", "context", True, "Explore", "frontier:old")
        snapshot = self.store.create_snapshot("task", "snapshot:old-projections")
        old_page = self.store.snapshot_page(snapshot["id"], 0, 1000)
        new = self.evaluation("new-best", "B11", 5.0)
        self.store.consider(new.id, "consider:new-best")
        self.store.set_frontier("A12", "context", False, "Finished", "frontier:finished")
        self.store.set_frontier("B12", "context", True, "Explore next", "frontier:new")
        newer = self.store.create_snapshot("task", "snapshot:new-projections")
        new_page = self.store.snapshot_page(newer["id"], 0, 1000)
        self.assertNotEqual(new_page["projections"], old_page["projections"])
        self.assertEqual(self.store.snapshot_page(snapshot["id"], 0, 1000), old_page)
        self.assertEqual(self.store.best("task", "context").id, new.id)
        self.assertEqual(self.store.frontier("task", "context"), ["B12"])

    def test_later_artifact_corruption_rejects_promotion_and_preserves_history(self):
        baseline = self.evaluation("uncorrupted", "A11", 10.0)
        self.store.consider(baseline.id, "consider:uncorrupted")
        candidate = self.evaluation("damaged", "B11", 1.0)
        evidence = self.store.get("timing:damaged")
        self.artifacts.path_for(evidence.artifacts[0]).write_bytes(b"damaged after metadata commit")
        decision = self.store.consider(candidate.id, "consider:damaged")
        self.assertFalse(decision.details["eligible"])
        self.assertFalse(decision.details["task_promoted"])
        self.assertEqual(self.store.best("task", "context").id, baseline.id)
        self.assertIn(candidate.id, {r.id for r in self.store.history("task")})

    def test_concurrent_process_promotions_recover_the_global_minimum(self):
        evaluations = [self.evaluation("parallel:" + str(i), "A11", value)
                       for i, value in enumerate((20.0, 4.0, 12.0, 6.0))]
        code = """
import sys
from rsi_kernel_agent.store import ExperimentStore
s = ExperimentStore(sys.argv[1], sys.argv[2])
d = s.consider(sys.argv[3], 'parallel-promotion:' + sys.argv[3])
print(d.id)
"""
        processes = [self.child(code, evaluation.id) for evaluation in evaluations]
        decision_ids = [self.finish(p) for p in processes]
        self.assertEqual(len(set(decision_ids)), 4)
        reopened = ExperimentStore(self.db, self.artifact_root)
        self.assertEqual(reopened.best("task", "context").latency_ms, 4.0)
        self.assertEqual(reopened.best("task", "context", "A").latency_ms, 4.0)
        self.assertEqual({r.id for r in reopened.history("task")} & set(decision_ids), set(decision_ids))

    def test_fresh_process_recovers_records_best_artifacts_and_snapshot(self):
        evaluation = self.evaluation("restart", "B11", 7.0)
        self.store.consider(evaluation.id, "consider:restart")
        snapshot = self.store.create_snapshot("task", "snapshot:restart")
        code = """
import json, sys
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.artifacts import ArtifactStore
s = ExperimentStore(sys.argv[1], sys.argv[2])
evidence = s.get('timing:restart')
raw = ArtifactStore(sys.argv[2]).read(evidence.artifacts[0]).decode()
page = s.snapshot_page(sys.argv[3], 0, 1000)
print(json.dumps({'best': s.best('task', 'context').id,
                  'run_state': s.run_state('run:restart')['execution_status'],
                  'raw': json.loads(raw), 'complete': page['complete'],
                  'snapshot_records': len(page['records'])}))
"""
        actual = json.loads(self.finish(self.child(code, snapshot["id"])))
        self.assertEqual(actual, {
            "best": "evaluation:restart", "run_state": "succeeded",
            "raw": {"synthetic_latency_ms": 7.0}, "complete": True,
            "snapshot_records": 24,
        })

    def test_process_death_rolls_back_uncommitted_record_and_remains_writable(self):
        code = """
import hashlib, json, os, sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
connection.execute('BEGIN IMMEDIATE')
payload = json.dumps({'id': 'crashed-plan', 'strategy_id': 'A',
                      'description': 'Uncommitted'}, sort_keys=True, separators=(',', ':'))
connection.execute(
    'INSERT INTO records (id, kind, task_id, payload, digest) VALUES (?, ?, ?, ?, ?)',
    ('crashed-plan', 'Plan', 'task', payload, hashlib.sha256(payload.encode()).hexdigest()))
os._exit(23)
"""
        self.finish(self.child(code), 23)
        reopened = ExperimentStore(self.db, self.artifact_root)
        self.assertNotIn("crashed-plan", {r.id for r in reopened.history("task")})
        self.assertEqual(reopened.append(Plan("after-crash", "A", "Committed after crash"),
                                         "after-crash"), "after-crash")


if __name__ == "__main__":
    unittest.main()
