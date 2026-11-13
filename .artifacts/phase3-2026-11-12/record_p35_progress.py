"""Append confirmed P3.5 progress to the authorized weekly local note."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.parse
import urllib.request

root = Path(__file__).resolve().parents[2]
work = Path(__file__).resolve().parent
manifest = root / 'knowledge-site/manifests/phase3-2026-11-12'
note_id = '待办清单/一周待办 · 2026-11-09.md'
try:
    with urllib.request.urlopen('http://127.0.0.1:8765/api/note?' + urllib.parse.urlencode({'id': note_id}), timeout=5) as response:
        status = response.status
except urllib.error.HTTPError as error:
    status = error.code
except OSError:
    status = 'unreachable'
if status not in (401, 'unreachable'):
    raise ValueError('Use the available revision API before publishing: ' + str(status))
test = json.loads((work / 'p35-root-tests-2026-11-13-result.json').read_text('utf-8-sig'))
if test['exit_code'] != 0:
    raise ValueError('Root regression has not passed')
for name in ('attention', 'cache'):
    result = json.loads((work / ('oracle-negative-' + name + '-runtime3-result.json')).read_text('utf-8'))
    if result['classification'] != 'incorrect' or result['confirmed'] is not False:
        raise ValueError('Both Oracle controls must have actual rejection results')
marker = '2026-11-13 P3.5 新Runtime与软件回归（助手执行）'
entry = ('- ' + marker + '：按明确授权持续申请后，于15:08取得TPU v6e；隔离环境同步探针返回140.0。'
    '新Runtime的attention/cache两类故意错误Candidate均已被原Oracle拒绝，未进入Benchmark/Profile；'
    'A基线、B候选及独立B对照仍在准入队列，ABBA与第二轮尚未完成。'
    '本次重新运行根级321项测试：318通过、3跳过、0失败，625.063秒，exit0。'
    '冻结复测规则、来源校验和新Snapshot门禁等本地改进已记录；未确认稳定收益，未勾选最终优化目标。'
    '进展见 docs/phase3/phase3-p35-final-validation.md。')
path = root / 'knowledge-site/content' / note_id
before = path.read_bytes()
text = before.decode('utf-8-sig')
if marker not in text:
    digest = sha256(before).hexdigest()
    backup = manifest / 'before' / ('weekly-before-p35-' + digest + '.md.original')
    if not backup.exists():
        backup.write_bytes(before)
    newline = '\r\n' if '\r\n' in text else '\n'
    after = (b'\xef\xbb\xbf' if before.startswith(b'\xef\xbb\xbf') else b'') + (
        text.rstrip() + newline * 2 + entry + newline).encode('utf-8')
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.p35-weekly-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(after); stream.flush(); os.fsync(stream.fileno())
        if path.read_bytes() != before:
            raise ValueError('Concurrent weekly update; reread and merge required')
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    result = {'updated': True, 'before_sha256': digest, 'after_sha256': sha256(after).hexdigest(),
              'backup': str(backup.relative_to(root))}
else:
    result = {'updated': False, 'reason': 'already recorded'}
result.update(utc=datetime.now(timezone.utc).isoformat(), api_status=status, auth_files_read=False)
(manifest / 'p35-weekly-progress.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result))
