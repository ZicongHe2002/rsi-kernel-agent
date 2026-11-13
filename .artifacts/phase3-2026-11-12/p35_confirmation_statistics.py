"""Pure local, Run-level descriptive analysis for a predeclared ABBA capture.

This helper does not read the Store, verify raw receipt closure, mutate evidence,
or promote a candidate. Its caller must supply already-verified capture rows in
execution order. Twenty samples describe one Run; two Runs per role are limited
replication, not forty independent observations.
"""

from copy import deepcopy
from fractions import Fraction
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from rsi_kernel_agent.workflow.real_protocol import timing_statistics


_POLICY = {
    "version": "p35-abba-v1",
    "min_paired_reduction": 0.02,
    "max_within_role_spread": 0.05,
}
_ORDER = ("control", "treatment", "treatment", "control")
_FIXED = ("context_id", "runtime_id", "protocol_version", "input_digest")
_ROLE_IDENTITIES = ("source_digest", "config_digest", "build_digest")
_STRING_FIELDS = (
    "role", "run_id", *_FIXED, *_ROLE_IDENTITIES,
    "profile_run_id", "profile_capture_operation_id",
)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _validate_samples(samples, label):
    _require(type(samples) is list and len(samples) == 20,
             label + " must contain exactly 20 samples")
    _require(all(type(value) in (int, float) and math.isfinite(value) and value > 0
                 for value in samples), label + " must be finite positive numbers")


