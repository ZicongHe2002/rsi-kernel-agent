"""Read-only task-owned worker state; no reinitialization or resubmission."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.workflow.__main__ import colab_remote
from rsi_kernel_agent.workflow.colab_bridge import extract_json_result, BridgeUnavailable
from rsi_kernel_agent.workflow.execution import stable_id

work = Path(__file__).resolve().parent
remote = colab_remote(work / 'colab-config-runtime2.json')
job = json.loads((work / 'oracle-negative-attention-runtime2-job.json').read_bytes())
op = stable_id('operation', job['run_id'], 'generate')
code = ('import subprocess,json\n'
        f'p=subprocess.run([{remote.remote_python!r},"-I","-B",{remote.worker_remote_path!r}],'
        f'input={json.dumps(remote._command("lookup",operation_id=op))!r},'
        'capture_output=True,text=True,timeout=30)\n'
        'print("RSI_RESULT:"+json.dumps({"returncode":p.returncode,"stdout":p.stdout,"stderr":p.stderr}))')
try:
    result = extract_json_result(remote.bridge.execute(code))
except BridgeUnavailable as exc:
    result = {'transport': 'unavailable', 'reason': str(exc)}
else:
    try:
        state = json.loads(result['stdout'])
    except ValueError:
        pass
    else:
        result = {'returncode': result['returncode'], 'status': state.get('status'),
                  'receipt_status': (state.get('receipt') or {}).get('status'),
                  'stderr': result['stderr']}
result['utc'] = datetime.now(timezone.utc).isoformat()
(work / 'runtime2-worker-inspection.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
