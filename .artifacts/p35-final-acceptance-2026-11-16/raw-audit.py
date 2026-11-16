"""Offline audit of retained hardware evidence. All original data are read-only.

Reuse the original verifier for contract validation; independently decode retained
XPlane protobuf and recompute cache finite counts, inputs, and descriptive stats.
No bridge, Controller, Store mutation, dependency install, or hardware execution.
"""
from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import statistics
import struct
import sys
import traceback
import zipfile

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / '.artifacts/phase3-2026-11-12'
OUT = Path(__file__).resolve().parent
sys.path[:0] = [str(BASE), str(ROOT / 'src')]
from verify_real_round import ReadOnlyStore, verify_job, npy_header, npz_metadata
from rsi_kernel_agent.contracts import ArtifactRef
from rsi_kernel_agent.workflow.execution import stable_id
from p35_confirmation_statistics import analyze_abba


def require(value, reason):
    if not value:
        raise ValueError(reason)


def read(path):
    return json.loads(Path(path).read_text('utf-8-sig'))


class PinnedStore(ReadOnlyStore):
    def __init__(self):
        super().__init__(BASE / 'experiments.sqlite3', BASE / 'artifacts')
        self.connection = sqlite3.connect(self.db_path.as_uri() + '?mode=ro', uri=True)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA query_only=ON')
        self.connection.execute('BEGIN')

    @contextmanager
    def _connection(self):
        yield self.connection


def independent_stats(values):
    ordered = sorted(values)
    n = len(values)
    require(n > 0 and all(math.isfinite(v) and v > 0 for v in values), 'invalid raw samples')
    mean = math.fsum(values) / n
    def quantile(p):
        i = (n - 1) * p
        lo, hi = math.floor(i), math.ceil(i)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (i - lo)
    return {'count': n, 'median': quantile(.5), 'mean': mean,
            'population_std': math.sqrt(math.fsum((v - mean) ** 2 for v in values) / n),
            'min': ordered[0], 'max': ordered[-1], 'p25': quantile(.25), 'p75': quantile(.75)}


FINITE_CACHE = {}
HI_EXP = bytes(int((i & 0x7f) == 0x7f) for i in range(256))
LO_EXP = bytes(int(bool(i & 0x80)) for i in range(256))
LO_INF = bytes(int(i == 0x80) for i in range(256))


def count_bf16(raw):
    # A byte-aligned bit mask lets Python count large opaque BF16 arrays without
    # converting their NaNs or requiring NumPy. Cross-byte carries do not occur.
    lo, hi = raw[0::2], raw[1::2]
    h = int.from_bytes(hi.translate(HI_EXP), 'little')
    nonfinite = (h & int.from_bytes(lo.translate(LO_EXP), 'little')).bit_count()
    infinity = (h & int.from_bytes(lo.translate(LO_INF), 'little')).bit_count()
    return len(lo), nonfinite, infinity


def finite_scan(path, member, metadata):
    key = (metadata['sha256'], tuple(metadata['shape']), metadata['dtype'])
    if key in FINITE_CACHE:
        return FINITE_CACHE[key]
    count = bad = inf = 0
    digest = sha256()
    with zipfile.ZipFile(path) as archive, archive.open(member) as stream:
        header = npy_header(stream)
        require(header['descr'] == '<u2', 'finite scan expects raw BF16 storage')
        while raw := stream.read(1024 * 1024):
            require(len(raw) % 2 == 0, 'odd BF16 byte length')
            digest.update(raw)
            n, b, i = count_bf16(raw)
            count += n; bad += b; inf += i
    require(count == math.prod(metadata['shape']), 'finite scan shape mismatch')
    require(digest.hexdigest() == metadata['sha256'], 'finite scan array SHA mismatch')
    row = {'array_sha256': digest.hexdigest(), 'elements': count,
           'finite': bad == 0, 'finite_count': count - bad,
           'nan_count': bad - inf, 'infinity_count': inf,
           'method': 'independent BF16 exponent/mantissa bit scan of retained NPZ'}
    FINITE_CACHE[key] = row
    return row


