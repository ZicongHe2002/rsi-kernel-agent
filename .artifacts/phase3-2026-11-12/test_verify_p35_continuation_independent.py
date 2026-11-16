"""Independent local regression for immutable continuation intake binding."""
import json
import unittest
from unittest.mock import patch

import test_verify_p35_continuation as fixtures
from rsi_kernel_agent.contracts import canonical_json


class IndependentIntakeBindingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fx = fixtures.ContinuationVerificationTests()
        self.fx.addCleanup = self.addCleanup
        await self.fx.asyncSetUp()

    async def test_changed_valid_variant_is_rejected_before_verification(self):
        target = await self.fx.finish()
        path = target / 'execution-input.json'
        inputs = json.loads(path.read_text(encoding='utf-8'))
        first = next(iter(inputs['variants'].values()))[0]
        first['priority'] = first.get('priority', 0) + 1
        path.write_text(canonical_json(inputs), encoding='utf-8')
        def verified(_store, job, **_kwargs):
            return {'id': job['id'], 'run_id': job['run_id'], 'verification': 'verified'}
        with patch.object(self.fx.api, 'verify_job', side_effect=verified) as verify:
            with self.assertRaises(ValueError):
                self.fx.api.verify_continuation(self.fx.fx.root, self.fx.fx.spec)
            verify.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
