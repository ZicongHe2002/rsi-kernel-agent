"""Run the original prerequisite stages with the locked Runtime 4 archive guard."""
import argparse
from pathlib import Path

from advance_runtime2 import advance
from admit_runtime4 import NAMES, observed_inputs, preflight_jobs
from real_round_aux import read_json, require
from p35_runtime_generations import generation_number


def runtime4_guard(store, directory, generation):
    generation_number(generation, minimum=4)
    directory = Path(directory)
    manifests = directory.parents[1] / 'knowledge-site/manifests/phase3-2026-11-12'
    environment, config = observed_inputs(directory, manifests, generation=generation)
    require(read_json(directory / ('colab-config-' + generation + '.json')) == config,
            'Runtime 4 configuration is not admitted')
    require(read_json(directory / (generation + '-admitted-environment.json')) == environment,
            'Runtime 4 environment is not admitted')
    _, expected = preflight_jobs(store, directory, environment, generation=generation)
    for name in NAMES:
        require(read_json(directory / (name + '-' + generation + '-job.json')) == expected[name],
                'Runtime 4 prerequisite is not the exact admitted job')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--generation', default='runtime4')
    args = parser.parse_args()
    advance(args.directory, args.generation, guard=runtime4_guard)
