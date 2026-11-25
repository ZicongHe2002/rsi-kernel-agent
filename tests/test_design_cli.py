import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from phase2b_fixtures import TemporaryKnowledgeSite
from phase2d_fixtures import make_workflow_fixture
from test_analyzer import proposal
from rsi_kernel_agent.contracts import Decision, Strategy, canonical_json
from rsi_kernel_agent.profiler.context import decode_context


class DesignCLITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = await make_workflow_fixture(self.root, intake=False)
        self.store = self.fixture['store']
        self.site = TemporaryKnowledgeSite().__enter__()
        self.addCleanup(self.site.__exit__, None, None, None)
        self.website = self.root / 'website'
        self.website.mkdir()
        (self.website / 'mcp_server.py').write_text(
            'import sys\nimport logging\nlogging.disable(logging.INFO)\n'
            f'sys.path.insert(0, {str(ROOT / "knowledge-site")!r})\n'
            'import mcp_server\n'
            f'mcp_server.local_headers = lambda root: {{"Authorization": "Bearer " + {self.site.server.local_token!r}}}\n'
            f'mcp_server.build_server({str(self.site.root)!r}, site_port={self.site.port}).run(transport="stdio")\n',
            encoding='utf-8')
        self.env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'PYTHONUTF8': '1'}
        self.command = [sys.executable, '-B', '-m', 'rsi_kernel_agent.workflow',
                        '--db', str(self.store.db_path), '--artifacts', str(self.store.artifacts.root)]

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding='utf-8')
        return str(path)

    def cli(self, *args, success=True, base=None):
        result = subprocess.run((base or self.command) + list(args), env=self.env,
                                capture_output=True, text=True, encoding='utf-8', timeout=90)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertEqual(result.returncode, 2, result.stderr)
        return result.stderr

    def begin(self):
        assessments = {sid: {'status': 'hypothesis' if sid in 'AB' else 'deferred',
                            'reason': 'Retain independent tile hypothesis' if sid in 'AB' else 'No exact source parent',
                            'hypothesis': 'Adjust tile' if sid in 'AB' else None, 'refs': []}
                       for sid in 'ABCDE'}
        return self.cli('design-begin', '--task', 'task', '--context', 'context', '--round-id', 'cli-design',
                        '--assessments', self.write('assessments.json', assessments),
                        '--parents', self.write('parents.json', {'A': 'A-parent', 'B': 'B-parent'}),
                        '--knowledge-refs', self.write('pins.json', {}), '--website', str(self.website))

    async def test_design_commands_drive_frozen_route_sessions_without_execution(self):
        batch = self.begin()
        self.assertEqual(set(batch['details']['assessments']), set('ABCDE'))
        self.assertEqual(self.begin()['id'], batch['id'])
        error = self.cli('design-complete', '--batch', batch['id'], success=False)
        self.assertIn('Every route', error)
        early_b = self.cli('design-route', '--batch', batch['id'], '--strategy', 'B',
                           '--session', str(self.root / 'B'), success=False)
        self.assertIn('previous route', early_b)
        snapshots = []
        for sid in 'AB':
            session = self.root / sid
            opened = self.cli('design-route', '--batch', batch['id'], '--strategy', sid, '--session', str(session))
            packet = json.loads(Path(opened['packet']).read_text(encoding='utf-8'))
            manifest = decode_context(packet['manifest'])
            self.assertEqual(set(manifest['strategies']), {sid})
            snapshots.append(manifest['request']['strategies'][0]['snapshot_id'])
            answer = proposal()
            for field in ('code_understanding', 'observations', 'hypotheses', 'plans'):
                answer[field] = [row for row in answer[field] if row['strategy_id'] == sid]
            answer['plans'] = answer['plans'][:1]
            if not answer['hypotheses']:
                answer['hypotheses'] = [{'id': 'h-' + sid, 'strategy_id': sid, 'text': 'Tile affects live state',
                                         'refs': ['source:' + sid], 'alternatives': ['Compute may dominate']}]
            response = self.cli('respond', '--session', str(session),
                                '--response', self.write(sid + '-response.json', {'final': answer}), '--key', 'cli-' + sid)
            closed = self.cli('design-finish', '--batch', batch['id'], '--strategy', sid,
                              '--publication', response['publication_id'])
            self.assertEqual(closed['details']['outcome']['status'], 'planned')
        self.assertEqual(snapshots[0], snapshots[1])
        complete = self.cli('design-complete', '--batch', batch['id'])
        self.assertEqual(len(complete['details']['plans']), 2)
        self.assertEqual(self.cli('design-complete', '--batch', batch['id']), complete)
        self.assertFalse(any(type(row) is Decision and row.details.get('type') == 'workflow_state.v1'
                             for row in self.store.history('task')))

    async def test_knowledge_search_uses_stdio_sources_and_does_not_create_experiment_store(self):
        (self.site.root / 'independent.md').write_text('# Independent buffers\nReuse staging slots.\n', encoding='utf-8')
        db = self.root / 'unused.sqlite'
        artifacts = self.root / 'unused-artifacts'
        command = [sys.executable, '-B', '-m', 'rsi_kernel_agent.workflow', '--db', str(db), '--artifacts', str(artifacts)]
        result = self.cli('knowledge-search', '--website', str(self.website),
                          '--queries', self.write('queries.json', ['buffers']), '--mode', 'exploratory',
                          '--max-reads', '2', base=command)
        self.assertEqual(result['availability']['external'], 'unavailable')
        self.assertEqual(result['documents'][0]['id'], 'independent.md')
        self.assertIn('Reuse staging slots.', result['documents'][0]['content'])
        self.assertTrue(result['documents'][0]['coverage']['complete'])
        self.assertFalse(db.exists())
        self.assertFalse(artifacts.exists())

    async def test_exploration_status_is_read_only_for_legacy_routes(self):
        before = self.store.history('task')
        result = self.cli('exploration-status', '--task', 'task')
        self.assertEqual(set(result['strategies']), set('ABCDE'))
        for status in result['strategies'].values():
            self.assertEqual(status['completed_effective_rounds'], 0)
            self.assertEqual(status['state'], 'legacy')
            self.assertFalse(status['protected'])
        self.assertEqual(self.store.history('task'), before)

    async def test_exploration_commands_preserve_admission_and_bounded_research(self):
        self.store.append(Strategy('new-D', 'task', 'Separate reduction lifecycle'), 'new-D')
        review = {'validation_status': 'validated_mechanism_review', 'route_action': 'fork',
                  'task_relation': 'new_to_task', 'strategy_id': 'new-D', 'structural_change': 'decomposition',
                  'mechanism_difference': 'Separate partial reduction lifetime from the final merge',
                  'code_refs': ['source:A-candidate'], 'coverage': {'complete': True},
                  'lineage': {'strategy_id': 'A', 'parent_candidate_id': 'A-candidate'},
                  'reviewed_strategy_ids': list('ABCDE'), 'admission_comparisons': []}
        ref = self.store.artifacts.put(canonical_json(review).encode())
        admission = {'strategy_id': 'new-D', 'initial_exploration_rounds': 7, 'policy_revision': 'cli-policy-v1',
                     'reason': 'Independent reduction question', 'mechanism_difference': review['mechanism_difference'],
                     'method_refs': [], 'parent_ids': ['A-parent'], 'review_ref': {'digest': ref.digest, 'size': ref.size}}
        spec = self.write('admit.json', admission)
        first = self.cli('exploration-admit', '--spec', spec)
        self.assertEqual(self.cli('exploration-admit', '--spec', spec), first)
        state = {'strategy_id': 'new-D', 'state': 'needs_research', 'reason': 'Read layout restrictions',
                 'evidence_refs': [], 'key': 'research-state'}
        self.cli('exploration-state', '--spec', self.write('state.json', state))
        attempt = {'strategy_id': 'new-D', 'round_id': 'r1', 'key': 'search-1', 'limit': 1}
        first_attempt = self.cli('exploration-research', '--spec', self.write('research.json', attempt))
        self.assertEqual(self.cli('exploration-research', '--spec', str(self.root / 'research.json')), first_attempt)
        attempt['key'] = 'search-2'
        self.assertIn('limit exhausted', self.cli('exploration-research',
                      '--spec', self.write('research-2.json', attempt), success=False))
        status = self.cli('exploration-status', '--task', 'task')['strategies']['new-D']
        self.assertEqual(status['remaining_rounds'], 7)
        self.assertEqual(status['state'], 'needs_research')
        self.assertFalse(any(type(row) is Decision and row.details.get('type') == 'workflow_state.v1'
                             for row in self.store.history('task')))

    async def test_feedback_cli_records_one_synthetic_round_idempotently(self):
        from rsi_kernel_agent.workflow.controller import Controller
        from rsi_kernel_agent.workflow.execution import SyntheticBackend
        from rsi_kernel_agent.workflow.intake import intake_publication
        intake = await intake_publication(self.store, self.fixture['publication'].id, 'feedback-r1',
                                          self.fixture['variants'], self.fixture['knowledge'])
        controller = Controller(self.store, 'task', SyntheticBackend(self.store, self.root / 'workspaces'))
        controller.accept_intake(intake)
        state = controller.run_until_idle()
        spec = {'input_snapshot_id': self.fixture['request']['strategies'][0]['snapshot_id'],
                'hypothesis': 'Change tile and evaluate the saved branch', 'change_kind': 'parameter',
                'job_ids': [j['id'] for j in state['jobs'].values() if j['strategy_id'] == 'A'],
                'next_step': 'Inspect shared tile live state', 'key': 'synthetic-feedback'}
        path = self.write('effective-feedback.json', spec)
        args = ('exploration-feedback', '--task', 'task', '--round-id', 'feedback-r1', '--strategy', 'A', '--spec', path)
        event = self.cli(*args)
        self.assertTrue(event['effective'])
        self.assertEqual(event['provenance'], 'synthetic')
        self.assertEqual(self.cli(*args), event)
        status = self.cli('exploration-status', '--task', 'task')['strategies']['A']
        self.assertEqual(status['synthetic_effective_rounds'], 1)
        self.assertEqual(status['completed_effective_rounds'], 0)
        self.assertEqual(controller.status()['spent'], state['spent'])

    async def test_feedback_cli_rejects_missing_feedback_and_nonobject_specs(self):
        spec = {'input_snapshot_id': self.fixture['request']['strategies'][0]['snapshot_id'],
                'hypothesis': 'A new tile changes live state', 'change_kind': 'parameter', 'job_ids': [],
                'next_step': 'Measure the tile', 'key': 'unsubmitted'}
        error = self.cli('exploration-feedback', '--task', 'task', '--round-id', 'r1', '--strategy', 'A',
                         '--spec', self.write('feedback.json', spec), success=False)
        self.assertIn('Distinct feedback jobs', error)
        error = self.cli('exploration-research', '--spec', self.write('bad.json', []), success=False)
        self.assertIn('object', error)


if __name__ == '__main__':
    unittest.main()
