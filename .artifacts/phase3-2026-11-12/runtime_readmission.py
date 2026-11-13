"""New Context/Run for unchanged candidate bytes on an observed replacement Runtime.

No remote calls or automatic best/Frontier changes. Actual v6e probing and
completed prerequisite gates are required by the caller before exploration.
"""
from copy import deepcopy
import argparse
from hashlib import sha256
import os
from pathlib import Path
import re
import tempfile
from rsi_kernel_agent.contracts import Decision, EvaluationContext, Run
from rsi_kernel_agent.workflow.execution import execution_contract, make_request, stable_id
from real_round_aux import read_json, write_once


def parse_observation_args(argv=None, *, allow_poll=False):
    parser = argparse.ArgumentParser(description='Pin observations to one Runtime generation')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--generation')
    group.add_argument('--runtime2', dest='generation', action='store_const', const='runtime2')
    group.add_argument('--runtime3', dest='generation', action='store_const', const='runtime3')
    if allow_poll:
        parser.add_argument('--poll', action='store_true')
    args = parser.parse_args(argv)
    if args.generation is not None and not re.fullmatch(r'runtime(?:[2-9]|[1-9][0-9]+)', args.generation):
        parser.error('generation must be runtimeN with N >= 2')
    args.suffix = '-' + args.generation if args.generation else ''
    return args


def pin_observation_runtime(directory, suffix, intent):
    directory = Path(directory)
    if (suffix != '' and not re.fullmatch(r'-runtime(?:[2-9]|[1-9][0-9]+)', suffix)
            or intent.get('status') != 'allocated'):
        raise ValueError('Expected explicit allocated Runtime observation')
    runtime = intent.get('runtime_id')
    if not isinstance(runtime, str) or not runtime.strip():
        raise ValueError('Missing allocated Runtime identity')
    config = directory/('colab-config'+suffix+'.json')
    if config.exists() and read_json(config)['runtime_id'] != runtime:
        raise ValueError('Observation generation differs from saved Runtime')
    write_once(directory/('observation'+suffix+'.json'), {'runtime_id':runtime})


def save_observation(path, value):
    """Keep every prior byte version while updating the current observation view."""
    from rsi_kernel_agent.contracts import canonical_json
    path = Path(path)
    before = path.read_bytes() if path.exists() else None
    if before is not None:
        archive = path.with_name(path.name+'.'+sha256(before).hexdigest()+'.original')
        if archive.exists():
            if archive.read_bytes() != before:
                raise ValueError('Original observation archive differs')
        else:
            with archive.open('xb') as out:
                out.write(before); out.flush(); os.fsync(out.fileno())
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.observation-')
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(canonical_json(value).encode()); out.flush(); os.fsync(out.fileno())
        current = path.read_bytes() if path.exists() else None
        if current != before:
            raise ValueError('Concurrent observation update')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def remeasure(store, old, environment):
    make_request(store, old, 'generate')
    previous = store.get(old['context_id'])
    runtime = environment.get('runtime_session_id')
    if (not isinstance(runtime, str) or not runtime.strip()
            or runtime == previous.environment['runtime_session_id']):
        raise ValueError('Expected a distinct observed Runtime')
    context = EvaluationContext(stable_id('runtime-context', previous.id, environment),
        old['task_id'], previous.workload_id, deepcopy(environment), deepcopy(previous.protocol), 'real')
    store.append(context, context.id)
    run = Run(stable_id('runtime-run', old['candidate_id'], context.id), old['candidate_id'], context.id)
    store.append(run, run.id)
    contract, digest = execution_contract(store, context.id)
    job = deepcopy(old)
    job.pop('operation', None)
    job.update(id=stable_id('runtime-job', run.id), run_id=run.id, context_id=context.id,
        round_id=stable_id('runtime-validation', context.id), contract=contract, contract_digest=digest,
        parent_evaluation_id=None, stage='generate', state='queued', receipts={}, result=None,
        reserved=False, reason='Replacement Runtime prerequisite; previous evidence remains in original Context')
    job['variant']['device'] = runtime
    make_request(store, job, 'generate')
    note = Decision(stable_id('runtime-readmission', run.id), job['task_id'], 'note', run.id,
        'Remeasure unchanged candidate bytes in a distinct observed Runtime',
        {'type':'runtime_readmission.v1', 'prior_run_id':old['run_id'],
         'prior_context_id':previous.id, 'new_context_id':context.id, 'completed_results_preserved':True})
    store.append(note, note.id)
    return job
