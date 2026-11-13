import json,time
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.workflow.__main__ import colab_remote
r=colab_remote(Path(__file__).with_name('colab-config.json'))
digest='e79b7b88ae3d6d6bba7ba755d9305bab46c62216b122381cd2496f6a82c10242'
start=time.monotonic()
try:
    r.bridge.get(r.root+'/transfers/'+digest+'/2',Path(__file__).with_name('transfer-probe.part'))
    result={'status':'downloaded','bytes':Path(__file__).with_name('transfer-probe.part').stat().st_size}
except Exception as error:
    result={'status':'failed','error_type':type(error).__name__,'sanitized_reason':str(error)}
result['seconds']=time.monotonic()-start
print(json.dumps(result))
