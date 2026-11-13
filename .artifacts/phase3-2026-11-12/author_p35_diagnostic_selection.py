"""Freeze only the requested independent diagnostic selection, not any Run."""
from pathlib import Path
from real_round_aux import read_json, write_once
d=Path(__file__).resolve().parent
source=read_json(d/'p35-confirmation-selection.json')
selection={'schema_version':1,'task_id':source['task_id'],'context_id':source['context_id'],
    'strategy_id':'mla-strategy:deee0989771642ee8e066be07adb0370a6c7a3bca6be04ebc8ab50e145c5b05e',
    'round_id':'p35-runtime3-single-tile-diagnostic:1','knowledge':source['knowledge']}
write_once(d/'p35-single-tile-selection-input.json',selection)
print(str(d/'p35-single-tile-selection-input.json'))
