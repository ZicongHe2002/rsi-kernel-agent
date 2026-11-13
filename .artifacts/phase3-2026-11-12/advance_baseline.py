"""Poll the prerequisite baseline helper; all execution uses the same backend IDs."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

root=Path(__file__).resolve().parent
repair='--repair' in sys.argv
while True:
    cmd=[sys.executable,'-B','-X','utf8',str(root/'start_baseline.py')]
    if repair:cmd.append('--repair')
    result=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8')
    if result.returncode:
        print(result.stderr,flush=True)
        raise SystemExit(result.returncode)
    value=json.loads(result.stdout)
    entry={'utc':datetime.now(timezone.utc).isoformat(),**value}
    with (root/('baseline-repair-progress.jsonl' if repair else 'baseline-progress.jsonl')).open('a',encoding='utf-8') as log:
        log.write(json.dumps(entry,ensure_ascii=False)+'\n')
    print(json.dumps(entry,ensure_ascii=False),flush=True)
    if 'result' in value:break
    time.sleep(15)
