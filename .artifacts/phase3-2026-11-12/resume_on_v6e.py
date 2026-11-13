"""Observe allocation, restore the isolated environment, run admitted prerequisites.

No allocation calls here. The separately running, user-authorized allocation
loop owns that request. This driver only proceeds on a distinct confirmed ID.
"""
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import sys
import time
d=Path(__file__).resolve().parent
ROOT=d.parents[1]
old=json.loads((d/'allocation-intent-runtime1.json').read_text('utf-8'))
manifest=ROOT/'knowledge-site/manifests/phase3-2026-11-12'

def log(value):
    entry={'utc':datetime.now(timezone.utc).isoformat(),**value}
    with (d/'runtime2-restoration.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(entry)+'\n')
    print(json.dumps(entry),flush=True)

def invoke(script,*args):
    completed=subprocess.run([sys.executable,'-B','-X','utf8',str(d/script),*args],
                             capture_output=True,text=True,encoding='utf-8')
    if completed.returncode:
        # These helpers suppress transport details. Keep their safe traceback locally.
        (d/(script+'.last-error.txt')).write_text(completed.stderr,encoding='utf-8')
        log({'step':script,'status':'helper_failed','exit_code':completed.returncode})
        return None
    return completed.stdout

while True:
    intent=json.loads((d/'allocation-intent.json').read_text('utf-8'))
    if intent.get('status')=='allocated' and intent.get('runtime_id')!=old['runtime_id']:
        break
    time.sleep(20)
log({'step':'allocation','status':'observed_new_runtime','runtime_id':intent['runtime_id']})
while True:
    output=invoke('setup_runtime_environment.py','--poll','--runtime2')
    if output is None:
        time.sleep(20);continue
    value=json.loads(output)
    log({'step':'isolated_environment','status':value['status']})
    if value['status']=='unknown':
        invoke('setup_runtime_environment.py','--runtime2')
    elif value['status']=='ready':break
    elif value['status']=='failed':raise SystemExit('Isolated setup failed; inspect its persisted status before retrying')
    time.sleep(20)
if invoke('probe_isolated.py','--runtime2') is None:raise SystemExit(1)
if invoke('admit_runtime2.py','--bootstrap') is None:raise SystemExit(1)
log({'step':'prerequisites','status':'starting_existing_backend'})
result=subprocess.run([sys.executable,'-B','-X','utf8',str(d/'advance_runtime2.py')])
log({'step':'prerequisites','status':'driver_exit','exit_code':result.returncode})
raise SystemExit(result.returncode)
