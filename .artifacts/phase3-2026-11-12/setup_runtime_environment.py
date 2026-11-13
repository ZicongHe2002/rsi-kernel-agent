"""Reuse historical Colab isolated uv environment preparation, without global updates."""
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.workflow.colab_bridge import ColabBridge,extract_json_result
from runtime_readmission import pin_observation_runtime,save_observation,parse_observation_args
args=parse_observation_args(allow_poll=True)
intent=json.loads(Path(__file__).with_name('allocation-intent.json').read_text('utf-8'))
suffix=args.suffix
pin_observation_runtime(Path(__file__).parent,suffix,intent)
bridge=ColabBridge(r'C:\Users\fengluo\Desktop\colab\google-colab-cli\.venv\Scripts\python.exe',intent['session'],intent['runtime_id'],timeout=60)
setup='''from pathlib import Path
import os,subprocess,sys,json,shutil,time
root=Path('/content/rsi-kernel-agent/env-20261112')
status_path=root/'status.json'
status={'status':'running','started_ns':time.time_ns()}
status_path.write_text(json.dumps(status))
env=dict(os.environ,UV_CACHE_DIR=str(root/'uv_cache'),UV_PYTHON_INSTALL_DIR=str(root/'python'))
for key in ('XLA_FLAGS','LIBTPU_INIT_ARGS','JAX_PLATFORMS','JAX_PLATFORM_NAME','PYTHONPATH'): env.pop(key,None)
try:
    uv=shutil.which('uv')
    if not uv:
        subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','--target',str(root/'bootstrap'),'uv'],check=True,env=env)
        uv=str(root/'bootstrap/bin/uv')
    subprocess.run([uv,'python','install','3.11'],check=True,env=env)
    subprocess.run([uv,'venv','--python','3.11',str(root/'venv')],check=True,env=env)
    python=str(root/'venv/bin/python')
    subprocess.run([uv,'pip','install','--python',python,'jax[tpu]==0.8.1','jaxlib==0.8.1','libtpu==0.0.30',
                    'tensorflow==2.20.0','xprof==2.22.1','PyYAML','pytest'],check=True,env=env)
    code="import importlib.metadata as m,json,sys; print(json.dumps({'python':sys.version,'executable':sys.executable,'versions':{n:m.version(n) for n in ['jax','jaxlib','libtpu','tensorflow','xprof','numpy','PyYAML']}}))"
    result=subprocess.run([python,'-B','-c',code],capture_output=True,text=True,check=True,env=env)
    status.update(status='ready',**json.loads(result.stdout))
except Exception as exc:
    status.update(status='failed',error_type=type(exc).__name__)
    raise
finally:
    status['finished_ns']=time.time_ns()
    status_path.write_text(json.dumps(status))
'''
if args.poll:
    code="from pathlib import Path\nimport json\np=Path('/content/rsi-kernel-agent/env-20261112/status.json')\nr=json.loads(p.read_text()) if p.exists() else {'status':'unknown'}\nprint('RSI_RESULT:'+json.dumps(r))"
else:
    code=('from pathlib import Path\nimport subprocess,sys,json,os\n'
          "r=Path('/content/rsi-kernel-agent/env-20261112');r.mkdir(parents=True,exist_ok=True)\n"
          "assert not (r/'launched.json').exists(), 'setup already submitted; poll it'\n"
          f"(r/'setup.py').write_text({setup!r})\n"
          "with (r/'setup.log').open('w') as log:\n"
          "    p=subprocess.Popen([sys.executable,'-B',str(r/'setup.py')],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)\n"
          "(r/'launched.json').write_text(json.dumps({'pid':p.pid}))\n"
          "print('RSI_RESULT:'+json.dumps({'status':'setup_started','pid':p.pid}))")
result=extract_json_result(bridge.execute(code))
save_observation(ROOT/('knowledge-site/manifests/phase3-2026-11-12/isolated-environment-setup'+suffix+'.json'),result)
print(json.dumps(result))
