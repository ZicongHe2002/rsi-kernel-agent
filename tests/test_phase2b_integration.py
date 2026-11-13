"""Real stdio MCP -> existing HTTP handler -> methods + Phase 2A SQLite.

Synthetic fixture data only; no .auth files, live notes, external CLI or TPU.
"""
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from phase2b_fixtures import TemporaryKnowledgeSite
from rsi_kernel_agent.associations import MemoryWriter
from rsi_kernel_agent.knowledge import extract_method
from rsi_kernel_agent.memory import MemoryReader
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.contracts import (
    Candidate, CandidateIdentity, Decision, EvaluationBundle, EvaluationContext,
    Evidence, ParentCandidate, Plan, Run, RunEvent, Strategy, Task, Workload,
)


def identity(name):
    return CandidateIdentity(*(sha256((name + part).encode()).hexdigest() for part in ('src', 'cfg', 'build')))


class Phase2BIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_strategy_round_reconstructs_methods_attempts_and_frozen_history(self):
        with TemporaryKnowledgeSite() as site:
            for title, body in (
                ('Pipelining', '# Synthetic source\n## Overlap\nOverlap independent buffer transfers; use more memory.\n'),
                ('Tiling', '# Synthetic source\n## Reuse\nReuse local tiles; boundary padding costs extra work.\n'),
                ('Layout', '# Synthetic source\n## Contiguity\nArrange contiguous accesses; conversion costs extra work.\n'),
            ):
                site.server.store.save({'title': title, 'category': 'Research', 'content': body}, create=True)
            db = site.workspace / 'experiments.sqlite'
            artifacts = site.workspace / 'artifacts'
            store = ExperimentStore(db, artifacts)
            writer = MemoryWriter(store)

            def add(record):
                store.append(record, 'append:' + record.id)
                return record

            for record in (Task('task', 'Synthetic 2B integration', 'v1'),
                           Workload('workload', 'task', {'shape': [4, 8]}),
                           EvaluationContext('context', 'task', 'workload', {'device': 'synthetic'},
                                             {'timer': 'fixture-ms'}, 'synthetic'),
                           ParentCandidate('baseline', 'task', identity('baseline'))):
                add(record)
            for route in 'ABCDE':
                add(Strategy(route, 'task', 'Route ' + route))
                if route in 'AB':
                    for index in '12':
                        plan = add(Plan(route + index, route, 'Synthetic exploration ' + index))
                        writer.bind_plan(plan.id, 'baseline', 'bind:' + plan.id)
                        for variant in '12':
                            add(Candidate(plan.id + variant, plan.id, 'baseline', identity(plan.id + variant)))

            def attempt(name, candidate, result, timing=None):
                run = add(Run(name, candidate, 'context'))
                store.transition(RunEvent(name + ':start', name, 'running', 'active', 'synthetic'), None, name + ':start')
                store.transition(RunEvent(name + ':end', name, result, 'finished', 'synthetic'), name + ':start', name + ':end')
                if timing is not None:
                    artifact = store.artifacts.put(json.dumps({'synthetic_latency_ms': timing}).encode())
                    evidence = add(Evidence(name + ':timing', name, 'measured_timing', 'available', 'completed',
                                            'supported', timing, 'ms', (artifact,), (), {'provenance': 'synthetic'}))
                    evaluation = add(EvaluationBundle(name + ':eval', name, 'passed', 'valid', timing, True, (evidence.id,)))
                    store.consider(evaluation.id, 'consider:' + evaluation.id)
                return run

            attempt('A-success', 'A11', 'succeeded', 10)
            attempt('A-failure', 'A12', 'failed')
            attempt('B-success', 'B11', 'succeeded', 12)
            attempt('B-invalid', 'B12', 'invalid')
            add(Decision('defer-B22', 'task', 'deferred', 'B22', 'Synthetic budget exhausted'))
            add(Decision('pause-E', 'task', 'paused', 'E', 'Synthetic pause'))
            store.set_frontier('B21', 'context', True, 'Keep B exploring', 'frontier-B')
            writer.select_parent('A', 'context', 'baseline', 'Explicit baseline', 'parent-A')
            writer.select_parent('B', 'context', 'baseline', 'Explicit baseline', 'parent-B')
            links = []
            async with site.connect() as knowledge:
                discovered = await knowledge.discover(('transfers', 'tiles', 'contiguous'))
                self.assertEqual(len(discovered['documents']), 3)
                for plans, title, heading, mechanism in (
                    (('A1', 'A2'), 'Pipelining', 'Overlap', 'overlap'),
                    (('B1', 'B2'), 'Tiling', 'Reuse', 'reuse'),
                    (('B2',), 'Layout', 'Contiguity', 'layout'),
                ):
                    source = await knowledge.fetch_document('Research/' + title + '.md', page_size=13)
                    method = extract_method(source, heading, hardware=('synthetic CPU',), workloads=('matrix',), mechanism=mechanism)
                    saved = await knowledge.save_method(method)
                    for plan in plans:
                        links.append(writer.link_method(method, saved['id'], saved['revision'], plan,
                                                        'baseline', 'Explore source mechanism', 'method-link:' + title + ':' + plan))
                    self.assertEqual((await knowledge.save_method(method))['revision'], saved['revision'])
                    self.assertEqual((await knowledge.get_method(method.method_id)).validation_status, 'source_supported')

                snapshot = store.create_snapshot('task', 'round:1')
                view = MemoryReader(store).open_snapshot(snapshot['id'], page_size=4)
                cards = view.status_cards('context')
                self.assertEqual({c['strategy_id'] for c in cards}, set('ABCDE'))
                by_strategy = {c['strategy_id']: c for c in cards}
                self.assertEqual(by_strategy['A']['branch_best']['candidate_id'], 'A11')
                self.assertEqual(by_strategy['B']['branch_best']['candidate_id'], 'B11')
                self.assertEqual(by_strategy['B']['task_best']['candidate_id'], 'A11')
                self.assertFalse(by_strategy['B']['paused'])
                self.assertTrue(by_strategy['E']['paused'])
                self.assertIn('B22', {x['record_id'] for x in by_strategy['B']['deferred']})
                histories = view.histories(('A', 'B'))
                for route in 'AB':
                    history = histories[route]
                    self.assertTrue(history['complete'])
                    self.assertEqual(len(history['records']), history['total'])
                    owned = set(history['owned_ids'])
                    self.assertTrue({route + '1', route + '2', route + '11', route + '12', route + '21', route + '22'} <= owned)
                    self.assertFalse({('B' if route == 'A' else 'A') + suffix for suffix in ('1', '2', '11', '22')} & owned)
                    method_links = [r for r in history['records'] if r['payload'].get('details', {}).get('type') == 'knowledge_link.v1']
                    self.assertEqual(len(method_links), 2 if route == 'A' else 3)
                    self.assertTrue(all(r['payload']['details']['method_snapshot']['validation_status'] == 'source_supported' for r in method_links))
                self.assertIn('A-failure', histories['A']['owned_ids'])
                self.assertIn('B-invalid', histories['B']['owned_ids'])
                self.assertIn('defer-B22', histories['B']['owned_ids'])

                # Editing the knowledge note later cannot rewrite the captured decision.
                last = links[-1]
                method = await knowledge.get_method(last.details['method_id'])
                await knowledge.save_method(replace(method, known_risks=('New human finding',)),
                                            expected_revision=last.details['method_note_revision'])
                self.assertEqual(store.get(last.id).details['method_snapshot'], last.details['method_snapshot'])
                self.assertEqual(view.histories(('A', 'B')), histories)

            code = (
                'import json,sys\nfrom rsi_kernel_agent.store import ExperimentStore\n'
                'from rsi_kernel_agent.memory import MemoryReader\n'
                'view=MemoryReader(ExperimentStore(sys.argv[1],sys.argv[2])).open_snapshot(sys.argv[3],page_size=3)\n'
                'print(json.dumps(view.histories(("A","B")),sort_keys=True))\n'
            )
            process = subprocess.run([sys.executable, '-B', '-c', code, str(db), str(artifacts), snapshot['id']],
                                     capture_output=True, text=True, timeout=30,
                                     env=dict(os.environ, PYTHONPATH=str(ROOT / 'src'), PYTHONIOENCODING='utf-8'))
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout), histories)
            self.assertFalse((site.workspace / '.auth').exists())
            example_path = os.environ.get('RSI_PHASE2B_EXAMPLE_OUTPUT')
            if example_path:
                example = {'provenance': 'synthetic', 'snapshot': snapshot, 'status_cards': cards,
                           'histories': histories, 'knowledge_link_example': links[0].details}
                Path(example_path).write_text(json.dumps(example, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
