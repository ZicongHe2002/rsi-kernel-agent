"""Fixed real execution contract checks used by the existing workflow evaluator.

This module validates producer evidence; it does not run kernels, manage state,
or promote candidates. A trusted worker supplies the actual oracle and timing.
"""
from dataclasses import asdict
import json
import math
from pathlib import PurePosixPath
import re
import statistics

from ..contracts import ArtifactRef, canonical_json, validate_record


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fields(value, names, label):
    require(type(value) is dict and set(value) == set(names.split()), 'Invalid ' + label + ' fields')


def digest(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value), 'Invalid SHA256 digest')


def number(value, label, *, positive=False):
    require(type(value) in (int, float) and math.isfinite(value)
            and (value > 0 if positive else value >= 0), 'Invalid ' + label)


def ref(value):
    fields(value, 'digest size', 'artifact reference')
    result = ArtifactRef(**value)
    validate_record(result)
    return result


def relative_path(value):
    require(type(value) is str and value and '\\' not in value and ':' not in value
            and not value.startswith('/') and all(p not in ('', '.', '..') for p in value.split('/')),
            'Invalid source bundle relative path')
    return value


def read_json(store, artifact):
    raw = store.artifacts.read(ref(artifact))
    value = json.loads(raw)
    require(raw == canonical_json(value).encode('utf-8'), 'Execution JSON must use canonical bytes')
    return value


def load_bundle(store, artifact):
    bundle = read_json(store, artifact)
    fields(bundle, 'schema_version entry files', 'source bundle')
    require(type(bundle['schema_version']) is int and bundle['schema_version'] == 1, 'Unknown source bundle version')
    fields(bundle['entry'], 'path symbol', 'bundle entry')
    relative_path(bundle['entry']['path'])
    require(type(bundle['entry']['symbol']) is str and re.fullmatch(r'[A-Za-z_]\w*(\.[A-Za-z_]\w*)*', bundle['entry']['symbol']),
            'Invalid bundle entry symbol')
    require(type(bundle['files']) is list and bool(bundle['files']), 'Empty source bundle')
    paths = set()
    for item in bundle['files']:
        fields(item, 'path artifact_ref role', 'bundle file')
        path = relative_path(item['path'])
        require(path.casefold() not in paths, 'Duplicate source bundle path')
        paths.add(path.casefold())
        require(item['role'] in ('source', 'dependency', 'oracle'), 'Invalid source bundle role')
        store.artifacts.read(ref(item['artifact_ref']))
    require(any(f['path'] == bundle['entry']['path'] for f in bundle['files']), 'Bundle entry file is missing')
    return bundle


