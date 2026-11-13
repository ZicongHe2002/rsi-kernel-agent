"""Local preparation tests; never import JAX or open a live Store."""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / 'src')]
from rsi_kernel_agent.workflow.mla_baseline import workload_matrix
from rsi_kernel_agent.workflow.mla_runner import validate_candidate_config, validate_workload
from rsi_kernel_agent.workflow.scheduling import reserve, validate_limits


def inputs():
    workload = {'id': 'original-workload', 'task_id': 'task',
                'spec': {'kernel': 'mla_decode', 'interface': 'contiguous', 'cases': workload_matrix()}}
    context = {'id': 'original-context', 'task_id': 'task', 'workload_id': workload['id'],
               'environment': {'runtime_session_id': 'runtime-3'}, 'provenance': 'real',
               'protocol': {'backend_id': 'colab.v1', 'runner_digest': '1' * 64,
                            'oracle_digest': '2' * 64, 'oracle_bundle_ref': {'digest': '3' * 64, 'size': 99},
                            'trusted_files': {'tools/tpu_duration_profile.py': '4' * 64},
                            'correctness': {'atol': .01, 'rtol': .01},
                            'benchmark_case_id': 'decode-small',
                            'benchmark': {'version': 'frozen'}, 'profile': {'required': True}}}
    config = {'schema_version': 1, 'kernel': 'mla_decode', 'case_overrides': {}}
    sources = {
        'baseline': (HERE / 'candidates/baseline-repair-1/mla_decode.py').read_bytes(),
        'single_tile': (HERE / 'candidates/round1-single-tile/mla_decode.py').read_bytes(),
    }
    return workload, context, config, sources


class SingleTileDiagnosticTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module('p35_single_tile_diagnostic')
        except ImportError:
            self.fail('Single-tile diagnostic preparation helper is not implemented')

    def prepare(self, *, workload=None, context=None, configs=None, sources=None):
        w, c, config, raw = inputs()
        return self.module.prepare_diagnostic(
            workload if workload is not None else w,
            context if context is not None else c,
            configs if configs is not None else {'baseline': deepcopy(config), 'single_tile': deepcopy(config)},
            sources if sources is not None else raw)

    def test_transform_creates_independent_context_with_frozen_protocol_and_no_old_mutation(self):
        workload, context, config, sources = inputs()
        configs = {'baseline': deepcopy(config), 'single_tile': deepcopy(config)}
        before = deepcopy((workload, context, configs))
        result = self.module.prepare_diagnostic(workload, context, configs, sources)
        self.assertEqual((workload, context, configs), before)
        self.assertNotEqual(result['workload']['id'], workload['id'])
        self.assertNotEqual(result['context']['id'], context['id'])
        self.assertEqual(result['context']['workload_id'], result['workload']['id'])
        self.assertEqual(result['context']['task_id'], 'task')
        self.assertEqual(result['context']['protocol'], context['protocol'])
        self.assertEqual(result['context']['environment'], context['environment'])
        self.assertEqual(result['status'], 'prepared_only_requires_analyzer_intake')
        self.assertFalse(result['hardware_executed'])
        self.assertEqual(self.module.prepare_diagnostic(workload, context, configs, sources), result)

    def test_small_zero_and_boundary_inputs_legally_reach_actual_single_tile_predicate(self):
        result = self.prepare()
        spec = result['workload']['spec']
        self.assertEqual(validate_workload(spec), spec)
        self.assertEqual([c['id'] for c in spec['cases']], ['decode-small', 'decode-boundary'])
        small, boundary = spec['cases']
        self.assertEqual(small['lengths'], [0])
        self.assertEqual(small['overrides']['shape']['seq_len'], 1024)
        self.assertEqual(small['overrides']['tiling']['block_size'], 1024)
        self.assertEqual(boundary['lengths'], [0, 1, 129])
        self.assertEqual(boundary['overrides']['shape']['seq_len'], 256)
        self.assertEqual(boundary['overrides']['tiling']['block_size'], 256)
        self.assertTrue(small['opaque_tail_nan'])
        self.assertTrue(boundary['opaque_tail_nan'])
        for role, config in result['configs'].items():
            self.assertEqual(validate_candidate_config(config, spec), config)
            self.assertEqual(config['case_overrides']['decode-small']['tiling']['block_size'], 1024)
            self.assertEqual(config['case_overrides']['decode-boundary']['tiling']['block_size'], 256)
        self.assertTrue(result['branch_audit']['single_tile']['decode-small']['predicate_result'])
        self.assertTrue(result['branch_audit']['single_tile']['decode-boundary']['predicate_result'])
        self.assertEqual(result['branch_audit']['single_tile']['decode-small']['tile_count'], 1)
        self.assertEqual(result['output_contract']['tested_outputs'], ['attention', 'cache'])
        self.assertFalse(result['output_contract']['lse_tested'])
        self.assertEqual(result['output_contract']['lse_status'], 'not_exposed_by_frozen_interface')

    def test_illegal_lengths_shapes_or_old_identity_mismatch_are_rejected(self):
        for lengths in ([-1], [1025], [True], [], [0, 1]):
            workload, _, _, _ = inputs()
            workload['spec']['cases'][0]['lengths'] = lengths
            with self.subTest(lengths=lengths), self.assertRaises(ValueError):
                self.prepare(workload=workload)
        workload, _, _, _ = inputs()
        workload['spec']['cases'][1]['overrides']['shape']['seq_len'] = 128
        with self.assertRaises(ValueError):
            self.prepare(workload=workload)
        _, context, _, _ = inputs()
        context['workload_id'] = 'another-workload'
        with self.assertRaises(ValueError):
            self.prepare(context=context)

    def test_candidate_semantic_override_and_wrong_source_are_rejected(self):
        _, _, config, sources = inputs()
        configs = {'baseline': deepcopy(config), 'single_tile': deepcopy(config)}
        configs['single_tile']['case_overrides'] = {'decode-small': {'shape': {'batch': 9}}}
        with self.assertRaises(ValueError):
            self.prepare(configs=configs)
        sources['single_tile'] = sources['baseline']
        with self.assertRaises(ValueError):
            self.prepare(sources=sources)

    def test_original_scheduler_can_charge_two_diagnostic_intents_without_new_executor(self):
        result = self.prepare()
        self.assertEqual([intent['role'] for intent in result['run_intents']], ['baseline', 'single_tile'])
        self.assertEqual(result['budget']['cost_total'], {'executions': 2, 'tokens': 0, 'time_s': 3600.0})
        self.assertEqual(result['budget']['authority'], 'existing_task_controller')
        state = {'limits': validate_limits({'executions': 16, 'time_s': 28800}),
                 'spent': {'executions': 5, 'tokens': 0, 'time_s': 9000.0}}
        for intent in result['run_intents']:
            self.assertTrue(intent['requires_distinct_run'])
            self.assertNotIn('run_id', intent)
            self.assertNotIn('plan_id', intent)
            self.assertTrue(reserve(state, {'variant': {'cost': intent['cost']}, 'reserved': False}))
        self.assertEqual(state['spent'], {'executions': 7, 'tokens': 0, 'time_s': 12600.0})
        self.assertFalse(result['budget']['charged_by_preparation'])

    def test_transformed_packet_rejects_changed_lengths_opaque_tail_or_multitile_config(self):
        for key, value in (('lengths', [-1]), ('lengths', [1025]), ('lengths', [True]),
                           ('lengths', []), ('opaque_tail_nan', False)):
            result = self.prepare()
            result['workload']['spec']['cases'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.module.validate_diagnostic(result['workload']['spec'], result['configs'])
        result = self.prepare()
        result['configs']['single_tile']['case_overrides']['decode-boundary']['tiling']['block_size'] = 128
        with self.assertRaises(ValueError):
            self.module.validate_diagnostic(result['workload']['spec'], result['configs'])

    def test_resource_override_difference_cannot_confound_the_two_roles(self):
        _, _, config, _ = inputs()
        configs = {'baseline': deepcopy(config), 'single_tile': deepcopy(config)}
        configs['single_tile']['case_overrides'] = {'decode-small': {'params': {'vmem_limit_bytes': 100000000}}}
        with self.assertRaises(ValueError):
            self.prepare(configs=configs)

    def test_archived_original_job_and_configs_transform_without_store_access(self):
        job = json.loads((HERE / 'baseline-repair-runtime2-job.json').read_text('utf-8'))
        ref = job['variant']['config_ref']
        config_bytes = (HERE / 'artifacts' / 'sha256' / ref['digest'][:2] / ref['digest']).read_bytes()
        config = json.loads(config_bytes)
        _, _, _, sources = inputs()
        original = deepcopy(job)
        result = self.module.prepare_diagnostic(job['contract']['workload'], job['contract']['context'],
                                               {'baseline': deepcopy(config), 'single_tile': deepcopy(config)}, sources)
        self.assertEqual(job, original)
        self.assertEqual(result['context']['protocol'], job['contract']['context']['protocol'])
        self.assertEqual(result['context']['environment'], job['contract']['context']['environment'])
        self.assertNotEqual(result['workload']['id'], job['contract']['workload']['id'])
        self.assertEqual(result['workload']['spec']['cases'][0]['outputs'][1]['comparison'], 'exact')


if __name__ == '__main__':
    unittest.main(verbosity=2)