def correctness_extra(store, receipt):
    refs = receipt['outputs']['raw_artifact_refs']
    rows = []
    for i, case in enumerate(receipt['outputs']['cases']):
        group = refs[i*6:i*6+6]
        paths = {name: store.artifacts.path_for(ArtifactRef(**group[index]))
                 for name, index in [('input', 0), ('oracle', 1), ('candidate', 5)]}
        result = {'case_id': case['id'], 'input_cache_after_device':
                  'not independently verifiable from retained artifacts', 'arrays': {}}
        metadata = {}
        for role, path in paths.items():
            with zipfile.ZipFile(path) as archive:
                metadata[role] = npz_metadata(archive)
                if role == 'input':
                    length = metadata[role][3]
                    with archive.open(length['name']+'.npy') as stream:
                        header = npy_header(stream); raw = stream.read()
                    require(header['descr'] == '<i4', 'unexpected lengths dtype')
                    require(sha256(raw).hexdigest() == length['sha256'], 'input lengths SHA mismatch')
                    result['retained_input_lengths'] = [v[0] for v in struct.iter_unpack('<i', raw)]
                    result['retained_input_shapes'] = [m['shape'] for m in metadata[role]]
                    array = metadata[role][2]
                    result['arrays']['input_cache'] = finite_scan(path, array['name']+'.npy', array)
                else:
                    array = metadata[role][1]
                    result['arrays'][role+'_cache'] = finite_scan(path, array['name']+'.npy', array)
        finite = all(result['arrays'][role+'_cache']['finite'] for role in ['candidate','oracle'])
        require(finite == case['outputs'][1]['finite'], 'retained cache finite metadata differs from receipt')
        result['cache_finite_metadata_recomputed'] = True
        result['recorded_cache_finite'] = case['outputs'][1]['finite']
        rows.append(result)
    return rows


def load_schema():
    os.environ['PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION'] = 'python'
    cached = Path(r'C:\Users\fengluo\AppData\Local\uv\cache\archive-v0')
    protobuf = cached / '9Bk3EPgBZR0j5_4u'
    schema = cached / 'lU_f1dw7hfuB6wpV/tensorflow/tsl/profiler/protobuf/xplane_pb2.py'
    sys.path.insert(0, str(protobuf))
    import google.protobuf
    spec = importlib.util.spec_from_file_location('audit_xplane_pb2', schema)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, {'schema_path': str(schema), 'schema_sha256': sha256(schema.read_bytes()).hexdigest(),
                    'protobuf_version': google.protobuf.__version__, 'protobuf_path': str(protobuf),
                    'implementation': 'python', 'installation_or_upgrade': False}


