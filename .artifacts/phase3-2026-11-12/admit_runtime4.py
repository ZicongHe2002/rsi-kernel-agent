"""Readmit four exact Runtime 3 origins after the archived unsubmitted R2 loss.

Importing this module has no side effects. Only main/admit writes new Runtime 4
records; setup, probing, allocation and active binding are separate operations.
"""
import argparse
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.store import ExperimentStore, StoreError
from rsi_kernel_agent.workflow.journal import WorkflowJournal
from rsi_kernel_agent.workflow.mla_baseline import _observed_v6e
from real_round_aux import read_json, require, require_gates, write_once
from runtime_readmission import remeasure

NAMES = ('oracle-negative-attention', 'oracle-negative-cache', 'baseline-repair', 'baseline-b')
REMOTE_ROOT = '/content/rsi-kernel-agent/phase3-20261112-runtime4'


def archived_runtime_loss(store, directory):
    from p35_archive_unsubmitted_runtime_loss import require_archived_unsubmitted_runtime_loss
    return require_archived_unsubmitted_runtime_loss(store, directory)


def load_task_state(store, task_id):
    return WorkflowJournal(store, task_id).load()


def require_runtime4_archive(store, directory):
    """Caller holds the original journal lock; this guard performs only reads."""
    archive = archived_runtime_loss(store, directory)
    base = read_json(Path(directory) / 'baseline-repair-runtime3-job.json')
    state = load_task_state(store, base['task_id'])
    require(state is not None and not state['cancelled'] and all(
        job['state'] in ('completed', 'failed', 'cancelled') for job in state['jobs'].values()),
        'Unfinished prior work requires reconciliation before Runtime 4')
    old_config = read_json(Path(directory) / 'colab-config-runtime3.json')
    require(archive['summary']['task_id'] == base['task_id'] == state['task_id']
            and archive['summary']['runtime_id'] == old_config['runtime_id'],
            'Archived loss does not bind the original task and Runtime 3')
    return state


def validate_origins(store, directory):
    # Reverify old evidence locally, including the existing R3 control. Only the
    # four selected origins below create replacement Runs; control is not rerun.
    prior, _ = require_gates(store, directory, {'generation': 'runtime3'})
    require(prior['baseline-repair']['variant']['source_ref']['digest'] ==
            '2fc23aa9d0358ea18698f916cbc7ed5e02a4218dec2de2fc955661a8b513ebae',
            'Runtime 4 A origin differs from the repaired baseline')
    require(prior['baseline-b']['variant']['source_ref']['digest'] ==
            '0579beaa21cbf1119b64585c11334ab7051976a0f4b22400997fa510bd3b3a34',
            'Runtime 4 B origin differs from delayed store wait')
    origins = {name: prior[name] for name in NAMES}
    require(len({job['run_id'] for job in origins.values()}) == len(NAMES), 'Duplicate Runtime 3 origins')
    return origins


class _ReadmissionPreview:
    """Exercise original remeasure validation with detached records, no writes."""
    def __init__(self, store):
        self.store, self.artifacts, self.records = store, store.artifacts, {}

    def get(self, record_id):
        return self.records[record_id] if record_id in self.records else self.store.get(record_id)

    def append(self, record, _key):
        try:
            existing = self.get(record.id)
        except StoreError:
            existing = None
        require(existing is None or existing == record, 'Existing Runtime 4 record differs')
        self.records[record.id] = record


def preflight_jobs(store, directory, environment):
    require_runtime4_archive(store, directory)
    origins = validate_origins(store, directory)
    preview = _ReadmissionPreview(store)
    jobs = {name: remeasure(preview, old, environment) for name, old in origins.items()}
    require(len({job['run_id'] for job in jobs.values()}) == len(NAMES), 'Replacement Runs are not distinct')
    for name, job in jobs.items():
        path = Path(directory) / (name + '-runtime4-job.json')
        if path.exists():
            require(read_json(path) == job, 'Saved Runtime 4 job differs: ' + name)
    return origins, jobs


