"""Advance only the Runtime 4 recovery, using the original Controller."""
import argparse
import asyncio
from pathlib import Path
from run_real_round import run
from real_round_aux import canonical_json, read_json
from p35_run_diagnostic_round import driver_lock
from rsi_kernel_agent.store import ExperimentStore


async def main(steps, interval):
    directory = Path(__file__).resolve().parent
    store = ExperimentStore(directory / 'experiments.sqlite3', directory / 'artifacts')
    task_id = read_json(directory / 'baseline-repair-runtime4-job.json')['task_id']
    with driver_lock(store, task_id):
        result = await run(2, directory / 'real-round-2-runtime4' / 'variants.json',
                           steps=steps, interval=interval, directory=directory, recovery_generation='runtime4')
    return {key: value for key, value in result.items() if key != 'intake'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--steps', type=int, default=1)
    parser.add_argument('--interval', type=int, default=15)
    args = parser.parse_args()
    print(canonical_json(asyncio.run(main(args.steps, args.interval))))
