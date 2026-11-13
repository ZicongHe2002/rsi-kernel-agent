"""Prepare the explicit Runtime 4 recovery through the existing Analyzer helper."""
import asyncio
from pathlib import Path
from prepare_real_round import prepare
from real_round_aux import canonical_json, read_json
from p35_run_diagnostic_round import driver_lock
from rsi_kernel_agent.store import ExperimentStore


async def main():
    directory = Path(__file__).resolve().parent
    store = ExperimentStore(directory / 'experiments.sqlite3', directory / 'artifacts')
    task_id = read_json(directory / 'baseline-repair-runtime4-job.json')['task_id']
    with driver_lock(store, task_id):
        return await prepare(2, directory, recovery_generation='runtime4')


if __name__ == '__main__':
    print(canonical_json(asyncio.run(main())))
