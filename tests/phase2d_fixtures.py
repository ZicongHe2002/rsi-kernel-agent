"""Recorded synthetic conversation through the real 2C publication boundary."""
from dataclasses import asdict
import json
from pathlib import Path

from phase2c_fixtures import make_analysis_fixture
from test_analyzer import proposal
from rsi_kernel_agent.associations import MemoryWriter
from rsi_kernel_agent.contracts import Strategy, canonical_json
from rsi_kernel_agent.profiler.inputs import AnalysisRequest, capture_inputs
from rsi_kernel_agent.profiler.outputs import publish_result
from rsi_kernel_agent.profiler.session import start_session, submit_response


async def make_workflow_fixture(root, scenarios=None, *, intake=True):
    root = Path(root)
    fixture = make_analysis_fixture(root)
    store = fixture['store']
    for sid in 'CDE':
        store.append(Strategy(sid, 'task', 'Synthetic reserve ' + sid), 'fixture:' + sid)
    for sid in 'AB':
        store.consider(sid + '-benchmark', 'fixture:best:' + sid)
        MemoryWriter(store).select_parent(sid, 'context', sid + '-parent',
                                         'Explicit synthetic initial parent', 'fixture:parent:' + sid)
    snapshot = store.create_snapshot('task', 'workflow:fixture:snapshot')
    for binding in fixture['request']['strategies']:
        binding['snapshot_id'] = snapshot['id']
    manifest = await capture_inputs(store, fixture['knowledge'], AnalysisRequest.from_dict(fixture['request']))
    answer = proposal()
    default = {'A-P1': ['success', 'compile_failure'], 'A-P2': ['success'],
               'B-P1': ['success'], 'B-P2': ['success', 'invalid_measurement']}
    scenarios = default if scenarios is None else scenarios
    specs = {}
    for plan in answer['plans']:
        modes = scenarios.get(plan['id'], ['success'])
        plan['variants'] = [plan['modification'] + ' variant ' + str(i) for i in range(len(modes))]
        plan['refs'].append('knowledge:' + fixture['request']['knowledge'][0]['method_id'])
        specs[plan['id']] = []
        binding = manifest.to_dict()['strategies'][plan['strategy_id']]
        for i, mode in enumerate(modes):
            source = store.artifacts.put((binding['source'] + '# synthetic proposal ' + plan['id'] + ':' + str(i)).encode())
            config = store.artifacts.put(canonical_json({'synthetic_variant': i, 'plan': plan['id']}).encode())
            build = store.artifacts.put(b'synthetic build recipe v1')
            specs[plan['id']].append({'label': plan['variants'][i], 'source_ref': asdict(source),
                'config_ref': asdict(config), 'build_ref': asdict(build), 'scenario': mode,
                'latency_ms': 8 + i if plan['strategy_id'] == 'A' else 11 + i,
                'priority': 0, 'device': 'synthetic:0',
                'cost': {'executions': 1, 'tokens': 0, 'time_s': 0.25}})
    directory = root / 'recorded-session'
    start_session(store, manifest, directory, model_label='recorded synthetic test response')
    response_path = directory / 'response.json'
    response_path.write_text(json.dumps({'tool_calls': [
        {'id': 'history-A', 'name': 'history', 'arguments': {'strategy_id': 'A'}},
        {'id': 'history-B', 'name': 'history', 'arguments': {'strategy_id': 'B'}},
        {'id': 'profile-A', 'name': 'profile', 'arguments': {'strategy_id': 'A', 'profile_id': 'A-profile'}}]}), encoding='utf-8')
    submit_response(store, directory, response_path)
    response_path.write_text(json.dumps({'final': answer}), encoding='utf-8')
    result = submit_response(store, directory, response_path)
    publication = publish_result(store, manifest, result, 'workflow:fixture:publication')
    fixture.update(manifest=manifest, publication=publication, variants=specs, result=result)
    if intake:
        from rsi_kernel_agent.workflow.intake import intake_publication
        fixture['intake'] = await intake_publication(store, publication.id, 'round:1', specs, fixture['knowledge'])
    return fixture
