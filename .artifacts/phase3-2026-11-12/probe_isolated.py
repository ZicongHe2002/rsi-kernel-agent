import base64
from hashlib import sha256
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.workflow.colab_bridge import ColabBridge,extract_json_result
from runtime_readmission import pin_observation_runtime,save_observation,parse_observation_args
args=parse_observation_args()
intent=json.loads(Path(__file__).with_name('allocation-intent.json').read_text('utf-8'))
suffix=args.suffix
pin_observation_runtime(Path(__file__).parent,suffix,intent)
bridge=ColabBridge(r'C:\Users\fengluo\Desktop\colab\google-colab-cli\.venv\Scripts\python.exe',intent['session'],intent['runtime_id'],timeout=120)
runner=(ROOT/'src/rsi_kernel_agent/workflow/mla_runner.py').read_bytes()
source=f'''import importlib.util,json,base64,hashlib
from pathlib import Path
p=Path('/content/rsi-kernel-agent/env-20261112/environment_probe_runner.py')
raw=base64.b64decode({base64.b64encode(runner).decode()!r})
assert hashlib.sha256(raw).hexdigest()=={sha256(runner).hexdigest()!r}
p.write_bytes(raw)
spec=importlib.util.spec_from_file_location('fixed_probe_runner',p)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
env=module.collect_environment({intent['runtime_id']!r})
import jax,jax.numpy as jnp
x=jnp.arange(8,dtype=jnp.float32)
y=jax.jit(lambda a:jnp.sum(a*a))(x)
jax.block_until_ready((x,y))
assert float(y)==140.0
from tensorflow.tsl.profiler.protobuf import xplane_pb2
assert xplane_pb2.XSpace().SerializeToString()==b''
print('RSI_PROBE:'+json.dumps({{'environment':env,'result':float(y),'synchronized':True,'xplane_parser_import':True}}))
'''
code='import subprocess,json\n'+f'p=subprocess.run(["/content/rsi-kernel-agent/env-20261112/venv/bin/python","-B","-c",{source!r}],capture_output=True,text=True,timeout=100)\n'+'print("RSI_RESULT:"+json.dumps({"returncode":p.returncode,"stdout":p.stdout,"stderr":p.stderr}))'
result=extract_json_result(bridge.execute(code))
directory=ROOT/'knowledge-site/manifests/phase3-2026-11-12'
save_observation(directory/('isolated-probe'+suffix+'.json'),result)
lines=[s[len('RSI_PROBE:'):] for s in result['stdout'].splitlines() if s.startswith('RSI_PROBE:')]
if result['returncode']==0 and len(lines)==1:
    report=json.loads(lines[0]);save_observation(directory/('environment-isolated'+suffix+'.json'),report['environment'])
    print(json.dumps(report))
else:
    print(json.dumps(result));raise SystemExit(1)
