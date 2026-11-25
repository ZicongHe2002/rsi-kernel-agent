"""Small allowlisted deterministic tools. No shell, network, kernel, or API calls."""
from hashlib import sha256
import json

from .models import calculate
from .outputs import detached, fields, require, references
from ..contracts import ArtifactRef, canonical_json


TOOL_HELP = {
    'source':'{strategy_id}: pinned full source and configuration',
    'history':'{strategy_id}: complete independently bound Memory history',
    'knowledge':'{method_id}: pinned method, source revisions and freshness issues',
    'reference':'{ref}: one immutable input reference including values and availability',
    'profile':'{strategy_id,profile_id}: normalize saved legacy parser artifact for this route',
    'candidate_evidence':'{candidate_id,section,run_id?}: frozen source, benchmark, profile or failure details; historical references only',
    'calculate':'{model,inputs,evidence_refs,assumptions}: compute_memory, tile, live_memory, pipeline',
    'capture_request':'{strategy_id,kind,reason,refs,phase}: register only; phase next_input_preparation or post_design_diagnostic',
    'skill':'{name}: inspect an explicitly supplied existing skill, with its digest',
}


class AnalysisTools:
    def __init__(self, manifest, artifacts=None, skills=None):
        self.manifest = detached(manifest)
        self.artifacts = artifacts
        self.skills = detached(skills or {})

    def _read(self, ref):
        require(self.artifacts is not None, 'Artifact reader required')
        raw = self.artifacts.read(ArtifactRef(**ref))
        require(type(raw) is bytes and len(raw) == ref['size'] and sha256(raw).hexdigest() == ref['digest'],
                'Candidate artifact integrity check failed')
        return raw

    def _candidate_source(self, candidate):
        refs = candidate['source_refs']
        result = {'source': None, 'config': None, 'source_artifact': refs.get('source_ref'),
                  'config_artifact': refs.get('config_ref'), 'files': []}
        try:
            require(refs.get('source_ref') is not None and refs.get('config_ref') is not None,
                    'Frozen candidate source or configuration is unavailable')
            for key in ('source', 'config'):
                require(refs[key + '_ref']['digest'] == candidate['identity'][key + '_digest'],
                        'Candidate source/config differs from frozen identity')
                result[key] = self._read(refs[key + '_ref']).decode('utf-8-sig')
            if refs.get('source_bundle_ref') is not None:
                require(refs.get('build_ref') is not None and refs['build_ref']['digest'] == candidate['identity']['build_digest'],
                        'Candidate build identity is unavailable or inconsistent')
                build = json.loads(self._read(refs['build_ref']))
                require(type(build) is dict and build.get('source_bundle_digest') == refs['source_bundle_ref']['digest'],
                        'Candidate source bundle differs from its frozen build identity')
                bundle = json.loads(self._read(refs['source_bundle_ref']))
                fields(bundle, 'schema_version entry files')
                require(bundle['schema_version'] == 1 and type(bundle['files']) is list, 'Invalid candidate bundle')
                entry = next((row for row in bundle['files'] if row['path'] == bundle['entry']['path']), None)
                require(entry is not None and entry['artifact_ref'] == refs['source_ref'], 'Candidate bundle entry mismatch')
                paths = set()
                for row in bundle['files']:
                    fields(row, 'path artifact_ref role')
                    require(type(row['path']) is str and row['path'] not in paths, 'Duplicate candidate bundle path')
                    paths.add(row['path'])
                    raw = self._read(row['artifact_ref'])
                    try:
                        content, encoding = raw.decode('utf-8-sig'), 'utf-8'
                    except UnicodeDecodeError:
                        content, encoding = None, 'binary'
                    result['files'].append({**row, 'availability': 'available', 'content': content, 'encoding': encoding})
                result['entry'] = bundle['entry']
            return {**result, 'availability': 'available', 'reason': None}
        except (ValueError, KeyError, TypeError, OSError, UnicodeError) as error:
            return {**result, 'source': None, 'config': None, 'files': [],
                    'availability': 'unavailable', 'reason': str(error)}

    def _candidate_evidence_row(self, evidence_id, run, visiting=None, *, read_artifacts=True):
        visiting = set() if visiting is None else visiting
        e = run['evidence'][evidence_id]
        result = {'evidence_id': evidence_id, 'evidence_kind': e['evidence_kind'], 'value': e['value'],
                  'unit': e['unit'], 'artifact_refs': e['artifacts'], 'reports': []}
        if evidence_id in visiting:
            return {**result, 'availability': 'unavailable', 'reason': 'Cyclic evidence dependencies'}
        visiting.add(evidence_id)
        dependencies = [self._candidate_evidence_row(eid, run, visiting, read_artifacts=False) for eid in e['input_ids']]
        visiting.remove(evidence_id)
        usable = (e['run_id'] == run['run_id'] and e['provenance'] == run['context']['provenance']
                  and e['availability'] == 'available' and e['request_status'] == 'completed'
                  and e['claim_status'] == 'supported' and bool(e['artifacts'])
                  and e['evidence_kind'] in ('measured_timing', 'measured_counter', 'trace_derived', 'derived_measurement')
                  and all(row['availability'] == 'available' for row in dependencies))
        if not usable:
            return {**result, 'availability': 'unavailable', 'reason': 'Frozen measurement or its dependencies are unsupported'}
        if read_artifacts and e['format'] == 'XPlane protobuf':
            return {**result, 'availability': 'unavailable', 'reason': 'Raw XPlane requires explicit diagnostic parsing',
                    'deferred_artifacts': e['artifacts']}
        if read_artifacts:
            try:
                result['reports'] = [json.loads(self._read(ref)) for ref in e['artifacts']]
                for report in result['reports']:
                    if type(report) is dict and report.get('backend_id') in ('colab.v1', 'synthetic.v1'):
                        require(report.get('run_id') == run['run_id'] and report.get('candidate_id') == run['candidate_id']
                                and report.get('context_id') == run['context']['id'],
                                'Saved execution receipt belongs to another candidate or Run')
                        if e['unit'] == 'ms':
                            require(report.get('stage') == 'benchmark', 'Timing evidence does not refer to benchmark receipt')
            except (ValueError, KeyError, TypeError, OSError, UnicodeError) as error:
                return {**result, 'reports': [], 'availability': 'unavailable', 'reason': str(error)}
        return {**result, 'availability': 'available', 'reason': None}

    def candidate_evidence(self, arguments):
        fields(arguments, 'candidate_id section' + (' run_id' if 'run_id' in arguments else ''))
        require(type(arguments['candidate_id']) is str and arguments['section'] in ('source', 'benchmark', 'profile', 'failure'),
                'Invalid candidate evidence request')
        ref = 'candidate:' + arguments['candidate_id']
        entry = self.manifest['references'].get(ref)
        require(entry is not None and entry['kind'] == 'History', 'Candidate is outside the frozen input snapshot')
        candidate = entry['value']
        require(candidate['candidate_id'] == arguments['candidate_id'], 'Candidate catalog identity differs')
        run_id = arguments.get('run_id')
        require('run_id' not in arguments or type(run_id) is str and run_id in candidate['runs'],
                'run_id is not a frozen Run of this candidate')
        if arguments['section'] != 'source' and run_id is None:
            require(len(candidate['runs']) <= 1, 'Multiple frozen Runs require an explicit run_id')
            run_id = next(iter(candidate['runs']), None)
        result = {key: candidate[key] for key in ('snapshot_id', 'strategy_id', 'candidate_id', 'parent_id', 'identity')}
        result.update(ref=ref, run_id=run_id, section=arguments['section'], current=False,
                      foreign_route=candidate['strategy_id'] not in self.manifest['strategies'],
                      classification='historical_claim')
        if arguments['section'] == 'source':
            return {**result, **self._candidate_source(candidate)}
        if arguments['section'] == 'failure':
            failures = candidate['runs'][run_id].get('failures', []) if run_id is not None else []
            return {**result, 'failures': detached(failures),
                    'availability': 'available' if failures else 'unavailable',
                    'reason': None if failures else 'No frozen failure detail'}
        key = 'benchmarks' if arguments['section'] == 'benchmark' else 'profiles'
        if run_id is None:
            return {**result, key: [], 'availability': 'unavailable', 'reason': 'Candidate has no frozen execution Run'}
        run = candidate['runs'][run_id]
        result.update(context_id=run['context']['id'], workload_id=run['context']['workload_id'],
                      provenance=run['context']['provenance'], execution_status=run['execution_status'])
        rows = []
        for bundle in run[key]:
            evidence = [self._candidate_evidence_row(eid, run) for eid in bundle['evidence_ids']]
            if key == 'benchmarks':
                matching = [e for e in evidence if e['availability'] == 'available' and e['unit'] == 'ms'
                            and e['value'] == bundle['latency_ms']]
                available = (run['execution_status'] == 'succeeded' and bundle['correctness'] == 'passed'
                             and bundle['timing_validity'] == 'valid' and bundle['confirmed'] is True
                             and type(bundle['latency_ms']) in (float, int) and bundle['latency_ms'] > 0 and bool(matching))
                rows.append({'evaluation_id': bundle['id'], 'value': bundle, 'evidence': evidence,
                    'availability': 'available' if available else 'unavailable',
                    'reason': None if available else 'Run correctness, timing or supporting measurement is not verified'})
                continue
            from .profiles import normalize_duration_report
            reports, issues, deferred = [], [], []
            for e in evidence:
                deferred.extend(e.get('deferred_artifacts', []))
                if e['availability'] != 'available':
                    issues.append(e['reason'])
                    continue
                binding = {key: result[key] for key in ('strategy_id', 'parent_id', 'candidate_id', 'run_id', 'context_id', 'provenance')}
                binding['declared_identity'] = candidate['identity']
                for report, artifact in zip(e['reports'], e['artifact_refs']):
                    try:
                        reports.append(normalize_duration_report(report, binding=binding, artifact_refs=[artifact]))
                    except (ValueError, KeyError, TypeError) as error:
                        issues.append(str(error))
            qualities = [report['quality']['availability'] for report in reports]
            availability = 'unavailable'
            if any(quality != 'unavailable' for quality in qualities) and bundle['availability'] != 'unavailable':
                availability = ('available' if all(quality == 'available' for quality in qualities)
                                and bundle['availability'] == 'available' and not issues else 'partial')
            rows.append({'profile_id': bundle['id'], 'reports': reports, 'availability': availability,
                         'reason': '; '.join(issues) or (None if reports else 'No supported compact Profile report'),
                         'deferred_artifacts': deferred})
        availability = ('available' if rows and all(row['availability'] == 'available' for row in rows) else
                        'partial' if any(row['availability'] != 'unavailable' for row in rows) else 'unavailable')
        return {**result, key: rows, 'availability': availability,
                'reason': None if availability == 'available' else run['failure_reason'] or 'No complete verified saved ' + arguments['section']}

    def call(self, name, arguments):
        require(name in TOOL_HELP and type(arguments) is dict, 'Unknown tool or arguments')
        m, a = self.manifest, arguments
        if name == 'candidate_evidence':
            return self.candidate_evidence(a)
        if name in ('source','history'):
            fields(a,'strategy_id'); require(a['strategy_id'] in m['strategies'], 'Unknown strategy')
            row = m['strategies'][a['strategy_id']]
            if name == 'history': return detached(row['history'])
            return {'source':row['source'],'config':row['config'],'parent_id':row['parent_id'],
                    'identity':row['parent']['identity'],'ref':'source:'+a['strategy_id']}
        if name == 'knowledge':
            fields(a,'method_id'); require(a['method_id'] in m['knowledge'], 'Knowledge method was not pinned')
            return detached(m['knowledge'][a['method_id']])
        if name == 'reference':
            fields(a,'ref'); require(a['ref'] in m['references'], 'Unknown reference')
            return detached(m['references'][a['ref']])
        if name == 'capture_request':
            fields(a, 'strategy_id kind reason refs phase')
            require(a['strategy_id'] in m['strategies'], 'Unknown strategy')
            require(a['kind'] in ('profile', 'correctness', 'benchmark', 'capability'), 'Unknown capture kind')
            require(type(a['reason']) is str and a['reason'].strip(), 'Capture rationale required')
            require(a['phase'] in ('next_input_preparation', 'post_design_diagnostic'), 'Inline execution is not an analysis tool')
            references(a['refs'], a['strategy_id'], m['references'])
            return {**detached(a), 'status': 'registered_not_executed', 'evidence_available': False,
                    'requires_design_complete': True, 'requires_existing_authorization': True}
        if name == 'skill':
            fields(a,'name'); require(a['name'] in self.skills, 'Skill not supplied for this analysis')
            content = self.skills[a['name']]
            return {'name':a['name'],'content':content,'sha256':sha256(content.encode()).hexdigest()}
        if name == 'calculate':
            fields(a,'model inputs evidence_refs assumptions')
            owners = {m['references'][r].get('strategy_id') for r in a['evidence_refs'] if r in m['references']}
            owners.discard(None)
            require(len(owners) <= 1, 'Calculation mixes strategy evidence')
            references(a['evidence_refs'],next(iter(owners),None),m['references'])
            result = calculate(a['model'],a['inputs'],evidence_refs=a['evidence_refs'],assumptions=a['assumptions'])
            result['strategy_id'] = next(iter(owners),None)
            return result
        fields(a,'strategy_id profile_id')
        require(a['strategy_id'] in m['strategies'], 'Unknown strategy')
        row = m['strategies'][a['strategy_id']]
        selected = next((p for p in row['profiles'] if p['id'] == a['profile_id']),None)
        require(selected is not None, 'Profile does not belong to selected strategy')
        require(self.artifacts is not None, 'Artifact reader required')
        run = row['run']
        require(run is not None, 'Measured Profile requires a bound Run')
        from .profiles import normalize_duration_report
        normalized = []
        for eid in selected['evidence_ids']:
            evidence = m['references']['evidence:'+eid]
            if evidence['value']['evidence_kind'] not in ('trace_derived','measured_timing','measured_counter','derived_measurement'):
                normalized.append({'evidence_id':eid,'availability':'unavailable',
                    'reason':'Legacy measured duration parser cannot reinterpret predicted/compiler/source claims',
                    'original_evidence_kind':evidence['value']['evidence_kind']})
                continue
            if evidence.get('availability') != 'available':
                normalized.append({'evidence_id':eid,'availability':'unavailable','reason':'Input manifest could not verify evidence'})
                continue
            for raw in evidence.get('artifacts',[]):
                report = json.loads(self.artifacts.read(ArtifactRef(**raw)))
                binding = dict(strategy_id=a['strategy_id'],parent_id=row['parent_id'],candidate_id=run['candidate_id'],
                               run_id=run['id'],context_id=row['context']['id'],provenance=row['context']['provenance'],
                               declared_identity=row['parent']['identity'])
                normalized.append(normalize_duration_report(report,binding=binding,artifact_refs=[raw]))
        states = [n.get('quality',{}).get('availability',n.get('availability','unavailable')) for n in normalized]
        bundle_state = m['references']['profile:'+a['profile_id']]['availability']
        availability = 'unavailable'
        if states and any(x != 'unavailable' for x in states):
            availability = 'available' if all(x=='available' for x in states) and bundle_state=='available' else 'partial'
        return {'profile_id':a['profile_id'],'reports':normalized,'availability':availability}
