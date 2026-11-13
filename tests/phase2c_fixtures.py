"""Small synthetic A/B experiment inputs; no kernels, network or credentials."""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from rsi_kernel_agent.contracts import (
    Candidate, CandidateIdentity, EvaluationBundle, EvaluationContext, Evidence,
    ParentCandidate, Plan, ProfileBundle, Run, RunEvent, Strategy, Task, Workload,
    canonical_json,
)
from rsi_kernel_agent.knowledge import KnowledgeClient, extract_method, SourceDocument, method_note_id, method_to_dict
from rsi_kernel_agent.store import ExperimentStore


def make_analysis_fixture(tmp_path):
    root = Path(tmp_path)
    store = ExperimentStore(root / 'experiments.sqlite3', root / 'artifacts')

    def add(record):
        store.append(record, 'fixture:' + record.id)

    add(Task('task', 'Synthetic tiled matrix multiplication', 'fixture-v1'))
    add(Workload('workload', 'task', {'M': 32, 'N': 32, 'K': 64, 'dtype': 'float32', 'semantics': 'C = A @ B'}))
    add(EvaluationContext('context', 'task', 'workload', {'device': 'synthetic CPU', 'throughput': None},
                          {'timer': 'synthetic', 'unit': 'ms'}, 'synthetic'))
    baseline_identity = CandidateIdentity(*([sha256(b'baseline').hexdigest()] * 3))
    add(ParentCandidate('baseline', 'task', baseline_identity))
    bindings = []
    for strategy, latency in [('A', 10), ('B', 12)]:
        source = (f'# Synthetic route {strategy}; this fixture is never executed.\n'
                  'def matmul(a, b, out, M, N, K, tile):\n'
                  '    for i in range(0, M, tile):\n'
                  '        for j in range(0, N, tile):\n'
                  '            acc = zeros((tile, tile), dtype="float32")\n'
                  '            for k in range(0, K, tile):\n'
                  '                left = load(a, i, k, tile, mask=True)\n'
                  '                right = load(b, k, j, tile, mask=True)\n'
                  '                acc += dot(left, right)\n'
                  '            store(out, i, j, acc, mask=True)\n').encode()
        config = canonical_json({'tile': 16 if strategy == 'A' else 8, 'dtype': 'float32'}).encode()
        src, cfg = store.artifacts.put(source), store.artifacts.put(config)
        identity = CandidateIdentity(src.digest, cfg.digest, sha256(b'synthetic-recipe').hexdigest())
        add(Strategy(strategy, 'task', 'Route ' + strategy))
        add(Plan(strategy + '-initial', strategy, 'Synthetic original implementation'))
        add(Candidate(strategy + '-candidate', strategy + '-initial', 'baseline', identity))
        add(ParentCandidate(strategy + '-parent', 'task', identity, strategy + '-candidate'))
        run_id = strategy + '-run'
        add(Run(run_id, strategy + '-candidate', 'context'))
        running = RunEvent(strategy + '-running', run_id, 'running', 'active', 'Synthetic fixture')
        store.transition(running, None, 'fixture:' + running.id)
        done = RunEvent(strategy + '-done', run_id, 'succeeded', 'finished', 'Synthetic fixture')
        store.transition(done, running.id, 'fixture:' + done.id)
        timing = store.artifacts.put(canonical_json({'synthetic': True, 'latency_ms': latency}).encode())
        add(Evidence(strategy + '-timing', run_id, 'measured_timing', 'available', 'completed', 'supported',
                     latency, 'ms', (timing,), source={'provenance': 'synthetic'}))
        add(EvaluationBundle(strategy + '-benchmark', run_id, 'passed', 'valid', latency, True, (strategy + '-timing',)))
        profile = store.artifacts.put(canonical_json({'time_source': 'TPU XPlane device_duration_ps',
            'unit': 'us', 'profile': 'synthetic.xplane.pb', 'calls': 1, 'devices': ['/device:TPU:0'],
            'groups': [{'kind': 'program', 'name': 'jit_matmul(123)', 'plane': '/device:TPU:0',
                        'line': 'XLA Modules', 'line_id': 1, 'program_id': 123, 'count': 1,
                        'mean_us': latency * 1000, 'median_us': latency * 1000, 'p95_us': latency * 1000,
                        'min_us': latency * 1000, 'max_us': latency * 1000, 'events_per_measured_call': 1,
                        'samples': [{'duration_ps': latency * 1000000000, 'device_offset_ps': 0,
                                     'step_num': 0, 'run_id': 1}], 'invalid_samples': 0}],
            'longest_device_per_call': [], 'multi_device_span': {'status': 'unavailable',
                'reason': 'Cross-device timestamp alignment has not been verified.'}, 'errors': [], 'warnings': []}).encode())
        add(Evidence(strategy + '-trace', run_id, 'trace_derived', 'available', 'completed', 'supported',
                     {'target': 'matmul'}, None, (profile,), source={'provenance': 'synthetic'}))
        add(ProfileBundle(strategy + '-profile', run_id, (strategy + '-trace',), 'available'))
        bindings.append({'strategy_id': strategy, 'parent_id': strategy + '-parent', 'context_id': 'context',
                         'snapshot_id': '', 'run_id': run_id, 'source_artifact': asdict(src),
                         'config_artifact': asdict(cfg), 'profile_ids': [strategy + '-profile'],
                         'benchmark_ids': [strategy + '-benchmark']})
    snapshot = store.create_snapshot('task', 'fixture:snapshot')
    for binding in bindings:
        binding['snapshot_id'] = snapshot['id']
    raw_source = b'\xef\xbb\xbf# Pipelining\n\n## Overlap\nIndependent loads may overlap computation; additional buffers consume memory.\n'
    source_revision = sha256(raw_source).hexdigest()
    source_doc = SourceDocument('Research/Pipelining.md', 'Pipelining', 'Research', source_revision, raw_source.decode('utf-8-sig'))
    method = extract_method(source_doc, 'Overlap', hardware=('synthetic CPU',), mechanism='overlap')
    note_id = method_note_id(method.method_id)
    raw_method = ('# Overlap\n\n```rsi-method-v1\n' + canonical_json({'schema_version': 1, 'method': method_to_dict(method)}) + '\n```\n').encode()
    documents = {source_doc.id: raw_source, note_id: raw_method}

    async def call_tool(name, args):
        if name == 'fetch':
            raw = documents[args['id']]
            content = raw.decode('utf-8-sig')
            offset, limit = args['offset'], args['limit']
            end = min(len(content), offset + limit)
            return {'id': args['id'], 'title': args['id'].rsplit('/', 1)[-1],
                    'category': args['id'].rsplit('/', 1)[0], 'revision': sha256(raw).hexdigest(),
                    'content': content[offset:end], 'offset': offset, 'total_chars': len(content),
                    'next_offset': end if end < len(content) else None}
        if name == 'search':
            results = [{'id': key, 'revision': sha256(raw).hexdigest(), 'category': key.rsplit('/', 1)[0],
                        'title': key.rsplit('/', 1)[-1]} for key, raw in sorted(documents.items()) if args['query'] in key]
            offset, limit = args['offset'], args['limit']
            end = min(len(results), offset + limit)
            return {'results': results[offset:end], 'total': len(results), 'next_offset': end if end < len(results) else None}
        raise ValueError('This read-only fixture does not support writes')

    request = {'schema_version': 1, 'request_id': 'analysis-fixture', 'task_id': 'task', 'workload_id': 'workload',
               'strategies': bindings, 'knowledge': [{'method_id': method.method_id, 'note_id': note_id,
                                                       'revision': sha256(raw_method).hexdigest()}],
               'environment': {'analysis_host': 'synthetic local fixture'}, 'tool_versions': {'fixture': '1'}}
    return {'store': store, 'request': request, 'knowledge': KnowledgeClient(call_tool), 'documents': documents}
