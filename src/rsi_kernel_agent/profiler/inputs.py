"""Versioned input capture and offline replay over the existing experiment store.

Source bytes and decoded Knowledge captures are content addressed. Knowledge's
opaque raw-file revision is retained separately from the decoded-content digest.
Capture does not create experiments, select parents, or update best projections.
"""
from dataclasses import asdict, dataclass
from contextlib import contextmanager
from contextvars import ContextVar
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
from ..memory import MemoryReader, _Index

_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z')
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
_PUBLICATION_VALIDATIONS = ContextVar('rsi_publication_validations', default=None)


@contextmanager
def _validation_scope():
    """Share recursive validation work only until the root synchronous call returns."""
    token = None
    if _PUBLICATION_VALIDATIONS.get() is None:
        token = _PUBLICATION_VALIDATIONS.set({})
    try:
        yield
    finally:
        if token is not None:
            _PUBLICATION_VALIDATIONS.reset(token)


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
        _require(type(payload['schema_version']) is int and payload['schema_version'] in (1, 2), 'Unknown AnalysisRequest version')
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
        if payload['schema_version'] == 2:
            _require(len(payload['strategies']) == 1, 'Route analysis requires exactly one strategy')
            for name in ('design_round_id', 'design_batch_id'):
                _id(payload['environment'].get(name))
            environment = payload['environment']
            phase = environment.get('analysis_phase')
            _require(phase in (None, 'offline_preparation', 'execution'), 'Invalid analysis phase')
            if phase == 'offline_preparation':
                _id(environment.get('preparation_id'))
                _require(environment['preparation_id'] == environment['design_batch_id']
                         and 'offline_route_id' not in environment, 'Offline preparation identity differs')
            if 'offline_route_id' in environment:
                _require(phase == 'execution', 'A prepared draft requires explicit execution review')
                _id(environment['offline_route_id'])
                _id(environment.get('preparation_id'))
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
    def __init__(self, store, format_revision=1, replay=None):
        self.store = store
        self.format_revision = format_revision
        self.replay = replay
        self.replay_artifacts = ({ref['digest'] for ref in replay['verified_artifacts']}
                                 if replay is not None else None)
        self.records = {}
        self.artifacts = {}
        self.references = {}
        self.issues = []
        self.route_context = None

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
        _require(self.replay_artifacts is None or ref.digest in self.replay_artifacts,
                 'Artifact was unavailable in the frozen capture')
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

    def evidence(self, rid, binding, allowed, visiting=None, *, compact=False):
        visiting = set() if visiting is None else visiting
        _require(rid not in visiting, 'Cyclic evidence dependencies')
        evidence = self.record(rid, Evidence, allowed)
        _require(evidence.run_id == binding['run_id'], 'Current evidence input belongs to a different Run/Candidate/strategy')
        _require(evidence.source.get('provenance') == binding['provenance'], 'Evidence provenance mismatch')
        visiting.add(rid)
        upstream = [self.evidence(x, binding, allowed, visiting, compact=compact) for x in evidence.input_ids]
        visiting.remove(rid)
        available, artifacts = evidence.availability, []
        if evidence.request_status != 'completed' or evidence.claim_status != 'supported':
            if available == 'available':
                available = 'unavailable' if evidence.request_status == 'failed' or evidence.claim_status == 'refuted' else 'partial'
            self.issue('evidence_not_supported', 'evidence:' + rid,
                       f'Request is {evidence.request_status}; claim is {evidence.claim_status}')
        raw_reference_only = compact and isinstance(evidence.value, dict) and evidence.value.get('format') == 'XPlane protobuf'
        for ref in (() if raw_reference_only else evidence.artifacts):
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


