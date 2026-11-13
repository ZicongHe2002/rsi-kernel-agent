"""Normalize saved Profile evidence without capturing or executing a kernel.

Legacy JSON comes from PallasKernels tools/tpu_duration_profile.py. The optional
reader is supplied by a caller in an environment that already has its protobuf
dependencies. This module never imports the external runtime or a TPU backend.
Bindings are declarations; relational identity checking belongs to capture_inputs.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import statistics

from ..contracts import ArtifactRef, CandidateIdentity, ValidationError, canonical_json, validate_record

_KINDS = {'program', 'operation', 'custom_call', 'dma', 'wait', 'synchronization'}
_CLASSES = {'measured', 'trace_derived', 'compiler_estimated', 'inferred'}
_META = {'compiler_metadata', 'resource_metadata', 'llo'}
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z')


def _require(condition, message):
    if not condition:
        raise ValidationError(message)


def _number(value, label, *, positive=False):
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
        valid = valid and (value > 0 if positive else value >= 0)
    except OverflowError:
        valid = False
    _require(valid, f'{label} must be finite and nonnegative')


def _text(value, label):
    _require(type(value) is str and bool(value.strip()), f'{label} must be nonempty text')


def _refs(value):
    _require(type(value) is list and bool(value), 'source_refs must be a nonempty list')
    for ref in value:
        _text(ref, 'source reference')


def _base(binding, artifact_refs):
    canonical_json(binding)
    keys = {'strategy_id', 'parent_id', 'candidate_id', 'run_id', 'context_id',
            'provenance', 'declared_identity'}
    _require(type(binding) is dict and set(binding) == keys, 'Invalid profile binding fields')
    for name in keys - {'provenance', 'declared_identity'}:
        _require(type(binding[name]) is str and _ID.fullmatch(binding[name]) is not None,
                 f'Invalid binding {name}')
    _require(type(binding['provenance']) is str and binding['provenance'] in {'synthetic', 'saved', 'real'},
             'Invalid profile provenance')
    try:
        validate_record(CandidateIdentity(**binding['declared_identity']))
    except (TypeError, AttributeError) as error:
        raise ValidationError('Invalid declared identity') from error
    _require(type(artifact_refs) is list, 'artifact_refs must be a list')
    for ref in artifact_refs:
        try:
            validate_record(ArtifactRef(**ref))
        except TypeError as error:
            raise ValidationError('Invalid artifact reference') from error
    unavailable = {'value': None, 'availability': 'unavailable'}
    return {
        'schema_version': 1, 'format': 'normalized_profile.v1',
        'binding': binding, 'binding_qualification': 'declared_identity_only',
        'artifact_refs': artifact_refs, 'events': [], 'operation_stats': [],
        'interval_unions': [], 'metadata': {name: [] for name in sorted(_META)},
        'capabilities': {},
        'critical_path': {**unavailable, 'reason': 'Event count or interval union does not establish dependencies or critical path.'},
        'multi_device_span': {**unavailable, 'reason': 'Cross-device clock alignment has not been verified.'},
        'quality': {'availability': 'unavailable', 'errors': [], 'warnings': [],
                    'issues': [] if artifact_refs else ['original_artifacts_unavailable']},
    }


def _statistics(samples):
    if not samples:
        return {'count': 0, **{name: None for name in ('mean_us', 'median_us', 'p95_us', 'min_us', 'max_us')}}
    values = sorted(value / 1e6 for value in samples)
    index = (len(values) - 1) * .95
    low, high = math.floor(index), math.ceil(index)
    return {'count': len(values), 'mean_us': statistics.mean(values),
            'median_us': statistics.median(values),
            'p95_us': values[low] + (values[high] - values[low]) * (index - low),
            'min_us': values[0], 'max_us': values[-1]}


def _interval_unions(events):
    domains = {}
    for event in events:
        classification = event['classification']
        # Measured/trace-derived timestamps share a domain; predictions do not.
        if classification in {'measured', 'trace_derived'}:
            classification = 'trace_derived'
        key = event['device'], event['clock'], classification
        domains.setdefault(key, []).append(event)
    result = []
    for (device, clock, classification), members in domains.items():
        intervals = sorted((item['start_ps'], item['start_ps'] + item['duration_ps'])
                           for item in members
                           if item['start_ps'] is not None and item['duration_ps'] is not None)
        merged = []
        for start, end in intervals:
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        value = sum(end - start for start, end in merged) if merged else None
        status = 'unavailable' if not merged else 'available' if len(intervals) == len(members) else 'partial'
        result.append({'device': device, 'clock': clock, 'classification': classification,
                       'duration_ps': value, 'unit': 'ps', 'availability': status,
                       'scope': 'union_of_supplied_intervals_not_end_to_end_or_critical_path',
                       'event_ids': [item['id'] for item in members],
                       'missing_interval_count': len(members) - len(intervals)})
    return result


def normalize_events(events: list, *, binding: dict, artifact_refs: list) -> dict:
    """Normalize explicit saved/synthetic events; absent metrics stay null.

    Each event has id/name/kind/device/clock/classification/start_ps/duration_ps/
    bytes/source_refs/reason/metadata. Metadata may carry compiler_metadata,
    resource_metadata or llo with value/classification/source_refs. No field is
    inferred from an event name. Units are always ps and bytes.
    """
    canonical_json(events)
    result = _base(binding, artifact_refs)
    _require(type(events) is list, 'events must be a list')
    keys = {'id', 'name', 'kind', 'device', 'clock', 'classification', 'start_ps',
            'duration_ps', 'bytes', 'source_refs', 'reason', 'metadata'}
    seen, groups = set(), {}
    for item in events:
        _require(type(item) is dict and set(item) == keys, 'Invalid normalized input event fields')
        for name in ('id', 'name', 'device', 'clock'):
            _text(item[name], name)
        _require(item['id'] not in seen, 'Duplicate event ID')
        seen.add(item['id'])
        _require(type(item['kind']) is str and item['kind'] in _KINDS, 'Unknown event kind')
        _require(type(item['classification']) is str and item['classification'] in _CLASSES,
                 'Unknown evidence classification')
        _refs(item['source_refs'])
        for name in ('start_ps', 'duration_ps', 'bytes'):
            if item[name] is not None:
                _number(item[name], name)
        if item['duration_ps'] is None or item['start_ps'] is None:
            _text(item['reason'], 'missing timing reason')
        elif item['reason'] is not None:
            _text(item['reason'], 'reason')
        _require(type(item['metadata']) is dict and not (set(item['metadata']) - _META), 'Unknown event metadata')
        for name, metadata in item['metadata'].items():
            _require(type(metadata) is dict and set(metadata) == {'value', 'classification', 'source_refs'},
                     'Invalid event metadata fields')
            _require(metadata['value'] is not None and type(metadata['classification']) is str
                     and metadata['classification'] in _CLASSES,
                     'Metadata requires a value and classification')
            _refs(metadata['source_refs'])
            result['metadata'][name].append({'event_id': item['id'], **metadata})
        key = (item['kind'], item['name'], item['device'], item['clock'], item['classification'])
        groups.setdefault(key, []).append(item)
    result['events'] = events
    for (kind, name, device, clock, classification), members in groups.items():
        result['operation_stats'].append({
            'kind': kind, 'name': name, 'device': device, 'clock': clock,
            'classification': 'trace_derived' if classification == 'measured' else classification,
            'event_count': len(members), 'missing_duration_count': sum(item['duration_ps'] is None for item in members),
            'event_ids': [item['id'] for item in members], 'unit': 'us',
            **_statistics([item['duration_ps'] for item in members if item['duration_ps'] is not None]),
        })
    result['interval_unions'] = _interval_unions(events)
    capabilities = {'target_execution': {'program', 'custom_call'}, 'execution_intervals': _KINDS,
                    'dma': {'dma'}, 'waits': {'wait', 'synchronization'}, 'custom_call': {'custom_call'}}
    for name, kinds in capabilities.items():
        selected = [item for item in events if item['kind'] in kinds]
        available = bool(selected)
        result['capabilities'][name] = {
            'availability': 'available' if available else 'unavailable',
            'event_count': len(selected) if available else None,
            'reason': None if available else 'Not present in supplied evidence.',
        }
    timed = sum(item['start_ps'] is not None and item['duration_ps'] is not None for item in events)
    result['capabilities']['execution_intervals']['availability'] = (
        'unavailable' if not timed else 'available' if timed == len(events) else 'partial')
    result['capabilities']['execution_intervals']['reason'] = (
        None if timed == len(events) and events else 'Some or all event timestamps are unavailable.')
    for name in _META:
        present = bool(result['metadata'][name])
        result['capabilities'][name] = {'availability': 'available' if present else 'unavailable',
                                        'reason': None if present else 'Not present in supplied evidence.'}
    incomplete = any(item['duration_ps'] is None or item['start_ps'] is None for item in events)
    result['quality']['availability'] = 'unavailable' if not events else 'partial' if incomplete or not artifact_refs else 'available'
    return json.loads(canonical_json(result))


def normalize_duration_report(report: dict, *, binding: dict, artifact_refs: list) -> dict:
    """Reuse audited read_profile JSON, retaining independently named scopes.

    The report's summaries must agree with its raw positive samples. This checks
    internal consistency, not whether the producer measured the claimed kernel.
    """
    canonical_json(report)
    _require(type(report) is dict, 'Duration report must be an object')
    required = {'time_source', 'unit', 'profile', 'calls', 'devices', 'groups',
                'longest_device_per_call', 'multi_device_span', 'errors', 'warnings'}
    _require(required <= set(report), 'Incomplete legacy duration report')
    _require(report['unit'] == 'us' and report['time_source'] == 'TPU XPlane device_duration_ps',
             'Unsupported legacy time source or units')
    calls = report['calls']
    _require(type(calls) is int and calls > 0, 'calls must be a positive integer')
    _require(type(report['devices']) is list and all(type(d) is str for d in report['devices'])
             and len(set(report['devices'])) == len(report['devices']), 'Invalid device list')
    _require(type(report['groups']) is list, 'Invalid legacy groups')
    for name in ('errors', 'warnings'):
        _require(type(report[name]) is list and all(type(item) is str for item in report[name]), 'Invalid diagnostics')
    events, stats_rows, issues = [], [], []
    for group_index, group in enumerate(report['groups']):
        _require(type(group) is dict and type(group.get('kind')) is str
                 and group['kind'] in {'program', 'kernel'}, 'Invalid legacy event group')
        _require(group.get('plane') in report['devices'], 'Event group references unlisted device')
        _text(group.get('name'), 'group name')
        _require(type(group.get('line')) is str and group['line'] in {'XLA Modules', 'XLA Ops', 'Async XLA Ops'},
                 'Unknown legacy event track')
        _require(type(group.get('line_id')) is int, 'Invalid line ID')
        program_id = group.get('program_id')
        _require(program_id is None or type(program_id) in (str, int), 'Invalid program ID')
        _require(group['kind'] != 'kernel' or program_id is not None, 'Kernel lacks program identity')
        _require(type(group.get('samples')) is list and bool(group['samples']), 'Legacy group needs samples')
        _require(type(group.get('invalid_samples')) is int and group['invalid_samples'] >= 0, 'Invalid missing sample count')
        durations = []
        for index, sample in enumerate(group['samples']):
            _require(type(sample) is dict and set(sample) == {'duration_ps', 'device_offset_ps', 'step_num', 'run_id'},
                     'Invalid legacy sample fields')
            _number(sample['duration_ps'], 'device_duration_ps', positive=True)
            start = sample['device_offset_ps']
            if start is not None:
                _number(start, 'device_offset_ps')
            step = sample['step_num']
            _require(step is None or type(step) is int and 0 <= step < calls, 'Invalid sample step')
            durations.append(sample['duration_ps'])
            events.append({
                'id': f'group:{group_index}:sample:{index}', 'name': group['name'],
                'kind': 'program' if group['kind'] == 'program' else 'custom_call',
                'device': group['plane'], 'clock': 'device_offset_ps', 'classification': 'measured',
                'start_ps': start, 'duration_ps': sample['duration_ps'], 'bytes': None,
                'source_refs': [f'legacy:groups/{group_index}/samples/{index}'],
                'reason': 'device_offset_ps unavailable' if start is None else None, 'metadata': {},
            })
        stats = _statistics(durations)
        if len(durations) % calls or group['kind'] == 'program' and len(durations) != calls:
            issues.append(f'group:{group_index}:sample_count_inconsistent_with_calls')
        for name, value in stats.items():
            _number(group.get(name), name)
            _require(math.isclose(group[name], value, rel_tol=1e-9, abs_tol=1e-12), f'Inconsistent legacy {name}')
        _number(group.get('events_per_measured_call'), 'events_per_measured_call')
        _require(math.isclose(group['events_per_measured_call'], len(durations) / calls), 'Inconsistent events per call')
        stats_rows.append({**stats, 'kind': events[-1]['kind'], 'name': group['name'],
                           'device': group['plane'], 'clock': 'device_offset_ps', 'classification': 'trace_derived',
                           'unit': 'us', 'program_id': group.get('program_id'), 'line': group['line'],
                           'line_id': group['line_id'], 'invalid_samples': group['invalid_samples'],
                           'events_per_measured_call': group['events_per_measured_call'],
                           'event_ids': [f'group:{group_index}:sample:{i}' for i in range(len(durations))]})
    result = normalize_events(events, binding=binding, artifact_refs=artifact_refs)
    result['operation_stats'] = stats_rows
    result['producer'] = {'format': 'PallasKernels.read_profile', 'profile_path_hint': report['profile'],
                          'calls': calls, 'time_source': report['time_source']}
    result['quality']['errors'] = report['errors']
    result['quality']['warnings'] = report['warnings']
    result['quality']['issues'].extend(issues)
    if events and (report['errors'] or report['warnings'] or issues or any(row['invalid_samples'] for row in stats_rows)):
        result['quality']['availability'] = 'partial'
    return json.loads(canonical_json(result))


def parse_offline(reader, directory: Path, *, calls: int, device_ids: list,
                  program_name: str, binding: dict, artifact_refs: list) -> dict:
    """Call a caller-selected offline parser; missing dependencies stay explicit."""
    # Validate binding before invoking any caller-supplied operation.
    _base(binding, artifact_refs)
    try:
        report = reader(directory, calls=calls, device_ids=device_ids, program_name=program_name)
    except ImportError as error:
        result = normalize_events([], binding=binding, artifact_refs=artifact_refs)
        result['quality']['issues'].append(f'offline_parser_dependency_unavailable: {type(error).__name__}')
        return result
    return normalize_duration_report(report, binding=binding, artifact_refs=artifact_refs)
