from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from ..contracts import ArtifactRef, Decision, canonical_json
from ..profiler.outputs import require
from ..store import StoreError
from .execution import validate_variant
from .mla_runner import validate_candidate_config, validate_workload
from .real_protocol import load_bundle, read_json, validate_bundle_identity


_CPU_TIMEOUT = 120
_FIELDS = {'label', 'source_ref', 'config_ref', 'build_ref', 'source_bundle_ref', 'priority', 'cost'}


def _digest(value):
    return sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _put(store, value):
    return asdict(store.artifacts.put(value if isinstance(value, bytes) else canonical_json(value).encode('utf-8')))


def _inputs(store, manifest, result, drafts):
    from ..profiler.outputs import validate_result
    data, reviewed = validate_result(store, manifest, result)
    return _inputs_from_validated_result(manifest, result, drafts, data, reviewed)


def _inputs_from_validated_result(manifest, result, drafts, data, reviewed):
    """Bind checks to a result already verified in this synchronous call."""
    require(len(data['strategies']) == 1, 'Offline checks require one independent route')
    sid, binding = next(iter(data['strategies'].items()))
    subject = {'manifest_ref': asdict(manifest.artifact_ref), 'result_digest': _digest(result),
               'drafts_digest': _digest(drafts), 'strategy_id': sid, 'task_id': data['request']['task_id'],
               'parent_id': binding['parent_id'], 'parent_identity': binding['parent']['identity'],
               'context_id': binding['context']['id'], 'protocol_digest': _digest(binding['context']['protocol']),
               'workload_digest': _digest(data['workload'])}
    return data, reviewed, subject


def _static(store, data, reviewed, drafts):
    plans = reviewed['plans']
    require(type(drafts) is dict and set(drafts) == {p['id'] for p in plans}, 'Drafts must cover every Plan exactly')
    if not plans:
        return []
    sid, binding = next(iter(data['strategies'].items()))
    require(binding['context']['provenance'] == 'real', 'Candidate preparation requires a real historical contract')
    workload = validate_workload(data['workload']['spec'])
    protocol = binding['context']['protocol']
    require(protocol['correctness'] == {'atol': .01, 'rtol': .01}, 'Frozen MLA tolerance must remain unchanged')
    oracle = load_bundle(store, protocol['oracle_bundle_ref'])
    entry = next(f for f in oracle['files'] if f['path'] == oracle['entry']['path'])
    require(entry['artifact_ref']['digest'] == protocol['oracle_digest'], 'Frozen Oracle entry identity differs')
    refs = {canonical_json(protocol['oracle_bundle_ref']): protocol['oracle_bundle_ref']}
    for item in oracle['files']:
        refs[canonical_json(item['artifact_ref'])] = item['artifact_ref']
    for plan in plans:
        require(plan['strategy_id'] == sid and plan['parent_id'] == binding['parent_id'], 'Draft Plan parent differs')
        variants = drafts[plan['id']]
        require(type(variants) is list and len(variants) == len(plan['variants']), 'Draft variant coverage differs')
        seen = set()
        for label, variant in zip(plan['variants'], variants):
            require(type(variant) is dict and set(variant) == _FIELDS, 'Offline variant fields differ; device is not allowed')
            require(variant['label'] == label, 'Draft variant label differs from Plan')
            validate_variant(store, {**variant, 'device': 'offline-not-executable'}, provenance='real')
            identity = _digest({k: variant[k] for k in ('source_ref', 'config_ref', 'build_ref', 'source_bundle_ref')})
            require(identity not in seen, 'Duplicate candidate implementation within Plan')
            seen.add(identity)
            bundle = validate_bundle_identity(store, variant, protocol)
            paths = {f['path'] for f in bundle['files']}
            require(set(protocol['trusted_files']) <= paths, 'Draft bundle omits frozen trusted files')
            validate_candidate_config(read_json(store, variant['config_ref']), workload)
            for name in ('source_ref', 'config_ref', 'build_ref', 'source_bundle_ref'):
                refs[canonical_json(variant[name])] = variant[name]
            for item in bundle['files']:
                raw = store.artifacts.read(ArtifactRef(**item['artifact_ref']))
                refs[canonical_json(item['artifact_ref'])] = item['artifact_ref']
                if item['path'].endswith('.py'):
                    compile(raw.decode('utf-8-sig'), item['path'], 'exec', dont_inherit=True)
    return sorted(refs.values(), key=lambda ref: ref['digest'])