def _validate(rows, policy):
    _require(type(policy) is dict and set(policy) == set(_POLICY),
             "Policy fields must match the predeclared p35-abba-v1 policy")
    _require(type(policy["version"]) is str and policy["version"] == _POLICY["version"],
             "Policy version is not p35-abba-v1")
    for key in ("min_paired_reduction", "max_within_role_spread"):
        _require(type(policy[key]) in (int, float) and policy[key] == _POLICY[key],
                 "Policy threshold differs from the predeclared " + key)
    _require(type(rows) is list and len(rows) == 4, "ABBA requires exactly four Run rows")
    for row in rows:
        _require(type(row) is dict, "Every Run row must be an object")
        for key in _STRING_FIELDS:
            _require(type(row.get(key)) is str and bool(row[key].strip()),
                     "Missing or invalid " + key)
        _validate_samples(row.get("raw_samples_ms"), "Host raw_samples_ms")
        profile = row.get("profile_samples_us")
        _require(type(profile) is dict and set(profile) == {"program", "prepare", "attention"},
                 "Profile must contain program, prepare, and attention samples")
        for kind, samples in profile.items():
            _validate_samples(samples, "Profile " + kind)
        _require(row["profile_run_id"] == row["run_id"],
                 "Profile capture belongs to a different Run")
    _require(tuple(row["role"] for row in rows) == _ORDER,
             "Rows must be in control, treatment, treatment, control execution order")
    for key in ("run_id", "profile_capture_operation_id"):
        _require(len({row[key] for row in rows}) == 4, "Repeated " + key)
    for key in _FIXED:
        _require(len({row[key] for row in rows}) == 1, "Fixed identity mismatch: " + key)
    for role in ("control", "treatment"):
        role_rows = [row for row in rows if row["role"] == role]
        for key in _ROLE_IDENTITIES:
            _require(len({row[key] for row in role_rows}) == 1,
                     "Identity differs within " + role + " role: " + key)
    try:
        json.dumps(rows, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Run rows must be finite JSON data") from exc


def _describe(rows, dimension, unit):
    runs = []
    for row in rows:
        samples = (row["raw_samples_ms"] if dimension == "host"
                   else row["profile_samples_us"][dimension])
        # Preserve the repository's exact host timing statistics and quantiles.
        raw_stats = timing_statistics(samples)
        stats = ({key.replace("_ms", "_us"): value for key, value in raw_stats.items()}
                 if unit == "us" else raw_stats)
        runs.append({
            "run_id": row["run_id"],
            "role": row["role"],
            "statistics": stats,
            "within_run_variation": {
                "coefficient_of_variation": stats["standard_deviation_" + unit] / stats["mean_" + unit],
                "range_over_median": (stats["max_" + unit] - stats["min_" + unit]) / stats["median_" + unit],
            },
        })
    medians = {role: [run["statistics"]["median_" + unit] for run in runs if run["role"] == role]
               for role in ("control", "treatment")}
    pairs = []
    for pair_id, control_index, treatment_index in (("A1_vs_B1", 0, 1), ("A2_vs_B2", 3, 2)):
        control = runs[control_index]["statistics"]["median_" + unit]
        treatment = runs[treatment_index]["statistics"]["median_" + unit]
        pairs.append({
            "pair_id": pair_id,
            "control_run_id": rows[control_index]["run_id"],
            "treatment_run_id": rows[treatment_index]["run_id"],
            "control_median_" + unit: control,
            "treatment_median_" + unit: treatment,
            "difference_" + unit: control - treatment,
            "speedup": control / treatment,
            "reduction": (control - treatment) / control,
        })
    return {
        "unit": unit,
        "runs": runs,
        "run_medians": medians,
        "run_medians_population_std": {role: statistics.pstdev(values) for role, values in medians.items()},
        "role_spread": {role: (max(values) - min(values)) / statistics.median(values)
                        for role, values in medians.items()},
        "pairs": pairs,
    }


def _classify(dimension, policy):
    # Exact rationals from decimal strings apply the inclusive boundaries to
    # reported Run medians without a fuzzy threshold or decimal-context rounding.
    medians = {role: [Fraction(str(value)) for value in values]
               for role, values in dimension['run_medians'].items()}
    minimum = Fraction(str(policy['min_paired_reduction']))
    spread_limit = Fraction(str(policy['max_within_role_spread']))
    criteria = {
        "both_paired_reductions_meet_minimum": all(
            control - treatment >= minimum * control
            for control, treatment in zip(medians['control'], medians['treatment'])),
        "treatment_medians_strictly_below_control_medians": (
            max(medians['treatment']) < min(medians['control'])),
        "both_roles_within_spread_limit": all(
            max(values) - min(values) <= spread_limit * statistics.median(values)
            for values in medians.values()),
    }
    if not criteria["both_roles_within_spread_limit"]:
        classification = "inconclusive_drift"
    elif all(criteria.values()):
        classification = "replicated_observed_improvement"
    else:
        classification = "not_confirmed"
    return {**dimension, "criteria": criteria, "classification": classification}


def analyze_abba(rows, policy):
    """Return JSON-compatible descriptive statistics for four verified Run rows.

    Each row supplies role/run_id, the fixed context/runtime/protocol/input
    identities, source/config/build digests, 20 raw_samples_ms, and 20 values for
    each program/prepare/attention profile_samples_us category. profile_run_id
    binds the capture to its Run; unique profile_capture_operation_id identifies
    the capture. Extra JSON metadata is preserved in raw_rows. This consistency
    check cannot replace the caller's verification of the original receipts.
    """
    _validate(rows, policy)
    dimensions = {name: _classify(_describe(rows, name, unit), policy)
                  for name, unit in (("host", "ms"), ("program", "us"))}
    classifications = {name: value["classification"] for name, value in dimensions.items()}
    if "inconclusive_drift" in classifications.values():
        outcome = "inconclusive"
    else:
        host = classifications["host"] == "replicated_observed_improvement"
        program = classifications["program"] == "replicated_observed_improvement"
        outcome = ("host_and_program" if host and program else "host_only" if host
                   else "program_only" if program else "neither_confirmed")
    report = {
        "schema_version": 1,
        "policy": deepcopy(policy),
        "threshold_arithmetic": "exact decimal strings of reported Run medians; inclusive limits without epsilon",
        "statistical_unit": "Run",
        "independent_runs_per_role": 2,
        "within_run_samples": 20,
        "order": list(_ORDER),
        "pairings": [{key: pair[key] for key in ("pair_id", "control_run_id", "treatment_run_id")}
                     for pair in dimensions["host"]["pairs"]],
        "fixed_identity": {key: rows[0][key] for key in _FIXED},
        "raw_rows": deepcopy(rows),
        "dimensions": dimensions,
        "profile_descriptive": {name: _describe(rows, name, "us") for name in ("prepare", "attention")},
        "outcome": outcome,
        "claims": {
            "confidence_interval": False,
            "statistical_significance": False,
            "generalizes_beyond_two_runs": False,
            "store_promotion": False,
        },
        "interpretation": (
            "Predeclared engineering confirmation criteria for two independent Runs per role. "
            "The 20 within-Run samples are correlated timing observations and are not pooled "
            "into 40 independent experiments. No confidence interval, statistical significance, "
            "generalization beyond these Runs, or Store promotion follows from this report."
        ),
    }
    try:
        json.dumps(report, allow_nan=False)
    except ValueError as exc:
        raise ValueError("Derived timing statistics are not finite JSON numbers") from exc
    return report
