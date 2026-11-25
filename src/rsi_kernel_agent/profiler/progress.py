import json
import math

from ..contracts import ArtifactRef, canonical_json


_METRICS = {'host_ms': 'ms', 'device_ms': 'ms', 'mfu': 'ratio', 'cycles': 'cycles'}


def _missing(unit, reason='No supported saved measurement'):
    return {'value': None, 'unit': unit, 'availability': 'unavailable', 'reason': reason,
            'evidence_ids': [], 'clock': None, 'definition': None, 'scope': None}


def _numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


class _Progress:
    def __init__(self, index, capture):
        self.index, self.capture = index, capture
        self.records = index.by_id
        self.states = index.states()
        self.by_run, self.jobs, self.results = {}, {}, {}
        for row in index.records:
            p = row['payload']
            if p.get('run_id') is not None:
                self.by_run.setdefault(p['run_id'], []).append(row)
            if row['kind'] != 'Decision':
                continue
            d = p['details']
            if d.get('type') == 'workflow_state.v1':
                for job in d.get('state', {}).get('jobs', {}).values():
                    if job.get('run_id'):
                        self.jobs[job['run_id']] = (p['id'], job)
            elif d.get('type') == 'colab_execution_request.v1':
                job = d['request']['job']
                self.jobs[job['run_id']] = (p['id'], job)
            elif d.get('type') == 'workflow_result.v1':
                self.results[p['target_id']] = (p['id'], d['result'])
        self.cache, self.artifacts = {}, {}

    def read(self, ref):
        value = self.capture.read(ArtifactRef(**ref))
        self.artifacts[ref['digest']] = ref
        return value

    def pin(self, rid):
        row = self.records[rid]
        self.capture.records[rid] = {'kind': row['kind'], 'digest': self.capture_digest(row['payload'])}
        return row['payload']

    @staticmethod
    def capture_digest(value):
        from hashlib import sha256
        return sha256(canonical_json(value).encode('utf-8')).hexdigest()

    def evidence(self, value, visiting=None, *, read_artifacts=True):
        self.pin(value['id'])
        visiting = set() if visiting is None else visiting
        if value['id'] in visiting:
            return False
        visiting.add(value['id'])
        usable = (value['availability'] == 'available' and value['request_status'] == 'completed'
                  and value['claim_status'] == 'supported' and bool(value['artifacts']))
        for rid in value['input_ids']:
            dependency = self.records.get(rid)
            if (dependency is None or dependency['kind'] != 'Evidence'
                    or dependency['payload']['run_id'] != value['run_id']
                    or dependency['payload']['evidence_kind'] not in (
                        'measured_counter', 'measured_timing', 'trace_derived', 'derived_measurement')
                    or not self.evidence(dependency['payload'], visiting, read_artifacts=False)):
                usable = False
        if usable and read_artifacts:
            for ref in value['artifacts']:
                try:
                    raw = self.read(ref)
                    if self.capture.format_revision >= 4:
                        try:
                            receipt = json.loads(raw)
                        except (ValueError, UnicodeError):
                            receipt = None
                        if isinstance(receipt, dict) and receipt.get('backend_id') in ('colab.v1', 'synthetic.v1'):
                            run = self.records[value['run_id']]['payload']
                            if (receipt.get('run_id') != run['id'] or receipt.get('candidate_id') != run['candidate_id']
                                    or receipt.get('context_id') != run['context_id']
                                    or value['unit'] == 'ms' and receipt.get('stage') != 'benchmark'):
                                usable = False
                except (ValueError, OSError):
                    usable = False
        visiting.remove(value['id'])
        return usable

    def metric(self, evidence, value, unit, *, clock=None, definition=None, scope=None):
        return {'value': value, 'unit': unit, 'availability': 'available', 'reason': None,
                'evidence_ids': [evidence['id']], 'clock': clock,
                'definition': definition, 'scope': scope,
                'evidence_kind': evidence['evidence_kind'], 'source': evidence['source']}

    def metrics(self, run, context, evaluations):
        values = {name: _missing(unit) for name, unit in _METRICS.items()}
        latency = _missing('ms')
        timer = context['protocol'].get('benchmark', {}).get('timer', context['protocol'].get('timer', ''))
        clock = ('synthetic' if context['provenance'] == 'synthetic' else
                 'host_synchronized' if 'host' in timer or 'profiler.measure' in timer else
                 'device' if 'device' in timer else 'unspecified')
        records = self.by_run.get(run['id'], [])
        timings = []
        for row in records:
            if row['kind'] != 'Evidence':
                continue
            e = row['payload']
            if e['evidence_kind'] not in ('measured_counter', 'measured_timing', 'trace_derived', 'derived_measurement'):
                continue
            source = e['source']
            if self.capture.format_revision >= 3 and isinstance(e['value'], dict) and e['value'].get('format') == 'XPlane protobuf':
                continue
            metric_id = source.get('metric_id')
            profile_ids = {eid for r in records if r['kind'] == 'ProfileBundle'
                           for eid in r['payload']['evidence_ids']}
            parsed_profile = (e['evidence_kind'] == 'trace_derived' and (e['id'] in profile_ids or
                              isinstance(e['value'], dict) and e['value'].get('format') == 'PallasKernels.read_profile'))
            if not (metric_id in ('mfu', 'cycles') or e['unit'] == 'ms' and _numeric(e['value']) or parsed_profile):
                continue
            if not self.evidence(e) or source.get('provenance') != context['provenance']:
                continue
            if metric_id in ('mfu', 'cycles') and _numeric(e['value']) and source.get('definition') and source.get('scope'):
                wanted = _METRICS[metric_id]
                if e['unit'] == wanted and e['value'] >= 0:
                    values[metric_id] = self.metric(e, e['value'], wanted, clock=source.get('clock'),
                        definition=source['definition'], scope=source['scope'])
            if e['unit'] == 'ms' and _numeric(e['value']) and e['evidence_kind'] in ('measured_timing', 'trace_derived', 'derived_measurement'):
                timings.append(e)
            if e['evidence_kind'] == 'trace_derived':
                for ref in e['artifacts']:
                    try:
                        report = json.loads(self.read(ref))
                    except (ValueError, OSError, UnicodeError):
                        continue
                    if not isinstance(report, dict) or report.get('time_source') != 'TPU XPlane device_duration_ps':
                        continue
                    from .profiles import normalize_duration_report
                    candidate = self.records[run['candidate_id']]['payload']
                    try:
                        normalized = normalize_duration_report(report, binding={
                            'strategy_id': self.index.owner(run['id']), 'parent_id': candidate['parent_id'],
                            'candidate_id': candidate['id'], 'run_id': run['id'], 'context_id': context['id'],
                            'provenance': context['provenance'], 'declared_identity': candidate['identity']},
                            artifact_refs=[ref])
                    except (ValueError, KeyError, TypeError):
                        continue
                    programs = [g for g in normalized['operation_stats'] if g['kind'] == 'program']
                    if len(programs) == 1 and normalized['quality']['availability'] == 'available':
                        values['device_ms'] = self.metric(e, programs[0]['median_us'] / 1000, 'ms',
                            clock='device', definition='Median saved program duration', scope='profile_program')
        evaluation = evaluations[-1] if evaluations else None
        if evaluation is not None and evaluation['timing_validity'] == 'valid' and _numeric(evaluation['latency_ms']):
            matching = [e for e in timings if e['id'] in evaluation['evidence_ids'] and e['value'] == evaluation['latency_ms']]
            if matching:
                e = matching[-1]
                latency = self.metric(e, evaluation['latency_ms'], 'ms', clock=clock,
                    definition='Saved EvaluationBundle latency', scope=context['protocol'].get('benchmark', {}).get('scope'))
                if clock == 'host_synchronized':
                    values['host_ms'] = dict(latency)
                elif clock == 'device' and values['device_ms']['value'] is None:
                    values['device_ms'] = dict(latency)
        return latency, values

    def prior_analysis(self, sid):
        candidates = [r['payload'] for r in self.index.records if r['kind'] == 'Decision'
                      and r['payload']['target_id'] == sid
                      and r['payload']['details'].get('type') == 'route_design.v1']
        for closure in reversed(candidates):
            publication_id = closure['details'].get('outcome', {}).get('publication_id')
            if publication_id is None:
                continue
            publication = self.pin(publication_id)
            ref = publication['details']['result']
            result = json.loads(self.read(ref))
            self.pin(closure['id'])
            return {'publication_id': publication_id, 'classification': 'historical_claim',
                    'hypotheses': [r for r in result['analysis']['hypotheses'] if r['strategy_id'] == sid],
                    'plans': [{k: r[k] for k in ('id', 'parent_id', 'mechanism', 'modification', 'risks', 'unknowns')}
                              for r in result['plan_set']['plans'] if r['strategy_id'] == sid],
                    'outcome': result['plan_set'].get('outcome')}
        return None

    def history(self, sid):
        if sid in self.cache:
            return self.cache[sid]
        self.pin(sid)
        rows = []
        for record in self.index.records:
            if record['kind'] != 'Candidate' or self.index.owner(record['payload']['id']) != sid:
                continue
            candidate = self.pin(record['payload']['id'])
            plan = self.pin(candidate['plan_id'])
            self.pin(candidate['parent_id'])
            runs = [r['payload'] for r in self.index.records if r['kind'] == 'Run'
                    and r['payload']['candidate_id'] == candidate['id']]
            for run in runs or [None]:
                base = {'strategy_id': sid, 'candidate_id': candidate['id'], 'plan_id': plan['id'],
                        'modification': plan['description'], 'parent_id': candidate['parent_id'],
                        'source_identity': candidate['identity'], 'change_kind': candidate['change_kind'],
                        'run_id': run['id'] if run else None, 'round_id': None, 'context_id': None,
                        'workload_id': None, 'environment': None, 'protocol': None, 'provenance': None,
                        'execution_status': 'not_run', 'scheduling_status': 'ready', 'failure_reason': None,
                        'correctness': 'unknown', 'timing_validity': 'unknown', 'confirmed': False,
                        'evaluation_ids': [], 'profile_ids': [], 'classification': 'not_run', 'cost': None,
                        'latency': _missing('ms'), 'metrics': {name: _missing(unit) for name, unit in _METRICS.items()}}
                if run is not None:
                    self.pin(run['id'])
                    context = self.pin(run['context_id'])
                    self.pin(context['workload_id'])
                    state = self.states[run['id']]
                    base.update(context_id=context['id'], workload_id=context['workload_id'],
                        environment=context['environment'], protocol=context['protocol'], provenance=context['provenance'],
                        execution_status=state['execution_status'], scheduling_status=state['scheduling_status'])
                    if state['event_id'] is not None:
                        event = self.pin(state['event_id'])
                        if state['execution_status'] in ('failed', 'invalid', 'incomplete') or state['scheduling_status'] == 'deferred':
                            base['failure_reason'] = event['reason']
                    evaluations = [self.pin(r['payload']['id']) for r in self.by_run.get(run['id'], [])
                                   if r['kind'] == 'EvaluationBundle']
                    base['evaluation_ids'] = [e['id'] for e in evaluations]
                    base['profile_ids'] = [self.pin(r['payload']['id'])['id'] for r in self.by_run.get(run['id'], [])
                                           if r['kind'] == 'ProfileBundle']
                    if evaluations:
                        base.update({k: evaluations[-1][k] for k in ('correctness', 'timing_validity', 'confirmed')})
                    base['latency'], base['metrics'] = self.metrics(run, context, evaluations)
                    if run['id'] in self.jobs:
                        marker, job = self.jobs[run['id']]
                        self.pin(marker)
                        base['round_id'] = job.get('round_id')
                        base['cost'] = job.get('variant', {}).get('cost')
                    if run['id'] in self.results:
                        marker, result = self.results[run['id']]
                        self.pin(marker)
                        base['classification'] = result.get('classification')
                        base['cost'] = result.get('cost', base['cost'])
                rows.append(base)
        rows.sort(key=lambda row: self.index.order[row['run_id'] or row['candidate_id']])
        history = {'scope': 'complete_metric_history', 'snapshot_id': self.index.body['snapshot']['id'],
                   'strategy_id': sid, 'rows': rows, 'total': len(rows), 'complete': True,
                   'latest_analysis': self.prior_analysis(sid),
                   'raw_history_included': False, 'raw_details': 'Read explicitly referenced records and artifacts on demand'}
        self.cache[sid] = history
        return history

    def candidate_catalog(self):
        bindings = {}
        for row in self.index.records:
            if row['kind'] != 'Decision' or row['payload']['details'].get('type') != 'workflow_candidate.v1':
                continue
            marker = row['payload']
            bindings.setdefault(marker['target_id'], []).append(marker)
        for row in self.index.records:
            if row['kind'] != 'Candidate':
                continue
            candidate = self.pin(row['payload']['id'])
            sid = self.index.owner(candidate['id'])
            sources = {}
            for marker in bindings.get(candidate['id'], []):
                self.pin(marker['id'])
                details = marker['details']
                for key in ('source_ref', 'config_ref', 'build_ref', 'source_bundle_ref'):
                    if key not in details:
                        continue
                    ref = details[key]
                    if key != 'source_bundle_ref' and ref['digest'] != candidate['identity'][key.replace('_ref', '_digest')]:
                        raise ValueError('Candidate source metadata differs from its frozen identity')
                    if key in sources and sources[key] != ref:
                        raise ValueError('Conflicting frozen candidate source references')
                    sources[key] = ref
            for key in ('source_ref', 'config_ref'):
                if key in sources:
                    continue
                digest = candidate['identity'][key.replace('_ref', '_digest')]
                if self.capture.replay is not None:
                    saved = self.capture.replay['references']['candidate:' + candidate['id']]['value']['source_refs'][key]
                    if saved is not None and (saved['digest'] != digest or type(saved['size']) is not int or saved['size'] < 0):
                        raise ValueError('Candidate frozen source reference changed')
                    sources[key] = saved
                    continue
                try:
                    size = self.capture.store.artifacts.path_for(ArtifactRef(digest, 0)).stat().st_size
                    sources[key] = {'digest': digest, 'size': size}
                except (ValueError, OSError):
                    sources[key] = None
            runs = {}
            for run_row in self.index.records:
                if run_row['kind'] != 'Run' or run_row['payload']['candidate_id'] != candidate['id']:
                    continue
                run = self.pin(run_row['payload']['id'])
                context = self.pin(run['context_id'])
                state = self.states[run['id']]
                event = self.pin(state['event_id']) if state['event_id'] else None
                entries = self.by_run.get(run['id'], [])
                evidence = {}

                def collect(eid):
                    if eid in evidence:
                        return
                    e = self.pin(eid)
                    if self.records[eid]['kind'] != 'Evidence':
                        raise ValueError('Candidate evidence is not an Evidence record')
                    if e['run_id'] != run['id'] and self.capture.format_revision < 6:
                        raise ValueError('Candidate evidence belongs to another frozen Run')
                    evidence[eid] = {key: e[key] for key in ('id', 'run_id', 'evidence_kind', 'availability',
                        'request_status', 'claim_status', 'unit', 'artifacts', 'input_ids')}
                    evidence[eid].update(provenance=e['source'].get('provenance'),
                        value=e['value'] if _numeric(e['value']) else None,
                        format=e['value'].get('format') if isinstance(e['value'], dict) else None)
                    for dependency in e['input_ids']:
                        collect(dependency)

                evaluations, profiles = [], []
                for entry in entries:
                    if entry['kind'] not in ('EvaluationBundle', 'ProfileBundle'):
                        continue
                    bundle = self.pin(entry['payload']['id'])
                    selected = []
                    for eid in bundle['evidence_ids']:
                        e = self.records[eid]['payload']
                        if entry['kind'] == 'ProfileBundle' or (
                                e['evidence_kind'] in ('measured_timing', 'trace_derived', 'derived_measurement')
                                and e['unit'] == 'ms' and _numeric(e['value'])):
                            collect(eid)
                            selected.append(eid)
                    compact = {**bundle, 'evidence_ids': selected}
                    (profiles if entry['kind'] == 'ProfileBundle' else evaluations).append(compact)
                runs[run['id']] = {'run_id': run['id'], 'candidate_id': candidate['id'], 'context': context,
                    'execution_status': state['execution_status'], 'scheduling_status': state['scheduling_status'],
                    'failure_reason': event['reason'] if event and state['execution_status'] in ('failed', 'invalid', 'incomplete') else None,
                    'benchmarks': evaluations, 'profiles': profiles, 'evidence': evidence}
                if self.capture.format_revision >= 6:
                    failures = []
                    for entry in entries:
                        if entry['kind'] != 'Evidence':
                            continue
                        e = entry['payload']
                        details = e['value']
                        if (isinstance(details, dict) and details.get('stage') in
                                ('generate', 'compile', 'correctness', 'benchmark', 'profile')
                                and details.get('status') not in (None, 'succeeded')):
                            self.pin(e['id'])
                            failures.append({'evidence_id': e['id'], 'stage': details['stage'],
                                'status': details['status'], 'outputs': details.get('outputs', {}),
                                'artifact_refs': e['artifacts']})
                    runs[run['id']]['failures'] = failures
            value = {'snapshot_id': self.index.body['snapshot']['id'], 'strategy_id': sid,
                'candidate_id': candidate['id'], 'parent_id': candidate['parent_id'], 'plan_id': candidate['plan_id'],
                'identity': candidate['identity'], 'source_refs': sources, 'runs': runs,
                'current': False, 'classification': 'historical_claim'}
            self.capture.reference('candidate:' + candidate['id'], 'History', value, strategy_id=None,
                owner_strategy_id=sid, current=False)

    def global_progress(self, context_id):
        routes = []
        for sid in self.index.strategy_ids():
            strategy = self.pin(sid)
            history = self.history(sid)
            rows = history['rows']
            latest = rows[-1] if rows else None
            latest_round = latest['round_id'] if latest else None
            recent = [r for r in rows if r['round_id'] == latest_round] if latest_round is not None else ([latest] if latest else [])
            decisions = [r['payload'] for r in self.index.records if r['kind'] == 'Decision' and r['payload']['target_id'] == sid]
            paused = next((d for d in reversed(decisions) if d['kind'] in ('paused', 'resumed')), None)
            route_decision = next((d for d in reversed(decisions) if d['details'].get('type') == 'route_decision.v1'), None)
            if paused:
                self.pin(paused['id'])
            if route_decision:
                self.pin(route_decision['id'])
            latest_control = max((d for d in (paused, route_decision) if d is not None),
                                 key=lambda d: self.index.order[d['id']], default=None)
            is_paused = bool(latest_control and (latest_control['kind'] == 'paused' or
                latest_control['details'].get('type') == 'route_decision.v1'
                and latest_control['details']['decision']['state'] == 'paused'))
            best = []
            for item in self.index.body['projections']['best']:
                if item['scope'] == 'strategy:' + sid:
                    evaluation = self.pin(item['evaluation_id'])
                    best.append({'context_id': item['context_id'], 'evaluation_id': evaluation['id'],
                                 'run_id': evaluation['run_id'], 'latency_ms': evaluation['latency_ms']})
            routes.append({'strategy_id': sid, 'name': strategy['name'], 'latest_round_id': latest_round,
                'latest_round_known': latest_round is not None, 'latest_results': recent,
                'latest_attempt': latest, 'best_by_context': best,
                'paused': is_paused,
                'decision': route_decision['details']['decision'] if route_decision else None,
                'latest_plan_summary': history['latest_analysis'],
                'total_history_rows': history['total'], 'comparison_context_id': context_id})
            if self.capture.format_revision >= 6:
                from ..workflow.exploration import exploration_status
                status = exploration_status(self.index.records, sid)
                control = next((d for d in reversed(decisions) if d['kind'] in ('paused', 'resumed')
                    or d['details'].get('type') in ('route_decision.v1', 'exploration_state.v1', 'exploration_admission.v1')), None)
                if control is not None:
                    self.pin(control['id'])
                routes[-1].update(state=status['state'], state_reason=control['reason'] if control else None,
                                  paused=status['state'] == 'paused', state_control_id=control['id'] if control else None)
        return {'schema_version': 1, 'snapshot_id': self.index.body['snapshot']['id'],
                'task_id': self.index.task_id, 'scope': 'all_strategy_latest_progress',
                'routes': routes, 'complete_strategy_coverage': True, 'raw_histories_shared': False,
                'foreign_results_are_context_only': True}


def capture_progress(index, item, capture):
    builder = _Progress(index, capture)
    progress = builder.global_progress(item['context_id'])
    capture.reference('progress:all', 'SharedProgress', progress, strategy_id=None,
                      artifacts=list(builder.artifacts.values()))
    history = builder.history(item['strategy_id'])
    if capture.format_revision >= 3:
        builder.candidate_catalog()
    capture.route_context = {'strategy_id': item['strategy_id'], 'snapshot_id': item['snapshot_id'],
        'fresh_input_scope': 'shared_latest_progress_and_complete_own_metrics',
        'history_coverage': {'scope': 'complete_metric_history', 'expected_rows': history['total'],
                             'read_rows': history['total'], 'missing_ids': []},
        'explicit_dependencies': [item['parent_id']], 'scoped_records': []}
    return history
