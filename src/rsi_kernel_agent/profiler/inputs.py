"""Versioned input capture and offline replay over the existing experiment store.

Source bytes and decoded Knowledge captures are content addressed. Knowledge's
opaque raw-file revision is retained separately from the decoded-content digest.
Capture does not create experiments, select parents, or update best projections.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
import re

from ..contracts import (
    ArtifactRef, Candidate, EvaluationBundle, EvaluationContext, Evidence,
    ParentCandidate, Plan, ProfileBundle, Run, Strategy, Task, ValidationError,
    Workload, canonical_json, record_to_dict, validate_record,
)
from ..knowledge import KnowledgeError, RevisionConflict, _parse_method, method_note_id, method_to_dict
from ..memory import MemoryReader

_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z')
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')


def _require(condition, message):
    if not condition:
        raise ValidationError(message)


def _fields(value, names, label):
    _require(type(value) is dict and set(value) == set(names.split()), f'Invalid {label} fields')


def _id(value):
    _require(type(value) is str and bool(_ID.fullmatch(value)), 'Expected a stable record ID')


def _artifact(value):
    _fields(value, 'digest size', 'artifact')
    ref = ArtifactRef(**value)
    validate_record(ref)
    return ref


def _digest(value):
    return sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _same(left, right):
    """JSON identity retains bool/int/float distinctions hidden by Python ==."""
    return canonical_json(left) == canonical_json(right)


@dataclass(frozen=True)
class AnalysisRequest:
    _body: str

    @classmethod
    def from_dict(cls, payload):
        _fields(payload, 'schema_version request_id task_id workload_id strategies knowledge environment tool_versions', 'AnalysisRequest')
        canonical_json(payload)
        _require(type(payload['schema_version']) is int and payload['schema_version'] == 1, 'Unknown AnalysisRequest version')
        for name in ('request_id', 'task_id', 'workload_id'):
            _id(payload[name])
        _require(type(payload['strategies']) is list and bool(payload['strategies']), 'At least one strategy is required')
        seen = set()
        for item in payload['strategies']:
            _fields(item, 'strategy_id parent_id context_id snapshot_id run_id source_artifact config_artifact profile_ids benchmark_ids', 'strategy binding')
            for name in ('strategy_id', 'parent_id', 'context_id', 'snapshot_id'):
                _id(item[name])
            _require(item['strategy_id'] not in seen, 'Duplicate strategy binding')
            seen.add(item['strategy_id'])
            if item['run_id'] is not None:
                _id(item['run_id'])
            for name in ('profile_ids', 'benchmark_ids'):
                _require(type(item[name]) is list, f'{name} must be an array')
                for rid in item[name]:
                    _id(rid)
                _require(len(item[name]) == len(set(item[name])), 'Duplicate evidence bundle reference')
                _require(item['run_id'] is not None or not item[name], 'Runless analysis cannot claim measured bundles')
            _artifact(item['source_artifact'])
            _artifact(item['config_artifact'])
        _require(type(payload['knowledge']) is list, 'Knowledge pins must be an array')
        seen = set()
        for pin in payload['knowledge']:
            _fields(pin, 'method_id note_id revision', 'Knowledge pin')
            _id(pin['method_id'])
            _require(pin['method_id'] not in seen, 'Duplicate Knowledge method')
            seen.add(pin['method_id'])
            _require(pin['note_id'] == method_note_id(pin['method_id']), 'Method note path does not match ID')
            _require(type(pin['revision']) is str and bool(_DIGEST.fullmatch(pin['revision'])), 'Invalid Knowledge revision')
        _require(type(payload['environment']) is dict and bool(payload['environment']), 'Analysis environment is required')
        versions = payload['tool_versions']
        _require(type(versions) is dict and bool(versions) and all(type(v) is str and v.strip() for v in versions.values()), 'Tool versions are required')
        return cls(canonical_json(payload))

    def to_dict(self):
        return json.loads(self._body)


@dataclass(frozen=True)
class AnalysisInputManifest:
    _body: str
    artifact_ref: ArtifactRef

    def to_dict(self):
        return json.loads(self._body)


class _Capture:
    def __init__(self, store):
        self.store = store
        self.records = {}
        self.artifacts = {}
        self.references = {}
        self.issues = []

    def issue(self, code, reference, reason):
        self.issues.append({'code': code, 'reference': reference, 'reason': reason})

    def record(self, rid, kind, allowed=None):
        record = self.store.get(rid)
        _require(type(record) is kind, f'{rid} must reference {kind.__name__}')
        payload = record_to_dict(record)
        if allowed is not None:
            _require(rid in allowed and allowed[rid] == {'kind': kind.__name__, 'payload': payload},
                     f'{rid} is missing or inconsistent in the selected Memory snapshot')
        self.records[rid] = {'kind': kind.__name__, 'digest': _digest(payload)}
        return record

    def read(self, ref):
        raw = self.store.artifacts.read(ref)
        self.artifacts[ref.digest] = asdict(ref)
        return raw

    def archive(self, text):
        ref = self.store.artifacts.put(text.encode('utf-8'))
        self.artifacts[ref.digest] = asdict(ref)
        return asdict(ref)

    def reference(self, key, kind, value, availability='available', **binding):
        entry = {'kind': kind, 'availability': availability, 'value': value, **binding}
        if key in self.references:
            _require(self.references[key] == entry, 'Conflicting reference catalog identity')
        self.references[key] = entry
        return entry

    def evidence(self, rid, binding, allowed, visiting=None):
        visiting = set() if visiting is None else visiting
        _require(rid not in visiting, 'Cyclic evidence dependencies')
        evidence = self.record(rid, Evidence, allowed)
        _require(evidence.run_id == binding['run_id'], 'Current evidence input belongs to a different Run/Candidate/strategy')
        _require(evidence.source.get('provenance') == binding['provenance'], 'Evidence provenance mismatch')
        visiting.add(rid)
        upstream = [self.evidence(x, binding, allowed, visiting) for x in evidence.input_ids]
        visiting.remove(rid)
        available, artifacts = evidence.availability, []
        if evidence.request_status != 'completed' or evidence.claim_status != 'supported':
            if available == 'available':
                available = 'unavailable' if evidence.request_status == 'failed' or evidence.claim_status == 'refuted' else 'partial'
            self.issue('evidence_not_supported', 'evidence:' + rid,
                       f'Request is {evidence.request_status}; claim is {evidence.claim_status}')
        for ref in evidence.artifacts:
            try:
                self.read(ref)
                artifacts.append(asdict(ref))
            except ValidationError as error:
                available = 'unavailable'
                self.issue('artifact_unavailable', 'evidence:' + rid, str(error))
        if evidence.availability != 'unavailable' and not evidence.artifacts:
            available = 'unavailable'
            self.issue('artifact_unavailable', 'evidence:' + rid, 'No raw artifact supplied for current evidence')
        if any(x['availability'] == 'unavailable' for x in upstream):
            available = 'unavailable'
        elif available == 'available' and any(x['availability'] == 'partial' for x in upstream):
            available = 'partial'
        if evidence.evidence_kind in ('measured_timing','measured_counter','trace_derived','derived_measurement') and any(
                x['value']['evidence_kind'] in ('model_predicted','compiler_estimated','source_claim','historical_claim') for x in upstream):
            available = 'unavailable'
            self.issue('measurement_from_nonmeasurement', 'evidence:' + rid,
                       'Predictions, compiler estimates and source/history claims cannot establish measured evidence')
        if available != 'available':
            self.issue('evidence_incomplete', 'evidence:' + rid, 'Current evidence cannot support an unqualified measurement claim')
        return self.reference('evidence:' + rid, 'Evidence', record_to_dict(evidence), available,
                              artifacts=artifacts, **binding)


async def _capture_knowledge(capture, knowledge, pins):
    results = {}
    for pin in pins:
        mid = pin['method_id']
        item = {**pin, 'availability': 'unavailable', 'method': None, 'document': None, 'artifact': None, 'sources': []}
        try:
            _require(knowledge is not None, 'Knowledge transport is unavailable')
            document = await knowledge.fetch_document(pin['note_id'], expected_revision=pin['revision'])
            method, _ = _parse_method(document.content)
            _require(method.method_id == mid, 'Pinned method has wrong identity')
            item.update(availability='available', method=method_to_dict(method), document=asdict(document),
                        artifact=capture.archive(document.content))
            for source in method.source_refs:
                status = {'document_id': source.document_id, 'revision': source.revision, 'availability': 'unavailable',
                          'current_revision': None, 'artifact': None, 'excerpt': source.excerpt, 'reason': None}
                try:
                    current = await knowledge.fetch_document(source.document_id)
                    status['current_revision'] = current.revision
                    if current.revision == source.revision:
                        status.update(availability='available', artifact=capture.archive(current.content))
                    else:
                        status.update(availability='stale', reason='Live source differs from the method-pinned revision; only pinned excerpt is retained')
                except KnowledgeError as error:
                    status['reason'] = str(error)
                if status['availability'] != 'available':
                    capture.issue('knowledge_source_' + status['availability'], 'knowledge:' + mid, status['reason'])
                item['sources'].append(status)
        except RevisionConflict as error:
            item['availability'] = 'stale'
            capture.issue('knowledge_stale', 'knowledge:' + mid, str(error))
        except (KnowledgeError, ValidationError) as error:
            capture.issue('knowledge_unavailable', 'knowledge:' + mid, str(error))
        results[mid] = item
        capture.reference('knowledge:' + mid, 'MethodRecord', item['method'], item['availability'],
                          strategy_id=None, revision=pin['revision'], sources=item['sources'], artifact=item['artifact'])
    return results


def _capture_local(store, raw, capture):
    task = capture.record(raw['task_id'], Task)
    workload = capture.record(raw['workload_id'], Workload)
    _require(workload.task_id == task.id, 'Workload belongs to another Task')
    snapshots, strategies = {}, {}
    for item in raw['strategies']:
        sid, snapshot_id = item['strategy_id'], item['snapshot_id']
        if snapshot_id not in snapshots:
            view = MemoryReader(store).open_snapshot(snapshot_id)
            _require(view.snapshot['task_id'] == task.id, 'Memory snapshot belongs to another Task')
            page = store.snapshot_page(snapshot_id, limit=view.snapshot['record_count'])
            _require(page['snapshot'] == view.snapshot and page['complete'], 'Memory snapshot coverage is incomplete')
            allowed = {r.id: {'kind': type(r).__name__, 'payload': record_to_dict(r)} for r in page['records']}
            snapshots[snapshot_id] = (view, allowed)
            for r in page['records']:
                capture.record(r.id, type(r), allowed)
        view, allowed = snapshots[snapshot_id]
        strategy = capture.record(sid, Strategy, allowed)
        parent = capture.record(item['parent_id'], ParentCandidate, allowed)
        context = capture.record(item['context_id'], EvaluationContext, allowed)
        _require(strategy.task_id == parent.task_id == context.task_id == task.id and context.workload_id == workload.id,
                 'Strategy/Parent/Workload/Environment binding mismatch')
        run = None
        if item['run_id'] is not None:
            run = capture.record(item['run_id'], Run, allowed)
            candidate = capture.record(run.candidate_id, Candidate, allowed)
            plan = capture.record(candidate.plan_id, Plan, allowed)
            _require(run.context_id == context.id and plan.strategy_id == sid and parent.candidate_id == candidate.id
                     and parent.identity == candidate.identity, 'Run does not measure the exact strategy parent and context')
        source_ref, config_ref = _artifact(item['source_artifact']), _artifact(item['config_artifact'])
        _require(source_ref.digest == parent.identity.source_digest and config_ref.digest == parent.identity.config_digest,
                 'Source/config bytes do not match exact ParentCandidate identity')
        try:
            source, config = capture.read(source_ref).decode('utf-8-sig'), capture.read(config_ref).decode('utf-8-sig')
        except UnicodeDecodeError as error:
            raise ValidationError('Pinned source/config must be UTF-8 text') from error
        history = view.histories((sid,))[sid]
        _require(history['complete'], 'Participating strategy requires its complete history')
        binding = {'strategy_id': sid, 'parent_id': parent.id, 'candidate_id': parent.candidate_id,
                   'run_id': item['run_id'], 'context_id': context.id, 'provenance': context.provenance}
        capture.reference('source:' + sid, 'Source', source, artifact=asdict(source_ref), **binding)
        capture.reference('config:' + sid, 'Configuration', config, artifact=asdict(config_ref), **binding)
        capture.reference('history:' + sid, 'History', history, **binding)
        bundles = {'profiles': [], 'benchmarks': []}
        for key, ids, kind, prefix in [('profiles', item['profile_ids'], ProfileBundle, 'profile:'),
                                      ('benchmarks', item['benchmark_ids'], EvaluationBundle, 'benchmark:')]:
            if not ids:
                capture.issue('not_supplied', key + ':' + sid, 'No current ' + key + ' supplied')
            for rid in ids:
                bundle = capture.record(rid, kind, allowed)
                _require(bundle.run_id == item['run_id'], 'Profile/Benchmark belongs to another strategy or Run')
                evidence = [capture.evidence(eid, binding, allowed) for eid in bundle.evidence_ids]
                availability = getattr(bundle, 'availability', 'available')
                if not evidence or any(e['availability'] == 'unavailable' for e in evidence):
                    availability = 'unavailable'
                elif availability == 'available' and any(e['availability'] == 'partial' for e in evidence):
                    availability = 'partial'
                if kind is EvaluationBundle:
                    events = [row['payload'] for row in allowed.values() if row['kind'] == 'RunEvent'
                              and row['payload']['run_id'] == bundle.run_id]
                    succeeded = bool(events) and events[-1]['execution_status'] == 'succeeded'
                    valid_timing = type(bundle.latency_ms) in (int, float) and math.isfinite(bundle.latency_ms) and bundle.latency_ms > 0
                    matching = any(e['availability'] == 'available' and e['value']['evidence_kind'] in ('measured_timing','trace_derived','derived_measurement')
                                   and e['value']['unit'] == 'ms' and type(e['value']['value']) in (int, float)
                                   and e['value']['value'] == bundle.latency_ms and bool(e['artifacts']) for e in evidence)
                    if not (succeeded and bundle.correctness == 'passed' and bundle.timing_validity == 'valid'
                            and bundle.confirmed and valid_timing and matching):
                        availability = 'unavailable'
                        capture.issue('benchmark_not_verified', prefix + rid,
                                      'Current timing requires a succeeded Run, confirmed valid correctness/timing, '
                                      'and matching supported completed measured/derived timing in ms with verified raw bytes')
                payload = record_to_dict(bundle)
                bundles[key].append(payload)
                capture.reference(prefix + rid, kind.__name__, payload, availability, **binding)
        strategies[sid] = {'strategy_id': sid, 'parent_id': parent.id, 'parent': record_to_dict(parent),
                           'context': record_to_dict(context), 'run': record_to_dict(run) if run else None,
                           'source': source, 'config': config, 'source_artifact': asdict(source_ref),
                           'config_artifact': asdict(config_ref), 'history': history, **bundles}
    return record_to_dict(task), record_to_dict(workload), strategies


async def capture_inputs(store, knowledge, request):
    """Capture strict independent bindings; missing optional evidence stays explicit."""
    _require(type(request) is AnalysisRequest, 'Expected AnalysisRequest')
    raw = AnalysisRequest.from_dict(request.to_dict()).to_dict()
    capture = _Capture(store)
    task, workload, strategies = _capture_local(store, raw, capture)
    pinned_knowledge = await _capture_knowledge(capture, knowledge, raw['knowledge'])
    data = {'schema_version': 1, 'request': raw, 'task': task, 'workload': workload,
            'strategies': strategies, 'knowledge': pinned_knowledge, 'references': capture.references,
            'record_refs': capture.records, 'verified_artifacts': list(capture.artifacts.values()),
            'issues': capture.issues, 'consistency': {'memory_snapshot_complete': True,
                'knowledge_corpus_snapshot_consistent': False, 'combined_atomic_snapshot': False}}
    body = canonical_json(data)
    ref = store.artifacts.put(body.encode('utf-8'))
    return AnalysisInputManifest(body, ref)


def _load_manifest(store, artifact_ref):
    """Replay captured bytes, never fetch latest Knowledge or mix newer Memory."""
    body = store.artifacts.read(artifact_ref).decode('utf-8')
    try:
        data = json.loads(body)
    except ValueError as error:
        raise ValidationError('Invalid manifest JSON') from error
    _fields(data, 'schema_version request task workload strategies knowledge references record_refs verified_artifacts issues consistency', 'AnalysisInputManifest')
    _require(type(data['schema_version']) is int and data['schema_version'] == 1 and body == canonical_json(data), 'Invalid manifest version or canonical bytes')
    request = AnalysisRequest.from_dict(data['request']).to_dict()
    for rid, expected in data['record_refs'].items():
        record = store.get(rid)
        _require(type(record).__name__ == expected['kind'] and _digest(record_to_dict(record)) == expected['digest'], 'Referenced record changed')
    for ref in data['verified_artifacts']:
        store.artifacts.verify(_artifact(ref))
    capture = _Capture(store)
    task, workload, strategies = _capture_local(store, request, capture)
    _require(_same((task, workload, strategies), (data['task'], data['workload'], data['strategies'])), 'Captured source/strategy/record payload changed')
    _require(_same(capture.records, data['record_refs']), 'Record reference coverage changed')
    expected_keys = set(capture.references) | {'knowledge:' + pin['method_id'] for pin in request['knowledge']}
    _require(set(data['references']) == expected_keys, 'Reference catalog coverage changed')
    # Repaired formerly missing blobs do not retrospectively upgrade this capture.
    # Identity, raw payload and binding remain strict; availability can only be
    # more conservative than the current availability discovered during replay.
    for key, current in capture.references.items():
        saved = data['references'][key]
        _require(type(saved) is dict and set(saved) == set(current), 'Reference catalog fields changed')
        _require(_same({k: v for k, v in current.items() if k not in ('availability', 'artifacts')},
                      {k: v for k, v in saved.items() if k not in ('availability', 'artifacts')}), 'Reference binding or value changed')
        _require(saved['availability'] in ('available', 'partial', 'unavailable'), 'Invalid reference availability')
        rank = {'unavailable': 0, 'partial': 1, 'available': 2}
        _require(rank[saved['availability']] <= rank[current['availability']], 'Available evidence no longer reproducible')
        if 'artifacts' in saved:
            _require(type(saved['artifacts']) is list and all(any(_same(ref, now) for now in current['artifacts'])
                                                            for ref in saved['artifacts']), 'Evidence artifact reference changed')
            if saved['availability'] == 'available':
                _require(_same(saved['artifacts'], current['artifacts']), 'Available evidence artifact coverage is incomplete')
    _require(set(data['knowledge']) == {pin['method_id'] for pin in request['knowledge']}, 'Knowledge pin coverage changed')
    for pin in request['knowledge']:
        saved = data['knowledge'][pin['method_id']]
        _fields(saved, 'method_id note_id revision availability method document artifact sources', 'captured Knowledge method')
        _require(all(saved[k] == v for k, v in pin.items()), 'Knowledge pin changed')
        _require(saved['availability'] in ('available', 'unavailable', 'stale'), 'Invalid Knowledge availability')
        if saved['availability'] == 'available':
            document = saved['document']
            _fields(document, 'id title category revision content', 'captured Knowledge document')
            _require(all(type(value) is str for value in document.values()), 'Captured Knowledge document fields must be text')
            text = store.artifacts.read(_artifact(saved['artifact'])).decode('utf-8')
            method, _ = _parse_method(text)
            _require(document['content'] == text and document['id'] == pin['note_id'] and document['revision'] == pin['revision']
                     and method.method_id == pin['method_id'] and _same(method_to_dict(method), saved['method']), 'Pinned Knowledge content changed')
            _require(type(saved['sources']) is list, 'Knowledge sources must be an array')
            _require(len(saved['sources']) == len(method.source_refs), 'Knowledge source coverage changed')
            for source, status in zip(method.source_refs, saved['sources']):
                _fields(status, 'document_id revision availability current_revision artifact excerpt reason', 'captured Knowledge source')
                _require(status['document_id'] == source.document_id and status['revision'] == source.revision
                         and status['excerpt'] == source.excerpt, 'Knowledge source reference changed')
                _require(status['availability'] in ('available', 'unavailable', 'stale'), 'Invalid Knowledge source availability')
                if status['availability'] == 'available':
                    _require(status['current_revision'] == source.revision and status['artifact'] is not None and status['reason'] is None,
                             'Available source has no pinned document')
                    store.artifacts.verify(_artifact(status['artifact']))
                else:
                    _require(status['artifact'] is None and type(status['reason']) is str and bool(status['reason'].strip()),
                             'Missing/stale source requires a reason and cannot contain substituted full text')
                    if status['availability'] == 'stale':
                        _require(type(status['current_revision']) is str and bool(_DIGEST.fullmatch(status['current_revision']))
                                 and status['current_revision'] != source.revision, 'Stale source requires a different observed revision')
                    else:
                        _require(status['current_revision'] is None, 'Unavailable source has no observed revision')
        else:
            _require(saved['method'] is None and saved['document'] is None and saved['artifact'] is None and not saved['sources'],
                     'Unavailable method cannot contain substituted content')
        expected = {'kind': 'MethodRecord', 'availability': saved['availability'], 'value': saved['method'],
                    'strategy_id': None, 'revision': pin['revision'], 'sources': saved['sources'], 'artifact': saved['artifact']}
        _require(_same(data['references']['knowledge:' + pin['method_id']], expected), 'Knowledge reference catalog changed')
    expected_artifacts = {}
    for entry in data['references'].values():
        refs = list(entry.get('artifacts', ()))
        if entry.get('artifact') is not None:
            refs.append(entry['artifact'])
        refs.extend(source['artifact'] for source in entry.get('sources', ()) if source['artifact'] is not None)
        for payload in refs:
            ref = _artifact(payload)
            _require(ref.digest not in expected_artifacts or _same(expected_artifacts[ref.digest], payload), 'Conflicting artifact sizes')
            expected_artifacts[ref.digest] = payload
    _require(type(data['verified_artifacts']) is list and len(data['verified_artifacts']) == len(expected_artifacts)
             and _same({ref['digest']: ref for ref in data['verified_artifacts']}, expected_artifacts),
             'Verified artifact catalog must cover every captured reference exactly once')
    _require(_same(data['consistency'], {'memory_snapshot_complete': True, 'knowledge_corpus_snapshot_consistent': False,
                                       'combined_atomic_snapshot': False}), 'Unjustified consistency claim')
    return AnalysisInputManifest(body, artifact_ref)


def load_manifest(store, artifact_ref):
    """Validate all reconstructed bindings and archived content before replay."""
    try:
        return _load_manifest(store, artifact_ref)
    except (KeyError, TypeError, UnicodeError, AttributeError, RecursionError) as error:
        raise ValidationError('Malformed analysis input manifest') from error