def _source_provenance(document):
    """Retain archived coverage separately from having read the entire saved note."""
    metadata, complete = None, True
    if document['id'].startswith('RSI/Sources/'):
        complete = False
        blocks = list(re.finditer(r'^```rsi-source-v1[ \t]*\r?\n(.*?)^```[ \t]*\r?$',
                                  document['content'], re.M | re.S))
        if len(blocks) == 1:
            try:
                parsed = json.loads(blocks[0][1])
                if type(parsed) is dict:
                    metadata = parsed
                    coverage = parsed.get('coverage', {})
                    content = document['content'][blocks[0].end():]
                    if content.startswith('\n\n'):
                        content = content[2:]
                    complete = (type(parsed.get('schema_version')) is int and parsed['schema_version'] == 1
                                and type(coverage) is dict and coverage.get('complete') is True
                                and type(coverage.get('read_chars')) is int
                                and type(coverage.get('expected_chars')) is int
                                and coverage['read_chars'] == coverage['expected_chars'] == len(content)
                                and parsed.get('content_sha256') == sha256(content.encode('utf-8')).hexdigest())
            except ValueError:
                pass
    return {'document': document, 'provenance': metadata, 'coverage_complete': complete}


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
                if capture.format_revision >= 2:
                    status.update(document=None, provenance=None, coverage_complete=False)
                try:
                    current = await knowledge.fetch_document(source.document_id)
                    status['current_revision'] = current.revision
                    if current.revision == source.revision:
                        status.update(availability='available', artifact=capture.archive(current.content))
                        if capture.format_revision >= 2:
                            status.update(_source_provenance(asdict(current)))
                            if not status['coverage_complete']:
                                status.update(availability='partial', reason='Archived source coverage is incomplete or unverified')
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


def _prior_analysis(capture, payload, sid):
    """Validate original publication bytes, expose only explicitly owned content."""
    from ..workflow.intake import _publication
    marker = capture.store.get(payload['id'])
    _require(_same(record_to_dict(marker), payload), 'Prior publication differs from frozen snapshot')
    details = payload['details']
    artifacts = [details['manifest'], details['result']]
    # Cache only deterministic replay work. Every occurrence still reads and
    # verifies the pinned original bytes, even when another dependency used it.
    for ref in artifacts:
        capture.read(_artifact(ref))
    validated = _PUBLICATION_VALIDATIONS.get()
    key = (id(capture.store), payload['id'], _digest(payload))
    if validated is not None and key in validated:
        original, result = validated[key]
    else:
        original, result = _publication(capture.store, marker)
        if validated is not None:
            validated[key] = original, result
    _require(sid in original['strategies'], 'Prior publication does not analyze this route')
    analysis = {key: [row for row in result['analysis'][key] if row['strategy_id'] == sid]
                for key in ('code_understanding', 'observations', 'hypotheses')}
    plans = [row for row in result['plan_set']['plans'] if row['strategy_id'] == sid]
    plan_set = {'schema_version': result['plan_set']['schema_version'], 'plans': plans}
    # Global unknowns/review text and exchanges have no ownership proof in v1.
    if original['schema_version'] == 2:
        analysis.update({key: value for key, value in result['analysis'].items() if key not in analysis})
        if 'outcome' in result['plan_set']:
            plan_set['outcome'] = result['plan_set']['outcome']
    projection = {'publication_id': payload['id'], 'strategy_id': sid,
                  'analysis': analysis, 'plan_set': plan_set,
                  'calculations': {key: row for key, row in result['calculations'].items()
                                   if row.get('strategy_id') == sid},
                  'scope': 'single_strategy_projection', 'omitted_global_payload': original['schema_version'] == 1}
    capture.reference('analysis:' + payload['id'] + ':' + sid, 'PriorAnalysis', projection,
                      strategy_id=sid, artifacts=artifacts)
    plan_ids = {row['id'] for row in plans}
    projected_details = {**details,
        'bundles': [row for row in details['bundles'] if original['strategies'][sid]['run'] is not None
                    and row['run_id'] == original['strategies'][sid]['run']['id']],
        'plans': [row for row in details['plans'] if row['proposal_id'] in plan_ids],
        'scope': 'single_strategy_projection'}
    return {'kind': 'Decision', 'payload': {**payload, 'details': projected_details}}, projection


