"""Read the exact current ABBA operation through the original bridge; never submit."""
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
from prepare_confirmation_round import target_for

d = Path(__file__).resolve().parent
selection = read_json(d / 'p35-confirmation-selection.json')
target = target_for(d, selection)
state = read_json(target / 'controller-status.json')
label = sys.argv[1]
assert label in [row['label'] for row in selection['sequence']]
jobs = [j for j in state['jobs'].values()
        if j['round_id'] == selection['round_id'] and j['variant']['label'] == label]
assert len(jobs) == 1
job = jobs[0]
operation = stable_id('operation', job['run_id'], job['stage'])
assert job['operation']['operation_id'] == operation
remote = colab_remote(d / 'colab-config-runtime3.json')
command = remote._command('lookup', operation_id=operation)
code = ('import subprocess,json,hashlib\nfrom pathlib import Path\n'
    f'assert hashlib.sha256(Path({remote.worker_remote_path!r}).read_bytes()).hexdigest()=={remote.worker_digest!r}\n'
    f'p=subprocess.run([{remote.remote_python!r},"-I","-B",{remote.worker_remote_path!r}],'
    f'input={json.dumps(command)!r},capture_output=True,text=True,timeout=30)\n'
    'r=json.loads(p.stdout)\n'
    'receipt=r.get("receipt") or {}\n'
    'out={"returncode":p.returncode,"status":r.get("status"),"reason":r.get("reason"),'
    '"receipt_status":receipt.get("status"),"artifact_sizes":[x["ref"]["size"] for x in r.get("artifacts",[])],'
    '"failed_outputs":receipt.get("outputs") if receipt.get("status")=="failed" else None}\n'
    'print("RSI_RESULT:"+json.dumps(out))')
result = extract_json_result(remote.bridge.execute(code))
result.update(observed_at_utc=datetime.now(timezone.utc).isoformat(), operation_id=operation,
              run_id=job['run_id'], label=label, stage=job['stage'])
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
write_once(target / ('operation-inspection-' + stamp + '.json'), result)
print(json.dumps({k: v for k, v in result.items() if k != 'failed_outputs'}))
