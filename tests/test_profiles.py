"""Synthetic saved-report fixtures, never TPU measurements."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rsi_kernel_agent.contracts import ValidationError
from rsi_kernel_agent.profiler.profiles import (
    normalize_duration_report, normalize_events, parse_offline,
)


def profile_binding():
    return {
        'strategy_id': 'A', 'parent_id': 'baseline', 'candidate_id': 'A1',
        'run_id': 'run-A', 'context_id': 'ctx', 'provenance': 'synthetic',
        'declared_identity': {'source_digest': 'a' * 64, 'config_digest': 'b' * 64,
                              'build_digest': 'c' * 64},
    }


def event(event_id='p', *, kind='program', start=0, duration=100, device='d0',
          clock='device', classification='trace_derived'):
    return {'id': event_id, 'name': event_id, 'kind': kind, 'device': device,
            'clock': clock, 'classification': classification, 'start_ps': start,
            'duration_ps': duration, 'bytes': None, 'source_refs': ['artifact:trace'],
            'reason': None, 'metadata': {}}


def duration_report():
    groups = []
    for kind, line, line_id, durations in (
        ('program', 'XLA Modules', 1, [100_000_000, 120_000_000]),
        ('kernel', 'XLA Ops', 2, [80_000_000, 100_000_000]),
    ):
        groups.append({
            'kind': kind, 'name': 'jit_example(123)' if kind == 'program' else '%x = custom-call()',
            'plane': '/device:TPU:0', 'line': line, 'line_id': line_id, 'program_id': 123,
            'count': 2, 'mean_us': 110 if kind == 'program' else 90,
            'median_us': 110 if kind == 'program' else 90,
            'p95_us': 119 if kind == 'program' else 99,
            'min_us': 100 if kind == 'program' else 80,
            'max_us': 120 if kind == 'program' else 100,
            'events_per_measured_call': 1,
            'samples': [{'duration_ps': value, 'device_offset_ps': i * 200_000_000,
                         'step_num': i, 'run_id': i + 1} for i, value in enumerate(durations)],
            'invalid_samples': 0,
        })
    return {'time_source': 'TPU XPlane device_duration_ps', 'unit': 'us',
            'profile': 'synthetic.xplane.pb', 'calls': 2, 'devices': ['/device:TPU:0'],
            'groups': groups, 'longest_device_per_call': [],
            'multi_device_span': {'status': 'unavailable', 'reason': 'Alignment unverified'},
            'errors': [], 'warnings': []}


class ProfileTests(unittest.TestCase):
    def test_legacy_samples_recompute_statistics_and_keep_scopes_separate(self):
        result = normalize_duration_report(duration_report(), binding=profile_binding(),
                                           artifact_refs=[{'digest': 'd' * 64, 'size': 10}])
        self.assertEqual(len(result['events']), 4)
        self.assertEqual([row['mean_us'] for row in result['operation_stats']], [110, 90])
        self.assertEqual(result['interval_unions'][0]['duration_ps'], 220_000_000)
        self.assertEqual(result['binding']['provenance'], 'synthetic')
        self.assertEqual(result['binding_qualification'], 'declared_identity_only')
        self.assertEqual(result['multi_device_span']['value'], None)
        for key in ('dma', 'waits', 'compiler_metadata', 'resource_metadata', 'llo'):
            self.assertEqual(result['capabilities'][key]['availability'], 'unavailable')

    def test_missing_offsets_do_not_become_zero_or_complete_intervals(self):
        report = duration_report()
        report['groups'][0]['samples'][0]['device_offset_ps'] = None
        result = normalize_duration_report(report, binding=profile_binding(), artifact_refs=[])
        self.assertIsNone(result['events'][0]['start_ps'])
        self.assertEqual(result['interval_unions'][0]['availability'], 'partial')
        self.assertIn('original_artifacts_unavailable', result['quality']['issues'])

    def test_malformed_duration_and_inconsistent_legacy_summary_are_rejected(self):
        for bad in (0, -1, True, None, float('nan'), float('inf'), '12'):
            with self.subTest(bad=bad):
                report = duration_report()
                report['groups'][1]['samples'][0]['duration_ps'] = bad
                with self.assertRaises(ValidationError):
                    normalize_duration_report(report, binding=profile_binding(), artifact_refs=[])
        report = duration_report()
        report['groups'][0]['mean_us'] = 999
        with self.assertRaises(ValidationError):
            normalize_duration_report(report, binding=profile_binding(), artifact_refs=[])

    def test_binding_required_and_detached_from_caller(self):
        binding = profile_binding()
        with self.assertRaises(ValidationError):
            normalize_events([], binding={k: v for k, v in binding.items() if k != 'run_id'}, artifact_refs=[])
        with self.assertRaises(ValidationError):
            normalize_events([], binding={**binding, 'declared_identity': {}}, artifact_refs=[])
        result = normalize_events([event()], binding=binding, artifact_refs=[])
        binding['declared_identity']['source_digest'] = 'e' * 64
        self.assertEqual(result['binding']['declared_identity']['source_digest'], 'a' * 64)

    def test_nested_overlap_union_and_clock_domains_are_independent(self):
        events = [event(), event('child', duration=60), event('overlap', start=80, duration=50),
                  event('second-device', duration=500, device='d1'),
                  event('other-clock', duration=700, clock='host')]
        result = normalize_events(events, binding=profile_binding(), artifact_refs=[])
        values = {(item['device'], item['clock']): item['duration_ps'] for item in result['interval_unions']}
        self.assertEqual(values, {('d0', 'device'): 130, ('d1', 'device'): 500, ('d0', 'host'): 700})
        self.assertIsNone(result['multi_device_span']['value'])

    def test_estimated_intervals_never_join_measured_intervals(self):
        result = normalize_events([event(classification='measured'),
                                   event('guess', duration=500, classification='inferred')],
                                  binding=profile_binding(), artifact_refs=[])
        self.assertEqual(len(result['interval_unions']), 2)
        self.assertEqual({row['classification'] for row in result['interval_unions']},
                         {'trace_derived', 'inferred'})

    def test_high_dma_count_does_not_claim_critical_path_or_known_bytes(self):
        events = [event(str(i), kind='dma', start=i, duration=10) for i in range(20)]
        result = normalize_events(events, binding=profile_binding(), artifact_refs=[])
        self.assertEqual(result['capabilities']['dma']['event_count'], 20)
        self.assertIsNone(result['critical_path']['value'])
        self.assertTrue(all(item['bytes'] is None for item in result['events']))

    def test_missing_metrics_remain_null_with_reason(self):
        item = event(duration=None, start=None)
        item['reason'] = 'Capture omitted timing'
        result = normalize_events([item], binding=profile_binding(), artifact_refs=[])
        self.assertIsNone(result['operation_stats'][0]['mean_us'])
        self.assertIsNone(result['interval_unions'][0]['duration_ps'])
        self.assertEqual(result['interval_unions'][0]['availability'], 'unavailable')
        item['reason'] = None
        with self.assertRaises(ValidationError):
            normalize_events([item], binding=profile_binding(), artifact_refs=[])

    def test_explicit_compiler_resource_llo_metadata_keeps_classification(self):
        item = event('compile')
        item['metadata'] = {
            'resource_metadata': {'value': {'vmem_bytes': 2048}, 'classification': 'compiler_estimated',
                                  'source_refs': ['compiler:fixture']},
            'llo': {'value': 'synthetic LLO text', 'classification': 'trace_derived',
                    'source_refs': ['compiler:fixture']},
        }
        result = normalize_events([item], binding=profile_binding(), artifact_refs=[])
        resource = result['metadata']['resource_metadata'][0]
        self.assertEqual(resource['classification'], 'compiler_estimated')
        self.assertEqual(resource['value']['vmem_bytes'], 2048)
        self.assertEqual(result['capabilities']['llo']['availability'], 'available')

    def test_quality_errors_and_invalid_counts_survive_normalization(self):
        report = duration_report()
        report['errors'] = ['Incomplete capture']
        report['warnings'] = ['No timestamp alignment']
        report['groups'][0]['invalid_samples'] = 2
        result = normalize_duration_report(report, binding=profile_binding(), artifact_refs=[])
        self.assertEqual(result['quality']['availability'], 'partial')
        self.assertIn('Incomplete capture', result['quality']['errors'])
        self.assertEqual(result['operation_stats'][0]['invalid_samples'], 2)

    def test_malformed_ids_kinds_artifacts_and_nonfinite_data_rejected(self):
        for change in ({'kind': 'invented'}, {'duration_ps': True}, {'bytes': -1},
                       {'clock': ''}, {'extra': 1}, {'source_refs': []}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                normalize_events([{**event(), **change}], binding=profile_binding(), artifact_refs=[])
        with self.assertRaises(ValidationError):
            normalize_events([event(), event()], binding=profile_binding(), artifact_refs=[])
        with self.assertRaises(ValidationError):
            normalize_events([], binding=profile_binding(), artifact_refs=[{'digest': 'd' * 64, 'size': True}])

    def test_legacy_no_groups_is_explicitly_unavailable(self):
        report = duration_report()
        report['groups'] = []
        report['errors'] = ['No valid custom-call device samples']
        result = normalize_duration_report(report, binding=profile_binding(), artifact_refs=[])
        self.assertEqual(result['quality']['availability'], 'unavailable')
        self.assertEqual(result['events'], [])

    def test_injected_offline_reader_reports_missing_dependencies_without_install(self):
        def unavailable(directory, **kwargs):
            raise ModuleNotFoundError('tensorflow')
        result = parse_offline(unavailable, Path('.'), calls=2, device_ids=[0], program_name='example',
                               binding=profile_binding(), artifact_refs=[])
        self.assertEqual(result['quality']['availability'], 'unavailable')
        self.assertTrue(any('dependency' in issue for issue in result['quality']['issues']))

    def test_timing_capability_requires_actual_intervals(self):
        item = event(start=None, duration=None)
        item['reason'] = 'Unavailable timing'
        result = normalize_events([item], binding=profile_binding(), artifact_refs=[])
        self.assertEqual(result['capabilities']['execution_intervals']['availability'], 'unavailable')

    def test_malformed_container_types_and_huge_numbers_raise_validation_error(self):
        for change in ({'kind': []}, {'classification': {}}, {'duration_ps': 10 ** 1000}):
            with self.subTest(change=str(change)[:40]), self.assertRaises(ValidationError):
                normalize_events([{**event(), **change}], binding=profile_binding(), artifact_refs=[])
        with self.assertRaises(ValidationError):
            normalize_events([], binding={**profile_binding(), 'provenance': []}, artifact_refs=[])

    def test_program_identity_and_sample_coverage_cannot_be_silently_lost(self):
        report = duration_report()
        report['groups'][1]['program_id'] = None
        with self.assertRaises(ValidationError):
            normalize_duration_report(report, binding=profile_binding(), artifact_refs=[])
        report = duration_report()
        report['calls'] = 3
        for group in report['groups']:
            group['events_per_measured_call'] = 2 / 3
        result = normalize_duration_report(report, binding=profile_binding(), artifact_refs=[])
        self.assertEqual(result['quality']['availability'], 'partial')
        self.assertTrue(any('sample_count' in issue for issue in result['quality']['issues']))


if __name__ == '__main__':
    unittest.main()
