"""Independent verifier edge reproduction with temporary small local artifacts."""
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / '.artifacts/phase3-2026-11-12'), str(ROOT / 'src'), str(ROOT / 'tests')]
from rsi_kernel_agent.artifacts import ArtifactStore
from test_verify_real_round import write_npz
import verify_real_round

with tempfile.TemporaryDirectory() as temporary:
    directory = Path(temporary)
    actual, oracle = directory / 'actual.npz', directory / 'oracle.npz'
    write_npz(actual, [[0x3f80], [0x7fc0]])
    write_npz(oracle, [[0x3f80], [0x7fc0]])
    artifacts = ArtifactStore(directory / 'artifacts')
    put = lambda value: asdict(artifacts.put(value if type(value) is bytes else json.dumps(value).encode()))
    specs = [{'name': 'attention', 'shape': [1], 'dtype': 'bfloat16', 'comparison': 'allclose'},
             {'name': 'cache', 'shape': [1], 'dtype': 'bfloat16', 'comparison': 'exact'}]
    case = {'id': 'input-mutation-fixture', 'outputs': specs}
    input_ref = put(b'input fixture')
    refs = [input_ref, put(oracle.read_bytes()), put({}),
            put({'case_id': case['id'], 'input_arrays': []}), put(b'HLO fixture'), put(actual.read_bytes())]
    manifest_ref = put({'schema_version': 1, 'cases': [{'case_id': case['id'], 'arrays': [], 'npz_digest': input_ref['digest']}]})
    output = {'raw_artifact_refs': [*refs, manifest_ref], 'input_digest': manifest_ref['digest'], 'cases': [
        {'id': case['id'], 'passed': False, 'outputs': [
            {'name': 'attention', 'finite': True, 'passed': True, 'tolerance_violations': 0, 'max_abs': 0., 'max_rel': 0., 'rms': 0.},
            {'name': 'cache', 'finite': False, 'passed': False, 'tolerance_violations': 1,
             'metric_reason': 'Candidate mutated the input cache'}]}]}
    job = {'contract': {'workload': {'spec': {'cases': [case]}},
                        'context': {'protocol': {'correctness': {'atol': .01, 'rtol': .01}}}}}
    try:
        compared = verify_real_round.verify_npz_cases(SimpleNamespace(artifacts=artifacts), job, output, {})
        result = {'input_mutation_output_match': 'reported', 'case_results': compared}
    except ValueError as error:
        result = {'input_mutation_output_match': 'rejected_as_inconsistent', 'error': str(error)}
    target = Path(__file__).with_name('round-verifier-input-mutation-probe.json')
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
