"""Versioned experiment metadata, independent of any kernel or transport.

These frozen records describe facts supplied by trusted adapters. They do not
execute experiments or establish that supplied measurements are real. Synthetic,
saved and real contexts remain separate. Store boundaries validate and detach
nested JSON values, because frozen dataclasses alone do not freeze dictionaries.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
import json
import math
import re
from types import UnionType
from typing import Any, get_args, get_origin, get_type_hints

SCHEMA_VERSION = 1


class ValidationError(ValueError):
    """A record or artifact violates the public contract."""


@dataclass(frozen=True)
class CandidateIdentity:
    source_digest: str
    config_digest: str
    build_digest: str


@dataclass(frozen=True)
class ArtifactRef:
    digest: str
    size: int


@dataclass(frozen=True)
class Task:
    id: str
    name: str
    contract_revision: str


@dataclass(frozen=True)
class Workload:
    id: str
    task_id: str
    spec: dict


@dataclass(frozen=True)
class EvaluationContext:
    id: str
    task_id: str
    workload_id: str
    environment: dict
    protocol: dict
    provenance: str


@dataclass(frozen=True)
class Strategy:
    id: str
    task_id: str
    name: str


@dataclass(frozen=True)
class Plan:
    id: str
    strategy_id: str
    description: str


@dataclass(frozen=True)
class ParentCandidate:
    """An exact parent; candidate_id=None explicitly imports a baseline."""

    id: str
    task_id: str
    identity: CandidateIdentity
    candidate_id: str | None = None


@dataclass(frozen=True)
class Candidate:
    id: str
    plan_id: str
    parent_id: str
    identity: CandidateIdentity
    change_kind: str = "proposal"


@dataclass(frozen=True)
class Run:
    id: str
    candidate_id: str
    context_id: str


@dataclass(frozen=True)
class RunEvent:
    id: str
    run_id: str
    execution_status: str
    scheduling_status: str
    reason: str


@dataclass(frozen=True)
class Evidence:
    id: str
    run_id: str
    evidence_kind: str
    availability: str
    request_status: str
    claim_status: str
    value: Any = None
    unit: str | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    input_ids: tuple[str, ...] = ()
    source: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationBundle:
    id: str
    run_id: str
    correctness: str
    timing_validity: str
    latency_ms: float | None
    confirmed: bool
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ProfileBundle:
    id: str
    run_id: str
    evidence_ids: tuple[str, ...]
    availability: str


@dataclass(frozen=True)
class AnalysisBundle:
    id: str
    run_id: str
    evidence_ids: tuple[str, ...]
    profile_ids: tuple[str, ...]
    claim_status: str
    summary: str


@dataclass(frozen=True)
class Decision:
    id: str
    task_id: str
    kind: str
    target_id: str
    reason: str
    details: dict = field(default_factory=dict)


RECORD_TYPES = {cls.__name__: cls for cls in (
    Task, Workload, EvaluationContext, Strategy, Plan, ParentCandidate,
    Candidate, Run, RunEvent, Evidence, EvaluationBundle, ProfileBundle,
    AnalysisBundle, Decision,
)}
EVIDENCE_KINDS = frozenset({
    "measured_counter", "measured_timing", "trace_derived", "derived_measurement",
    "compiler_estimated", "model_predicted", "code_fact", "source_claim", "historical_claim",
})
VALID_RUN_STATES = frozenset({
    ("not_run", "ready"), ("not_run", "deferred"), ("running", "active"),
    ("succeeded", "finished"), ("failed", "finished"),
    ("invalid", "finished"), ("incomplete", "finished"),
})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_AVAILABILITY = frozenset({"available", "partial", "unavailable"})
_CLAIM_STATUS = frozenset({"hypothesis", "supported", "refuted", "inconclusive"})


def _json_value(value: Any) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) in (list, tuple):
        for item in value:
            _json_value(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item)
        return
    raise ValidationError("Metadata must be finite JSON with string dictionary keys")


def canonical_json(value: Any) -> str:
    """Stable JSON for identity checks, never coercing non-string object keys."""
    try:
        _json_value(value)
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as error:
        raise ValidationError(str(error)) from error


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _check_type(value: Any, annotation: Any, label: str) -> None:
    if annotation is Any:
        _json_value(value)
        return
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is UnionType:
        for option in args:
            try:
                _check_type(value, option, label)
                return
            except ValidationError:
                pass
        raise ValidationError(f"Invalid type for {label}")
    if origin is tuple:
        _require(type(value) is tuple, f"{label} must be a tuple")
        for item in value:
            _check_type(item, args[0], label)
    elif annotation is float:
        _require(type(value) in (int, float) and math.isfinite(value), f"{label} must be finite numeric data")
    elif is_dataclass(annotation):
        _require(type(value) is annotation, f"Invalid type for {label}")
        _validate(value)
    else:
        _require(type(value) is annotation, f"Invalid type for {label}")
        if annotation is dict:
            _json_value(value)


def _validate(record: Any) -> None:
    hints = get_type_hints(type(record))
    for item in fields(record):
        value = getattr(record, item.name)
        _check_type(value, hints[item.name], item.name)
        if value is not None and (item.name == "id" or item.name.endswith("_id")):
            _require(bool(_ID.fullmatch(value)), f"Invalid stable ID: {item.name}")
        if item.name.endswith("_ids"):
            _require(len(set(value)) == len(value), f"Duplicate references in {item.name}")
            _require(all(_ID.fullmatch(ref) for ref in value), f"Invalid IDs in {item.name}")
        if item.name in {"name", "contract_revision", "description", "reason", "summary", "unit"} and value is not None:
            _require(bool(value.strip()), f"{item.name} cannot be blank")
    if isinstance(record, CandidateIdentity):
        _require(all(_DIGEST.fullmatch(getattr(record, item.name)) for item in fields(record)), "Identity digests must be lowercase SHA-256")
    elif isinstance(record, ArtifactRef):
        _require(bool(_DIGEST.fullmatch(record.digest)), "Artifact digest must be lowercase SHA-256")
        _require(record.size >= 0, "Artifact size cannot be negative")
    elif isinstance(record, EvaluationContext):
        _require(bool(record.environment) and bool(record.protocol), "Comparison environment and protocol cannot be empty")
        _require(record.provenance in {"synthetic", "saved", "real"}, "Invalid context provenance")
    elif isinstance(record, Candidate):
        _require(record.change_kind in {"proposal", "repair"}, "Invalid candidate change kind")
    elif isinstance(record, RunEvent):
        _require((record.execution_status, record.scheduling_status) in VALID_RUN_STATES, "Invalid run state pair")
    elif isinstance(record, Evidence):
        _require(record.evidence_kind in EVIDENCE_KINDS, "Invalid evidence kind")
        _require(record.availability in _AVAILABILITY, "Invalid evidence availability")
        _require(record.request_status in {"not_requested", "requested", "completed", "failed"}, "Invalid evidence request status")
        _require(record.claim_status in _CLAIM_STATUS, "Invalid claim status")
        if record.availability == "available":
            _require(record.value is not None, "Available evidence needs a value")
        elif record.availability == "unavailable":
            _require(record.value is None, "Unavailable evidence must use null")
        if record.value is None:
            _require(isinstance(record.source.get("reason"), str) and bool(record.source["reason"].strip()), "Missing evidence needs a reason")
    elif isinstance(record, EvaluationBundle):
        _require(record.correctness in {"passed", "failed", "unknown"}, "Invalid correctness verdict")
        _require(record.timing_validity in {"valid", "invalid", "unknown"}, "Invalid timing verdict")
        if record.latency_ms is not None:
            _require(record.latency_ms > 0, "Latency must be positive")
        if record.timing_validity == "valid":
            _require(record.latency_ms is not None, "Valid timing needs a latency")
        if record.confirmed:
            _require(record.correctness == "passed" and record.timing_validity == "valid", "Confirmed evaluation requires correctness and valid timing")
    elif isinstance(record, ProfileBundle):
        _require(record.availability in _AVAILABILITY, "Invalid profile availability")
    elif isinstance(record, AnalysisBundle):
        _require(record.claim_status in _CLAIM_STATUS, "Invalid analysis claim status")
    elif isinstance(record, Decision):
        _require(record.kind in {"promotion", "frontier", "paused", "resumed", "deferred", "note"}, "Invalid decision kind")


def validate_record(record: Any) -> None:
    """Validate field shapes; relational ownership is enforced by ExperimentStore."""
    _require(type(record) in (*RECORD_TYPES.values(), CandidateIdentity, ArtifactRef), "Unknown record type")
    try:
        _validate(record)
    except (TypeError, RecursionError) as error:
        raise ValidationError(str(error)) from error


def record_to_dict(record: Any) -> dict:
    """Validate and return detached JSON-compatible metadata."""
    validate_record(record)
    return json.loads(canonical_json(asdict(record)))


def _decode(cls: type, payload: dict) -> Any:
    _require(type(payload) is dict, "Record payload must be an object")
    known = {item.name for item in fields(cls)}
    _require(not (payload.keys() - known), "Unknown record fields")
    values = dict(payload)
    hints = get_type_hints(cls)
    for key, value in payload.items():
        annotation = hints[key]
        if is_dataclass(annotation):
            values[key] = _decode(annotation, value)
        elif get_origin(annotation) is tuple:
            _require(type(value) in (list, tuple), f"{key} must be an array")
            item_type = get_args(annotation)[0]
            values[key] = tuple(_decode(item_type, item) if is_dataclass(item_type) else item for item in value)
    try:
        return cls(**values)
    except TypeError as error:
        raise ValidationError(str(error)) from error


def record_from_dict(kind: str, payload: dict) -> Any:
    """Deserialize only known entity kinds; no silent dropped or extra fields."""
    _require(type(kind) is str and kind in RECORD_TYPES, "Unknown record kind")
    canonical_json(payload)
    record = _decode(RECORD_TYPES[kind], payload)
    validate_record(record)
    return record