def _route_history(index, item, allowed, capture):
    sid = item['strategy_id']
    owned = [rid for rid in allowed if index.owner(rid) == sid]
    _require(len(owned) == index.body['snapshot']['strategy_counts'][sid],
             'Route history ownership coverage is incomplete')
    shared = [rid for rid, row in allowed.items() if row['kind'] in ('Task', 'Workload', 'EvaluationContext')]
    seeds = [*owned, *shared, item['parent_id'], *item['profile_ids'], *item['benchmark_ids']]
    if capture.format_revision >= 2:
        for rid, row in allowed.items():
            if row['kind'] != 'Decision':
                continue
            details = row['payload']['details']
            # Publications bind bundle IDs to the analyzed strategy, while
            # Memory owns bundles through their Run. A fork may analyze its
            # parent's Run, so Run ownership cannot select its conversation.
            bundle_id = rid.removesuffix(':complete') + ':' + sha256(sid.encode()).hexdigest()[:24]
            if details.get('type') == 'analysis_result.v1' and (
                    any(plan.get('record_id') in owned for plan in details.get('plans', ()))
                    or any(bundle.get('id') == bundle_id for bundle in details.get('bundles', ()))):
                seeds.append(rid)
    if item['run_id'] is not None:
        seeds.append(item['run_id'])
    parent_candidates = {rid for rid in owned if allowed[rid]['kind'] == 'Candidate'}
    parent_ids = [item['parent_id']]
    parent_ids.extend(allowed[rid]['payload']['parent_id'] for rid in parent_candidates)
    parent_ids.extend(allowed[rid]['payload']['details']['parent_id'] for rid in owned
                      if allowed[rid]['kind'] == 'Decision' and allowed[rid]['payload']['details'].get('type') == 'parent_selection.v1')
    seen_parents = set()
    while parent_ids:
        parent_id = parent_ids.pop()
        if parent_id in seen_parents:
            continue
        seen_parents.add(parent_id)
        parent = allowed[parent_id]['payload']
        candidate_id = parent.get('candidate_id')
        if candidate_id is not None:
            parent_candidates.add(candidate_id)
            parent_ids.append(allowed[candidate_id]['payload']['parent_id'])
    explicit, pending = set(), seeds[:]
    while pending:
        rid = pending.pop()
        if rid in explicit:
            continue
        _require(rid in allowed, 'Explicit route dependency is outside the frozen snapshot: ' + rid)
        explicit.add(rid)
        row = allowed[rid]
        value = row['payload']
        refs = [value[name] for name in ('task_id', 'workload_id', 'strategy_id', 'plan_id', 'parent_id',
                'candidate_id', 'run_id', 'context_id', 'target_id') if value.get(name) is not None]
        refs.extend(ref for name in ('evidence_ids', 'profile_ids', 'input_ids') for ref in value.get(name, ()))
        if row['kind'] == 'Candidate':
            refs.extend(key for key, other in allowed.items() if other['kind'] == 'Run'
                        and other['payload']['candidate_id'] == rid)
        if row['kind'] == 'Run':
            refs.extend(key for key, other in allowed.items() if other['payload'].get('run_id') == rid)
        if row['kind'] not in ('Task', 'Strategy', 'Workload', 'EvaluationContext'):
            for key, other in allowed.items():
                if other['kind'] != 'Decision' or other['payload']['target_id'] != rid:
                    continue
                details = other['payload']['details']
                if row['kind'] == 'Plan' and index.owner(rid) != sid:
                    if details.get('type') not in ('plan_parent.v1', 'knowledge_link.v1'):
                        continue
                    if details.get('candidate_id') is not None and details['candidate_id'] not in parent_candidates:
                        continue
                refs.append(key)
        if row['kind'] == 'Decision' and value['details'].get('type') in (
                'parent_selection.v1', 'plan_parent.v1', 'knowledge_link.v1'):
            details = value['details']
            refs.extend(details[name] for name in ('strategy_id', 'context_id', 'plan_id', 'parent_id',
                        'candidate_id', 'candidate_parent_id', 'run_id') if details.get(name) is not None)
            refs.extend(details.get('evidence_ids', ()))
        if (capture.format_revision >= 2 and row['kind'] == 'Decision'
                and index.owner(rid) == sid and value['details'].get('type') == 'route_design.v1'):
            publication_id = value['details'].get('outcome', {}).get('publication_id')
            if publication_id is not None:
                refs.append(publication_id)
        pending.extend(ref for ref in refs if ref not in explicit)
    records = [row for rid, row in allowed.items() if rid in explicit]
    dependencies = [row['payload']['id'] for row in records if row['payload']['id'] not in set(owned)]
    pages = []
    for offset in range(0, len(records), 100):
        page_records = records[offset:offset + 100]
        end = offset + len(page_records)
        pages.append({'offset': offset, 'count': len(page_records),
                      'next_offset': end if end < len(records) else None})
        for row in page_records:
            rid = row['payload']['id']
            capture.records[rid] = {'kind': row['kind'], 'digest': _digest(row['payload'])}
    scoped, rendered, analyses = [], [], []
    for row in records:
        payload = row['payload']
        if (capture.format_revision >= 2 and row['kind'] == 'Decision'
                and payload['details'].get('type') == 'analysis_result.v1'):
            projected, analysis = _prior_analysis(capture, payload, sid)
            rendered.append(projected)
            analyses.append(analysis)
            scoped.append({'record_id': payload['id'], 'original_digest': capture.records[payload['id']]['digest'],
                           'scope': 'route_analysis_projection', 'omitted_global_payload': True})
        elif row['kind'] == 'Decision' and payload['details'].get('type') == 'workflow_state.v1':
            value = {'kind': row['kind'], 'payload': {**payload, 'details': dict(payload['details'])}}
            details = value['payload']['details']
            state = details['state']
            jobs = {key: job for key, job in state['jobs'].items() if job['strategy_id'] == sid}
            projection = {key: state[key] for key in ('task_id', 'revision', 'cancelled', 'spent') if key in state}
            projection.update(jobs=jobs, served={sid: state.get('served', {}).get(sid, 0)},
                paused=[sid] if sid in state.get('paused', []) else [],
                commands={key: command for key, command in state.get('commands', {}).items()
                          if command.get('job_id') in jobs or command.get('source') in jobs},
                control_events=[event for event in state.get('control_events', []) if event['strategy_id'] == sid])
            projection['limits'] = {key: limit for key, limit in state.get('limits', {}).items() if key != 'weights'}
            projection['limits']['weights'] = {sid: state.get('limits', {}).get('weights', {}).get(sid, 1)}
            details['state'] = projection
            details['state_scope'] = 'single_strategy_projection'
            scoped.append({'record_id': payload['id'], 'original_digest': capture.records[payload['id']]['digest'],
                           'scope': 'route_job_projection', 'omitted_global_payload': True})
            rendered.append(value)
        else:
            rendered.append(row)
    snapshot = {**index.body['snapshot'], 'strategy_counts': {sid: index.body['snapshot']['strategy_counts'][sid]}}
    history = {'snapshot': snapshot, 'strategy_id': sid, 'records': rendered,
               'total': len(records), 'owned_total': len(owned), 'dependency_total': len(dependencies),
               'owned_ids': owned, 'dependency_ids': dependencies, 'offset': 0, 'next_offset': None, 'complete': True}
    if capture.format_revision >= 2:
        history['analyses'] = analyses
    capture.route_context = {'strategy_id': sid, 'snapshot_id': item['snapshot_id'],
        'history_coverage': {'expected_records': len(records), 'read_records': len(records), 'missing_ids': [],
            'dependency_ids': dependencies, 'pages': pages},
        'explicit_dependencies': [row['payload']['id'] for row in records if row['payload']['id'] in explicit
                                  and row['payload']['id'] not in owned],
        'fresh_input_scope': 'single_strategy', 'scoped_records': scoped}
    return history


