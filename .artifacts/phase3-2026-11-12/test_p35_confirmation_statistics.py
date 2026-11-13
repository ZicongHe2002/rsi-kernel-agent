"""Pure CPU tests for the local P3.5 ABBA descriptive-statistics helper."""

from copy import deepcopy
import math
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))

try:
    from p35_confirmation_statistics import analyze_abba
except ImportError:
    analyze_abba = None


POLICY = {
    "version": "p35-abba-v1",
    "min_paired_reduction": 0.02,
    "max_within_role_spread": 0.05,
}


def _samples(value):
    return [float(value)] * 20


def _row(role, run_id, host, program, *, prepare=8.0, attention=12.0,
         source="source", config="config", build="build"):
    return {
        "role": role,
        "run_id": run_id,
        "context_id": "context-1",
        "runtime_id": "runtime-1",
        "protocol_version": "protocol-1",
        "input_digest": "input-digest-1",
        "source_digest": source,
        "config_digest": config,
        "build_digest": build,
        "raw_samples_ms": _samples(host),
        "profile_samples_us": {
            "program": _samples(program),
            "prepare": _samples(prepare),
            "attention": _samples(attention),
        },
        "profile_run_id": run_id,
        "profile_capture_operation_id": "capture-" + run_id,
    }


def _valid_rows():
    return [
        _row("control", "C1", 100.0, 50.0),
        _row("treatment", "T1", 90.0, 40.0),
        _row("treatment", "T2", 90.0, 40.0),
        _row("control", "C2", 100.0, 50.0),
    ]


