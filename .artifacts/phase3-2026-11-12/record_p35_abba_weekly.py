"""Update the authorized weekly completion record with verified ABBA progress."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.request
import urllib.parse
import urllib.error
d=Path(__file__).resolve().parent
root=d.parents[1]
pointer=json.loads((d/'p35-confirmation-result.json').read_text('utf-8'))
ref=pointer['artifact_ref']
raw=(d/'artifacts/sha256'/ref['digest'][:2]/ref['digest']).read_bytes()
assert len(raw)==ref['size'] and sha256(raw).hexdigest()==ref['digest']
report=json.loads(raw)
assert report['statistics']['dimensions']['host']['classification']=='inconclusive_drift'
assert report['statistics']['dimensions']['program']['classification']=='replicated_observed_improvement'
root_test=json.loads((d/'p35-root-tests-final-2026-11-13-result.json').read_text('utf-8-sig'))
assert root_test['exit_code']==0
note_id='待办清单/一周待办 · 2026-11-09.md'
try:
    with urllib.request.urlopen('http://127.0.0.1:8765/api/note?'+urllib.parse.urlencode({'id':note_id}),timeout=5) as response:
        api_status=response.status
except urllib.error.HTTPError as error:
    api_status=error.code
except OSError:
    api_status='unreachable'
assert api_status in (401,'unreachable'),'Revision API is available; use it to merge'
path=root/'knowledge-site/content'/note_id
before=path.read_bytes()
text=before.decode('utf-8-sig')
marker='2026-11-13 P3.5 新Runtime与软件回归（助手执行）'
assert text.count(marker)==1
entry='- '+marker+'：15:08取得TPU v6e，隔离环境同步探针140.0；五项重新准入已完成，独立复核5 verified/0 failed，198个工件及原始NPZ核验通过，两个错误负例均被Oracle拒绝。ABBA四Run已完成并写回原Memory，program两对median降低约6.50%/5.92%，host因漂移为inconclusive_drift，未确认稳定端到端收益。第二轮已从新Snapshot version10（776 records/high-watermark801）读取完整A/B历史，4个新Plan通过Analyzer并打包，真实执行进行中；单tile硬件诊断尚未完成。最新根级321项测试318通过/3跳过（567.523秒、exit0），确认恢复专项18/18、诊断派发11/11通过。未勾选最终优化目标，进展见 docs/phase3/phase3-p35-final-validation.md。'
updated=re.sub(r'^- '+re.escape(marker)+r'[^\r\n]*',lambda _:entry,text,count=1,flags=re.M)
assert updated!=text
backup=root/'knowledge-site/manifests/phase3-2026-11-12/before'/('weekly-before-abba-'+sha256(before).hexdigest()+'.original')
if not backup.exists(): backup.write_bytes(before)
after=(b'\xef\xbb\xbf' if before.startswith(b'\xef\xbb\xbf') else b'')+updated.encode()
fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.p35-weekly-')
try:
    with os.fdopen(fd,'wb') as out:
        out.write(after);out.flush();os.fsync(out.fileno())
    assert path.read_bytes()==before,'Concurrent note edit; reread and merge'
    os.replace(tmp,path)
finally:
    Path(tmp).unlink(missing_ok=True)
assert path.read_bytes()==after
print(json.dumps({'utc':datetime.now(timezone.utc).isoformat(),'api_status':api_status,
                  'path':str(path),'before':sha256(before).hexdigest(),'after':sha256(after).hexdigest()}))
