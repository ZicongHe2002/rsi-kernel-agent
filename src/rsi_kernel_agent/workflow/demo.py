"""Two recorded synthetic RSI rounds through production APIs, with no hardware.

The response builder below is demonstration fixture data, never an Analyzer or
repair reasoner. Live operation supplies the same session/repair responses from
the current conversation model. All latency and correctness outcomes are seeded.
"""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from ..associations import MemoryWriter
from ..contracts import (ArtifactRef, Candidate, CandidateIdentity, EvaluationBundle,
    EvaluationContext, Evidence, ParentCandidate, Plan, ProfileBundle, Run, RunEvent,
    Strategy, Task, Workload, canonical_json, record_to_dict)
from ..knowledge import SourceDocument, extract_method, method_note_id, method_to_dict
from ..knowledge_mcp import StdioKnowledge
from ..profiler.outputs import publish_result
from ..profiler.session import start_session, submit_response
from ..store import ExperimentStore
from .controller import Controller
from .execution import SyntheticBackend
from .intake import intake_publication
from .rounds import prepare_round


def _put(store, value):
    return store.artifacts.put(canonical_json(value).encode('utf-8'))


def _bootstrap(store):
    def add(record):
        store.append(record, 'demo:' + record.id)
    add(Task('demo-task', 'Synthetic RSI software loop', 'synthetic-reference-v1'))
    add(Workload('demo-workload', 'demo-task', {'operation': 'sum', 'length': 32, 'dtype': 'float32',
        'correctness': {'reference': 'sum of inputs', 'atol': 0, 'rtol': 0}, 'synthetic': True}))
    add(EvaluationContext('demo-context', 'demo-task', 'demo-workload', {'device': 'synthetic:0'},
                          {'timer': 'synthetic', 'unit': 'ms', 'repeat': 1, 'fixed_inputs': True}, 'synthetic'))
    for sid in 'ABCDE':
        add(Strategy(sid, 'demo-task', 'Synthetic route ' + sid))
    for sid, latency in (('A', 10), ('B', 12)):
        source = store.artifacts.put(('# Synthetic baseline ' + sid + '\ndef kernel(values):\n    return sum(values)\n').encode())
        config, build = _put(store, {'tile': 16 if sid == 'A' else 8}), store.artifacts.put(b'synthetic-recipe-v1')
        identity = CandidateIdentity(source.digest, config.digest, build.digest)
        add(ParentCandidate(sid + '-import', 'demo-task', identity))
        add(Plan(sid + '-initial-plan', sid, 'Seeded synthetic reference'))
        MemoryWriter(store).bind_plan(sid + '-initial-plan', sid + '-import', 'demo:bind:' + sid)
        add(Candidate(sid + '-initial', sid + '-initial-plan', sid + '-import', identity))
        add(ParentCandidate(sid + '-parent', 'demo-task', identity, sid + '-initial'))
        run = Run(sid + '-initial-run', sid + '-initial', 'demo-context')
        add(run)
        store.transition(RunEvent(sid + '-start', run.id, 'running', 'active', 'Synthetic initial observation'), None, 'demo:' + sid + '-start')
        store.transition(RunEvent(sid + '-finish', run.id, 'succeeded', 'finished', 'Synthetic initial observation'), sid + '-start', 'demo:' + sid + '-finish')
        timing = _put(store, {'provenance': 'synthetic', 'latency_ms': latency})
        add(Evidence(sid + '-timing', run.id, 'measured_timing', 'available', 'completed', 'supported',
                     latency, 'ms', (timing,), source={'provenance': 'synthetic'}))
        add(EvaluationBundle(sid + '-evaluation', run.id, 'passed', 'valid', latency, True, (sid + '-timing',)))
        profile = _put(store, {'time_source': 'TPU XPlane device_duration_ps', 'unit': 'us',
            'profile': 'synthetic-no-hardware.xplane.pb', 'calls': 1, 'devices': ['synthetic:0'],
            'groups': [{'kind': 'program', 'name': 'synthetic_sum', 'plane': 'synthetic:0', 'line': 'XLA Modules',
                'line_id': 1, 'program_id': 1, 'count': 1, 'mean_us': latency * 1000,
                'median_us': latency * 1000, 'p95_us': latency * 1000, 'min_us': latency * 1000,
                'max_us': latency * 1000, 'events_per_measured_call': 1, 'invalid_samples': 0,
                'samples': [{'duration_ps': latency * 1_000_000_000, 'device_offset_ps': 0, 'step_num': 0, 'run_id': 1}]}],
            'longest_device_per_call': [], 'multi_device_span': {'status': 'unavailable', 'reason': 'Synthetic fixture'},
            'errors': [], 'warnings': [], 'provenance': 'synthetic'})
        add(Evidence(sid + '-trace', run.id, 'trace_derived', 'available', 'completed', 'supported',
                     {'synthetic': True}, artifacts=(profile,), source={'provenance': 'synthetic'}))
        add(ProfileBundle(sid + '-profile', run.id, (sid + '-trace',), 'available'))
        store.consider(sid + '-evaluation', 'demo:best:' + sid)
        MemoryWriter(store).select_parent(sid, 'demo-context', sid + '-parent', 'Explicit synthetic initial parent', 'demo:parent:' + sid)


