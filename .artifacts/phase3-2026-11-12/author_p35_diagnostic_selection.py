"""Freeze only the requested independent diagnostic selection, not any Run."""
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from real_round_aux import read_json, write_once
from p35_recovery_round import recovery_identity
d=Path(__file__).resolve().parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--generation', help='Canonical runtimeN, N >= 4; omitted keeps the original Runtime 3 selection')
args=parser.parse_args()
if args.generation is not None:
    try:
        recovery_identity(d, args.generation)
    except ValueError as error:
        parser.error(str(error))
source=read_json(d/'p35-confirmation-selection.json')
selection={'schema_version':1,'task_id':source['task_id'],'context_id':source['context_id'],
    'strategy_id':'mla-strategy:deee0989771642ee8e066be07adb0370a6c7a3bca6be04ebc8ab50e145c5b05e',
    'round_id':'p35-runtime3-single-tile-diagnostic:1','knowledge':source['knowledge']}
name='p35-single-tile-selection-input.json'
if args.generation is not None:
    baseline=read_json(d/('baseline-repair-'+args.generation+'-job.json'))
    selection.update(schema_version=2, runtime_generation=args.generation,
        task_id=baseline['task_id'], context_id=baseline['context_id'], strategy_id=baseline['strategy_id'],
        round_id='p35-'+args.generation+'-single-tile-diagnostic:1')
    name='p35-single-tile-selection-input-'+args.generation+'.json'
write_once(d/name,selection)
print(str(d/name))