def xplane_check(store, job, receipt, schema):
    output = receipt['outputs']
    require(len(output['raw_xplane_refs']) == 1, 'expected one process XPlane')
    ref = output['raw_xplane_refs'][0]
    raw = store.artifacts.read(ArtifactRef(**ref))
    space = schema.XSpace.FromString(raw)
    parsed = json.loads(store.artifacts.read(ArtifactRef(**output['report_ref'])))
    name = 'rsi_mla_' + sha256(receipt['operation_id'].encode()).hexdigest()[:16] + '_0'
    calls = job['contract']['context']['protocol']['profile']['profile_calls']
    require(parsed['calls'] == calls == 20, 'profile call count changed')
    def stats(plane, seq):
        out = {}
        for item in seq:
            k = plane.stat_metadata[item.metadata_id].name
            field = item.WhichOneof('value')
            value = getattr(item, field) if field else None
            if field == 'ref_value': value = plane.stat_metadata[value].name
            out[k] = None if k in out else value
        return out
    def valid(v, zero=False):
        return type(v) in (int,float) and math.isfinite(v) and (v >= 0 if zero else v > 0)
    host_steps = defaultdict(set)
    for plane in space.planes:
        if not plane.name.startswith('/host:CPU'): continue
        for line in plane.lines:
            for ev in line.events:
                meta = plane.event_metadata[ev.metadata_id]
                if meta.name != name: continue
                s = stats(plane, meta.stats) | stats(plane, ev.stats)
                if type(s.get('step_num')) is int and 0 <= s['step_num'] < calls and s.get('group_id') is not None:
                    host_steps[s['group_id']].add(s['step_num'])
    groups = defaultdict(list)
    planes = []
    for plane in space.planes:
        if plane.name not in parsed['devices']: continue
        require(plane.name not in planes, 'duplicate TPU plane')
        planes.append(plane.name)
        modules, kernels = [], []
        for line in plane.lines:
            if line.name not in ['XLA Modules','XLA Ops','Async XLA Ops']: continue
            for ev in line.events:
                meta = plane.event_metadata[ev.metadata_id]
                s = stats(plane, meta.stats) | stats(plane, ev.stats)
                module = line.name == 'XLA Modules'
                if module and not re.fullmatch(r'(?:jit_)?'+re.escape(name)+r'(?:\(\d+\))?',meta.name): continue
                if not module and not (s.get('hlo_category')=='custom-call' or 'custom-call' in meta.name): continue
                program = s.get('program_id')
                if module and program is None:
                    match = re.search(r'\((\d+)\)$',meta.name)
                    program = int(match[1]) if match else None
                step = s.get('step_num')
                associated = host_steps.get(s.get('group_id'),set())
                if step is None and len(associated)==1: step = next(iter(associated))
                event = {'kind':'program' if module else 'kernel', 'name':meta.name, 'plane':plane.name,
                    'line':line.name, 'line_id':line.id, 'program_id':program,
                    'duration_ps':s.get('device_duration_ps'),
                    'device_offset_ps': s.get('device_offset_ps') if valid(s.get('device_offset_ps'),True) else None,
                    'step_num':step if type(step) is int and 0 <= step < calls else None, 'run_id':s.get('run_id')}
                (modules if module else kernels).append(event)
        require(modules, 'no matching raw program events')
        ids = {m['program_id'] for m in modules if m['program_id'] is not None}
        for e in modules+kernels:
            if e['kind']=='kernel':
                if e['program_id'] not in ids: continue
                start = e['device_offset_ps']
                parents = [m for m in modules if m['program_id']==e['program_id'] and
                    start is not None and m['device_offset_ps'] is not None and valid(e['duration_ps']) and valid(m['duration_ps']) and
                    m['device_offset_ps'] <= start and start+e['duration_ps'] <= m['device_offset_ps']+m['duration_ps']]
                if e['step_num'] is None and len(parents)==1:
                    e['step_num'],e['run_id']=parents[0]['step_num'],parents[0]['run_id']
            require(valid(e['duration_ps']), 'raw XPlane duration invalid')
            groups[tuple(e[k] for k in ['kind','name','plane','line','line_id','program_id'])].append(
                {k:e[k] for k in ['duration_ps','device_offset_ps','step_num','run_id']})
    require(sorted(planes)==parsed['devices'], 'raw TPU planes differ')
    expected = {tuple(g[k] for k in ['kind','name','plane','line','line_id','program_id']):g for g in parsed['groups']}
    require(set(groups)==set(expected), 'independently decoded XPlane group set differs')
    recomputed = []
    for key, samples in groups.items():
        samples.sort(key=lambda e:e['device_offset_ps'] if e['device_offset_ps'] is not None else 0)
        require(samples==expected[key]['samples'], 'raw XPlane sample lineage differs from saved parser')
        require(len(samples)==calls, 'incomplete/extra raw event coverage')
        description=independent_stats([e['duration_ps']/1e6 for e in samples])
        require(math.isclose(description['median'],expected[key]['median_us'],rel_tol=1e-12), 'profile median differs')
        recomputed.append({'kind':key[0], 'name':key[1], 'plane':key[2], 'program_id':key[5],
                           'statistics_us':description, 'samples':samples})
    return {'status':'independently_decoded_and_matched', 'raw_ref':ref,
        'raw_xspace_errors':list(space.errors), 'raw_xspace_warnings':list(space.warnings),
        'expected_program_name_from_operation':name, 'decoded_planes':[p.name for p in space.planes],
        'groups':recomputed, 'all_duration_offset_step_run_samples_equal_saved_parser':True}


def terminal_job(store, path):
    job = read(path)
    marker = store.get(stable_id('workflow-result',job['run_id']))
    result = marker.details['result']
    job.update(result=result, state='completed' if result['classification'].startswith('correct_') else 'failed', stage='evaluate')
    return job


