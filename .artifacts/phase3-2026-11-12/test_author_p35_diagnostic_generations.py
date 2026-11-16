"""Run diagnostic author CLIs against temporary inputs, never the real Store."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / 'src')]
from p35_prepare_diagnostic_round import target_for
from real_round_aux import read_json, write_once


class DiagnosticAuthorGenerationTests(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name) / 'workspace' / '.artifacts' / 'phase3'
        directory.mkdir(parents=True)
        for name in ('author_p35_diagnostic_selection.py', 'author_p35_diagnostic_reads.py'):
            (directory / name).write_bytes((HERE / name).read_bytes())
        source = {'task_id': 'task', 'context_id': 'historical-context', 'knowledge': [{'method_id': 'method', 'revision': 1}]}
        write_once(directory / 'p35-confirmation-selection.json', source)
        return directory, source

    def run_script(self, directory, name, generation):
        args = [str(directory / name)]
        if generation is not None:
            args += ['--generation', generation]
        with patch.object(sys, 'argv', args), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            runpy.run_path(args[0], run_name='__main__')

    def selection(self, generation):
        return {'schema_version': 2, 'runtime_generation': generation, 'task_id': 'task',
                'context_id': 'context-' + generation, 'strategy_id': 'A',
                'round_id': 'p35-' + generation + '-single-tile-diagnostic:1', 'knowledge': []}

    def test_selection_accepts_generations_preserves_admitted_identity_and_write_once(self):
        for generation in ('runtime4', 'runtime5', 'runtime7', 'runtime12'):
            with self.subTest(generation=generation):
                directory, source = self.fixture()
                baseline = {'task_id': 'new-task', 'context_id': 'context-' + generation, 'strategy_id': 'A'}
                write_once(directory / ('baseline-repair-' + generation + '-job.json'), baseline)
                self.run_script(directory, 'author_p35_diagnostic_selection.py', generation)
                path = directory / ('p35-single-tile-selection-input-' + generation + '.json')
                expected = {**self.selection(generation), **baseline, 'knowledge': source['knowledge']}
                self.assertEqual(read_json(path), expected)
                raw = path.read_bytes()
                self.run_script(directory, 'author_p35_diagnostic_selection.py', generation)
                self.assertEqual(path.read_bytes(), raw)
                changed = {**baseline, 'context_id': 'changed-context'}
                (directory / ('baseline-repair-' + generation + '-job.json')).write_text(json.dumps(changed), encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.run_script(directory, 'author_p35_diagnostic_selection.py', generation)
                self.assertEqual(path.read_bytes(), raw)

    def test_omitted_generation_preserves_original_runtime3_selection(self):
        directory, source = self.fixture()
        self.run_script(directory, 'author_p35_diagnostic_selection.py', None)
        selected = read_json(directory / 'p35-single-tile-selection-input.json')
        self.assertEqual(selected['schema_version'], 1)
        self.assertNotIn('runtime_generation', selected)
        self.assertEqual(selected['context_id'], source['context_id'])
        self.assertEqual(selected['round_id'], 'p35-runtime3-single-tile-diagnostic:1')

    def test_reads_use_exact_generation_session_and_do_not_duplicate_response(self):
        for generation in ('runtime4', 'runtime5', 'runtime7', 'runtime12'):
            with self.subTest(generation=generation):
                directory, _ = self.fixture()
                selection = self.selection(generation)
                write_once(directory / ('p35-single-tile-selection-input-' + generation + '.json'), selection)
                target = target_for(directory, selection)
                target.mkdir()
                write_once(target / 'packet.json', {'tool_results': [], 'remaining': {}})
                write_once(target / 'session.json', {'responses': []})
                with patch('rsi_kernel_agent.store.ExperimentStore') as store, patch('rsi_kernel_agent.profiler.session.submit_response') as submit:
                    self.run_script(directory, 'author_p35_diagnostic_reads.py', generation)
                    response_path = target / 'response-1-read-inputs.json'
                    submit.assert_called_once_with(store.return_value, target, response_path)
                    response = read_json(response_path)
                    self.assertEqual([row['name'] for row in response['tool_calls']], ['source', 'history', 'knowledge', 'knowledge', 'knowledge'])
                    self.assertTrue(all(row['arguments']['strategy_id'] == 'A' for row in response['tool_calls'][:2]))
                    (target / 'session.json').write_text(json.dumps({'responses': [response]}), encoding='utf-8')
                    submit.reset_mock()
                    self.run_script(directory, 'author_p35_diagnostic_reads.py', generation)
                    submit.assert_not_called()

    def test_invalid_generation_is_rejected_before_any_input_or_store_access(self):
        for name in ('author_p35_diagnostic_selection.py', 'author_p35_diagnostic_reads.py'):
            for value in ('runtime3', 'runtime07', 'Runtime7', '../runtime7', 'runtime7/8', 'runtime0'):
                with self.subTest(script=name, generation=value):
                    directory, _ = self.fixture()
                    with patch('real_round_aux.read_json') as read, patch('rsi_kernel_agent.store.ExperimentStore') as store:
                        with self.assertRaises(SystemExit) as error:
                            self.run_script(directory, name, value)
                        self.assertEqual(error.exception.code, 2)
                        read.assert_not_called()
                        store.assert_not_called()


if __name__ == '__main__':
    unittest.main()
