"""One-off report/index/weekly maintenance; no Store or TPU operations."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'knowledge-site/manifests/phase3-2026-11-12'
note='待办清单/一周待办 · 2026-11-09.md'
try:
    with urllib.request.urlopen('http://127.0.0.1:8765/api/note?'+urllib.parse.urlencode({'id':note}),timeout=5) as r:
        api_status=r.status
except urllib.error.HTTPError as e: api_status=e.code
except OSError: api_status='unreachable'
if api_status not in (401,'unreachable'):
    raise RuntimeError('Use available revision API before changing notes: '+str(api_status))
rows=[]
def update(relative,transform):
    path=ROOT/relative
    for attempt in range(5):
        before=path.read_bytes(); digest=sha256(before).hexdigest()
        text=before.decode('utf-8-sig'); newline='\r\n' if '\r\n' in text else '\n'
        changed=transform(text.replace('\r\n','\n')).replace('\n',newline)
        after=(b'\xef\xbb\xbf' if before.startswith(b'\xef\xbb\xbf') else b'')+changed.encode('utf-8')
        if before==after:return
        backup=OUT/'before'/('report-status-2026-11-13-'+digest+'.md.original')
        if not backup.exists():backup.write_bytes(before)
        fd,name=tempfile.mkstemp(dir=path.parent,prefix='.report-status-')
        try:
            with os.fdopen(fd,'wb') as f:f.write(after);f.flush();os.fsync(f.fileno())
            if path.read_bytes()!=before:continue
            os.replace(name,path)
        finally:Path(name).unlink(missing_ok=True)
        if path.read_bytes()!=after:raise RuntimeError('Concurrent edit after publication')
        rows.append({'path':relative,'before_sha256':digest,'after_sha256':sha256(after).hexdigest(),
                     'snapshot':backup.relative_to(ROOT).as_posix()})
        return
    raise RuntimeError('Concurrent edits require remerge')
current=('> CURRENT｜2026-11-13。Phase 3已完成真实TPU v6e上的Decode基线、两类Oracle负例、XPlane与首轮4 Plan / 5候选；'
         '五项正确性通过，一项单次更快，尚无复测确认的稳定收益。第二轮未开始；今天10:04控制面无Runtime，申请循环已于昨晚分配成功后退出。'
         '整体状态以Phase 3进展报告为准；后文Phase 2范围与停止点保留为历史阶段背景。')
for relative in ('knowledge-site/content/学习方法/rsi-document-index.md',
                 'knowledge-site/content/学习方法/rsi-kernel-agent.md',
                 'knowledge-site/content/学习方法/rsi-optimization-workflow.md',
                 'knowledge-site/content/学习方法/RSI实践/rsi-phase2-roadmap.md','knowledge-site/README.md'):
    def transform(text):
        text,count=re.subn(r'(?m)(<!-- phase3-current-status -->\n)> CURRENT[^\n]*',lambda m:m.group(1)+current,text,count=1)
        if count!=1:raise ValueError('Expected current status marker')
        if relative.endswith('rsi-document-index.md'):
            text=text.replace('两轮 synthetic 闭环不代表真实优化，Phase 3 未开始','Phase 3已完成首轮真实Decode实验，第二轮与最终验收未完成')
        return text
    update(relative,transform)
update('knowledge-site/index.md',lambda t:t.replace('真实 TPU v6e、Decode 修复基线与 XPlane 已返回；负例、优化与多轮闭环待验收',
    '2026-11-13：真实Decode基线、负例、Profile和首轮5候选完成；第二轮及稳定收益未验证，当前无Runtime'))
marker='2026-11-13 Phase 3 总体进展报告整理（助手执行）'
entry=('- '+marker+'：已重写Phase 3实现报告，集中列出四目录审计、真实执行模块、历史知识、Decode基线/Oracle负例、Profile、'
       '首轮4 Plan/5候选结果、恢复时间线、321项已保存全套测试及31项辅助测试、范围限制和下一阶段条件。'
       '修正“首轮尚未执行”“持续申请中”等陈旧状态；今天10:04只读查询确认Runtime列表为空，昨晚分配成功后申请循环已退出。'
       '第二轮未开始，稳定性能收益未确认，Phase3未完成；本次仅整理与核验报告、同步索引，未重跑测试或执行TPU，未勾选优化目标。'
       '报告：manifests/phase3-2026-11-12/implementation-report.md。')
update('knowledge-site/content/'+note,lambda t:t if marker in t else t.rstrip()+'\n\n'+entry+'\n')
update('docs/phase3/phase3-optimization-report.md',lambda t:t.replace(
    '第一轮完成后该 Runtime 已释放，正在申请新 v6e。',
    '第一轮完成后该Runtime已释放；新v6e于11月12日22:54分配成功，申请循环随即退出，但11月13日10:04查询已无Runtime，第二轮未执行。').replace(
    '当前持续按用户授权申请新的 TPU v6e。',
    '随后于11月12日22:54申请到新v6e，循环退出；11月13日10:04再次查询列表为空，尚无新环境准入或第二轮结果。'))
update('docs/phase3/phase3-real-execution.md',lambda t:t if '<!-- report-status-20261113 -->' in t else t.replace('\n\n',
    '\n\n<!-- report-status-20261113 -->\n> 2026-11-13状态索引：真实Oracle负例、基线、Profile和首轮5候选均已完成；第二轮和稳定收益未确认，当前无Runtime。下文接口说明中的“当前/尚未”及测试数量记录其原撰写时点；最新总体结论见 [Phase 3进展报告](../../knowledge-site/manifests/phase3-2026-11-12/implementation-report.md)。\n\n',1))
(OUT/'overall-report-maintenance-2026-11-13.json').write_text(json.dumps({'api_status':api_status,'auth_files_read':False,
    'changes':rows,'store_writes':0,'hardware_execution':False},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'updated':[r['path'] for r in rows],'api_status':api_status},ensure_ascii=False))
