"""Bounded full-context delivery through the public session and CLI."""
from collections import defaultdict
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from phase2c_fixtures import make_analysis_fixture
from test_analyzer import proposal
from rsi_kernel_agent.contracts import Decision, canonical_json
from rsi_kernel_agent.profiler.analyzer import Analyzer
from rsi_kernel_agent.profiler.context import ContextReader, encode_context
from rsi_kernel_agent.profiler.inputs import AnalysisRequest, capture_inputs
from rsi_kernel_agent.profiler.session import start_session, submit_response


class ContextPageTests(unittest.TestCase):
    def test_pages_cross_small_nodes_and_chunk_large_nodes_without_gaps(self):
        value = {'small': [{'source': str(i) * 600} for i in range(30)]}
        value['repeated'] = value['small'] * 3
        value['large'] = 'long independent source ' * 4000
        encoded = encode_context(value)
        reader = ContextReader(encoded)
        self.assertTrue(callable(getattr(reader, 'page', None)), 'Cross-node paging is required')
        actual, offset, calls = defaultdict(str), 0, 0
        while True:
            page = reader.page(offset=offset, limit=24000)
            calls += 1
            self.assertEqual(page['offset'], offset)
            self.assertLessEqual(sum(len(row['content']) for row in page['segments']), 24000)
            for row in page['segments']:
                self.assertEqual(row['offset'], len(actual[row['node_digest']]))
                self.assertEqual(row['page_sha256'], sha256(row['content'].encode()).hexdigest())
                actual[row['node_digest']] += row['content']
            if page['next_offset'] is None:
                break
            offset = page['next_offset']
        self.assertEqual(dict(actual), {key: canonical_json(node) for key, node in encoded['nodes'].items()})
        self.assertLess(calls, len(encoded['nodes']))
        before = reader.coverage()['read_chars']
        reader.page(offset=0, limit=24000)
        self.assertEqual(reader.coverage()['read_chars'], before)
        self.assertTrue(reader.coverage()['complete'])
        self.assertFalse(reader.coverage()['semantic_comprehension_verified'])
        for arguments in ({'offset': -1}, {'offset': True}, {'offset': 10**9}, {'limit': 0}, {'limit': 24001}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                reader.page(**arguments)


class ContextSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture = make_analysis_fixture(self.root)
        self.fixture = fixture
        self.store = fixture['store']
        self.store.append(Decision('large-history', 'task', 'note', 'A', 'Full source history',
                                   {'source': 'pinned-context-' * 42000}), 'large-history')
        snapshot = self.store.create_snapshot('task', 'large-context-snapshot')
        request = fixture['request']
        request['schema_version'] = 2
        request['environment'].update(design_batch_id='delivery-batch', design_round_id='delivery-round')
        request['strategies'] = request['strategies'][:1]
        request['strategies'][0]['snapshot_id'] = snapshot['id']
        self.manifest = await capture_inputs(self.store, fixture['knowledge'], AnalysisRequest.from_dict(request))

    async def test_default_session_reads_large_input_then_publishes_replayable_result(self):
        directory = self.root / 'session'
        packet_path = start_session(self.store, self.manifest, directory, model_label='test conversation')
        packet = json.loads(packet_path.read_text(encoding='utf-8'))
        self.assertIn('context_page', packet['tools'])
        offset, calls = 0, 0
        while True:
            response = directory / 'response.json'
            response.write_text(json.dumps({'tool_calls': [{'id': 'page-' + str(calls), 'name': 'context_page',
                                 'arguments': {'offset': offset, 'limit': 24000}}]}), encoding='utf-8')
            self.assertIsNone(submit_response(self.store, directory, response))
            page = json.loads(packet_path.read_text(encoding='utf-8'))['tool_results'][0]['result']
            calls += 1
            if page['next_offset'] is None:
                break
            offset = page['next_offset']
        self.assertGreater(calls, 12)
        response.write_text(json.dumps({'tool_calls': [{'id': 'coverage', 'name': 'context_coverage',
                                                      'arguments': {}}]}), encoding='utf-8')
        submit_response(self.store, directory, response)
        self.assertTrue(json.loads(packet_path.read_text(encoding='utf-8'))['tool_results'][0]['result']['complete'])
        answer = proposal()
        for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
            answer[field] = [row for row in answer[field] if row['strategy_id'] == 'A']
        response.write_text(json.dumps({'final': answer}), encoding='utf-8')
        result = submit_response(self.store, directory, response)
        replay = Analyzer(self.manifest, **result['limits'])
        for exchange in result['exchanges']:
            replay.advance(exchange)
        self.assertEqual(replay.result['tool_calls'], result['tool_calls'])

    async def test_explicit_session_limits_remain_enforced(self):
        directory = self.root / 'bounded'
        start_session(self.store, self.manifest, directory, model_label='bounded', max_turns=2, max_tool_calls=1)
        response = directory / 'response.json'
        for index in range(2):
            response.write_text(json.dumps({'tool_calls': [{'id': 'page-' + str(index), 'name': 'context_page',
                                'arguments': {'offset': index * 24000, 'limit': 24000}}]}), encoding='utf-8')
            if index == 0:
                submit_response(self.store, directory, response)
            else:
                with self.assertRaisesRegex(ValueError, 'budget'):
                    submit_response(self.store, directory, response)

    async def test_design_cli_persists_explicit_limits_and_rejects_excess_calls(self):
        from rsi_kernel_agent.workflow.design import begin_design_round
        assessments = {sid: {'status': 'hypothesis' if sid == 'A' else 'deferred',
                             'reason': 'Read isolated input', 'hypothesis': 'Tile' if sid == 'A' else None,
                             'refs': []} for sid in 'AB'}
        batch = await begin_design_round(self.store, self.fixture['knowledge'], 'task', 'context', 'cli-delivery',
                                         assessments, parents={'A': 'A-parent'})
        directory = self.root / 'cli'
        command = [sys.executable, '-B', '-m', 'rsi_kernel_agent.workflow', '--db', str(self.store.db_path),
                   '--artifacts', str(self.store.artifacts.root)]
        env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'PYTHONUTF8': '1'}
        started = subprocess.run([*command, 'design-route', '--batch', batch.id, '--strategy', 'A',
            '--session', str(directory), '--max-turns', '5', '--max-tool-calls', '7', '--max-response-chars', '12000'],
            env=env, capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(started.returncode, 0, started.stderr)
        state = json.loads((directory / 'session.json').read_text(encoding='utf-8'))
        self.assertEqual([state['options'][key] for key in ('max_turns', 'max_tool_calls', 'max_response_chars')],
                         [5, 7, 12000])
        response = directory / 'response.json'
        response.write_text(json.dumps({'tool_calls': [{'id': str(i), 'name': 'context_coverage', 'arguments': {}}
                                                       for i in range(8)]}), encoding='utf-8')
        rejected = subprocess.run([*command, 'respond', '--session', str(directory), '--response', str(response),
                                   '--key', 'excess'], env=env, capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn('budget', rejected.stderr)
        self.assertEqual(json.loads((directory / 'session.json').read_text(encoding='utf-8'))['responses'], [])