def _capture_local(store, raw, capture):
    task = capture.record(raw['task_id'], Task)
    workload = capture.record(raw['workload_id'], Workload)
    _require(workload.task_id == task.id, 'Workload belongs to another Task')
    snapshots, strategies = {}, {}
    for item in raw['strategies']:
        sid, snapshot_id = item['strategy_id'], item['snapshot_id']
        if snapshot_id not in snapshots:
            if raw['schema_version'] == 2:
                page = store.snapshot_page(snapshot_id, limit=2**31 - 1)
                _require(page['snapshot']['id'] == snapshot_id and page['snapshot']['task_id'] == task.id
                         and page['offset'] == 0 and page['complete'] and page['next_offset'] is None
                         and len(page['records']) == page['total'] == page['snapshot']['record_count'],
                         'Memory snapshot coverage is incomplete')
                rows = [{'kind': type(record).__name__, 'payload': record_to_dict(record)} for record in page['records']]
                _require(len({row['payload']['id'] for row in rows}) == len(rows), 'Duplicate snapshot record')
                view = _Index({'snapshot': page['snapshot'], 'records': rows, 'projections': page['projections']})
                allowed = view.by_id
            else:
                view = MemoryReader(store).open_snapshot(snapshot_id)
                _require(view.snapshot['task_id'] == task.id, 'Memory snapshot belongs to another Task')
                page = store.snapshot_page(snapshot_id, limit=view.snapshot['record_count'])
                _require(page['snapshot'] == view.snapshot and page['complete'], 'Memory snapshot coverage is incomplete')
                allowed = {r.id: {'kind': type(r).__name__, 'payload': record_to_dict(r)} for r in page['records']}
                for r in page['records']:
                    capture.record(r.id, type(r), allowed)
            snapshots[snapshot_id] = (view, allowed)
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
            _require(run.context_id == context.id and (raw['schema_version'] == 2 or plan.strategy_id == sid)
                     and parent.candidate_id == candidate.id
                     and parent.identity == candidate.identity, 'Run does not measure the exact strategy parent and context')
        source_ref, config_ref = _artifact(item['source_artifact']), _artifact(item['config_artifact'])
        _require(source_ref.digest == parent.identity.source_digest and config_ref.digest == parent.identity.config_digest,
                 'Source/config bytes do not match exact ParentCandidate identity')
        try:
            source, config = capture.read(source_ref).decode('utf-8-sig'), capture.read(config_ref).decode('utf-8-sig')
        except UnicodeDecodeError as error:
            raise ValidationError('Pinned source/config must be UTF-8 text') from error
        metric_mode = raw['schema_version'] == 2 and raw['tool_versions'].get('route_progress') == '1'
        if metric_mode:
            from .progress import capture_progress
            history = capture_progress(view, item, capture)
        else:
            history = (_route_history(view, item, allowed, capture) if raw['schema_version'] == 2
                       else view.histories((sid,))[sid])
        _require(history['complete'], 'Participating strategy requires its complete history')
        binding = {'strategy_id': sid, 'parent_id': parent.id, 'candidate_id': parent.candidate_id,
                   'run_id': item['run_id'], 'context_id': context.id, 'provenance': context.provenance}
        if raw['schema_version'] == 2:
            parent_strategy = None
            if parent.candidate_id is not None:
                original = capture.record(parent.candidate_id, Candidate, allowed)
                parent_strategy = capture.record(original.plan_id, Plan, allowed).strategy_id
            binding['parent_strategy_id'] = parent_strategy
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
                evidence_ids = list(bundle.evidence_ids)
                if metric_mode and kind is EvaluationBundle:
                    evidence_ids = [eid for eid in evidence_ids if allowed[eid]['kind'] == 'Evidence'
                        and allowed[eid]['payload']['unit'] == 'ms'
                        and type(allowed[eid]['payload']['value']) in (int, float)
                        and allowed[eid]['payload']['evidence_kind'] in ('measured_timing', 'trace_derived', 'derived_measurement')]
                evidence = [capture.evidence(eid, binding, allowed, compact=metric_mode) for eid in evidence_ids]
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
        if raw['schema_version'] == 2:
            strategies[sid]['parent_strategy_id'] = parent_strategy
    if raw['environment'].get('offline_route_id') is not None:
        from ..workflow.offline_preparation import capture_draft
        capture_draft(capture, raw, strategies)
    return record_to_dict(task), record_to_dict(workload), strategies


