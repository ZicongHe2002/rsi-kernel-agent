"""Record verified admission and the observed confirmation journal, preserving history."""
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.request
import urllib.parse
import urllib.error

d = Path(__file__).resolve().parent
root = d.parents[1]
manifest = root / 'knowledge-site/manifests/phase3-2026-11-12'
report = json.loads((d / 'runtime3-prerequisite-verification.json').read_text('utf-8'))
assert report['status'] == 'runtime3_prerequisites_verified'
target = d / 'confirmation-f686274a57bb7ebb'
state = json.loads((target / 'controller-status.json').read_text('utf-8'))
selection = json.loads((target / 'selection.json').read_text('utf-8'))
prepared = json.loads((target / 'prepared.json').read_text('utf-8'))
session = json.loads((target / 'session.json').read_text('utf-8'))
assert session['result_digest'] is not None
jobs = [j for j in state['jobs'].values() if j['round_id'] == selection['round_id']]
assert len(jobs) == 4
now = datetime.now(timezone.utc)
local = now.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
snapshot = next(iter(prepared['histories'].values()))['snapshot']
observed = {j['variant']['label']: {'state': j['state'], 'stage': j['stage'], 'run_id': j['run_id']} for j in jobs}
data = {'observed_at_utc': now.isoformat(), 'scope': 'archived controller-status snapshot, not a new device query',
        'admission_verification': report['status'], 'controller_revision': state['revision'],
        'spent': state['spent'], 'snapshot': snapshot, 'confirmation_jobs': observed}
stamp = now.strftime('%Y%m%dT%H%M%S%fZ')
(manifest / ('p35-progress-' + stamp + '.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
marker = '<!-- p35-admission-complete-progress -->'
states = '；'.join(f"{r['label']}：{observed[r['label']]['state']}/{observed[r['label']]['stage']}" for r in selection['sequence'])
summary = (f'**P3.5 更新｜{local}（Asia/Shanghai，进行中）**：新 TPU v6e 的五项重新准入已完成并独立复核，'
    f'**5 verified、0 pending、0 failed**，核验 {report["verified_unique_artifacts"]} 个去重工件、'
    f'{report["verified_unique_artifact_bytes"]:,} bytes；原始 NPZ attention/cache 已重算。'
    '两个故意错误的 Candidate 均被 Oracle 拒绝；A 基线、delayed-store-wait 与独立 B control 均通过三组正确性及五阶段执行。'
    '三项准入 small host median 分别为 0.232400、0.217205、0.228915 ms；这些单次准入值不构成稳定收益结论。')
status = (f'确认轮已通过原 Analyzer：2 Plan / 4 变体，review 无结构性降级、winner_selected=false；'
    f'输入 Snapshot version {snapshot["version"]} / {snapshot["record_count"]} records / high-watermark {snapshot["high_watermark"]}，'
    'B 路线完整历史含依赖共 315 条。已冻结 C–T–T–C 顺序与 2%/5% 判据。'
    f'最近保存的 Controller revision {state["revision"]} 状态为：{states}。'
    f'原探索预算已用 {state["spent"]["executions"]} / 16 executions、{state["spent"]["time_s"]:g} / 28800 声明秒。'
    '桥接 unknown 按原 operation 查询恢复，不盲目重跑。ABBA、第二轮与独立单 tile 硬件诊断尚未完成。')
tests = ('本次根级测试 321 项（318 通过、3 跳过），625.063 秒，退出码 0；完整 P3.5 辅助专项 68 项通过，103.970 秒。'
    '新增诊断派发专项 7 项通过，48.541 秒，退出码 0；均为本地测试，不代替硬件验收。')

def replace(path, transform):
    before = path.read_bytes()
    text = before.decode('utf-8-sig')
    updated = transform(text)
    if updated == text:
        return
    digest = sha256(before).hexdigest()
    backup = manifest / 'before' / (path.stem + '-before-admission-update-' + digest + '.original')
    if not backup.exists():
        backup.write_bytes(before)
    after = (b'\xef\xbb\xbf' if before.startswith(b'\xef\xbb\xbf') else b'') + updated.encode('utf-8')
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.p35-update-')
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(after); out.flush(); os.fsync(out.fileno())
        assert path.read_bytes() == before, 'Concurrent document edit; reread and merge'
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)

def insert(text, block):
    end = '<!-- /p35-admission-complete-progress -->'
    full = marker + '\n' + block + '\n' + end
    if marker in text:
        return re.sub(re.escape(marker) + r'.*?' + re.escape(end), lambda _: full, text, count=1, flags=re.S)
    head, rest = text.split('\n', 1)
    return head + '\n\n' + full + '\n' + rest

links = ('[五项准入核验](../../../.artifacts/phase3-2026-11-12/runtime3-prerequisite-verification.json)、'
         '[确认轮](../../../.artifacts/phase3-2026-11-12/confirmation-f686274a57bb7ebb/selection.json)、'
         '[确认轮状态](../../../.artifacts/phase3-2026-11-12/confirmation-f686274a57bb7ebb/controller-status.json)。')
replace(manifest / 'implementation-report.md', lambda t: insert(t, '> ' + summary + '\n>\n> ' + status + '\n>\n> ' + tests + ' ' + links + '\n>\n> 以下带旧时间的段落保留为历史记录，以本次更新为较新进展。'))
replace(root / 'docs/phase3/phase3-p35-final-validation.md', lambda t: insert(t, summary + '\n\n' + status + '\n\n' + tests + '\n\n' + links.replace('../../../', '../../') + '\n\n以下 15:15:54 的审计保留为历史状态，不代表准入仍在等待。'))
note_id = '待办清单/一周待办 · 2026-11-09.md'
try:
    with urllib.request.urlopen('http://127.0.0.1:8765/api/note?' + urllib.parse.urlencode({'id': note_id}), timeout=5) as response:
        api_status = response.status
except urllib.error.HTTPError as error:
    api_status = error.code
except OSError:
    api_status = 'unreachable'
assert api_status in (401, 'unreachable'), 'Available revision API must be used'
weekly_marker = '2026-11-13 P3.5 新Runtime与软件回归（助手执行）'
entry = '- ' + weekly_marker + '：15:08取得TPU v6e，隔离环境同步探针140.0；五项重新准入已完成，独立复核5 verified/0 failed，198个工件及原始NPZ核验通过，两个错误负例均被Oracle拒绝。确认轮2 Plan/4变体已进入原Controller；ABBA、第二轮及单tile硬件诊断尚未完成。根级321项测试318通过/3跳过，辅助专项68项通过，新增诊断派发7项通过。未确认稳定收益，未勾选最终优化目标。进展见 docs/phase3/phase3-p35-final-validation.md。'
replace(root / 'knowledge-site/content' / note_id,
        lambda t: re.sub(r'^- ' + re.escape(weekly_marker) + r'[^\r\n]*', lambda _: entry, t, count=1, flags=re.M))
print(json.dumps({'updated_at': local, 'journal_revision': state['revision'], 'api_status': api_status,
                  'evidence': str(manifest / ('p35-progress-' + stamp + '.json'))}))
