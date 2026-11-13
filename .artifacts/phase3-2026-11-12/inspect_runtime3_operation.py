"""Read only the task-owned worker's operation status; never submit a stage."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.workflow.__main__ import colab_remote
from rsi_kernel_agent.workflow.colab_bridge import extract_json_result
from rsi_kernel_agent.workflow.execution import stable_id
from real_round_aux import read_json, write_once

d = Path(__file__).resolve().parent
name, stage = sys.argv[1:3]
if name not in ('oracle-negative-attention','oracle-negative-cache','baseline-repair','baseline-b','confirmation-control'):
    raise ValueError('Only task-owned Runtime3 prerequisite names')
if stage not in ('generate','compile','correctness','benchmark','profile'):
    raise ValueError('Unknown stage')
remote = colab_remote(d / 'colab-config-runtime3.json')
job = read_json(d / (name + '-runtime3-job.json'))
op = stable_id('operation', job['run_id'], stage)
command = remote._command('lookup', operation_id=op)
code = ('import subprocess,json\n'
    f'p=subprocess.run([{remote.remote_python!r},"-I","-B",{remote.worker_remote_path!r}],'
    f'input={json.dumps(command)!r},capture_output=True,text=True,timeout=30)\n'
    'r=json.loads(p.stdout)\n'
    'receipt=r.get("receipt") or {}\n'
    'out={"returncode":p.returncode,"status":r.get("status"),"reason":r.get("reason"),'
    '"receipt_status":receipt.get("status"),"artifact_sizes":[x["ref"]["size"] for x in r.get("artifacts",[])],'
    '"error_output":receipt.get("outputs") if receipt.get("status")=="failed" else None}\n'
    'print("RSI_RESULT:"+json.dumps(out))')
result = extract_json_result(remote.bridge.execute(code))
result.update(observed_at_utc=datetime.now(timezone.utc).isoformat(), operation_id=op, run_id=job['run_id'])
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
write_once(d / ('runtime3-operation-inspection-' + stamp + '.json'), result)
summary = {key: value for key, value in result.items() if key != 'error_output'}
output = result.get('error_output') or {}
summary['correctness'] = output.get('correctness')
summary['cases'] = [{'id': case['id'], 'passed': case['passed']} for case in output.get('cases', [])]
print(json.dumps(summary))
