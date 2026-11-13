import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.workflow.__main__ import colab_remote
from rsi_kernel_agent.workflow.colab_bridge import extract_json_result
remote=colab_remote(Path(__file__).with_name('colab-config.json'))
job=json.loads(Path(__file__).with_name('baseline-repair-job.json' if '--repair' in sys.argv else 'baseline-job.json').read_text('utf-8'))
from rsi_kernel_agent.workflow.execution import stable_id
op=stable_id('operation',job['run_id'],sys.argv[1] if len(sys.argv)>1 else 'generate')
command=remote._command('lookup',operation_id=op)
code=('import subprocess,json\n'+f'p=subprocess.run([{remote.remote_python!r},"-I","-B",{remote.worker_remote_path!r}],input={json.dumps(command)!r},capture_output=True,text=True,timeout=30)\n'+
      'print("RSI_RESULT:"+json.dumps({"returncode":p.returncode,"stdout":p.stdout,"stderr":p.stderr}))')
result=extract_json_result(remote.bridge.execute(code))
Path(__file__).with_name('worker-inspection-'+(sys.argv[1] if len(sys.argv)>1 else 'generate')+'.json').write_text(json.dumps(result),encoding='utf-8')
try:
    state=json.loads(result['stdout'])
except ValueError:
    print(json.dumps(result))
else:
    print(json.dumps({'returncode':result['returncode'],'status':state.get('status'),
       'receipt_status':(state.get('receipt') or {}).get('status'),
       'outputs':(state.get('receipt') or {}).get('outputs') if (state.get('receipt') or {}).get('status')!='succeeded' else None,
       'artifact_sizes':[x['ref']['size'] for x in state.get('artifacts',[])],'stderr':result['stderr']}))