def _knowledge_files(directory, website_dir):
    content = directory / 'knowledge' / 'content'
    source_path = content / 'Research' / 'Synthetic.md'
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_bytes = b'# Synthetic optimization methods\n\n## Tiling\nVary tile sizes while preserving the same reference computation.\n'
    source_path.write_bytes(source_bytes)
    source = SourceDocument('Research/Synthetic.md', 'Synthetic optimization methods', 'Research',
                            sha256(source_bytes).hexdigest(), source_bytes.decode())
    method = extract_method(source, 'Tiling', hardware=('synthetic CPU',), mechanism='tiling')
    method_path = content / method_note_id(method.method_id)
    method_path.parent.mkdir(parents=True, exist_ok=True)
    raw = ('# Synthetic method\n\n```rsi-method-v1\n' + canonical_json(
        {'schema_version': 1, 'method': method_to_dict(method)}) + '\n```\n').encode()
    method_path.write_bytes(raw)
    entry = directory / 'knowledge' / 'stdio.py'
    entry.write_text('import sys\nimport logging\nlogging.disable(logging.INFO)\n'
        f'sys.path.insert(0, {str(website_dir)!r})\n'
        'from mcp_server import build_server\n'
        f'build_server({str(content)!r}).run(transport="stdio")\n', encoding='utf-8')
    return entry, {'method_id': method.method_id, 'note_id': method_note_id(method.method_id),
                   'revision': sha256(raw).hexdigest()}


def _recorded_proposal(prepared, number, method_id):
    data = prepared['manifest'].to_dict()
    modes = ({'A-P1': ['success', 'compile_failure'], 'A-P2': ['correctness_failure'],
              'B-P1': ['success'], 'B-P2': ['timeout', 'invalid_measurement']} if number == 1 else
             {'A-P1': ['success'], 'A-P2': ['success'], 'B-P1': ['success'], 'B-P2': ['success']})
    proposal = {'code_understanding': [], 'observations': [], 'hypotheses': [], 'plans': [],
                'unknowns': ['Recorded synthetic input does not establish real hardware benefit']}
    for sid, binding in data['strategies'].items():
        proposal['code_understanding'].append({'strategy_id': sid, 'stages': ['load', 'sum', 'return'],
            'loops': ['reduction'], 'movement': ['read inputs'], 'dependencies': ['sum depends on input'],
            'layout_memory': ['Python fixture; hardware layout unknown'], 'synchronization': ['not measured'],
            'edge_cases': ['empty and odd sizes'], 'refs': ['source:' + sid, 'history:' + sid]})
        proposal['hypotheses'].append({'id': sid + '-h', 'strategy_id': sid,
            'text': 'Tile grouping may change resource demand', 'refs': ['source:' + sid],
            'alternatives': ['Loop overhead may dominate', 'No measurable change']})
        for index, mechanism, modification in ((1, 'tiling', 'change tile grouping only'),
                                                 (2, 'unrolling', 'unroll the reduction by two')):
            pid = sid + '-P' + str(index)
            proposal['plans'].append({'id': pid, 'strategy_id': sid, 'parent_id': binding['parent_id'],
                'mechanism': mechanism, 'modification': modification, 'refs': ['source:' + sid, 'history:' + sid, 'knowledge:' + method_id],
                'observation_ids': [], 'calculation_ids': [], 'tradeoffs': ['extra temporary state'],
                'risks': ['tail handling'], 'unknowns': ['real hardware effect'],
                'variants': [modification + ' variant ' + str(i) for i in range(len(modes[pid]))],
                'validation': {'correctness': ['fixed reference including empty and odd sizes'],
                               'performance': ['same fixed synthetic context; real hardware untested']}})
    return proposal, modes


