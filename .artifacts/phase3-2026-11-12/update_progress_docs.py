"""Maintain current status and authorized weekly progress with guarded byte writes."""
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.parse
import urllib.request
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'knowledge-site/manifests/phase3-2026-11-12'
rows=[]
def change(relative,transform):
    p=ROOT/relative
    before=p.read_bytes()
    text=before.decode('utf-8-sig')
    newline='\r\n' if '\r\n' in text else '\n'
    result=transform(text.replace('\r\n','\n'))
    after=(b'\xef\xbb\xbf' if before.startswith(b'\xef\xbb\xbf') else b'')+result.replace('\n',newline).encode('utf-8')
    if before==after:return
    snapshot=OUT/'before'/(relative+'.original')
    snapshot.parent.mkdir(parents=True,exist_ok=True)
    if not snapshot.exists():snapshot.write_bytes(before)
    fd,name=tempfile.mkstemp(dir=p.parent,prefix='.phase3-')
    try:
        with os.fdopen(fd,'wb') as f:f.write(after);f.flush();os.fsync(f.fileno())
        if p.read_bytes()!=before:raise RuntimeError('Concurrent edit; reread and merge: '+relative)
        os.replace(name,p)
    finally:Path(name).unlink(missing_ok=True)
    rows.append({'path':relative,'before_sha256':sha256(before).hexdigest(),'after_sha256':sha256(after).hexdigest()})

status='Phase 3 已获授权并实施中：真实 TPU v6e Decode 修复基线已完成编译、三组正确性、计时和 XPlane；Oracle 负例与两轮优化仍待验收，详见当前 Phase 3 进展报告。'
def current(text):
    marker='<!-- phase3-current-status -->'
    if marker in text:return text
    lines=text.split('\n',2)
    return lines[0]+'\n\n'+marker+'\n> CURRENT｜2026-11-12。'+status+' 后文 Phase 2 的范围与停止点保留为阶段背景；当前请求以 Phase 3 为准。\n\n'+'\n'.join(lines[1:]).lstrip('\n')
for path in ('knowledge-site/content/学习方法/rsi-document-index.md','knowledge-site/content/学习方法/rsi-kernel-agent.md',
             'knowledge-site/content/学习方法/rsi-optimization-workflow.md','knowledge-site/README.md'):
    change(path,current)
def index(text):
    text=text.replace('2D+ 已实现，已通过本地验收，等待用户审查；Phase 3 未开始','2D+ 已本地验收；Phase 3 真实集成进行中，完整硬件闭环未验收')
    row='| [Phase 3进展报告](manifests/phase3-2026-11-12/implementation-report.md) · [实施计划](../docs/phase3/phase3-implementation-plan.md) | 真实 TPU v6e、Decode 修复基线与 XPlane 已返回；负例、优化与多轮闭环待验收 |\n'
    if '[Phase 3进展报告]' not in text:text=text.replace('| [Phase 2D+实现报告]',row+'| [Phase 2D+实现报告]',1)
    return text
change('knowledge-site/index.md',index)
def roadmap(text):
    text=current(text)
    text=text.replace('- 不运行 TPU 作业，不修改 CLI 配置/认证，不改变全局 Codex 配置、账号权限或 `.auth`。',
        '- Phase 3 已授权复用原 Colab 桥接执行 TPU v6e；只操作专用 Runtime 和隔离 Candidate。不读取认证文件，不改变全局配置、账号权限或 `.auth`。')
    text=text.replace('- 不安装新的 Skill 或依赖；复用已有解释器与标准库，不创建仅为凑架构的空模块。',
        '- 本地复用已有解释器与依赖；远端采用任务专属隔离环境，保留默认环境，不升级全局依赖。')
    return text
change('knowledge-site/content/学习方法/RSI实践/rsi-phase2-roadmap.md',roadmap)
week='knowledge-site/content/待办清单/一周待办 · 2026-11-09.md'
note='待办清单/一周待办 · 2026-11-09.md'
url='http://127.0.0.1:8765/api/note?'+urllib.parse.urlencode({'id':note})
try:
    with urllib.request.urlopen(url,timeout=5) as response:api_status=response.status
except urllib.error.HTTPError as e:api_status=e.code
except OSError:api_status='unreachable'
entry='- 2026-11-12 RSI Phase 3 阶段进展（助手执行）：完成四目录审计及 project2 历史恢复，10项来源方法通过原MCP保存；已用既有Colab桥接在真实TPU v6e完成Decode兼容修复基线三组正确性、20次正式同步计时和XPlane回收。small中位数0.220715ms属于host同步延迟，不代表已优化或大shape性能。权威源码及原Oracle未改；原始工件和失败保留在原Store。Oracle负例和两轮真实优化尚未验收，Phase3继续中；报告为manifests/phase3-2026-11-12/implementation-report.md。'
if api_status==200:
    raise RuntimeError('Authenticated note API is available; use revision-aware API for weekly write')
for attempt in range(3):
    try:
        change(week,lambda text:text if '2026-11-12 RSI Phase 3 阶段进展（助手执行）' in text else text.rstrip()+'\n\n'+entry+'\n')
        break
    except RuntimeError:
        if attempt==2:raise
(OUT/'current-docs-progress.json').write_text(json.dumps({'api_status':api_status,'auth_files_read':False,
    'method':'reread, original-byte snapshot, hash guard, fsync atomic replace','changes':rows},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'changed':[r['path'] for r in rows],'weekly_api_status':api_status},ensure_ascii=False))
