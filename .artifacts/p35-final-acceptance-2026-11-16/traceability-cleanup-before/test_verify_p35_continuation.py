"""Read-only continuation completion binding; local protocol fixtures only."""
from copy import deepcopy
from contextlib import closing
import importlib
import json
import os
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_p35_continuation_round as chain
from rsi_kernel_agent.contracts import canonical_json


class ContinuationVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fx = chain.ContinuationTests()
        await self.fx.asyncSetUp()
        self.addCleanup(self.fx.doCleanups)
        self.api = importlib.import_module('verify_p35_continuation')
        original = self.api.TransactionStore
        reader = patch.object(self.api, 'TransactionStore', side_effect=lambda db, artifacts:
                             original(self.fx.store.db_path, artifacts))
        reader.start(); self.addCleanup(reader.stop)

    async def finish(self):
        variants = await self.fx.author()
        await self.fx.api.run(self.fx.store, self.fx.root, self.fx.spec, variants, None,
                             chain.FixtureBackend(self.fx.fx), steps=30)
        return self.fx.api.target_for(self.fx.root, self.fx.spec)

    async def test_prepared_but_unstarted_is_pending_and_does_not_invent_results(self):
        await self.fx.prepare()
        before = len(self.fx.store.history('task'))
        result = self.api.verify_continuation(self.fx.root, self.fx.spec, recompute_npz=False)
        self.assertEqual(result['status'], 'pending_execution')
        self.assertEqual(result['jobs'], [])
        self.assertEqual(len(self.fx.store.history('task')), before)

    async def test_completed_snapshot_and_each_original_job_are_verified_without_writes(self):
        target = await self.finish()
        before = len(self.fx.store.history('task'))
        seen = []
        # The CPU fixture does not contain production raw NPZ arrays; the common
        # verifier's actual device artifact checks have their own fixture suite.
        def verify(store, job, **kwargs):
            self.assertFalse(hasattr(store, 'append'))
            seen.append(job['run_id'])
            return {'id': job['id'], 'run_id': job['run_id'], 'verification': 'verified'}
        with patch.object(self.api, 'verify_job', side_effect=verify):
            result = self.api.verify_continuation(self.fx.root, self.fx.spec, recompute_npz=True)
        self.assertEqual(result['status'], 'selected_terminal_jobs_verified')
        self.assertEqual(len(set(seen)), 2)
        self.assertEqual(result['counts']['verified'], 2)
        self.assertEqual(result['snapshot'], json.loads((target/'completed.json').read_text())['snapshot'])
        self.assertEqual(len(self.fx.store.history('task')), before)

    async def test_changed_completion_or_frozen_input_is_rejected_before_artifact_verification(self):
        target = await self.finish()
        for name, mutate in (
            ('completed.json', lambda body: body.update(grant_key='different')),
            ('execution-input.json', lambda body: body.update(result_digest='0'*64)),
            ('intake.json', lambda body: body['items'].pop()),
        ):
            path = target/name
            original = path.read_bytes()
            body = json.loads(original); mutate(body)
            path.write_text(canonical_json(body), encoding='utf-8')
            try:
                with self.subTest(name=name),patch.object(self.api,'verify_job') as verify,self.assertRaises(ValueError):
                    self.api.verify_continuation(self.fx.root,self.fx.spec)
                verify.assert_not_called()
            finally:
                path.write_bytes(original)

    async def test_common_verifier_rechecks_actual_protocol_receipts_and_saved_samples(self):
        await self.finish()
        before = len(self.fx.store.history('task'))
        report = self.api.verify_continuation(self.fx.root, self.fx.spec, recompute_npz=False)
        self.assertEqual(report['status'], 'selected_terminal_jobs_verified')
        self.assertEqual(report['counts']['verified'], 2)
        self.assertTrue(all(r['benchmark_statistics']['median_ms'] == 3.0
                            and r['all_stage_artifacts_reachable_from_evaluation'] is True
                            and r['npz_comparison_status'] == 'disabled_raw_hashes_and_receipt_only'
                            for r in report['jobs']))
        self.assertEqual(len(self.fx.store.history('task')), before)

    async def test_cli_returns_failure_for_changed_archived_stage_artifact(self):
        target = await self.finish()
        state = self.fx.base.c.journal.load()
        job = next(j for j in state['jobs'].values() if j['round_id'] == self.fx.spec['round_id'])
        from rsi_kernel_agent.contracts import ArtifactRef
        artifact = self.fx.store.artifacts.path_for(ArtifactRef(**job['result']['receipt_refs']['compile']))
        artifact.write_bytes(b'changed archived stage bytes')
        with closing(sqlite3.connect(self.fx.store.db_path)) as source, closing(sqlite3.connect(self.fx.root/'experiments.sqlite3')) as dest:
            source.backup(dest)
        gates = self.fx.root/'fixture-gates.json'
        gates.write_text(canonical_json({'baseline-repair': self.fx.previous,
                                        'baseline-b': self.fx.previous}), encoding='utf-8')
        script = '''import json,runpy,sys
from pathlib import Path
from unittest.mock import patch
import p35_continuation_round as chain
directory,spec,gates_file=sys.argv[1:]
gates=json.loads(Path(gates_file).read_text(encoding="utf-8"))
sys.argv=["verify_p35_continuation.py","--directory",directory,"--spec",spec,"--no-npz"]
with patch.object(chain,"require_gates",return_value=(gates,{})), patch.object(chain,"validate_p35_milestone"), patch.object(chain,"validate_b2_variants"):
    runpy.run_path(str(Path(chain.__file__).with_name("verify_p35_continuation.py")),run_name="__main__")
'''
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)}
        process = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script,
            str(self.fx.root), str(target/'spec.json'), str(gates)], env=env,
            capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(process.returncode, 1, process.stdout+process.stderr)
        outcome = json.loads(process.stdout)
        self.assertEqual(outcome['status'], 'verification_failed')
        saved = json.loads(__import__('pathlib').Path(outcome['report']).read_text(encoding='utf-8'))
        self.assertEqual(saved['counts']['failed_verification'], 1)


if __name__ == '__main__': unittest.main(verbosity=2)