async def run_demo(directory, *, website_dir=None):
    """Create an isolated artifact, MCP content, experiment DB, and two rounds."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / 'experiments.sqlite3').exists():
        raise ValueError('Demo requires a fresh directory; use Controller recovery for an existing task')
    website_dir = Path(website_dir or Path(__file__).resolve().parents[3] / 'knowledge-site').resolve()
    store = ExperimentStore(directory / 'experiments.sqlite3', directory / 'artifacts')
    _bootstrap(store)
    entry, pin = _knowledge_files(directory, website_dir)
    controller = Controller(store, 'demo-task', SyntheticBackend(store, directory / 'candidates'))
    rounds = []
    async with StdioKnowledge(website_dir, entrypoint=entry) as knowledge:
        search = await knowledge.search('Synthetic')
        for number in (1, 2):
            round_id = 'demo-round:' + str(number)
            prepared = await prepare_round(store, knowledge, 'demo-task', 'demo-context', ('A', 'B'), round_id, [pin])
            proposal, modes = _recorded_proposal(prepared, number, pin['method_id'])
            session = directory / ('session-' + str(number))
            start_session(store, prepared['manifest'], session, model_label='recorded synthetic demonstration')
            calls = [{'id': 'history-' + sid, 'name': 'history', 'arguments': {'strategy_id': sid}} for sid in 'AB']
            for sid, binding in prepared['manifest'].to_dict()['strategies'].items():
                if binding['profiles']:
                    calls.append({'id': 'profile-' + sid, 'name': 'profile',
                                  'arguments': {'strategy_id': sid, 'profile_id': binding['profiles'][0]['id']}})
            response = session / 'response.json'
            response.write_text(canonical_json({'tool_calls': calls}), encoding='utf-8')
            submit_response(store, session, response)
            response.write_text(canonical_json({'final': proposal}), encoding='utf-8')
            analysis = submit_response(store, session, response)
            publication = publish_result(store, prepared['manifest'], analysis, 'demo:publication:' + str(number))
            specs = {}
            for plan in proposal['plans']:
                sid = plan['strategy_id']
                binding = prepared['manifest'].to_dict()['strategies'][sid]
                specs[plan['id']] = []
                for index, scenario in enumerate(modes[plan['id']]):
                    source = store.artifacts.put((binding['source'] + '# synthetic ' + round_id + ':' + plan['id'] + ':' + str(index) + '\n').encode())
                    config = _put(store, {'round': number, 'plan': plan['id'], 'variant': index})
                    build = store.artifacts.put(b'synthetic-recipe-v1')
                    latency = (8 if sid == 'A' else 11) if number == 1 else (7 if sid == 'A' else 9)
                    specs[plan['id']].append({'label': plan['variants'][index], 'source_ref': asdict(source),
                        'config_ref': asdict(config), 'build_ref': asdict(build), 'scenario': scenario,
                        'latency_ms': latency, 'priority': 0, 'device': 'synthetic:0',
                        'cost': {'executions': 1, 'tokens': 0, 'time_s': 0.01}})
            intake = await intake_publication(store, publication.id, round_id, specs, knowledge)
            controller.accept_intake(intake)
            controller.run_until_idle()
            if number == 1:
                failed = next(j for j in controller.status()['jobs'].values() if j['result']['classification'] == 'compile_failed')
                repair = controller.repair_packet(failed['id'])
                variant = json.loads(canonical_json(failed['variant']))
                raw = store.artifacts.read(ArtifactRef(**variant['source_ref']))
                variant.update(source_ref=asdict(store.artifacts.put(raw + b'# recorded synthetic compile repair\n')),
                               scenario='success', latency_ms=7.8)
                repair.update(variant=variant, reason='Recorded synthetic repair response; preserve fixed reference contract')
                (directory / 'repair-response.json').write_text(canonical_json(repair), encoding='utf-8')
                controller.submit_repair(failed['id'], repair, 'demo:repair:1')
                controller.run_until_idle()
            rounds.append({'round_id': round_id, 'snapshot': prepared['snapshot'], 'cards': prepared['cards'],
                'histories': prepared['histories'], 'knowledge': [pin], 'manifest': asdict(prepared['manifest'].artifact_ref),
                'publication': record_to_dict(publication), 'plan_set': analysis['plan_set'], 'intake': intake})
    state = controller.status()
    jobs = list(state['jobs'].values())
    result = {'provenance': 'synthetic', 'hardware_executed': False, 'model_transport': 'recorded_session_fixture',
        'task_id': 'demo-task', 'strategies': list('ABCDE'), 'knowledge_search': search, 'rounds': rounds,
        'candidates': [record_to_dict(r) for r in store.history('demo-task') if isinstance(r, Candidate)],
        'runs': [record_to_dict(r) for r in store.history('demo-task') if isinstance(r, Run)],
        'results': [j['result'] for j in jobs if j['result'] is not None],
        'repairs': [j for j in jobs if j.get('repair_of')],
        'best': {key: record_to_dict(store.best('demo-task', 'demo-context', None if key == 'task' else key))
                 for key in ('task', 'A', 'B')},
        'frontier': {sid: [cid for cid in store.frontier('demo-task', 'demo-context')
                          if store.get(store.get(cid).plan_id).strategy_id == sid] for sid in 'AB'}, 'workflow': state}
    (directory / 'report.json').write_text(canonical_json(result), encoding='utf-8')
    return result