def validate_context(store, context, workload):
    protocol, environment = context.protocol, context.environment
    require(protocol.get('backend_id') == 'colab.v1', 'Real context must explicitly select colab.v1')
    for name in ('runner_digest', 'oracle_digest'):
        digest(protocol.get(name))
    require(type(environment.get('runtime_session_id')) is str and environment['runtime_session_id'], 'Runtime session is missing')
    require(type(environment.get('tpu_model')) is str and environment['tpu_model'], 'TPU model is missing')
    require(type(environment.get('device_count')) is int and environment['device_count'] > 0, 'Invalid device count')
    oracle = load_bundle(store, protocol.get('oracle_bundle_ref'))
    entry = next(f for f in oracle['files'] if f['path'] == oracle['entry']['path'])
    require(entry['artifact_ref']['digest'] == protocol['oracle_digest'], 'Frozen oracle identity mismatch')
    trusted = protocol.get('trusted_files')
    require(type(trusted) is dict and bool(trusted), 'Trusted file identities are required')
    for path, identity in trusted.items():
        relative_path(path)
        digest(identity)
    correctness = protocol.get('correctness')
    fields(correctness, 'rtol atol', 'correctness policy')
    for key in correctness:
        number(correctness[key], key)
    benchmark = protocol.get('benchmark')
    required = {'version', 'timer', 'warmup', 'measurements', 'statistic', 'unit', 'scope', 'outlier_rule'}
    require(type(benchmark) is dict and required <= set(benchmark), 'Incomplete benchmark policy')
    for key in ('version', 'timer', 'scope'):
        require(type(benchmark[key]) is str and benchmark[key].strip(), 'Invalid benchmark ' + key)
    require(type(benchmark['warmup']) is int and benchmark['warmup'] >= 1, 'Benchmark needs warmup')
    require(type(benchmark['measurements']) is int and benchmark['measurements'] >= 2, 'Benchmark needs raw repeated measurements')
    require(benchmark['statistic'] == 'median' and benchmark['unit'] == 'ms' and benchmark['outlier_rule'] == 'none',
            'Unsupported benchmark aggregation')
    profile = protocol.get('profile')
    require(type(profile) is dict and profile.get('required') is True, 'Real profile is mandatory')
    digest(profile.get('parser_digest'))
    require(profile['parser_digest'] in trusted.values(), 'Profile parser is not pinned in trusted files')
    require(type(profile.get('trace_mode')) is str and profile['trace_mode'], 'Trace mode is missing')
    cases = workload.spec.get('cases')
    require(type(cases) is list and bool(cases), 'Workload needs correctness cases')
    ids = set()
    for case in cases:
        require(type(case) is dict and type(case.get('id')) is str and case['id'] and case['id'] not in ids,
                'Invalid or duplicate correctness case')
        ids.add(case['id'])
        require(type(case.get('outputs')) is list and bool(case['outputs']), 'Case outputs are missing')
        names = set()
        for out in case['outputs']:
            fields(out, 'name shape dtype comparison', 'expected output')
            require(type(out['name']) is str and out['name'] and out['name'] not in names, 'Duplicate output name')
            names.add(out['name'])
            require(type(out['shape']) is list and all(type(n) is int and n >= 0 for n in out['shape']), 'Invalid output shape')
            require(type(out['dtype']) is str and out['dtype'] and out['comparison'] in ('allclose', 'exact'), 'Invalid output comparison')


def validate_bundle_identity(store, variant, protocol=None):
    bundle = load_bundle(store, variant['source_bundle_ref'])
    entry = next(f for f in bundle['files'] if f['path'] == bundle['entry']['path'])
    require(entry['artifact_ref'] == variant['source_ref'], 'Source entry differs from candidate source_ref')
    build = read_json(store, variant['build_ref'])
    fields(build, 'schema_version source_bundle_digest oracle_bundle_digest runner_digest oracle_digest', 'real build')
    require(type(build['schema_version']) is int and build['schema_version'] == 1, 'Unknown real build version')
    for key in set(build) - {'schema_version'}:
        digest(build[key])
    require(build['source_bundle_digest'] == variant['source_bundle_ref']['digest'], 'Build does not bind source bundle')
    require(type(read_json(store, variant['config_ref'])) is dict, 'Real config must be a JSON object')
    if protocol is not None:
        for name in ('runner_digest', 'oracle_digest'):
            require(build[name] == protocol[name], 'Build changes fixed ' + name)
        require(build['oracle_bundle_digest'] == protocol['oracle_bundle_ref']['digest'], 'Build changes frozen oracle bundle')
        for item in bundle['files']:
            expected = protocol['trusted_files'].get(item['path'])
            require(expected is None or item['artifact_ref']['digest'] == expected, 'Candidate changes a trusted file')
    return bundle


def validate_identity(store, request, output):
    job = request['job']
    require(canonical_json(output['environment']) == canonical_json(job['contract']['context']['environment']),
            'Runtime environment drift or mismatch')
    identity = output['identity']
    fields(identity, 'runtime_session_id workspace module_path entry loaded_files source_bundle_digest config_digest fresh_process specialization_digest',
           'loaded identity')
    variant = job['variant']
    bundle = validate_bundle_identity(store, variant, job['contract']['context']['protocol'])
    require(identity['runtime_session_id'] == output['environment']['runtime_session_id'], 'Loaded runtime session mismatch')
    require(type(identity['workspace']) is str and identity['workspace'].startswith('/')
            and '..' not in identity['workspace'].split('/'), 'Invalid remote workspace')
    require(identity['entry'] == bundle['entry'], 'Loaded entry mismatch')
    require(identity['module_path'] == str(PurePosixPath(identity['workspace']) / bundle['entry']['path']),
            'Imported module path does not match candidate workspace')
    require(identity['source_bundle_digest'] == variant['source_bundle_ref']['digest']
            and identity['config_digest'] == variant['config_ref']['digest']
            and identity['specialization_digest'] == variant['config_ref']['digest']
            and identity['fresh_process'] is True, 'Loaded specialization or source bundle mismatch')
    require(type(identity['loaded_files']) is list, 'Missing loaded dependency identities')
    for item in identity['loaded_files']:
        fields(item, 'path digest', 'loaded file')
    expected = sorted((f['path'], f['artifact_ref']['digest']) for f in bundle['files'])
    require(sorted((f['path'], f['digest']) for f in identity['loaded_files']) == expected,
            'Actually loaded source dependencies differ from bundle')