def main():
    report={'schema_version':1, 'started_utc':datetime.now(timezone.utc).isoformat(),
        'store_writes':0, 'network_calls':0, 'hardware_executions':0, 'jobs':[], 'failures':[]}
    schema, report['xplane_decoder'] = load_schema()
    # Exhaustive BF16 classification self-check is independent of all fixtures.
    all_words = b''.join(struct.pack('<H',i) for i in range(65536))
    require(count_bf16(all_words)==(65536,256,2),'BF16 finite scanner self-check failed')
    report['finite_scanner_self_check']='all 65536 BF16 bit patterns; 256 nonfinite, 2 infinities'
    store=PinnedStore()
    sqlrow=store.connection.execute("select * from records where id like 'wf-state:%' order by seq desc limit 1").fetchone()
    state=store._decode(sqlrow).details['state']
    jobs=[deepcopy(j) for j in state['jobs'].values() if j['state']=='completed']
    for generation in ['runtime2','runtime3','runtime7']:
        names=['baseline-repair','baseline-b','oracle-negative-attention','oracle-negative-cache']
        if generation=='runtime3': names.append('confirmation-control')
        for label in names:
            job=terminal_job(store,BASE/(label+'-'+generation+'-job.json'))
            job['_audit_name']=generation+'/'+label
            jobs.append(job)
    jobs.sort(key=lambda j: (j['round_id'],j['variant']['label']))
    report['journal_revision']=state['revision']
    report['expected_job_count']=len(jobs)
    comparison_cache={}
    for index, job in enumerate(jobs,1):
        name=job.get('_audit_name',job['round_id']+'/'+job['variant']['label'])
        print(f'[{index}/{len(jobs)}] {name}',flush=True)
        try:
            row=verify_job(store,job,recompute_npz=True,comparison_cache=comparison_cache)
            row['audit_name']=name
            row['round_id']=job['round_id']
            protocol=job['contract']['context']['protocol']
            require(protocol['correctness']=={'atol':.01,'rtol':.01},'attention policy changed')
            require(protocol['benchmark']['version']=='mla-contiguous-host-sync-v1','benchmark version changed')
            row['fixed_contract']={k:protocol[k] for k in ['correctness','benchmark','benchmark_case_id','oracle_digest','oracle_bundle_ref','runner_digest','profile']}
            row['environment']=job['contract']['context']['environment']
            row['workload_cases']=job['contract']['workload']['spec']['cases']
            receipts={stage:json.loads(store.artifacts.read(ArtifactRef(**ref))) for stage,ref in job['result']['receipt_refs'].items()}
            row['operation_ids']={stage:r['operation_id'] for stage,r in receipts.items()}
            row['parent_candidate_id']=store.get(job['parent_id']).candidate_id
            if 'correctness' in receipts:
                row['independent_correctness_extra']=correctness_extra(store,receipts['correctness'])
            if 'benchmark' in receipts:
                b=receipts['benchmark']['outputs']
                require(b['warmup_count']==5 and len(b['samples_ms'])==20 and b['synchronized'],'benchmark policy mismatch')
                s=independent_stats(b['samples_ms'])
                row['independent_host_statistics_ms']=s
                mapping={'median':'median_ms','mean':'mean_ms','population_std':'standard_deviation_ms','min':'min_ms','max':'max_ms','p25':'p25_ms','p75':'p75_ms'}
                for a,k in mapping.items(): require(math.isclose(s[a],row['benchmark_statistics'][k],rel_tol=1e-12,abs_tol=1e-15),'independent host statistic mismatch: '+k)
                parent=row['parent_latency_ms']
                row['same_context_comparison']=None if parent is None else {'parent_evaluation_id':job['parent_evaluation_id'],
                    'parent_ms':parent,'candidate_ms':s['median'],'speedup':parent/s['median'],
                    'latency_reduction':(parent-s['median'])/parent,
                    'classification':'single_run_faster' if s['median']<parent else 'slower',
                    'independent_confirmation':False}
                row['benchmark_lease']=b['lease']
            if 'profile' in receipts:
                row['independent_xplane']=xplane_check(store,job,receipts['profile'],schema)
                row['independent_raw_protobuf_reparse']=True
            # Keep complete identity and exact module path; drop enormous repeated
            # loaded_files maps after the original validator checked all entries.
            for identity in row['loaded_identities'].values():
                files=identity.pop('loaded_files',{})
                identity['loaded_files_count']=len(files)
                identity['loaded_files_canonical_sha256']=sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            report['jobs'].append(row)
            (OUT/'raw-audit-progress.json').write_text(json.dumps({'last':name,'verified':len(report['jobs']),'failures':report['failures']},indent=2)+'\n',encoding='utf-8')
        except Exception as exc:
            failure={'name':name,'run_id':job['run_id'],'error_type':type(exc).__name__,'error':str(exc),'traceback':traceback.format_exc()}
            report['failures'].append(failure)
            print(json.dumps(failure),flush=True)
    abba_order=['p35-control-before','p35-treatment-first','p35-treatment-second','p35-control-after']
    abba=[]
    by_label={r['label']:r for r in report['jobs'] if r['round_id']=='p35-runtime3-abba:1'}
    for index,label in enumerate(abba_order):
        r=by_label[label]
        j=next(j for j in jobs if j['run_id']==r['run_id'])
        corr=json.loads(store.artifacts.read(ArtifactRef(**j['result']['receipt_refs']['correctness'])))
        pro=json.loads(store.artifacts.read(ArtifactRef(**j['result']['receipt_refs']['profile'])))
        parsed=json.loads(store.artifacts.read(ArtifactRef(**pro['outputs']['report_ref'])))
        samples={kind:[s['duration_ps']/1e6 for g in parsed['groups'] if (g['kind']=='program' if kind=='program' else kind in g['name']) for s in g['samples']] for kind in ['program','prepare','attention']}
        abba.append({'role':'control' if index in (0,3) else 'treatment','run_id':r['run_id'],
            'context_id':r['context_id'],'runtime_id':r['runtime_id'],'protocol_version':r['fixed_contract']['benchmark']['version'],
            'input_digest':corr['outputs']['input_digest'],**r['declared_identity'],
            'raw_samples_ms':r['raw_samples_ms'],'profile_samples_us':samples,
            'profile_run_id':r['run_id'],'profile_capture_operation_id':pro['operation_id']})
    report['abba']=analyze_abba(abba,{'version':'p35-abba-v1','min_paired_reduction':.02,'max_within_role_spread':.05})
    ordered=[by_label[label]['benchmark_lease'] for label in abba_order]
    require(all(a['finished_ns']<=b['started_ns'] for a,b in zip(ordered,ordered[1:])),'ABBA actual execution order differs')
    report['abba']['actual_order_verified_from_benchmark_leases']=True
    report['verified_unique_artifacts']=len(store.artifacts.checked)
    report['verified_unique_artifact_bytes']=sum(v[0] for v in store.artifacts.checked.values())
    report['artifact_inventory']=[{'digest':k,'size':v[0]} for k,v in sorted(store.artifacts.checked.items())]
    report['counts']={'verified':len(report['jobs']),'failed_verification':len(report['failures']),
        'profile_xplanes_independently_decoded':sum('independent_xplane' in j for j in report['jobs']),
        'correctness_cases_recomputed':sum(len(j.get('independent_correctness_extra',[])) for j in report['jobs']),
        'unique_bf16_finite_scans':len(FINITE_CACHE)}
    report['status']='verified' if not report['failures'] and len(report['jobs'])==len(jobs) else 'failed_verification'
    report['finished_utc']=datetime.now(timezone.utc).isoformat()
    report['limitations']=[
        'Device input-cache immutability: not independently verifiable from retained artifacts; post-device input cache was not retained.',
        'XPlane re-decoding validates retained event metadata and full duration/offset/step/run sample lineage, not the truth of the original hardware capture.',
        'Full bundle hashes and loaded module paths validate identity, not that every bundled file executed.',
        'Existing fixed correctness cases only; no LSE or wider workload coverage.',
        'Two ABBA Runs per role offer limited replication; within-Run samples are not independent Runs.',
        'Parent comparisons use exact same Context Evaluation; no cross-Runtime speedup ranking.']
    (OUT/'raw-audit-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'counts':report['counts'],'artifacts':report['verified_unique_artifacts'],'bytes':report['verified_unique_artifact_bytes'],'abba':report['abba']['outcome']}),flush=True)
    store.connection.close()
    return 0 if report['status']=='verified' else 1


if __name__=='__main__':
    raise SystemExit(main())
