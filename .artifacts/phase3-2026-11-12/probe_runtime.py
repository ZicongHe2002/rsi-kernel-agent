"""One explicit real connectivity probe in a fresh remote process."""
from hashlib import sha256
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.workflow.colab_bridge import ColabBridge, extract_json_result

intent = json.loads(Path(__file__).with_name('allocation-intent.json').read_text('utf-8'))
runtime_id = intent['runtime_id']
bridge = ColabBridge(r'C:\Users\fengluo\Desktop\colab\google-colab-cli\.venv\Scripts\python.exe',
                     intent['session'], runtime_id, timeout=180)
source = '''import importlib.metadata, json, sys, time
import jax
import jax.numpy as jnp
devices = jax.devices('tpu')
assert devices
def ver(name):
    try: return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError: return None
with jax.default_device(devices[0]):
    x = jnp.arange(16, dtype=jnp.float32).reshape(4,4)
    y = jax.jit(lambda a: a @ a.T)(x)
    jax.block_until_ready((x,y))
    result = y.tolist()
assert result == [[14.,38.,62.,86.],[38.,126.,214.,302.],[62.,214.,366.,518.],[86.,302.,518.,734.]]
report = {'provenance':'real_tpu_execution','synchronized':True,'result':result,
 'environment':{'runtime_session_id':RUNTIME_ID,'python':sys.version.split()[0],
 'jax':jax.__version__,'jaxlib':ver('jaxlib'),'libtpu':ver('libtpu'),
 'tensorflow':ver('tensorflow'),'xprof':ver('xprof'),
 'tpu_model':','.join(sorted({d.device_kind for d in devices})),
 'device_count':len(devices),'process_count':jax.process_count(),
 'devices':[{'id':d.id,'platform':d.platform,'device_kind':d.device_kind,'process_index':d.process_index,
             'coords':list(d.coords) if getattr(d,'coords',None) is not None else None,
             'core_on_chip':getattr(d,'core_on_chip',None)} for d in devices],
 'xla':{'jaxlib_version':ver('jaxlib'),'backend_platform_version':getattr(getattr(devices[0],'client',None),'platform_version',None)}}}
print('RSI_PROBE:'+json.dumps(report,allow_nan=False))
'''.replace('RUNTIME_ID', repr(runtime_id))
remote_code = ('import subprocess, sys, json\n'
               f'_rsi_probe=subprocess.run([sys.executable,"-B","-c",{source!r}],capture_output=True,text=True,timeout=160)\n'
               '_rsi_result={"returncode":_rsi_probe.returncode,"stdout":_rsi_probe.stdout,"stderr":_rsi_probe.stderr}\n'
               'print("RSI_RESULT:"+json.dumps(_rsi_result))\n')
result = extract_json_result(bridge.execute(remote_code))
directory = ROOT / 'knowledge-site/manifests/phase3-2026-11-12'
(directory / 'connectivity-probe.json').write_text(json.dumps({'source_digest':sha256(source.encode()).hexdigest(),
       'runtime_id':runtime_id, **result},ensure_ascii=False,indent=2),encoding='utf-8')
lines = [line[len('RSI_PROBE:'):] for line in result['stdout'].splitlines() if line.startswith('RSI_PROBE:')]
if result['returncode'] == 0 and len(lines) == 1:
    report=json.loads(lines[0])
    (directory/'environment.json').write_text(json.dumps(report['environment'],ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
else:
    print(json.dumps(result,ensure_ascii=False))
    raise SystemExit(1)
