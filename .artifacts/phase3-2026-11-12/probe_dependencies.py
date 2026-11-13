import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.workflow.colab_bridge import ColabBridge,extract_json_result
intent=json.loads(Path(__file__).with_name('allocation-intent.json').read_text('utf-8'))
bridge=ColabBridge(r'C:\Users\fengluo\Desktop\colab\google-colab-cli\.venv\Scripts\python.exe',intent['session'],intent['runtime_id'],timeout=60)
source='''import importlib.util, importlib.metadata, json
names=['numpy','yaml','google.protobuf','tensorboard','tensorboard_plugin_profile','xprof','tensorflow','jax.experimental.pallas','jax.experimental.pallas.tpu','jax._src.lib.xla_extension']
result={}
for name in names:
    try:
        s=importlib.util.find_spec(name)
        result[name]={'available':s is not None,'origin':None if s is None else s.origin}
    except Exception as e: result[name]={'available':False,'error_type':type(e).__name__}
print(json.dumps(result))
'''
code='import subprocess,sys,json\n'+f'p=subprocess.run([sys.executable,"-B","-c",{source!r}],capture_output=True,text=True,timeout=40)\n'+'print("RSI_RESULT:"+json.dumps({"returncode":p.returncode,"stdout":p.stdout,"stderr":p.stderr}))'
result=extract_json_result(bridge.execute(code))
(ROOT/'knowledge-site/manifests/phase3-2026-11-12/dependency-probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
