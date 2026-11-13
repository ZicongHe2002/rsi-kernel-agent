"""Append verified P3.2/P3.3 progress without rewriting prior weekly entries."""
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'knowledge-site/manifests/phase3-2026-11-12'
RELATIVE = 'knowledge-site/content/待办清单/一周待办 · 2026-11-09.md'
NOTE = '待办清单/一周待办 · 2026-11-09.md'
MARKER = '2026-11-12 RSI Phase 3 P3.2/P3.3 验收进展（助手执行）'
ENTRY = ('- ' + MARKER + '：新独立 TPU v6e Runtime 的 Decode contiguous A/B 同源码基线均完成三组正确性、'
         '20次正式同步计时和各自 XPlane；small 中位数分别0.193195/0.215365ms，属于host同步延迟，'
         '同源码差异不作为优化收益。固定Oracle在真实TPU拒绝attention/cache两个已知错误负例，'
         '独立NPZ重算一致，均未晋升Best。P3.2/P3.3仅在当前BF16 contiguous、small/boundary/default、'
         'window=0/cap=0与默认duration parser范围通过。独立最小JAX诊断四个TRACE模式均成功，'
         '设备事件0/16/16/134；其额外slice/squeeze工作及计数器能力限制已记入报告。'
         '原始工件、独立审查和范围说明已保存；尚无已验证优化收益，两轮真实RSI未完成，'
         '未勾选其他待办。报告为manifests/phase3-2026-11-12/implementation-report.md。')

url = 'http://127.0.0.1:8765/api/note?' + urllib.parse.urlencode({'id': NOTE})
try:
    with urllib.request.urlopen(url, timeout=5) as response:
        api_status = response.status
except urllib.error.HTTPError as error:
    api_status = error.code
except OSError:
    api_status = 'unreachable'
if api_status == 200:
    raise RuntimeError('Revision API is now available; use its returned revision for this write')
if api_status not in (401, 'unreachable'):
    raise RuntimeError(f'Unexpected note API status: {api_status}')

path = ROOT / RELATIVE
report = {'api_status': api_status, 'auth_files_read': False, 'path': RELATIVE,
          'method': 'original-byte snapshot, reread merge, immediate SHA guard, fsync and atomic replace',
          'unrelated_checkboxes_changed': False}
for attempt in range(5):
    before = path.read_bytes()
    digest = sha256(before).hexdigest()
    text = before.decode('utf-8-sig')
    report['before_sha256'] = digest
    if MARKER in text:
        report.update(changed=False, after_sha256=digest)
        break
    if '## 完成记录' not in text:
        raise RuntimeError('Expected completion-record section is missing')
    snapshot = OUT / 'before' / ('weekly-gates-' + digest + '.md.original')
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    if not snapshot.exists():
        snapshot.write_bytes(before)
    newline = '\r\n' if '\r\n' in text else '\n'
    addition = ('' if text.endswith(('\n', '\r')) else newline) + newline + ENTRY + newline
    after = before + addition.encode('utf-8')
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.phase3-gates-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(after)
            stream.flush()
            os.fsync(stream.fileno())
        if sha256(path.read_bytes()).hexdigest() != digest:
            continue
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    if path.read_bytes() != after:
        raise RuntimeError('Weekly content changed after publication; inspect and merge')
    assert after.startswith(before)
    report.update(changed=True, after_sha256=sha256(after).hexdigest(),
                  snapshot=str(snapshot.relative_to(ROOT)).replace('\\', '/'), attempts=attempt + 1,
                  original_prefix_bytes_preserved=len(before))
    break
else:
    raise RuntimeError('Repeated concurrent weekly edits; no replacement performed')

(OUT / 'weekly-gates-update.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
