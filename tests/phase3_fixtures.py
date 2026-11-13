"""Local CPU contract fixtures; these do not execute or validate a real TPU."""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rsi_kernel_agent.contracts import (Candidate, CandidateIdentity, Decision, EvaluationContext,
    ParentCandidate, Plan, Run, RunEvent, Strategy, Task, Workload, canonical_json)
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.execution import execution_contract, make_request, stable_id, validate_variant
from rsi_kernel_agent.workflow.evaluation import integrate_result


class RealProtocolFixture:
    def prepare(self, root):
        self.root = Path(root)
        self.store = ExperimentStore(self.root / 'db.sqlite', self.root / 'artifacts')
        self.source = self.put(b'def kernel(x): return x\n')
        self.oracle = self.put(b'def reference(x): return x\n')
        self.config = self.put({'case': 'tiny', 'shape': [2]})
        self.bundle = self.put({'schema_version': 1, 'entry': {'path': 'kernel.py', 'symbol': 'kernel'},
            'files': [{'path': 'kernel.py', 'artifact_ref': self.source, 'role': 'source'}]})
        self.oracle_bundle = self.put({'schema_version': 1, 'entry': {'path': 'oracle.py', 'symbol': 'reference'},
            'files': [{'path': 'oracle.py', 'artifact_ref': self.oracle, 'role': 'oracle'}]})
        self.runner_digest, self.parser_digest = 'a' * 64, 'b' * 64
        self.build = self.put({'schema_version': 1, 'source_bundle_digest': self.bundle['digest'],
            'oracle_bundle_digest': self.oracle_bundle['digest'], 'runner_digest': self.runner_digest,
            'oracle_digest': self.oracle['digest']})
        self.environment = {'runtime_session_id': 'local-fixture-session', 'tpu_model': 'TPU v6e fixture',
                            'device_count': 1, 'jax': 'fixture-only'}
        self.protocol = {'backend_id': 'colab.v1', 'runner_digest': self.runner_digest,
            'oracle_digest': self.oracle['digest'], 'oracle_bundle_ref': self.oracle_bundle,
            'trusted_files': {'parser.py': self.parser_digest}, 'correctness': {'rtol': 0.01, 'atol': 0.01},
            'benchmark': {'version': 'fixture-v1', 'timer': 'host_perf_counter_block_until_ready',
                'warmup': 2, 'measurements': 3, 'statistic': 'median', 'unit': 'ms',
                'scope': 'kernel plus required preparation', 'outlier_rule': 'none'},
            'profile': {'required': True, 'parser_digest': self.parser_digest, 'trace_mode': 'fixture'}}
        self.case = {'id': 'tiny', 'outputs': [{'name': 'attention', 'shape': [2], 'dtype': 'float32',
            'comparison': 'allclose'}, {'name': 'cache', 'shape': [2], 'dtype': 'float32', 'comparison': 'exact'}]}
        for record in (Task('task', 'CPU validation of real protocol', 'fixed-v1'),
            Workload('workload', 'task', {'kernel': 'fixture', 'cases': [self.case]}),
            EvaluationContext('context', 'task', 'workload', self.environment, self.protocol, 'real'),
            Strategy('A', 'task', 'A'), Plan('plan', 'A', 'CPU protocol fixture')):
            self.store.append(record, 'setup:' + record.id)
        self.identity = CandidateIdentity(self.source['digest'], self.config['digest'], self.build['digest'])
        for record in (ParentCandidate('parent', 'task', self.identity),
                       Candidate('candidate', 'plan', 'parent', self.identity), Run('run', 'candidate', 'context')):
            self.store.append(record, 'setup:' + record.id)
        self.variant = {'label': 'fixture', 'source_ref': self.source, 'config_ref': self.config,
            'build_ref': self.build, 'source_bundle_ref': self.bundle, 'priority': 0,
            'device': 'local-fixture-session', 'cost': {'executions': 1, 'tokens': 0, 'time_s': 1}}

    def put(self, value):
        raw = value if isinstance(value, bytes) else canonical_json(value).encode()
        return asdict(self.store.artifacts.put(raw))

    def job(self):
        contract, digest = execution_contract(self.store, 'context')
        return {'id': 'job', 'task_id': 'task', 'round_id': 'round:1', 'strategy_id': 'A', 'plan_id': 'plan',
            'parent_id': 'parent', 'context_id': 'context', 'candidate_id': 'candidate', 'run_id': 'run',
            'parent_evaluation_id': None, 'contract': contract, 'contract_digest': digest, 'variant': self.variant}

    def identity_output(self):
        return {'runtime_session_id': 'local-fixture-session', 'workspace': '/content/rsi/candidate',
            'module_path': '/content/rsi/candidate/kernel.py', 'entry': {'path': 'kernel.py', 'symbol': 'kernel'},
            'loaded_files': [{'path': 'kernel.py', 'digest': self.source['digest']}],
            'source_bundle_digest': self.bundle['digest'], 'config_digest': self.config['digest'],
            'fresh_process': True, 'specialization_digest': self.config['digest']}

    def receipt(self, job, stage, output, status='succeeded'):
        request = make_request(self.store, job, stage)
        body = {name: request[name] for name in ('schema_version', 'backend_id', 'operation_id',
            'request_digest', 'stage', 'candidate_identity', 'parent_identity', 'provenance')}
        body.update({name: job[name] for name in ('contract_digest', 'run_id', 'candidate_id', 'parent_id', 'context_id')})
        body.update(status=status, outputs=output,
                    cost=self.variant['cost'] if stage == 'generate' else {'executions': 0, 'tokens': 0, 'time_s': 0})
        refs = [*output.get('raw_artifact_refs', []), *output.get('raw_xplane_refs', [])]
        if output.get('report_ref'):
            refs.append(output['report_ref'])
        body['artifact_refs'] = refs
        ref = self.put(body)
        marker = Decision(stable_id('execution-receipt', request['operation_id']), job['task_id'], 'note', job['run_id'],
            'CPU fixture publication', {'type': 'execution_receipt.v1', 'operation_id': request['operation_id'],
                                      'request_digest': request['request_digest'], 'artifact_ref': ref})
        self.store.append(marker, marker.id)
        return {**body, 'artifact_ref': ref}

    def lease(self, job, stage):
        return {'runtime_session_id': 'local-fixture-session', 'operation_id': stable_id('operation', job['run_id'], stage),
            'owner_run_id': job['run_id'], 'exclusive_worker': True, 'interference_observed': False,
            'started_ns': 20 if stage == 'profile' else 10, 'finished_ns': 30 if stage == 'profile' else 20}

    def outputs(self, job):
        common = {'environment': self.environment, 'identity': self.identity_output()}
        cases = [{'id': 'tiny', 'outputs': [{**{k: out[k] for k in ('name', 'shape', 'dtype')},
            'finite': True, 'max_abs': 0.0, 'max_rel': 0.0, 'rms': 0.0, 'tolerance_violations': 0, 'passed': True,
            'metric_reason': None}
            for out in self.case['outputs']], 'passed': True}]
        report = {'time_source': 'TPU XPlane device_duration_ps', 'unit': 'us', 'profile': 'fixture.xplane.pb',
            'calls': 1, 'devices': ['TPU:0'], 'longest_device_per_call': None, 'multi_device_span': None,
            'errors': [], 'warnings': [], 'groups': [{'kind': 'program', 'name': 'fixture', 'plane': 'TPU:0',
                'line': 'XLA Modules', 'line_id': 1, 'program_id': 1, 'count': 1, 'mean_us': 2000,
                'median_us': 2000, 'p95_us': 2000, 'min_us': 2000, 'max_us': 2000,
                'events_per_measured_call': 1, 'invalid_samples': 0,
                'samples': [{'duration_ps': 2_000_000_000, 'device_offset_ps': 0, 'step_num': 0, 'run_id': 1}]}]}
        inputs = self.put({'fixture_inputs': 'tiny'})
        return {'generate': {'workspace': '/content/rsi/candidate', 'source_digest': self.source['digest'],
                    'config_digest': self.config['digest'], 'build_digest': self.build['digest'],
                    'source_bundle_digest': self.bundle['digest']},
            'compile': {**common, 'compile_time_ms': 5.0, 'executed': True},
            'correctness': {**common, 'oracle_digest': self.oracle['digest'], 'input_digest': inputs['digest'],
                'raw_artifact_refs': [inputs], 'oracle_identity': {'source_bundle_digest': self.oracle_bundle['digest'],
                    'entry': {'path': 'oracle.py', 'symbol': 'reference'},
                    'loaded_files': [{'path': 'oracle.py', 'digest': self.oracle['digest']}]},
                            'cases': cases, 'correctness': 'passed'},
            'benchmark': {**common, 'protocol': self.protocol['benchmark'], 'samples_ms': [2.0, 3.0, 4.0],
                'latency_ms': 3.0, 'synchronized': True, 'compile_time_ms': 5.0, 'warmup_count': 2,
                'lease': self.lease(job, 'benchmark')},
            'profile': {**common, 'parser_digest': self.parser_digest, 'trace_mode': 'fixture',
                'raw_xplane_refs': [self.put(b'CPU fixture, not an XPlane protobuf')], 'report_ref': self.put(report),
                'capture_operation_id': stable_id('operation', job['run_id'], 'profile'),
                'benchmark_operation_id': stable_id('operation', job['run_id'], 'benchmark'), 'synchronized': True,
                'lease': self.lease(job, 'profile')}}

    def execute_fixture(self, mutate=None, profile_failed=False):
        job = self.job()
        self.store.transition(RunEvent('run:start', 'run', 'running', 'active', 'CPU fixture'), None, 'run:start')
        outputs = self.outputs(job)
        if mutate:
            mutate(outputs)
        receipts = {stage: self.receipt(job, stage, output) for stage, output in outputs.items()
                    if not (profile_failed and stage == 'profile')}
        if profile_failed:
            receipts['profile'] = self.receipt(job, 'profile', {'error_type': 'CaptureError', 'message': 'fixture failure'}, 'failed')
        return job, receipts



def make_real_fixture(root):
    fixture = RealProtocolFixture()
    fixture.prepare(root)
    return {'store': fixture.store, 'job': fixture.job(), 'fixture': fixture}