def _safe(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
                    for p in (path, *path.parents)), 'Offline output paths cannot contain links')
    return path.resolve()


def _row(store, kind, status, reason, subject, log, *, versions=None, artifacts=()):
    return {'kind': kind, 'status': status, 'reason': reason, 'subject_digest': subject,
            'tool_versions': versions or {'python': sys.version.split()[0]},
            'log_ref': _put(store, log), 'artifact_refs': list(artifacts)}


def _probe(python, directory):
    command = [str(python), '-I', '-B', '-c',
        'import importlib.util,json,sys; '
        'print(json.dumps({"python":sys.version.split()[0],"executable":sys.executable,'
        '"missing":[n for n in ("jax","numpy") if importlib.util.find_spec(n) is None]}))']
    env = dict(os.environ, JAX_PLATFORMS='cpu', PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH', None)
    process = subprocess.run(command, cwd=directory, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=_CPU_TIMEOUT, check=False)
    log = {'command': command, 'exit_code': process.returncode,
           'stdout': process.stdout.decode('utf-8', 'replace'), 'stderr': process.stderr.decode('utf-8', 'replace')}
    if process.returncode != 0:
        return None, log
    try:
        info = json.loads(process.stdout)
        require(type(info) is dict and set(info) == {'python', 'executable', 'missing'}
                and type(info['missing']) is list, 'Invalid CPU dependency probe')
        return info, log
    except (ValueError, TypeError):
        return None, log


def _oracle(store, data, python, output_dir, subject):
    if python is None:
        return _row(store, 'oracle_reference', 'unavailable', 'No compatible CPU interpreter was selected', subject,
                    {'executed': False, 'reason': 'cpu_python was not provided'})
    root = _safe(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix='oracle-', dir=_safe(root))
    directory = Path(temporary.name)
    logs, artifacts, cases = [], [], []
    try:
        info, probe = _probe(python, directory)
        logs.append(probe)
        if info is None or info['missing']:
            reason = 'CPU dependency probe failed' if info is None else 'CPU dependencies missing: ' + ', '.join(info['missing'])
            return _row(store, 'oracle_reference', 'unavailable', reason, subject, logs,
                        versions={'python': info['python']} if info else None)
        protocol = next(iter(data['strategies'].values()))['context']['protocol']
        runner_path = store.artifacts.path_for(ArtifactRef(protocol['runner_digest'], 0))
        runner_ref = ArtifactRef(protocol['runner_digest'], runner_path.stat().st_size)
        runner = store.artifacts.read(runner_ref)
        control, workspace, results = directory / 'control', directory / 'workspace', directory / 'results'
        for path in (control, workspace, results):
            path.mkdir()
        script = control / 'frozen_runner.py'
        script.write_bytes(runner)
        artifacts.append(asdict(runner_ref))
        bundle = load_bundle(store, protocol['oracle_bundle_ref'])
        for item in bundle['files']:
            target = _safe(workspace / item['path'])
            require(target.is_relative_to(workspace), 'Oracle path escapes workspace')
            target.parent.mkdir(parents=True, exist_ok=True)
            _safe(target).write_bytes(store.artifacts.read(ArtifactRef(**item['artifact_ref'])))
        env = dict(os.environ, JAX_PLATFORMS='cpu', PYTHONDONTWRITEBYTECODE='1')
        env.pop('PYTHONPATH', None)
        for index, case in enumerate(data['workload']['spec']['cases']):
            case_dir = results / str(index)
            case_dir.mkdir()
            payload = {'workspace': str(workspace), 'bundle': bundle, 'bundle_digest': protocol['oracle_bundle_ref']['digest'],
                       'case': case, 'tolerance': protocol['correctness'],
                       'inputs_path': str(case_dir / 'inputs.npz'), 'outputs_path': str(case_dir / 'oracle_outputs.npz'),
                       'metadata_path': str(case_dir / 'oracle_metadata.json')}
            payload_path = case_dir / 'request.json'
            payload_path.write_text(canonical_json(payload), encoding='utf-8')
            command = [str(python), '-I', '-B', str(script), '--oracle', str(payload_path)]
            process = subprocess.run(command, cwd=case_dir, env=env, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, timeout=_CPU_TIMEOUT, check=False)
            logs.append({'case_id': case['id'], 'command': command, 'exit_code': process.returncode,
                         'stdout': process.stdout.decode('utf-8', 'replace'),
                         'stderr': process.stderr.decode('utf-8', 'replace')})
            require(process.returncode == 0, 'Frozen CPU Oracle failed for case ' + case['id'])
            metadata = json.loads(_safe(case_dir / 'oracle_metadata.json').read_bytes())
            expected = {'source_bundle_digest': protocol['oracle_bundle_ref']['digest'], 'entry': bundle['entry'],
                        'loaded_files': [{'path': f['path'], 'digest': f['artifact_ref']['digest']}
                                         for f in sorted(bundle['files'], key=lambda f: f['path'])]}
            require(metadata['oracle_identity'] == expected, 'CPU Oracle loaded identity differs')
            saved = {'case_id': case['id']}
            for filename in ('inputs.npz', 'oracle_outputs.npz', 'oracle_metadata.json'):
                raw = _safe(case_dir / filename).read_bytes()
                require(bool(raw), 'CPU Oracle output is empty')
                ref = _put(store, raw)
                artifacts.append(ref)
                saved[filename.split('.')[0] + '_ref'] = ref
            cases.append(saved)
            require(sha256(script.read_bytes()).hexdigest() == protocol['runner_digest'], 'Frozen CPU runner changed')
            for item in bundle['files']:
                require(sha256(_safe(workspace / item['path']).read_bytes()).hexdigest() == item['artifact_ref']['digest'],
                        'Frozen Oracle workspace changed')
        return _row(store, 'oracle_reference', 'passed', 'Frozen CPU reference executed independently of candidates',
                    subject, {'commands': logs, 'cases': cases, 'oracle_identity': expected},
                    versions={'python': info['python']}, artifacts=artifacts)
    except FileNotFoundError as error:
        status = 'unavailable' if not logs else 'failed'
        logs.append({'error_type': type(error).__name__, 'reason': 'Required local executable or artifact is missing'})
        return _row(store, 'oracle_reference', status, 'Required CPU executable or frozen artifact is missing',
                    subject, logs, artifacts=artifacts)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        logs.append({'error_type': type(error).__name__, 'reason': str(error)})
        return _row(store, 'oracle_reference', 'failed', 'CPU reference check failed: ' + type(error).__name__,
                    subject, logs, artifacts=artifacts)
    finally:
        temporary.cleanup()


def _checker_ref():
    raw = Path(__file__).with_name('cpu_checks.py').read_bytes()
    return {'digest': sha256(raw).hexdigest(), 'size': len(raw)}


def _candidate_subject(variant, data, runner_ref, checker_ref):
    protocol = next(iter(data['strategies'].values()))['context']['protocol']
    return {**{name: variant[name] for name in ('source_ref', 'source_bundle_ref', 'config_ref', 'build_ref')},
            'oracle_bundle_ref': protocol['oracle_bundle_ref'], 'oracle_digest': protocol['oracle_digest'],
            'workload_digest': _digest(data['workload']), 'protocol_digest': _digest(protocol),
            'runner_ref': runner_ref, 'checker_ref': checker_ref}


def _validate_candidate_result(store, result, subject, data, cases, oracle_identity):
    require(result.get('schema_version') == 1 and result.get('type') == 'cpu_candidate_result.v1'
            and result.get('subject') == subject and result.get('status') in ('passed', 'failed', 'unavailable'),
            'CPU result has another candidate or execution contract')
    if 'error_type' in result:
        require(result['status'] != 'passed' and type(result.get('reason')) is str,
                'Failed CPU process cannot report success')
        return
    require(result['backend'] == 'cpu' and result['interpret_mode'] is True and result['hardware_executed'] is False
            and type(result['process_id']) is int and result['process_id'] > 0
            and type(result['environment']) is dict, 'Invalid CPU execution scope')
    bundle = load_bundle(store, subject['source_bundle_ref'])
    expected = {'entry': bundle['entry'], 'loaded_files': [{'path': f['path'], 'digest': f['artifact_ref']['digest']}
                for f in sorted(bundle['files'], key=lambda item: item['path'])],
                'source_bundle_ref': subject['source_bundle_ref'], 'config_ref': subject['config_ref']}
    require(result['identity'] == expected and result['oracle_identity'] == oracle_identity,
            'CPU loaded source or Oracle identity differs')
    expected_cases = data['workload']['spec']['cases']
    require(type(cases) is list and [row['case_id'] for row in cases] == [case['id'] for case in expected_cases],
            'CPU Oracle case coverage differs')
    oracle_bundle = load_bundle(store, subject['oracle_bundle_ref'])
    expected_oracle = {'source_bundle_digest': subject['oracle_bundle_ref']['digest'], 'entry': oracle_bundle['entry'],
        'loaded_files': [{'path': f['path'], 'digest': f['artifact_ref']['digest']}
                         for f in sorted(oracle_bundle['files'], key=lambda item: item['path'])]}
    require(oracle_identity == expected_oracle, 'CPU Oracle source identity differs')
    for case in cases:
        for key in ('inputs_ref', 'oracle_outputs_ref', 'oracle_metadata_ref'):
            store.artifacts.verify(ArtifactRef(**case[key]))
        metadata = json.loads(store.artifacts.read(ArtifactRef(**case['oracle_metadata_ref'])))
        require(metadata['oracle_identity'] == expected_oracle, 'CPU Oracle metadata differs')
    require(type(result['cases']) is list and len(result['cases']) == len(expected_cases), 'CPU case coverage differs')
    passed = []
    for observed, case, saved in zip(result['cases'], expected_cases, cases):
        require(observed['case_id'] == case['id'] == saved['case_id'] and type(observed['input_unchanged']) is bool
                and all(observed[name] == saved[name] for name in ('inputs_ref', 'oracle_outputs_ref', 'oracle_metadata_ref')),
                'CPU case inputs differ from frozen Oracle outputs')
        require(type(observed['outputs']) is list and len(observed['outputs']) == len(case['outputs']),
                'CPU output coverage differs')
        for value, output in zip(observed['outputs'], case['outputs']):
            require(value['name'] == output['name'] and type(value['passed']) is bool, 'CPU output identity differs')
            if value['passed']:
                require(value['shape'] == output['shape'] and value['dtype'] == output['dtype']
                        and value['tolerance_violations'] == 0
                        and (output['comparison'] == 'exact' or value['finite'] is True), 'CPU passing output is invalid')
        valid = all(value['passed'] for value in observed['outputs']) and observed['input_unchanged']
        require(observed['passed'] is valid, 'CPU case status contradicts its checks')
        passed.append(valid)
    require(result['status'] == ('passed' if all(passed) else 'failed'), 'CPU result contradicts case outcomes')


def _candidates(store, data, drafts, oracle, python, output_dir, subject):
    if oracle['status'] != 'passed' or python is None:
        return _row(store, 'candidate_numerics', 'unavailable', 'Verified CPU Oracle inputs are required', subject,
                    {'executed': False})
    variants = [(plan, i, row) for plan, rows in sorted(drafts.items()) for i, row in enumerate(rows)]
    entry = {'path': 'pallas_kernels/kernels/mla_decode.py', 'symbol': 'build_mla_decode_contiguous'}
    if any(load_bundle(store, row['source_bundle_ref'])['entry'] != entry for _, _, row in variants):
        return _row(store, 'candidate_numerics', 'unavailable', 'CPU interpreter supports only the frozen MLA entry',
                    subject, {'executed': False})
    protocol = next(iter(data['strategies'].values()))['context']['protocol']
    runner_path = store.artifacts.path_for(ArtifactRef(protocol['runner_digest'], 0))
    runner_ref = asdict(ArtifactRef(protocol['runner_digest'], runner_path.stat().st_size))
    runner_raw = store.artifacts.read(ArtifactRef(**runner_ref))
    checker_raw = Path(__file__).with_name('cpu_checks.py').read_bytes()
    checker_ref = _put(store, checker_raw)
    saved_oracle = json.loads(store.artifacts.read(ArtifactRef(**oracle['log_ref'])))
    results, refs = [], [checker_ref, runner_ref, oracle['log_ref'], *oracle['artifact_refs']]
    root = _safe(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='candidate-', dir=root) as temporary:
        base = Path(temporary)
        control = base / 'control'
        control.mkdir()
        checker, runner = control / 'cpu_checks.py', control / 'frozen_runner.py'
        checker.write_bytes(checker_raw)
        runner.write_bytes(runner_raw)
        inputs_root = base / 'oracle'
        inputs_root.mkdir()
        cases = []
        for index, saved in enumerate(saved_oracle['cases']):
            case_dir = inputs_root / str(index)
            case_dir.mkdir()
            row = dict(saved)
            for name in ('inputs', 'oracle_outputs', 'oracle_metadata'):
                path = case_dir / (name + ('.json' if name == 'oracle_metadata' else '.npz'))
                path.write_bytes(store.artifacts.read(ArtifactRef(**saved[name + '_ref'])))
                row[name + '_path'] = str(path)
            cases.append(row)
        env = dict(os.environ, JAX_PLATFORMS='cpu', PYTHONDONTWRITEBYTECODE='1')
        env.pop('PYTHONPATH', None)
        for ordinal, (plan, index, variant) in enumerate(variants):
            candidate_dir = base / str(ordinal)
            candidate_dir.mkdir()
            workspace = candidate_dir / 'workspace'
            workspace.mkdir()
            bundle = load_bundle(store, variant['source_bundle_ref'])
            for item in bundle['files']:
                path = _safe(workspace / item['path'])
                require(path.is_relative_to(workspace), 'CPU candidate path escaped workspace')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(store.artifacts.read(ArtifactRef(**item['artifact_ref'])))
            config = candidate_dir / 'config.json'
            config.write_bytes(store.artifacts.read(ArtifactRef(**variant['config_ref'])))
            bound = _candidate_subject(variant, data, runner_ref, checker_ref)
            target = candidate_dir / 'result.json'
            payload = {'subject': bound, 'workspace': str(workspace), 'source_bundle': bundle,
                'runner_path': str(runner), 'config_path': str(config), 'workload': data['workload']['spec'],
                'tolerance': protocol['correctness'], 'oracle_identity': saved_oracle['oracle_identity'],
                'cases': cases, 'result_path': str(target)}
            request = control / ('request-' + str(ordinal) + '.json')
            request.write_text(canonical_json(payload), encoding='utf-8')
            command = [str(python), '-I', '-B', str(checker), str(request)]
            log = {'plan_id': plan, 'variant_index': index, 'command': command}
            try:
                process = subprocess.run(command, cwd=control, env=env, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, timeout=_CPU_TIMEOUT, check=False)
                log.update(exit_code=process.returncode, stdout=process.stdout.decode('utf-8', 'replace'),
                           stderr=process.stderr.decode('utf-8', 'replace'))
                require(_safe(target).is_file(), 'CPU candidate process produced no result')
                observed = json.loads(target.read_bytes())
                _validate_candidate_result(store, observed, bound, data, saved_oracle['cases'], saved_oracle['oracle_identity'])
                require(process.returncode == {'passed': 0, 'failed': 1, 'unavailable': 2}[observed['status']],
                        'CPU process exit differs from result')
                require(checker.read_bytes() == checker_raw and runner.read_bytes() == runner_raw,
                        'CPU trusted control bytes changed')
                for item in bundle['files']:
                    require(_safe(workspace / item['path']).read_bytes() == store.artifacts.read(ArtifactRef(**item['artifact_ref'])),
                            'CPU candidate workspace changed')
            except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
                observed = {'schema_version': 1, 'type': 'cpu_candidate_result.v1', 'subject': bound,
                            'status': 'failed', 'error_type': type(error).__name__, 'reason': str(error)}
                log['error_type'] = type(error).__name__
            result_ref = _put(store, observed)
            refs.append(result_ref)
            results.append({**log, 'result': observed, 'result_ref': result_ref})
    statuses = [row['result']['status'] for row in results]
    status = 'failed' if 'failed' in statuses else 'unavailable' if 'unavailable' in statuses else 'passed'
    return _row(store, 'candidate_numerics', status, 'Frozen CPU candidate interpreter checks: ' + status,
                subject, {'candidates': results}, versions={'checker_digest': checker_ref['digest'],
                    'runner_digest': runner_ref['digest'], 'executable': str(Path(python).resolve())},
                artifacts=list({ref['digest']: ref for ref in refs}.values()))


def _prior_oracle(store, subject):
    for record in reversed(store.history(subject['task_id'])):
        if type(record) is Decision and record.details.get('type') == 'offline_checks.v1':
            report = json.loads(store.artifacts.read(ArtifactRef(**record.details['report_ref'])))
            old = report['subject']
            if all(old[key] == subject[key] for key in ('task_id', 'protocol_digest', 'workload_digest')):
                row = next(r for r in report['checks'] if r['kind'] == 'oracle_reference')
                if row['status'] != 'unavailable':
                    return row
    return None


def _prior_candidate_failure(store, subject, drafts):
    def identity(row):
        return canonical_json({key: row[key] for key in ('source_ref', 'source_bundle_ref', 'config_ref', 'build_ref')})
    expected = {identity(row) for entries in drafts.values() for row in entries}
    for record in reversed(store.history(subject['task_id'])):
        if type(record) is not Decision or record.details.get('type') != 'offline_checks.v1':
            continue
        report = json.loads(store.artifacts.read(ArtifactRef(**record.details['report_ref'])))
        previous = report['subject']
        if report['schema_version'] != 2 or any(previous[key] != subject[key] for key in
                ('task_id', 'protocol_digest', 'workload_digest')):
            continue
        candidate = next(row for row in report['checks'] if row['kind'] == 'candidate_numerics')
        if candidate['status'] == 'unavailable':
            continue
        log = json.loads(store.artifacts.read(ArtifactRef(**candidate['log_ref'])))
        if 'candidates' not in log:
            continue
        passed = set()
        for entry in log['candidates']:
            result = entry['result']
            key = identity(result['subject'])
            if key not in expected:
                continue
            if result['status'] == 'failed':
                return True
            if result['status'] == 'passed':
                passed.add(key)
        expected -= passed
        if not expected:
            return False
    return False


def check_drafts(store, manifest, result, drafts, *, cpu_python=None, output_dir):
    data, reviewed, subject = _inputs(store, manifest, result, drafts)
    digest = _digest(subject)
    prior = _prior_oracle(store, subject)
    for record in reversed(store.history(subject['task_id'])):
        if type(record) is not Decision or record.details.get('type') != 'offline_checks.v1' or record.details.get('subject_digest') != digest:
            continue
        saved = json.loads(store.artifacts.read(ArtifactRef(**record.details['report_ref'])))
        oracle = next(row for row in saved['checks'] if row['kind'] == 'oracle_reference')
        candidate = next(row for row in saved['checks'] if row['kind'] == 'candidate_numerics')
        if candidate['status'] == 'failed':
            break
        if (saved['schema_version'] == 2 and cpu_python is not None
                and candidate['tool_versions'].get('executable') != str(Path(cpu_python).resolve())):
            break
        if (saved['schema_version'] == 2 and saved['ready'] and oracle['status'] == candidate['status'] == 'passed'
                and candidate['tool_versions'].get('checker_digest') == _checker_ref()['digest']
                and (cpu_python is None or candidate['tool_versions'].get('executable') == str(Path(cpu_python).resolve()))
                and (prior is None or prior['status'] != 'failed')
                and not _prior_candidate_failure(store, subject, drafts)):
            report = {**saved, 'report_ref': record.details['report_ref'], 'decision_id': record.id}
            validate_checks(store, manifest, result, drafts, report)
            require_current_checks(store, report)
            return report
    checks = []
    try:
        artifacts = _static(store, data, reviewed, drafts)
        checks.append(_row(store, 'static', 'passed', 'All proposed variants match frozen source/config/protocol and parse',
                           digest, {'plans': len(reviewed['plans']), 'candidate_execution': False}, artifacts=artifacts))
    except (ValueError, KeyError, TypeError, OSError, SyntaxError) as error:
        checks.append(_row(store, 'static', 'failed', 'Offline candidate validation failed: ' + str(error),
                           digest, {'error_type': type(error).__name__, 'reason': str(error)}))
    if checks[0]['status'] == 'passed' and reviewed['plans']:
        oracle = _oracle(store, data, cpu_python, output_dir, digest)
        prior = _prior_oracle(store, subject)
        if oracle['status'] == 'unavailable' and prior is not None and prior['status'] == 'failed':
            oracle = _row(store, 'oracle_reference', 'failed', 'A previous CPU reference failure is unresolved', digest,
                          {'previous_failure_log': prior['log_ref'], 'current_unavailable_log': oracle['log_ref']},
                          artifacts=[prior['log_ref'], oracle['log_ref'], *prior['artifact_refs']])
        checks.append(oracle)
    else:
        checks.append(_row(store, 'oracle_reference', 'unavailable', 'No statically valid candidate set requires reference execution',
                           digest, {'executed': False}))
    checks.append(_candidates(store, data, drafts, checks[1], cpu_python, output_dir, digest)
                  if reviewed['plans'] and checks[0]['status'] == 'passed' else
                  _row(store, 'candidate_numerics', 'unavailable', 'No statically valid candidates to execute',
                       digest, {'executed': False}))
    candidate_status = checks[2]['status']
    ready = checks[0]['status'] == 'passed' and (not reviewed['plans'] or all(row['status'] == 'passed' for row in checks))
    report = {'schema_version': 2, 'type': 'offline_checks.v1', 'subject': subject, 'subject_digest': digest,
              'drafts': drafts, 'checks': checks, 'ready': ready,
              'candidate_correctness': candidate_status if candidate_status in ('passed', 'failed') else 'unknown',
              'hardware_executed': False}
    report_ref = _put(store, report)
    decision_id = 'offline-checks:' + report_ref['digest']
    marker = Decision(decision_id, subject['task_id'], 'note', subject['strategy_id'],
        'Local candidate checks; no hardware correctness claim',
        {'type': 'offline_checks.v1', 'subject_digest': digest, 'report_ref': report_ref})
    store.append(marker, marker.id)
    return json.loads(canonical_json({**report, 'report_ref': report_ref, 'decision_id': marker.id}))


def require_current_checks(store, checks):
    prior = _prior_oracle(store, checks['subject'])
    require(checks['ready'] and (prior is None or prior['status'] != 'failed'),
            'CPU reference has an unresolved failure or static checks failed')
    if checks['drafts']:
        candidate = next(row for row in checks['checks'] if row['kind'] == 'candidate_numerics')
        require(not _prior_candidate_failure(store, checks['subject'], checks['drafts']),
                'A newer candidate CPU failure is unresolved')
        require(checks['schema_version'] == 2 and checks['candidate_correctness'] == 'passed'
                and all(row['status'] == 'passed' for row in checks['checks'])
                and candidate['tool_versions'].get('checker_digest') == _checker_ref()['digest'],
                'Current same-source CPU candidate numerics are required before execution')


def validate_checks(store, manifest, result, drafts, checks):
    data, reviewed, subject = _inputs(store, manifest, result, drafts)
    return _validate_checks_for_inputs(store, data, reviewed, subject, drafts, checks)


def _validate_checks_for_inputs(store, data, reviewed, subject, drafts, checks):
    """Verify every report/artifact assertion using current-call validated inputs."""
    require(type(checks) is dict and set(checks) == {'schema_version', 'type', 'subject', 'subject_digest',
            'checks', 'ready', 'drafts', 'candidate_correctness', 'hardware_executed', 'report_ref', 'decision_id'},
            'Invalid offline check report')
    saved = json.loads(store.artifacts.read(ArtifactRef(**checks['report_ref'])))
    require(canonical_json(saved) == canonical_json({k: v for k, v in checks.items() if k not in ('report_ref', 'decision_id')}),
            'Offline check report differs from archived bytes')
    digest = _digest(subject)
    require(saved['schema_version'] in (1, 2) and saved['type'] == 'offline_checks.v1'
            and saved['subject'] == subject and saved['subject_digest'] == digest
            and canonical_json(saved['drafts']) == canonical_json(drafts)
            and saved['candidate_correctness'] in ('unknown', 'passed', 'failed') and saved['hardware_executed'] is False,
            'Offline check subject or scope differs')
    require(checks['decision_id'] == 'offline-checks:' + checks['report_ref']['digest'], 'Offline check registration differs')
    marker = store.get(checks['decision_id'])
    require(type(marker) is Decision and marker.task_id == subject['task_id'] and marker.target_id == subject['strategy_id']
            and marker.kind == 'note' and marker.details == {'type': 'offline_checks.v1', 'subject_digest': digest,
                                                            'report_ref': checks['report_ref']},
            'Offline checks are not registered for this route')
    rows = saved['checks']
    require(type(rows) is list and [row.get('kind') for row in rows] == ['static', 'oracle_reference', 'candidate_numerics'],
            'Offline check coverage differs')
    for row in rows:
        require(set(row) == {'kind', 'status', 'reason', 'subject_digest', 'tool_versions', 'log_ref', 'artifact_refs'}
                and row['status'] in ('passed', 'failed', 'unavailable') and row['subject_digest'] == digest
                and type(row['reason']) is str and bool(row['reason']) and type(row['tool_versions']) is dict,
                'Invalid offline check outcome')
        store.artifacts.verify(ArtifactRef(**row['log_ref']))
        for ref in row['artifact_refs']:
            store.artifacts.verify(ArtifactRef(**ref))
    require(rows[0]['status'] in ('passed', 'failed') and type(saved['ready']) is bool,
            'Offline check readiness contradicts outcomes')
    if saved['schema_version'] == 1:
        require(rows[-1]['status'] == 'unavailable' and saved['candidate_correctness'] == 'unknown'
                and saved['ready'] == all(row['status'] != 'failed' for row in rows), 'Legacy CPU check scope differs')
    else:
        expected_ready = rows[0]['status'] == 'passed' and (not reviewed['plans'] or all(row['status'] == 'passed' for row in rows))
        correctness = rows[2]['status'] if rows[2]['status'] in ('passed', 'failed') else 'unknown'
        require(saved['ready'] is expected_ready and saved['candidate_correctness'] == correctness,
                'CPU candidate readiness contradicts outcomes')
        candidate_log = json.loads(store.artifacts.read(ArtifactRef(**rows[2]['log_ref'])))
        if 'candidates' in candidate_log:
            oracle_log = json.loads(store.artifacts.read(ArtifactRef(**rows[1]['log_ref'])))
            variants = [(pid, i, v) for pid, entries in sorted(drafts.items()) for i, v in enumerate(entries)]
            require(len(candidate_log['candidates']) == len(variants), 'CPU candidate coverage differs')
            for observed, (pid, index, variant) in zip(candidate_log['candidates'], variants):
                require(observed['plan_id'] == pid and observed['variant_index'] == index, 'CPU variant binding differs')
                result = json.loads(store.artifacts.read(ArtifactRef(**observed['result_ref'])))
                require(result == observed['result'] and observed['result_ref'] in rows[2]['artifact_refs'],
                        'CPU result artifact differs from archived report')
                expected_subject = _candidate_subject(variant, data, result['subject']['runner_ref'], result['subject']['checker_ref'])
                require(expected_subject['runner_ref']['digest'] == next(iter(data['strategies'].values()))['context']['protocol']['runner_digest']
                        and result['subject']['checker_ref']['digest'] == rows[2]['tool_versions']['checker_digest']
                        and result['subject']['runner_ref']['digest'] == rows[2]['tool_versions']['runner_digest'],
                        'CPU checker or frozen runner identity differs')
                for key in ('checker_ref', 'runner_ref'):
                    require(expected_subject[key] in rows[2]['artifact_refs'], 'CPU control artifact coverage differs')
                    store.artifacts.verify(ArtifactRef(**expected_subject[key]))
                _validate_candidate_result(store, result, expected_subject, data, oracle_log['cases'], oracle_log['oracle_identity'])
            states = [row['result']['status'] for row in candidate_log['candidates']]
            expected_status = 'failed' if 'failed' in states else 'unavailable' if 'unavailable' in states else 'passed'
            require(rows[2]['status'] == expected_status, 'CPU aggregate status differs')
        else:
            require(rows[2]['status'] == 'unavailable', 'Candidate result evidence is missing')
    if rows[0]['status'] == 'passed':
        expected = _static(store, data, reviewed, drafts)
        require(expected == rows[0]['artifact_refs'], 'Static check artifact coverage differs')
    return checks
