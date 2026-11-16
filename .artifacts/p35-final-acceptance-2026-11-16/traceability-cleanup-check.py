"""Offline test-fixture cleanup audit; no production Store or remote calls."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPERS = ROOT / '.artifacts' / 'phase3-2026-11-12'
sys.path[:0] = [str(HELPERS), str(ROOT / 'src'), str(ROOT / 'tests')]
OUTPUT = Path(__file__).resolve().parent
PATHS = [
    'test_p35_diagnostic_dispatch.py',
    'test_resume_p35_diagnostic.py',
    'test_resume_p35_diagnostic_independent.py',
    'test_verify_p35_continuation.py',
    'test_verify_p35_continuation_independent.py',
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('tests', nargs='+')
    args = parser.parse_args()
    recovery = importlib.import_module('p35_recovery_round')
    original_gate = recovery.require_recovery_context
    original_cleanup = unittest.TestCase.doCleanups
    suppressed = []
    cleanup_count = 0

    def audited_cleanup(case):
        nonlocal cleanup_count
        cleanup_count += 1
        callback_count = len(case._cleanups)
        success = original_cleanup(case)
        if not success:
            suppressed.append({
                'test_id': case.id(),
                'callbacks_before': callback_count,
                'asyncio_runner_initialized': getattr(case, '_asyncioRunner', None) is not None,
                'has_own_outcome': case._outcome is not None,
            })
        return success

    unittest.TestCase.doCleanups = audited_cleanup
    started = time.monotonic()
    try:
        suite = unittest.defaultTestLoader.loadTestsFromNames(args.tests)
        with (OUTPUT / (args.label + '.log')).open('w', encoding='utf-8') as stream:
            result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    finally:
        unittest.TestCase.doCleanups = original_cleanup
    facts = {
        'tests': args.tests,
        'tests_run': result.testsRun,
        'failures': len(result.failures),
        'errors': len(result.errors),
        'skipped': len(result.skipped),
        'seconds': time.monotonic() - started,
        'cleanup_calls': cleanup_count,
        'cleanup_unsuccessful_returns': suppressed,
        'require_recovery_context_restored': recovery.require_recovery_context is original_gate,
        'test_file_sha256': {name: hashlib.sha256((HELPERS / name).read_bytes()).hexdigest() for name in PATHS},
    }
    facts['exit_code'] = 0 if result.wasSuccessful() and not suppressed and facts['require_recovery_context_restored'] else 1
    (OUTPUT / (args.label + '.json')).write_text(json.dumps(facts, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(facts, indent=2))
    return facts['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
