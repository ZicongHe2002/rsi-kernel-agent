"""Current hashes and relative links; no clean-tree or phase-completion claim."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import unquote,urlsplit
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'knowledge-site/manifests/phase3-2026-11-12'


def summarize_real_validation(work, manifest, previous):
    """Summarize observed files without closing any execution/optimization Gate."""
    status=deepcopy(previous)
    status['evidence_summary_observed_at_utc']=datetime.now(timezone.utc).isoformat()
    if (manifest/'runtime1-loss.json').exists():
        status['runtime1']='lost_after_completed_baseline; original evidence retained'
    environment_path=manifest/'environment-isolated-runtime2.json'
    probe_path=manifest/'isolated-probe-runtime2.json'
    if environment_path.exists() and probe_path.exists():
        environment=json.loads(environment_path.read_text('utf-8-sig'))
        probe=json.loads(probe_path.read_text('utf-8-sig'))
        lines=[line.removeprefix('RSI_PROBE:') for line in probe.get('stdout','').splitlines() if line.startswith('RSI_PROBE:')]
        observed=json.loads(lines[-1]) if lines else None
        confirmed=(probe.get('returncode')==0 and isinstance(observed,dict)
                   and observed.get('environment')==environment and observed.get('synchronized') is True
                   and observed.get('result')==140.0 and environment.get('device_count',0)>0)
        status['runtime2_connectivity']={'confirmed_from_probe':confirmed,
            'runtime_id':environment.get('runtime_session_id'),'environment':'environment-isolated-runtime2.json',
            'probe':'isolated-probe-runtime2.json','result':None if observed is None else observed.get('result'),
            'synchronized':False if observed is None else observed.get('synchronized')}
        status['runtime']=('runtime2 allocated and minimal synchronized TPU result observed; original runtime evidence retained'
                           if confirmed else 'runtime2 evidence files present; successful synchronized probe not confirmed')
    elif 'runtime' not in status:
        status['runtime']='current runtime not established by available evidence'
    rows={}
    for name in ('oracle-negative-attention','oracle-negative-cache','baseline-repair','baseline-b'):
        path=work/(name+'-runtime2-result.json')
        if not path.exists():
            rows[name]={'status':'no_result_file_observed','source':path.relative_to(ROOT).as_posix()}
            continue
        result=json.loads(path.read_text('utf-8-sig'))
        rows[name]={'status':'result_file_observed','source':path.relative_to(ROOT).as_posix(),
            **{key:result.get(key) for key in ('run_id','candidate_id','classification','provenance','correctness',
                'confirmed','timing_validity','latency_ms','evaluation_id','profile_id')},
            'receipt_stages':sorted(result.get('receipt_refs',{}))}
    status['runtime2_prerequisite_results']=rows
    status['gate_summary_policy']='Recorded result files only; this helper does not approve or close Gates'
    return status


baseline=json.loads((OUT/'runtime-baseline.json').read_text('utf-8-sig'))
before={r['path']:r['sha256'] for r in baseline}
rows=[]
for p in sorted((ROOT/'src/rsi_kernel_agent').rglob('*.py')):
    rel=p.relative_to(ROOT).as_posix();digest=sha256(p.read_bytes()).hexdigest()
    rows.append({'path':rel,'before_sha256':before.get(rel),'after_sha256':digest,'changed':before.get(rel)!=digest})
protected=[r for r in rows if r['before_sha256'] and not r['path'].startswith('src/rsi_kernel_agent/workflow/')]
assert all(not r['changed'] for r in protected),'Original 2A/2B/2C modules unexpectedly changed'
(OUT/'runtime-current-check.json').write_text(json.dumps({'phase_complete':False,'protected_2abc_unchanged':True,
    'files':rows},indent=2),encoding='utf-8')
docs=list((ROOT/'docs/phase3').glob('*.md'))+list(OUT.glob('*.md'))+[ROOT/'knowledge-site/index.md']
links=[]
for p in docs:
    text=p.read_text('utf-8-sig')
    text=re.sub(r'^\s*```.*?^\s*```\s*$','',text,flags=re.M|re.S)
    text=re.sub(r'`[^`\n]*`','',text)
    for value in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)',text):
        value=value.strip().strip('<>');parsed=urlsplit(value)
        if parsed.scheme or value.startswith(('#','/')):continue
        target=unquote(parsed.path)
        if not target:continue
        links.append({'file':p.relative_to(ROOT).as_posix(),'target':value,'exists':(p.parent/target).resolve().exists()})
missing=[r for r in links if not r['exists']]
(OUT/'links.json').write_text(json.dumps({'documents':len(docs),'links':len(links),'missing':missing,'results':links},
    ensure_ascii=False,indent=2),encoding='utf-8')
env_path=OUT/'test-environment.json'
env=json.loads(env_path.read_text('utf-8'))
log=(OUT/'tests-final.txt').read_bytes()
logtext=log.decode('utf-16') if log.startswith((b'\xff\xfe',b'\xfe\xff')) else log.decode('utf-8-sig')
if 'Ran 315 tests in 583.186s' in logtext and 'OK (skipped=2)' in logtext:
    if not any(r.get('log')=='tests-final.txt' for r in env['commands']):
        row=deepcopy(env['commands'][0]);row.update(tests=315,passed=313,skipped=2,
             unittest_elapsed_seconds=583.186,log='tests-final.txt',full_suite_runs_for_this_result=1)
        env['commands'].append(row)
env['real_validation_status_at_report']=summarize_real_validation(
    ROOT/'.artifacts/phase3-2026-11-12',OUT,env.get('real_validation_status_at_report',{}))
env_path.write_text(json.dumps(env,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'protected_unchanged':len(protected),'runtime_files':len(rows),'documents':len(docs),
                  'links':len(links),'missing':missing},ensure_ascii=False))
assert not missing