def validate_lease(request, lease):
    fields(lease, 'runtime_session_id operation_id owner_run_id exclusive_worker interference_observed started_ns finished_ns', 'device lease')
    job = request['job']
    require(lease['runtime_session_id'] == job['contract']['context']['environment']['runtime_session_id']
            and lease['operation_id'] == request['operation_id'] and lease['owner_run_id'] == job['run_id'], 'Device lease binding mismatch')
    require(lease['exclusive_worker'] is True and lease['interference_observed'] is False, 'Device exclusivity or interference is unresolved')
    require(type(lease['started_ns']) is int and type(lease['finished_ns']) is int
            and 0 <= lease['started_ns'] < lease['finished_ns'], 'Invalid device lease interval')


def timing_statistics(samples):
    ordered = sorted(samples)
    def quantile(q):
        index = (len(ordered) - 1) * q
        lower = math.floor(index)
        return ordered[lower] + (ordered[math.ceil(index)] - ordered[lower]) * (index - lower)
    return {'count': len(samples), 'median_ms': statistics.median(samples), 'mean_ms': statistics.mean(samples),
            'standard_deviation_ms': statistics.pstdev(samples), 'min_ms': min(samples), 'max_ms': max(samples),
            'p25_ms': quantile(.25), 'p75_ms': quantile(.75), 'outlier_rule': 'none'}