async def capture_inputs(store, knowledge, request):
    """Capture strict independent bindings; missing optional evidence stays explicit."""
    _require(type(request) is AnalysisRequest, 'Expected AnalysisRequest')
    raw = AnalysisRequest.from_dict(request.to_dict()).to_dict()
    revision = 6 if raw['schema_version'] == 2 and raw['tool_versions'].get('route_progress') == '1' else 2
    capture = _Capture(store, format_revision=revision)
    with _validation_scope():
        task, workload, strategies = _capture_local(store, raw, capture)
    pinned_knowledge = await _capture_knowledge(capture, knowledge, raw['knowledge'])
    data = {'schema_version': raw['schema_version'], 'format_revision': capture.format_revision,
            'request': raw, 'task': task, 'workload': workload,
            'strategies': strategies, 'knowledge': pinned_knowledge, 'references': capture.references,
            'record_refs': capture.records, 'verified_artifacts': list(capture.artifacts.values()),
            'issues': capture.issues, 'consistency': {'memory_snapshot_complete': True,
                'knowledge_corpus_snapshot_consistent': False, 'combined_atomic_snapshot': False}}
    if raw['schema_version'] == 2:
        data['route_context'] = capture.route_context
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
    names = 'schema_version request task workload strategies knowledge references record_refs verified_artifacts issues consistency'
    format_revision = data.get('format_revision', 1)
    _require(type(format_revision) is int and format_revision in (1, 2, 3, 4, 5, 6), 'Invalid manifest format revision')
    if 'format_revision' in data:
        names += ' format_revision'
    _require(type(data.get('schema_version')) is int and data['schema_version'] in (1, 2)
             and body == canonical_json(data), 'Invalid manifest version or canonical bytes')
    _fields(data, names + (' route_context' if data['schema_version'] == 2 else ''), 'AnalysisInputManifest')
    request = AnalysisRequest.from_dict(data['request']).to_dict()
    _require(request['schema_version'] == data['schema_version'], 'Manifest and request versions differ')
    for rid, expected in data['record_refs'].items():
        record = store.get(rid)
        _require(type(record).__name__ == expected['kind'] and _digest(record_to_dict(record)) == expected['digest'], 'Referenced record changed')
    for ref in data['verified_artifacts']:
        store.artifacts.verify(_artifact(ref))
    capture = _Capture(store, format_revision=format_revision, replay=data if format_revision >= 5 else None)
    task, workload, strategies = _capture_local(store, request, capture)
    if request['schema_version'] == 2:
        _require(_same(data['route_context'], capture.route_context), 'Route input scope or coverage changed')
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
                source_fields = 'document_id revision availability current_revision artifact excerpt reason'
                if format_revision >= 2:
                    source_fields += ' document provenance coverage_complete'
                _fields(status, source_fields, 'captured Knowledge source')
                _require(status['document_id'] == source.document_id and status['revision'] == source.revision
                         and status['excerpt'] == source.excerpt, 'Knowledge source reference changed')
                states = ('available', 'unavailable', 'stale', 'partial') if format_revision >= 2 else ('available', 'unavailable', 'stale')
                _require(status['availability'] in states, 'Invalid Knowledge source availability')
                if status['availability'] in ('available', 'partial'):
                    _require(status['current_revision'] == source.revision and status['artifact'] is not None
                             and (status['reason'] is None if status['availability'] == 'available'
                                  else type(status['reason']) is str and bool(status['reason'].strip())),
                             'Available source has no pinned document')
                    text = store.artifacts.read(_artifact(status['artifact'])).decode('utf-8')
                    if format_revision >= 2:
                        document = status['document']
                        _fields(document, 'id title category revision content', 'captured source document')
                        _require(all(type(value) is str for value in document.values())
                                 and document['id'] == source.document_id and document['revision'] == source.revision
                                 and document['content'] == text, 'Pinned source content changed')
                        expected = _source_provenance(document)
                        _require(_same({key: status[key] for key in expected}, expected)
                                 and status['availability'] == ('available' if expected['coverage_complete'] else 'partial'),
                                 'Pinned source coverage or provenance changed')
                else:
                    _require(status['artifact'] is None and type(status['reason']) is str and bool(status['reason'].strip()),
                             'Missing/stale source requires a reason and cannot contain substituted full text')
                    if status['availability'] == 'stale':
                        _require(type(status['current_revision']) is str and bool(_DIGEST.fullmatch(status['current_revision']))
                                 and status['current_revision'] != source.revision, 'Stale source requires a different observed revision')
                    else:
                        _require(status['current_revision'] is None, 'Unavailable source has no observed revision')
                    if format_revision >= 2:
                        _require(status['document'] is None and status['provenance'] is None
                                 and status['coverage_complete'] is False, 'Missing source cannot claim captured coverage')
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
        with _validation_scope():
            return _load_manifest(store, artifact_ref)
    except (KeyError, TypeError, UnicodeError, AttributeError, RecursionError) as error:
        raise ValidationError('Malformed analysis input manifest') from error
