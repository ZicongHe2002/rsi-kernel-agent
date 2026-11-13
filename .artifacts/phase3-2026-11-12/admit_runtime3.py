"""Admit already probed Runtime 3; never allocate or relabel previous results."""
from pathlib import Path
import sys
import uuid
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.mla_baseline import _observed_v6e
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from rsi_kernel_agent.workflow.__main__ import colab_remote
from real_round_aux import read_json, write_once, verified_gate_result, prerequisite_names
from runtime_readmission import remeasure


def prepare_prerequisite_jobs(store, directory, environment, state):
    """Readmit frozen origins; the caller holds the original journal lock."""
    directory = Path(directory)
    originals = {}
    for name in prerequisite_names(directory, 'runtime3'):
        origin_name = 'baseline-b' if name == 'confirmation-control' else name
        old = read_json(directory / (origin_name + '-runtime2-job.json'))
        if name == 'baseline-b':
            matches = [job for job in state['jobs'].values()
                       if job['candidate_id'] == 'wf-c:afa5b476cde0e73bb0bedb604b2d5c51']
            if len(matches) != 1 or matches[0]['state'] != 'completed':
                raise ValueError('Expected exact completed delayed-store-wait parent')
            old = matches[0]
        verified_gate_result(store, old, negative=name.startswith('oracle-negative-'))
        originals[name] = old
    jobs = {}
    for name, old in originals.items():
        jobs[name] = remeasure(store, old, environment)
        write_once(directory / (name + '-runtime3-job.json'), jobs[name])
    return jobs


if __name__ == '__main__':
    d = Path(__file__).resolve().parent
    env = read_json(ROOT/'knowledge-site/manifests/phase3-2026-11-12/environment-isolated-runtime3.json')
    _observed_v6e(env)
    intent = read_json(d/'allocation-intent.json')
    old_config = read_json(d/'colab-config-runtime2.json')
    if env['runtime_session_id'] != intent.get('runtime_id') or env['runtime_session_id'] == old_config['runtime_id']:
        raise ValueError('Probe must match the newly allocated Runtime')
    config_path = d/'colab-config-runtime3.json'
    config = (read_json(config_path) if config_path.exists() else
        {**old_config, 'runtime_id':env['runtime_session_id'], 'session':intent['session'],
         'nonce':uuid.uuid4().hex, 'remote_root':'/content/rsi-kernel-agent/phase3-20261112-runtime3'})
    if (config['runtime_id'] != env['runtime_session_id'] or not config['nonce']
            or config['nonce'] == old_config['nonce']
            or config['remote_root'] != '/content/rsi-kernel-agent/phase3-20261112-runtime3'):
        raise ValueError('Saved Runtime 3 config differs')
    write_once(config_path, config)
    write_once(d/'runtime3-admitted-environment.json', env)
    if not (d/'experiments.sqlite3').is_file():
        raise ValueError('Original experiment Store is missing')
    store = ExperimentStore(d/'experiments.sqlite3', d/'artifacts')
    base = read_json(d/'baseline-repair-runtime2-job.json')
    with WorkflowJournal(store, base['task_id']).lock():
        state = WorkflowJournal(store, base['task_id']).load()
        if state is None or any(j['state'] not in ('completed','failed','cancelled') for j in state['jobs'].values()):
            raise ValueError('Unfinished prior work requires reconciliation first')
        prepare_prerequisite_jobs(store, d, env, state)
    if '--bootstrap' in sys.argv:
        print(colab_remote(config_path).bootstrap())
    print('Runtime 3 prerequisite jobs prepared; new gates must pass before active binding changes.')
