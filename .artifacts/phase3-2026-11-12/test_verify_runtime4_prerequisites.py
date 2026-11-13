"""Fixture tests for the read-only Runtime 4 prerequisite audit."""
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


class Runtime4VerificationTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module("verify_runtime4_prerequisites")
        except ModuleNotFoundError:
            self.fail("Runtime 4 read-only prerequisite verifier is missing")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_missing_runtime4_inputs_stay_pending(self):
        report = self.api.verify_prerequisites(self.directory)
        self.assertEqual(report["status"], "partial_pending")
        self.assertEqual(report["counts"], {"verified": 0, "pending": 4, "failed_verification": 0})
        self.assertEqual({row["reason"] for row in report["jobs"]}, {"missing_archived_job"})

    def test_four_terminal_fixture_rows_are_aggregated(self):
        runtime = "runtime4-fixture"
        (self.directory / "colab-config-runtime4.json").write_text(
            json.dumps({"runtime_id": runtime}), encoding="utf-8"
        )
        for name in self.api.NAMES:
            (self.directory / f"{name}-runtime4-job.json").write_text(
                json.dumps({"id": name, "run_id": name + "-run", "task_id": "task",
                            "context_id": "context", "contract": {"context": {
                                "environment": {"runtime_session_id": runtime}}}}),
                encoding="utf-8",
            )

        class FakeStore:
            def __init__(self, *_):
                self.artifacts = type("Artifacts", (), {"checked": {}})()

            def history(self, task_id):
                return {}

        with patch.object(self.api, "ReadOnlyStore", FakeStore), \
             patch.object(self.api, "terminal_view", side_effect=lambda store, job, history:
                          dict(job, state="completed")), \
             patch.object(self.api, "verify_job", side_effect=lambda store, job, **kwargs:
                          {"verification": "verified", "classification": "correct"}), \
             patch.object(self.api, "require_gates", return_value=(
                 {name: {} for name in self.api.NAMES}, {name: {} for name in self.api.NAMES}
             )) as gates:
            report = self.api.verify_prerequisites(self.directory)

        self.assertEqual(report["status"], "runtime4_prerequisites_verified")
        self.assertEqual(report["counts"]["verified"], 4)
        gates.assert_called_once()
        self.assertEqual(gates.call_args.args[2], {"generation": "runtime4"})

    def test_verification_error_is_failed_but_missing_marker_is_pending(self):
        runtime = "runtime4-fixture"
        (self.directory / "colab-config-runtime4.json").write_text(
            json.dumps({"runtime_id": runtime}), encoding="utf-8"
        )
        for index, name in enumerate(self.api.NAMES):
            (self.directory / f"{name}-runtime4-job.json").write_text(
                json.dumps({"id": name, "run_id": name + "-run", "task_id": "task",
                            "context_id": "context", "contract": {"context": {
                                "environment": {"runtime_session_id": runtime}}}}),
                encoding="utf-8",
            )

        class FakeStore:
            def __init__(self, *_):
                self.artifacts = type("Artifacts", (), {"checked": {}})()

            def history(self, task_id):
                return {}

        def terminal(store, job, history):
            return None if job["id"] == self.api.NAMES[0] else dict(job, state="completed")

        def verify(store, job, **kwargs):
            if job["id"] == self.api.NAMES[1]:
                raise ValueError("bad receipt")
            return {"verification": "verified"}

        with patch.object(self.api, "ReadOnlyStore", FakeStore), \
             patch.object(self.api, "terminal_view", side_effect=terminal), \
             patch.object(self.api, "verify_job", side_effect=verify), \
             patch.object(self.api, "require_gates"):
            report = self.api.verify_prerequisites(self.directory)

        rows = {row["name"]: row for row in report["jobs"]}
        self.assertEqual(rows[self.api.NAMES[0]]["verification"], "pending")
        self.assertEqual(rows[self.api.NAMES[1]]["verification"], "failed_verification")
        self.assertEqual(report["status"], "verification_failed")

    def test_each_main_call_archives_a_distinct_report_without_overwrite(self):
        self.assertEqual(self.api.main(["--directory", str(self.directory)]), 0)
        first = next((self.directory / "runtime4-local-verifications").glob("*.json"))
        original = first.read_bytes()
        self.assertEqual(self.api.main(["--directory", str(self.directory)]), 0)
        self.assertEqual(len(list(first.parent.glob("*.json"))), 2)
        self.assertEqual(first.read_bytes(), original)
        self.assertEqual(json.loads(original)["generation"], "runtime4")


if __name__ == "__main__":
    unittest.main(verbosity=2)