def validate_stage(store, request, receipt):
    """Check fixed stage outputs after the shared raw receipt verification."""
    stage, status, output = request['stage'], receipt['status'], receipt['outputs']
    job, variant = request['job'], request['job']['variant']
    protocol = job['contract']['context']['protocol']
    if status != 'succeeded' and not (stage == 'correctness' and 'cases' in output):
        fields(output, 'error_type message', 'failed stage output')
        require(status in ('failed', 'incomplete', 'invalid'), 'Unsupported real stage status')
        require(all(type(v) is str and v.strip() for v in output.values()), 'Failed stage needs an error description')
        return
    if stage == 'generate':
        fields(output, 'workspace source_digest config_digest build_digest source_bundle_digest', 'materialization')
        require(type(output['workspace']) is str and output['workspace'].startswith('/'), 'Invalid remote workspace')
        for key, value in request['candidate_identity'].items():
            require(output[key] == value, 'Materialized identity mismatch')
        require(output['source_bundle_digest'] == variant['source_bundle_ref']['digest'], 'Materialized source bundle mismatch')
        return
    validate_identity(store, request, output)
    if stage == 'compile':
        fields(output, 'environment identity compile_time_ms executed', 'compile output')
        number(output['compile_time_ms'], 'compile time')
        require(output['executed'] is True, 'Real compilation was not executed')
    elif stage == 'correctness':
        fields(output, 'environment identity oracle_digest oracle_identity input_digest raw_artifact_refs cases correctness', 'correctness output')
        require(output['oracle_digest'] == protocol['oracle_digest'], 'Correctness oracle changed')
        digest(output['input_digest'])
        require(type(output['raw_artifact_refs']) is list and bool(output['raw_artifact_refs']), 'Correctness raw artifacts are missing')
        for artifact in output['raw_artifact_refs']:
            store.artifacts.verify(ref(artifact))
        require(output['input_digest'] in {r['digest'] for r in output['raw_artifact_refs']}, 'Input manifest is not archived')
        oracle = load_bundle(store, protocol['oracle_bundle_ref'])
        identity = output['oracle_identity']
        fields(identity, 'source_bundle_digest entry loaded_files', 'oracle loaded identity')
        require(identity['source_bundle_digest'] == protocol['oracle_bundle_ref']['digest']
                and identity['entry'] == oracle['entry'], 'Loaded oracle bundle differs from fixed oracle')
        require(type(identity['loaded_files']) is list, 'Oracle loaded files are missing')
        for item in identity['loaded_files']:
            fields(item, 'path digest', 'oracle loaded file')
        require(sorted((f['path'], f['digest']) for f in identity['loaded_files']) ==
                sorted((f['path'], f['artifact_ref']['digest']) for f in oracle['files']), 'Loaded oracle dependencies differ')
        expected = job['contract']['workload']['spec']['cases']
        require(type(output['cases']) is list and [c['id'] for c in output['cases']] == [c['id'] for c in expected],
                'Correctness cases differ from fixed workload')
        verdicts = []
        for case, fixed in zip(output['cases'], expected):
            fields(case, 'id outputs passed', 'correctness case')
            require(type(case['outputs']) is list and len(case['outputs']) == len(fixed['outputs']), 'Missing correctness output')
            checks = []
            for actual, wanted in zip(case['outputs'], fixed['outputs']):
                fields(actual, 'name shape dtype finite max_abs max_rel rms tolerance_violations passed metric_reason', 'correctness tensor')
                require(actual['name'] == wanted['name'], 'Correctness output name mismatch')
                require(type(actual['finite']) is bool and type(actual['tolerance_violations']) is int
                        and actual['tolerance_violations'] >= 0, 'Invalid correctness verdict metrics')
                for key in ('max_abs', 'max_rel', 'rms'):
                    if actual[key] is None:
                        require(type(actual['metric_reason']) is str and actual['metric_reason'], 'Unavailable numerical metric needs a reason')
                    else:
                        number(actual[key], key)
                passed = (actual['shape'] == wanted['shape'] and actual['dtype'] == wanted['dtype']
                          and actual['tolerance_violations'] == 0
                          and (wanted['comparison'] == 'exact' or actual['finite'] and
                               all(actual[k] is not None for k in ('max_abs', 'max_rel', 'rms'))))
                require(type(actual['passed']) is bool and actual['passed'] == passed, 'Correctness output contradicts metrics')
                checks.append(passed)
            require(type(case['passed']) is bool and case['passed'] == all(checks), 'Case verdict contradicts outputs')
            verdicts.append(all(checks))
        passed = all(verdicts)
        require(output['correctness'] == ('passed' if passed else 'failed')
                and status == ('succeeded' if passed else 'failed'), 'Correctness verdict contradicts cases')
    elif stage == 'benchmark':
        fields(output, 'environment identity protocol samples_ms latency_ms synchronized compile_time_ms warmup_count lease', 'benchmark output')
        require(canonical_json(output['protocol']) == canonical_json(protocol['benchmark']), 'Benchmark protocol changed')
        require(type(output['samples_ms']) is list and len(output['samples_ms']) == protocol['benchmark']['measurements'], 'Raw measurement count mismatch')
        for value in output['samples_ms']:
            number(value, 'raw timing sample', positive=True)
        number(output['compile_time_ms'], 'compile time')
        require(type(output['warmup_count']) is int and output['warmup_count'] == protocol['benchmark']['warmup']
                and output['synchronized'] is True, 'Benchmark lacks fixed warmup or synchronized outputs')
        require(type(output['latency_ms']) in (int, float) and output['latency_ms'] == statistics.median(output['samples_ms']),
                'Benchmark latency differs from raw sample median')
        validate_lease(request, output['lease'])
    elif stage == 'profile':
        fields(output, 'environment identity parser_digest trace_mode raw_xplane_refs report_ref capture_operation_id benchmark_operation_id synchronized lease', 'profile output')
        from .execution import stable_id
        require(output['parser_digest'] == protocol['profile']['parser_digest']
                and output['trace_mode'] == protocol['profile']['trace_mode'], 'Profile parser or trace mode mismatch')
        require(output['capture_operation_id'] == request['operation_id']
                and output['benchmark_operation_id'] == stable_id('operation', job['run_id'], 'benchmark')
                and output['synchronized'] is True, 'Profile operation binding mismatch')
        require(type(output['raw_xplane_refs']) is list and bool(output['raw_xplane_refs']), 'Original XPlane is missing')
        for artifact in output['raw_xplane_refs']:
            store.artifacts.verify(ref(artifact))
        store.artifacts.verify(ref(output['report_ref']))
        validate_lease(request, output['lease'])
    else:
        raise ValueError('Unsupported real execution stage')