def commit_jobs(store, directory, environment, origins, expected):
    for name, old in origins.items():
        job = remeasure(store, old, environment)
        require(job == expected[name], 'Readmission changed after preflight')
        write_once(Path(directory) / (name + '-runtime4-job.json'), job)
    return expected


def prepare_prerequisite_jobs(store, directory, environment):
    """Caller holds the original journal lock; all four jobs preflight first."""
    origins, jobs = preflight_jobs(store, directory, environment)
    return commit_jobs(store, directory, environment, origins, jobs)


def observed_inputs(directory, manifests):
    directory, manifests = Path(directory), Path(manifests)
    environment = read_json(manifests / 'environment-isolated-runtime4.json')
    _observed_v6e(environment)
    intent = read_json(directory / 'allocation-intent.json')
    old = read_json(directory / 'colab-config-runtime3.json')
    pin = read_json(directory / 'observation-runtime4.json')
    setup = read_json(manifests / 'isolated-environment-setup-runtime4.json')
    probe = read_json(manifests / 'isolated-probe-runtime4.json')
    require(intent.get('status') == 'allocated' and type(intent.get('session')) is str and intent['session']
            and pin == {'runtime_id': environment['runtime_session_id']}
            and intent['runtime_id'] == environment['runtime_session_id'] != old['runtime_id'],
            'Probe must match the new pinned Runtime 4 allocation')
    require(setup.get('status') == 'ready' and probe.get('returncode') == 0,
            'Runtime 4 setup and probe must succeed')
    import json
    rows = [line[len('RSI_PROBE:'):] for line in probe['stdout'].splitlines() if line.startswith('RSI_PROBE:')]
    require(len(rows) == 1, 'Runtime 4 probe must contain exactly one result')
    report = json.loads(rows[0])
    require(report['environment'] == environment and report['result'] == 140.0
            and report['synchronized'] is True and report['xplane_parser_import'] is True,
            'Runtime 4 measured probe does not match the saved environment')
    path = directory / 'colab-config-runtime4.json'
    existing = read_json(path) if path.exists() else None
    nonce = existing.get('nonce') if existing is not None else uuid.uuid4().hex
    require(type(nonce) is str and bool(nonce) and nonce != old['nonce'], 'Runtime 4 needs a new nonce')
    config = {**old, 'runtime_id': environment['runtime_session_id'], 'session': intent['session'],
              'nonce': nonce, 'remote_root': REMOTE_ROOT}
    require(existing is None or existing == config, 'Saved Runtime 4 config differs')
    admitted = directory / 'runtime4-admitted-environment.json'
    require(not admitted.exists() or read_json(admitted) == environment, 'Saved Runtime 4 environment differs')
    return environment, config


def admit(directory, manifests, *, bootstrap=False):
    directory = Path(directory)
    environment, config = observed_inputs(directory, manifests)
    require((directory / 'experiments.sqlite3').is_file(), 'Original Store is missing')
    store = ExperimentStore(directory / 'experiments.sqlite3', directory / 'artifacts')
    base = read_json(directory / 'baseline-repair-runtime3-job.json')
    with WorkflowJournal(store, base['task_id']).lock():
        origins, jobs = preflight_jobs(store, directory, environment)
        write_once(directory / 'colab-config-runtime4.json', config)
        write_once(directory / 'runtime4-admitted-environment.json', environment)
        commit_jobs(store, directory, environment, origins, jobs)
    if bootstrap:
        from rsi_kernel_agent.workflow.__main__ import colab_remote
        print(colab_remote(directory / 'colab-config-runtime4.json').bootstrap())
    return jobs


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--manifests', type=Path, default=ROOT / 'knowledge-site/manifests/phase3-2026-11-12')
    parser.add_argument('--bootstrap', action='store_true')
    args = parser.parse_args()
    jobs = admit(args.directory, args.manifests, bootstrap=args.bootstrap)
    print('Runtime 4 four prerequisite jobs prepared; gates must pass before active binding changes.')
