"""Serialize the current conversation's fixed comparison; no execution."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from real_round_aux import read_json, write_once

d = Path(__file__).resolve().parent
control = read_json(d / 'confirmation-control-runtime3-job.json')
treatment = read_json(d / 'baseline-b-runtime3-job.json')
if control['strategy_id'] != treatment['strategy_id'] or control['context_id'] != treatment['context_id']:
    raise ValueError('Confirmation origins must share exact strategy and Context')
selection = {
    'schema_version': 1,
    'task_id': treatment['task_id'], 'context_id': treatment['context_id'],
    'strategy_id': treatment['strategy_id'], 'candidate_id': treatment['candidate_id'],
    'baseline_candidate_id': control['candidate_id'],
    'round_id': 'p35-runtime3-abba:1', 'previous_round_ids': ['real-runtime2-round:1'],
    'knowledge': read_json(d / 'real-round-1/preparation-input.json')['knowledge'],
    'plan_roles': {'baseline': 'P35-C-control', 'candidate': 'P35-C-treatment'},
    'sequence': [{'role': role, 'label': label, 'priority': priority} for role, label, priority in (
        ('baseline', 'p35-control-before', 40), ('candidate', 'p35-treatment-first', 30),
        ('candidate', 'p35-treatment-second', 20), ('baseline', 'p35-control-after', 10))],
}
write_once(d / 'p35-confirmation-selection.json', selection)
print('Saved fixed control/treatment identities and ABBA sequence; no Candidate or Run executed.')
