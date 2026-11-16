"""Prepare the explicit Runtime 4 recovery through the existing Analyzer helper."""
import argparse
import asyncio
from pathlib import Path
from prepare_real_round import prepare
from real_round_aux import canonical_json, read_json
from p35_run_diagnostic_round import driver_lock
from rsi_kernel_agent.store import ExperimentStore


async def main(generation='runtime4'):
    directory = Path(__file__).resolve().parent
    store = ExperimentStore(directory / 'experiments.sqlite3', directory / 'artifacts')
    from p35_recovery_round import recovery_identity
    recovery_identity(directory, generation)
    task_id = read_json(directory / ('baseline-repair-' + generation + '-job.json'))['task_id']
    with driver_lock(store, task_id):
        return await prepare(2, directory, recovery_generation=generation)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generation', default='runtime4', help='runtimeN, N >= 4')
    print(canonical_json(asyncio.run(main(parser.parse_args().generation))))
