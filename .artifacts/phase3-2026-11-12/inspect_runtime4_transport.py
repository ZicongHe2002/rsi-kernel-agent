"""Read-only Runtime4 bridge diagnosis; expose only sanitized status fields."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.workflow.__main__ import colab_remote
from rsi_kernel_agent.workflow.execution import stable_id
from real_round_aux import read_json, write_once

d = Path(__file__).resolve().parent
job = read_json(d / 'oracle-negative-attention-runtime4-job.json')
op = stable_id('operation', job['run_id'], 'correctness')
remote = colab_remote(d / 'colab-config-runtime4.json')
bridge = remote.bridge
request = {'action': 'execute', 'session': bridge.session,
           'expected_runtime_id': bridge.runtime_id, 'timeout': bridge.timeout,
           'code': remote._code(remote._command('lookup', operation_id=op))}
out = {'operation_id': op, 'stage': 'correctness', 'runtime_id': bridge.runtime_id}
try:
    reply = subprocess.run([bridge.python, '-B', '-X', 'utf8', str(ROOT / 'src/rsi_kernel_agent/workflow/colab_bridge_call.py')],
        input=json.dumps(request), capture_output=True, text=True, encoding='utf-8', timeout=bridge.timeout + 10,
        env={**os.environ, 'PYTHONUTF8': '1', 'COLAB_CLI_REQUEST_TIMEOUT': str(bridge.timeout)},
        creationflags=subprocess.CREATE_NO_WINDOW)
    parsed = json.loads(reply.stdout)
    out.update(returncode=reply.returncode, status=parsed.get('status'), error_type=parsed.get('error_type'))
    if parsed.get('status') == 'ok':
        outputs = parsed.get('result', [])
        out['output_types'] = [row.get('output_type') for row in outputs]
        streams = ''.join(row.get('text', '') for row in outputs if row.get('output_type') == 'stream')
        marks = [line for line in streams.splitlines() if line.startswith('RSI_RESULT:')]
        out.update(stream_bytes=len(streams.encode()), result_markers=len(marks))
        if len(marks) == 1:
            worker = json.loads(marks[0][len('RSI_RESULT:'):])
            out.update(worker_status=worker.get('status'), receipt_status=(worker.get('receipt') or {}).get('status'))
except Exception as error:
    out['error_type'] = type(error).__name__
out['observed_at_utc'] = datetime.now(timezone.utc).isoformat()
write_once(d / ('runtime4-bridge-inspection-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json'), out)
print(json.dumps(out))
