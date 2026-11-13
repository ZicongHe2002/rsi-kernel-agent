"""Archive local verification facts and maintain the authorized weekly record."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[2]
D=Path(__file__).resolve().parent
OUT=ROOT/'knowledge-site/manifests/phase3-2026-11-12'
names=['real_round_aux.py','prepare_real_round.py','run_real_round.py','resume_active_round.py',
       'prepare_confirmation_round.py','run_confirmation_round.py','runtime_readmission.py',
       'admit_runtime3.py','setup_runtime_environment.py','probe_isolated.py','advance_runtime2.py']
report={'observed_at_utc':datetime.now(timezone.utc).isoformat(),
 'scope':'local CPU/synthetic auxiliary tests; not real Runtime3 evidence',
 'tests':[{'file':'test_runtime_binding.py','count':2,'seconds':0.009,'exit_code':0},
          {'file':'test_real_round_aux.py','count':11,'seconds':71.213,'exit_code':0},
          {'file':'test_confirmation_round.py','count':7,'seconds':342.526,'exit_code':0},
          {'file':'test_runtime2_aux.py','count':6,'seconds':1.559,'exit_code':0},
          {'file':'test_runtime_readmission.py','count':5,'seconds':0.658,'exit_code':0}],
 'evidence_source':'executed command outputs in this session; original asyncio diagnostics not suppressed',
 'files':{name:sha256((D/name).read_bytes()).hexdigest() for name in names},
 'runtime3_allocated':False,'round2_executed':False,'exploration_budget_reset':False}
(OUT/'runtime3-preparation-tests.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
source=(D/'update_verified_gates_weekly.py').read_text('utf-8')
start=source.index('MARKER = ');end=source.index('\nurl = ',start)
marker='2026-11-12 RSI Phase 3 第一轮真实实验完成（助手执行）'
entry=('- '+marker+'：4个Plan、5个候选均完成真实TPU执行和三组正确性验证；原始NPZ、完整cache、'
       '回执/身份/证据闭包及计时统计已本地复核。delayed-store-wait单次host median为0.183795ms，'
       '其余四项按原规则更慢；尚无独立Run复测确认的稳定收益。原Runtime随后释放，旧结果完整保留，'
       '第二轮尚未运行。新Runtime绑定/重新准入及确认轮恢复的31项本地测试通过；这些是CPU/synthetic'
       '辅助验证，不是新TPU实验。未勾选无关待办，Phase3未完成。')
source=source[:start]+'MARKER = '+repr(marker)+'\nENTRY = '+repr(entry)+'\n'+source[end:]
source=source.replace('weekly-gates-', 'weekly-round1-').replace('weekly-gates-update.json','weekly-round1-update.json')
path=D/'update_round1_weekly.py'
path.write_text(source,encoding='utf-8')
subprocess.run([sys.executable,'-B','-X','utf8',str(path)],check=True,cwd=ROOT)
print(json.dumps({'tests_recorded':31,'live_runtime_claim':False}))
