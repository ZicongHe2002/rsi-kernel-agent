"""Hand-calculated mathematical model checks; no hardware execution."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
try:
    from rsi_kernel_agent.profiler.models import calculate, ModelError
except ImportError:
    calculate = None
    ModelError = ValueError


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(calculate, 'Mathematical tools are not implemented')

    def test_compute_counts_work_and_compulsory_movement_without_inventing_rates(self):
        result = calculate('compute_memory', dict(m=2, n=3, k=4, dtype_bytes=2))
        self.assertEqual(result['outputs']['work_flops'], 48)
        self.assertEqual(result['outputs']['read_bytes'], 40)
        self.assertEqual(result['outputs']['write_bytes'], 12)
        self.assertEqual(result['outputs']['total_bytes'], 52)
        self.assertAlmostEqual(result['outputs']['arithmetic_intensity'], 12 / 13)
        self.assertIsNone(result['outputs']['resource_lower_bound_s'])
        self.assertIn('compute_bound_s', result['unavailable'])
        self.assertIn('memory_bound_s', result['unavailable'])

    def test_compute_uses_explicit_hardware_rates_and_marks_predictions(self):
        result = calculate('compute_memory', dict(m=2, n=3, k=4, dtype_bytes=2,
                           compute_rate_flops_s=48, bandwidth_bytes_s=26,
                           hardware_basis='Synthetic rates, not TPU specification'),
                           assumptions=('Illustrative throughput and bandwidth',),
                           evidence_refs=('source:A',))
        self.assertEqual(result['outputs']['compute_bound_s'], 1)
        self.assertEqual(result['outputs']['memory_bound_s'], 2)
        self.assertEqual(result['outputs']['resource_lower_bound_s'], 2)
        self.assertEqual(result['classification'], 'model_predicted')
        self.assertEqual(result['output_units']['resource_lower_bound_s'], 's')
        self.assertEqual(result['input_units']['compute_rate_flops_s'], 'FLOP/s')
        self.assertEqual(result['evidence_refs'], ['source:A'])

    def test_one_available_bound_is_explicitly_partial(self):
        result = calculate('compute_memory', dict(m=2, n=3, k=4, dtype_bytes=2,
                           bandwidth_bytes_s=26, hardware_basis='Assumed memory ceiling'),
                           assumptions=('Memory throughput is hypothetical',))
        self.assertEqual(result['outputs']['memory_bound_s'], 2)
        self.assertEqual(result['outputs']['resource_lower_bound_s'], 2)
        self.assertIn('compute_bound_s', result['unavailable'])

    def test_hardware_values_need_basis_and_reference_or_assumption(self):
        base = dict(m=2, n=3, k=4, dtype_bytes=2, compute_rate_flops_s=48)
        with self.assertRaises(ModelError):
            calculate('compute_memory', base)
        with self.assertRaises(ModelError):
            calculate('compute_memory', dict(base, hardware_basis='unattributed'))
        calculate('compute_memory', dict(base, hardware_basis='hardware specification'),
                  evidence_refs=('knowledge:hardware',))

    def test_tile_counts_padding_loop_iterations_and_conditional_reuse(self):
        result = calculate('tile', dict(m=5, n=7, k=3, tile_m=4, tile_n=4, tile_k=2, dtype_bytes=2))
        expected = dict(padded_m=8, padded_n=8, padded_k=4, output_tiles=4,
                        reduction_steps=2, tile_iterations=8, useful_flops=210,
                        padded_flops=512, padding_flops=302, tiled_read_bytes=256,
                        tiled_write_bytes=128, tiled_total_bytes=384,
                        unique_bytes=142, a_tile_reuse=4, b_tile_reuse=4)
        for field, value in expected.items():
            self.assertEqual(result['outputs'][field], value, field)
        self.assertAlmostEqual(result['outputs']['work_efficiency'], 210 / 512)
        self.assertIn('padding', ' '.join(result['assumptions']).lower())

    def test_tile_larger_than_problem_has_one_output_tile_and_nonzero_padding(self):
        result = calculate('tile', dict(m=2, n=3, k=1, tile_m=4, tile_n=4, tile_k=2, dtype_bytes=4))
        self.assertEqual(result['outputs']['tile_iterations'], 1)
        self.assertEqual(result['outputs']['useful_flops'], 12)
        self.assertEqual(result['outputs']['padded_flops'], 64)

    def test_live_memory_respects_half_open_lifetimes_and_buffer_copies(self):
        buffers = [dict(name='A', elements=10, dtype_bytes=2, copies=2, start=0, end=2),
                   dict(name='B', elements=5, dtype_bytes=4, copies=1, start=1, end=3),
                   dict(name='C', elements=10, dtype_bytes=8, copies=1, start=2, end=4)]
        result = calculate('live_memory', dict(buffers=buffers, capacity_bytes=100,
                           hardware_basis='Synthetic memory budget'), assumptions=('Capacity is illustrative',))
        self.assertEqual(result['outputs']['peak_bytes'], 100)
        self.assertEqual(result['outputs']['peak_step'], 2)
        self.assertEqual(result['outputs']['capacity_fraction'], 1)
        self.assertEqual(result['outputs']['headroom_bytes'], 0)
        self.assertEqual(result['input_units']['buffers[].start'], 'schedule_step')

    def test_live_memory_does_not_invent_capacity_or_merge_different_buffers(self):
        result = calculate('live_memory', dict(buffers=[
            dict(name='input', elements=4, dtype_bytes=4, copies=1, start=0, end=1),
            dict(name='output', elements=4, dtype_bytes=4, copies=1, start=0, end=1)]))
        self.assertEqual(result['outputs']['peak_bytes'], 32)
        self.assertIsNone(result['outputs']['capacity_fraction'])
        self.assertIn('capacity_fraction', result['unavailable'])

    def test_live_empty_schedule_is_zero_known_usage_and_unknown_capacity(self):
        result = calculate('live_memory', {'buffers': []})
        self.assertEqual(result['outputs']['peak_bytes'], 0)
        self.assertIsNone(result['outputs']['peak_step'])
        self.assertIsNone(result['outputs']['headroom_bytes'])

    def test_pipeline_includes_fill_drain_and_steady_state(self):
        result = calculate('pipeline', dict(stages=4, transfer_s=3, compute_s=2))
        self.assertEqual(result['outputs']['serial_s'], 20)
        self.assertEqual(result['outputs']['ideal_pipeline_s'], 14)
        self.assertEqual(result['outputs']['saved_s'], 6)
        self.assertAlmostEqual(result['outputs']['ideal_speedup'], 10 / 7)
        self.assertEqual(calculate('pipeline', dict(stages=1, transfer_s=3, compute_s=2))['outputs']['saved_s'], 0)

    def test_pipeline_zero_duration_does_not_fabricate_speedup(self):
        result = calculate('pipeline', dict(stages=4, transfer_s=0, compute_s=0))
        self.assertEqual(result['outputs']['ideal_pipeline_s'], 0)
        self.assertIsNone(result['outputs']['ideal_speedup'])
        self.assertIn('ideal_speedup', result['unavailable'])

    def test_invalid_fields_types_units_sizes_and_nonfinite_inputs_are_rejected(self):
        invalid = [
            ('compute_memory', dict(m=True, n=3, k=4, dtype_bytes=2)),
            ('compute_memory', dict(m=0, n=3, k=4, dtype_bytes=2)),
            ('compute_memory', dict(m=2, n=3, k=4, dtype_bytes=2, bandwidth_bytes_s=0)),
            ('compute_memory', dict(m=2, n=3, k=4, dtype_bytes=2, rate=10)),
            ('tile', dict(m=2, n=3, k=4, tile_m=1.5, tile_n=2, tile_k=2, dtype_bytes=2)),
            ('pipeline', dict(stages=0, transfer_s=3, compute_s=2)),
            ('pipeline', dict(stages=2, transfer_s=-1, compute_s=2)),
            ('pipeline', dict(stages=2, transfer_s=float('inf'), compute_s=2)),
            ('pipeline', dict(stages=2, transfer_s=float('nan'), compute_s=2)),
            ('pipeline', dict(stages=2, transfer_s=True, compute_s=2)),
            ('pipeline', dict(stages=2, transfer_ms=3, compute_s=2)),
            ('live_memory', dict(buffers=[dict(name='A', elements=1, dtype_bytes=1, copies=1, start=1, end=1)])),
            ('live_memory', dict(buffers=[dict(name='A', elements=1, dtype_bytes=1, copies=1, start=-1, end=1)])),
        ]
        for model, inputs in invalid:
            with self.subTest(model=model, inputs=inputs), self.assertRaises(ModelError):
                calculate(model, inputs)
        with self.assertRaises(ModelError):
            calculate('unknown', {})
        with self.assertRaises(ModelError):
            calculate('pipeline', dict(stages=2, transfer_s=1e308, compute_s=1e308))

    def test_metadata_is_reproducible_detached_and_json_finite(self):
        inputs = dict(stages=4, transfer_s=3, compute_s=2)
        result = calculate('pipeline', inputs, assumptions=['independent resources'], evidence_refs=['evidence:A'])
        replay = calculate(result['model'], result['inputs'], assumptions=['independent resources'],
                           evidence_refs=result['evidence_refs'])
        self.assertEqual(result, replay)
        for key in ('formula', 'model_revision', 'assumptions', 'omitted_costs', 'input_units', 'output_units'):
            self.assertTrue(result[key], key)
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)
        original = copy.deepcopy(inputs)
        result['inputs']['stages'] = 100
        self.assertEqual(inputs, original)
        for refs in ([''], [1], ['duplicate', 'duplicate'], 'evidence:A'):
            with self.subTest(refs=refs), self.assertRaises(ModelError):
                calculate('pipeline', inputs, evidence_refs=refs)


if __name__ == '__main__':
    unittest.main()