class P35ConfirmationStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(analyze_abba, "P3.5 ABBA statistics helper is not implemented")

    def test_valid_abba_reports_literal_pair_stats_and_preserves_raw_order(self):
        rows = _valid_rows()
        before = deepcopy(rows)

        result = analyze_abba(rows, POLICY)

        self.assertEqual(result["outcome"], "host_and_program")
        self.assertEqual(result["dimensions"]["host"]["classification"],
                         "replicated_observed_improvement")
        self.assertEqual(result["dimensions"]["program"]["classification"],
                         "replicated_observed_improvement")
        self.assertEqual(result["pairings"][0]["pair_id"], "A1_vs_B1")
        self.assertEqual(result["pairings"][1]["pair_id"], "A2_vs_B2")
        self.assertEqual(result["pairings"][0]["control_run_id"], "C1")
        self.assertEqual(result["pairings"][0]["treatment_run_id"], "T1")
        self.assertEqual(result["pairings"][1]["control_run_id"], "C2")
        self.assertEqual(result["pairings"][1]["treatment_run_id"], "T2")
        self.assertEqual(result["dimensions"]["host"]["runs"][0]["statistics"], {
            "count": 20,
            "median_ms": 100.0,
            "mean_ms": 100.0,
            "standard_deviation_ms": 0.0,
            "min_ms": 100.0,
            "max_ms": 100.0,
            "p25_ms": 100.0,
            "p75_ms": 100.0,
            "outlier_rule": "none",
        })
        self.assertEqual(result["dimensions"]["host"]["pairs"][0]["difference_ms"], 10.0)
        self.assertAlmostEqual(result["dimensions"]["host"]["pairs"][0]["speedup"],
                               1.1111111111111112)
        self.assertAlmostEqual(result["dimensions"]["host"]["pairs"][0]["reduction"], 0.10)
        self.assertEqual(result["dimensions"]["host"]["run_medians"]["control"], [100.0, 100.0])
        self.assertEqual(result["dimensions"]["host"]["run_medians"]["treatment"], [90.0, 90.0])
        self.assertEqual(result["dimensions"]["host"]["run_medians_population_std"], {
            "control": 0.0,
            "treatment": 0.0,
        })
        self.assertEqual(result["raw_rows"], before)
        self.assertEqual(rows, before)

    def test_reversed_or_incomplete_abba_order_is_rejected(self):
        rows = _valid_rows()
        rows[0]["role"], rows[1]["role"] = rows[1]["role"], rows[0]["role"]
        with self.assertRaises(ValueError):
            analyze_abba(rows, POLICY)

        rows = _valid_rows()[:-1]
        with self.assertRaises(ValueError):
            analyze_abba(rows, POLICY)

    def test_nan_zero_and_short_samples_are_rejected(self):
        cases = []
        rows = _valid_rows()
        rows[0]["raw_samples_ms"][0] = math.nan
        cases.append(rows)
        rows = _valid_rows()
        rows[1]["raw_samples_ms"][0] = 0.0
        cases.append(rows)
        rows = _valid_rows()
        rows[2]["profile_samples_us"]["attention"] = [1.0] * 19
        cases.append(rows)
        rows = _valid_rows()
        rows[3]["profile_samples_us"].pop("prepare")
        cases.append(rows)

        for invalid in cases:
            with self.subTest(rows=invalid):
                with self.assertRaises(ValueError):
                    analyze_abba(invalid, POLICY)

    def test_fixed_identity_and_profile_capture_bindings_are_required(self):
        rows = _valid_rows()
        rows[2]["context_id"] = "other-context"
        with self.assertRaises(ValueError):
            analyze_abba(rows, POLICY)

        rows = _valid_rows()
        rows[1]["source_digest"] = "different-treatment-source"
        with self.assertRaises(ValueError):
            analyze_abba(rows, POLICY)

        rows = _valid_rows()
        rows[0]["profile_run_id"] = "other-run"
        with self.assertRaises(ValueError):
            analyze_abba(rows, POLICY)

    def test_role_drift_masks_improvement_and_joint_outcome_is_inconclusive(self):
        rows = _valid_rows()
        rows[3]["raw_samples_ms"] = _samples(110.0)
        rows[3]["profile_samples_us"]["program"] = _samples(55.0)

        result = analyze_abba(rows, POLICY)

        self.assertEqual(result["dimensions"]["host"]["classification"], "inconclusive_drift")
        self.assertEqual(result["dimensions"]["program"]["classification"], "inconclusive_drift")
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertGreater(result["dimensions"]["host"]["role_spread"]["control"], 0.05)

    def test_host_program_disagreement_reports_host_only_without_significance_claim(self):
        rows = _valid_rows()
        rows[1]["profile_samples_us"]["program"] = _samples(51.0)
        rows[2]["profile_samples_us"]["program"] = _samples(51.0)

        result = analyze_abba(rows, POLICY)

        self.assertEqual(result["dimensions"]["host"]["classification"],
                         "replicated_observed_improvement")
        self.assertEqual(result["dimensions"]["program"]["classification"], "not_confirmed")
        self.assertEqual(result["outcome"], "host_only")
        self.assertEqual(result["statistical_unit"], "Run")
        self.assertFalse(result["claims"]["confidence_interval"])
        self.assertFalse(result["claims"]["statistical_significance"])
        self.assertFalse(result["claims"]["generalizes_beyond_two_runs"])

    def test_policy_is_predeclared_and_exact(self):
        rows = _valid_rows()
        for key, value in (
            ("version", "p35-abba-v0"),
            ("min_paired_reduction", 0.01),
            ("max_within_role_spread", 0.10),
        ):
            policy = dict(POLICY)
            policy[key] = value
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    analyze_abba(rows, policy)

        with self.assertRaises(ValueError):
            analyze_abba(rows, {**POLICY, "extra": 1})

    def test_nonconstant_samples_report_population_statistics_without_pooling(self):
        rows = _valid_rows()
        values = list(range(20, 0, -1))
        rows[0]["raw_samples_ms"] = values
        rows[0]["profile_samples_us"]["prepare"] = values.copy()
        result = analyze_abba(rows, POLICY)

        host = result["dimensions"]["host"]["runs"][0]
        self.assertEqual(host["statistics"]["median_ms"], 10.5)
        self.assertEqual(host["statistics"]["mean_ms"], 10.5)
        self.assertAlmostEqual(host["statistics"]["standard_deviation_ms"], 5.766281297335398)
        self.assertEqual(host["statistics"]["min_ms"], 1)
        self.assertEqual(host["statistics"]["max_ms"], 20)
        self.assertEqual(host["statistics"]["p25_ms"], 5.75)
        self.assertEqual(host["statistics"]["p75_ms"], 15.25)
        self.assertAlmostEqual(host["within_run_variation"]["coefficient_of_variation"], 0.549169647365276)
        self.assertAlmostEqual(host["within_run_variation"]["range_over_median"], 1.8095238095238095)
        self.assertEqual(result["dimensions"]["host"]["run_medians"]["control"], [10.5, 100.0])
        self.assertEqual(result["dimensions"]["host"]["run_medians_population_std"]["control"], 44.75)
        self.assertEqual(result["profile_descriptive"]["prepare"]["runs"][0]["statistics"]["p75_us"], 15.25)
        self.assertEqual(result["raw_rows"][0]["raw_samples_ms"], list(range(20, 0, -1)))
        self.assertEqual(result["independent_runs_per_role"], 2)
        self.assertEqual(result["within_run_samples"], 20)
        json.dumps(result, allow_nan=False)

    def test_two_percent_and_five_percent_limits_are_inclusive(self):
        rows = _valid_rows()
        rows[0]["raw_samples_ms"] = _samples(97.5)
        rows[3]["raw_samples_ms"] = _samples(102.5)
        rows[1]["raw_samples_ms"] = _samples(95.55)
        rows[2]["raw_samples_ms"] = _samples(98.0)

        result = analyze_abba(rows, POLICY)

        self.assertAlmostEqual(result["dimensions"]["host"]["pairs"][0]["reduction"], 0.02)
        self.assertEqual(result["dimensions"]["host"]["role_spread"]["control"], 0.05)
        self.assertEqual(result["dimensions"]["host"]["classification"], "not_confirmed")
        self.assertFalse(result["dimensions"]["host"]["criteria"]["treatment_medians_strictly_below_control_medians"])
        self.assertTrue(result["dimensions"]["host"]["criteria"]["both_roles_within_spread_limit"])

    def test_both_pairs_must_meet_minimum_and_all_treatments_must_be_faster(self):
        rows = _valid_rows()
        rows[1]["raw_samples_ms"] = _samples(98.0)
        rows[2]["raw_samples_ms"] = _samples(98.1)
        result = analyze_abba(rows, POLICY)
        self.assertEqual(result["dimensions"]["host"]["classification"], "not_confirmed")
        self.assertFalse(result["dimensions"]["host"]["criteria"]["both_paired_reductions_meet_minimum"])

        rows[2]["raw_samples_ms"] = _samples(98.0)
        result = analyze_abba(rows, POLICY)
        self.assertEqual(result["dimensions"]["host"]["classification"], "replicated_observed_improvement")

    def test_treatment_drift_and_single_dimension_drift_make_joint_inconclusive(self):
        rows = _valid_rows()
        rows[2]["raw_samples_ms"] = _samples(80.0)
        result = analyze_abba(rows, POLICY)
        self.assertEqual(result["dimensions"]["host"]["classification"], "inconclusive_drift")
        self.assertEqual(result["dimensions"]["program"]["classification"], "replicated_observed_improvement")
        self.assertEqual(result["outcome"], "inconclusive")

    def test_program_only_and_neither_outcomes(self):
        rows = _valid_rows()
        for index in (1, 2):
            rows[index]["raw_samples_ms"] = _samples(101.0)
        result = analyze_abba(rows, POLICY)
        self.assertEqual(result["outcome"], "program_only")
        for index in (1, 2):
            rows[index]["profile_samples_us"]["program"] = _samples(51.0)
        result = analyze_abba(rows, POLICY)
        self.assertEqual(result["outcome"], "neither_confirmed")

    def test_duplicate_run_and_capture_ids_and_each_identity_mismatch_are_rejected(self):
        for key in ("run_id", "profile_capture_operation_id", "context_id", "runtime_id",
                    "protocol_version", "input_digest", "source_digest", "config_digest", "build_digest"):
            rows = _valid_rows()
            rows[2][key] = rows[1][key] if key in ("run_id", "profile_capture_operation_id") else "changed"
            if key == "run_id":
                rows[2]["profile_run_id"] = rows[2]["run_id"]
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    analyze_abba(rows, POLICY)

    def test_each_required_field_and_invalid_numeric_sample_is_rejected(self):
        for key in _valid_rows()[0]:
            rows = _valid_rows()
            rows[0].pop(key)
            with self.subTest(missing=key):
                with self.assertRaises(ValueError):
                    analyze_abba(rows, POLICY)
        for sample in (True, "1.0", None, math.inf, -math.inf, -1.0, 0.0, math.nan):
            for dimension in ("host", "program", "prepare", "attention"):
                rows = _valid_rows()
                samples = rows[0]["raw_samples_ms"] if dimension == "host" else rows[0]["profile_samples_us"][dimension]
                samples[0] = sample
                with self.subTest(sample=sample, dimension=dimension):
                    with self.assertRaises(ValueError):
                        analyze_abba(rows, POLICY)

    def test_distinct_role_identities_are_allowed_and_copied_result_has_no_aliases(self):
        rows = _valid_rows()
        for index in (1, 2):
            rows[index].update(source_digest="treatment-source", config_digest="treatment-config", build_digest="treatment-build")
        rows[0]["extra_capture_metadata"] = {"timestamp": [1, 2, 3]}
        policy = deepcopy(POLICY)
        result = analyze_abba(rows, policy)
        self.assertEqual(result["outcome"], "host_and_program")
        self.assertEqual(result["policy"], POLICY)
        result["raw_rows"][0]["raw_samples_ms"][0] = 10000.0
        result["raw_rows"][0]["extra_capture_metadata"]["timestamp"].append(4)
        result["policy"]["min_paired_reduction"] = 0.9
        self.assertEqual(rows[0]["raw_samples_ms"][0], 100.0)
        self.assertEqual(rows[0]["extra_capture_metadata"], {"timestamp": [1, 2, 3]})
        self.assertEqual(policy, POLICY)

    def test_extra_metadata_must_be_finite_json_data(self):
        for extra in (math.nan, {1, 2}, object()):
            rows = _valid_rows()
            rows[0]["extra"] = extra
            with self.subTest(extra=type(extra).__name__):
                with self.assertRaises(ValueError):
                    analyze_abba(rows, POLICY)

    def test_finite_samples_that_overflow_a_derived_ratio_are_rejected(self):
        rows = _valid_rows()
        rows[0]["raw_samples_ms"] = _samples(1e200)
        rows[3]["raw_samples_ms"] = _samples(1e200)
        rows[1]["raw_samples_ms"] = _samples(1e-200)
        rows[2]["raw_samples_ms"] = _samples(1e-200)
        with self.assertRaises(ValueError):
            analyze_abba(rows, POLICY)

    def test_exact_decimal_reduction_boundary_is_inclusive_without_binary_roundoff(self):
        rows = _valid_rows()
        for index in (0, 3):
            rows[index]['raw_samples_ms'] = _samples(0.03)
        for index in (1, 2):
            rows[index]['raw_samples_ms'] = _samples(0.0294)
        result = analyze_abba(rows, POLICY)
        self.assertTrue(result['dimensions']['host']['criteria']['both_paired_reductions_meet_minimum'])
        self.assertEqual(result['dimensions']['host']['classification'], 'replicated_observed_improvement')

        rows[2]['raw_samples_ms'] = _samples(0.029400000000001)
        result = analyze_abba(rows, POLICY)
        self.assertFalse(result['dimensions']['host']['criteria']['both_paired_reductions_meet_minimum'])
        self.assertEqual(result['dimensions']['host']['classification'], 'not_confirmed')

    def test_exact_decimal_spread_boundary_is_inclusive_without_binary_roundoff(self):
        rows = _valid_rows()
        rows[0]['raw_samples_ms'] = _samples(0.00975)
        rows[3]['raw_samples_ms'] = _samples(0.01025)
        for index in (1, 2):
            rows[index]['raw_samples_ms'] = _samples(0.009)
        result = analyze_abba(rows, POLICY)
        self.assertTrue(result['dimensions']['host']['criteria']['both_roles_within_spread_limit'])
        self.assertEqual(result['dimensions']['host']['classification'], 'replicated_observed_improvement')
        rows[3]['raw_samples_ms'] = _samples(0.010250000000001)
        result = analyze_abba(rows, POLICY)
        self.assertFalse(result['dimensions']['host']['criteria']['both_roles_within_spread_limit'])
        self.assertEqual(result['dimensions']['host']['classification'], 'inconclusive_drift')


if __name__ == "__main__":
    unittest.main(verbosity=2)
