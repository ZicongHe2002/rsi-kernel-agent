"""Contract boundaries for synthetic experiment records (no kernel execution)."""

import dataclasses
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from rsi_kernel_agent import contracts as c
except ImportError:
    c = None


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(c, "Phase 2A contracts are not implemented")

    def test_round_trip_reconstructs_candidate_identity(self):
        identity = c.CandidateIdentity("a" * 64, "b" * 64, "c" * 64)
        candidate = c.Candidate("candidate-a", "plan-a", "parent-baseline", identity)
        wire = c.record_to_dict(candidate)
        self.assertEqual(wire["identity"]["source_digest"], "a" * 64)
        restored = c.record_from_dict("Candidate", json.loads(c.canonical_json(wire)))
        self.assertIsInstance(restored.identity, c.CandidateIdentity)
        self.assertEqual(restored, candidate)

    def test_round_trip_evidence_detaches_nested_data(self):
        evidence = c.Evidence(
            "e-1", "r-1", "measured_timing", "available", "completed", "supported",
            2.5, "ms", (c.ArtifactRef("a" * 64, 4),), (), {"locator": "fixture", "revision": "v1"},
        )
        wire = c.record_to_dict(evidence)
        wire["source"]["revision"] = "v2"
        self.assertEqual(evidence.source["revision"], "v1")
        restored = c.record_from_dict("Evidence", wire)
        self.assertIsInstance(restored.artifacts[0], c.ArtifactRef)
        self.assertIsInstance(restored.artifacts, tuple)

    def test_canonical_json_orders_keys_without_nonfinite_or_key_coercion(self):
        self.assertEqual(c.canonical_json({"z": 1, "a": [True, None]}), '{"a":[true,null],"z":1}')
        for invalid in ({"n": float("nan")}, {"n": float("inf")}, {1: "collision"}, {"data": b"raw"}):
            with self.subTest(invalid=invalid), self.assertRaises(c.ValidationError):
                c.canonical_json(invalid)

    def test_ids_and_source_digests_reject_paths_or_wrong_types(self):
        invalid_records = [
            c.Task("../escape", "task", "v1"), c.Task("", "task", "v1"),
            c.Task(42, "task", "v1"), c.Task("task", " ", "v1"),
            c.ParentCandidate("p", "task", c.CandidateIdentity("../bad", "b" * 64, "c" * 64)),
        ]
        for record in invalid_records:
            with self.subTest(record=record), self.assertRaises(c.ValidationError):
                c.validate_record(record)

    def test_decode_rejects_schema_typos_and_unknown_entity(self):
        for kind, payload in [
            ("Task", {"id": "t", "name": "T", "contract_revision": "v1", "extra": 1}),
            ("Task", {"id": "t", "name": "T"}),
            ("PretendTask", {"id": "t"}),
            ("Task", {"id": "t", "name": "T", "contract_revision": 4}),
            ("Candidate", {"id": "c", "plan_id": "p", "parent_id": "b", "identity": {"source_digest": "a" * 64}}),
        ]:
            with self.subTest(kind=kind, payload=payload), self.assertRaises(c.ValidationError):
                c.record_from_dict(kind, payload)

    def test_missing_measurement_is_null_and_requires_reason(self):
        missing = c.Evidence("e", "r", "measured_counter", "unavailable", "not_requested", "inconclusive", source={"reason": "unsupported"})
        c.validate_record(missing)
        for broken in (
            dataclasses.replace(missing, value=0), dataclasses.replace(missing, source={}),
            dataclasses.replace(missing, availability="available"),
            dataclasses.replace(missing, evidence_kind="action_plan"),
            dataclasses.replace(missing, claim_status="proven"),
        ):
            with self.subTest(record=broken), self.assertRaises(c.ValidationError):
                c.validate_record(broken)
        c.validate_record(dataclasses.replace(missing, availability="available", value=0))

    def test_confirmed_evaluation_requires_correctness_and_valid_positive_timing(self):
        valid = c.EvaluationBundle("ev", "r", "passed", "valid", 2.0, True, ("e",))
        c.validate_record(valid)
        c.validate_record(dataclasses.replace(valid, correctness="failed", confirmed=False))
        c.validate_record(dataclasses.replace(valid, timing_validity="unknown", latency_ms=None, confirmed=False))
        for broken in (
            dataclasses.replace(valid, correctness="failed"),
            dataclasses.replace(valid, latency_ms=None), dataclasses.replace(valid, latency_ms=0),
            dataclasses.replace(valid, latency_ms=True), dataclasses.replace(valid, latency_ms=float("nan")),
            dataclasses.replace(valid, confirmed=1), dataclasses.replace(valid, evidence_ids="e"),
        ):
            with self.subTest(record=broken), self.assertRaises(c.ValidationError):
                c.validate_record(broken)

    def test_context_requires_shared_environment_protocol_and_provenance(self):
        context = c.EvaluationContext("ctx", "task", "w", {"device": "synthetic"}, {"revision": "v1"}, "synthetic")
        c.validate_record(context)
        for broken in (dataclasses.replace(context, environment={}), dataclasses.replace(context, protocol={}), dataclasses.replace(context, provenance="TPU validated")):
            with self.subTest(record=broken), self.assertRaises(c.ValidationError):
                c.validate_record(broken)

    def test_run_state_pairs_keep_deferred_distinct_from_failure(self):
        for execution, scheduling in (("not_run", "ready"), ("not_run", "deferred"), ("running", "active"), ("failed", "finished"), ("incomplete", "finished")):
            c.validate_record(c.RunEvent("event", "run", execution, scheduling, "fixture"))
        for execution, scheduling in (("failed", "deferred"), ("running", "ready"), ("not_run", "finished"), ("success", "finished")):
            with self.subTest(state=(execution, scheduling)), self.assertRaises(c.ValidationError):
                c.validate_record(c.RunEvent("event", "run", execution, scheduling, "fixture"))

    def test_all_record_kinds_round_trip_and_validate_relational_ids(self):
        identity = c.CandidateIdentity("a" * 64, "b" * 64, "c" * 64)
        records = [
            c.Task("t", "Synthetic task", "v1"), c.Workload("w", "t", {"size": 4}),
            c.Strategy("s", "t", "strategy A"), c.Plan("p", "s", "try one"),
            c.ParentCandidate("base", "t", identity), c.Run("r", "candidate", "ctx"),
            c.ProfileBundle("profile", "r", (), "unavailable"),
            c.AnalysisBundle("analysis", "r", ("e",), ("profile",), "hypothesis", "fixture claim"),
            c.Decision("d", "t", "paused", "s", "budget", {"synthetic": True}),
        ]
        for record in records:
            with self.subTest(kind=type(record).__name__):
                self.assertEqual(c.record_from_dict(type(record).__name__, c.record_to_dict(record)), record)
        with self.assertRaises(c.ValidationError):
            c.validate_record(c.Run("r", "", "ctx"))
        with self.assertRaises(c.ValidationError):
            c.validate_record(c.Decision("d", "t", "winner", "s", "budget"))


if __name__ == "__main__":
    unittest.main()
