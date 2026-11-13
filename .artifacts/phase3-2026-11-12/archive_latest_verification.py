"""Archive completed session 99027 facts; never runs tests or changes Gates."""
import ast
from copy import deepcopy
from datetime import datetime,timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile

ROOT=Path(__file__).resolve().parents[2]
WORK=Path(__file__).resolve().parent
OUT=ROOT/'knowledge-site/manifests/phase3-2026-11-12'


def replace_if_unchanged(path,before,after):
    fd,name=tempfile.mkstemp(prefix='.verified-progress-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(after);stream.flush();os.fsync(stream.fileno())
        if path.exists() and path.read_bytes()!=before:
            raise RuntimeError('Concurrent edit; reread instead of overwriting: '+str(path))
        os.replace(name,path)
    finally:Path(name).unlink(missing_ok=True)


def main():
    name='tests-final-after-chunk-cache.txt'
    raw=(WORK/name).read_bytes()
    encoding='utf-16' if raw[:2] in (bytes([255,254]),bytes([254,255])) else 'utf-8-sig'
    text=raw.decode(encoding)
    if 'Ran 321 tests in 477.822s' not in text or 'OK (skipped=3)' not in text:
        raise ValueError('Expected completed session 99027 output was not found')
    skip_blocks=re.findall(r'(?m)^test_.*?(?=^test_|\Z)',text,flags=re.S)
    skipped=[block for block in skip_blocks if '... skipped ' in block]
    if len(skipped)!=3:
        raise ValueError('Expected all three skipped cases in the retained log')
    skip_details=[]
    for block in skipped:
        test_name=block.split(' (',1)[0]
        exact=block.split('... skipped ',1)[1].strip()
        normalized=('Windows symlink creation permission unavailable: WinError 1314'
                    if 'WinError 1314' in exact else 'System-owned Debian Python startup file is not present on this Windows host')
        skip_details.append({'test':test_name,'reason':normalized,'raw_log_reason':exact})
    expected={'test_symlink_object_is_rejected_when_platform_allows_symlinks',
              'test_shared_chunk_symlink_is_rejected_without_following_it',
              'test_system_owned_startup_hook_is_allowed_without_allowing_other_etc_files'}
    if {row['test'] for row in skip_details}!=expected:
        raise ValueError('Skip names differ from inspected output')
    destination=OUT/name
    if destination.exists() and destination.read_bytes()!=raw:
        raise ValueError('Archived log already has different bytes')
    if not destination.exists():replace_if_unchanged(destination,b'',raw)
    assert destination.read_bytes()==raw

    env_path=OUT/'test-environment.json'
    before=env_path.read_bytes();env=json.loads(before)
    mcp_before=[deepcopy(row) for row in env['commands'] if row.get('log')=='existing-mcp-tests.txt']
    command={'cwd':str(ROOT),'command':[env['executable'],'-B','-m','unittest','discover','-s','tests','-v'],
        'exit_code':0,'exit_code_source':'Root observed completed unified exec session 99027',
        'tests':321,'passed':318,'skipped':3,'failures':0,'errors':0,'unittest_elapsed_seconds':477.822,
        'log':name,'log_sha256':sha256(raw).hexdigest(),'log_size':len(raw),'log_encoding':encoding,
        'archived_original_bytes':True,'full_suite_runs_for_this_result':1,'skip_reasons':skip_details,
        'environment_note':'Inherited process environment was not separately captured for session 99027; command did not specify -X utf8'}
    existing=[row for row in env['commands'] if row.get('log')==name]
    if existing and existing!=[command]:raise ValueError('Existing command entry differs; inspect before replacing')
    if not existing:env['commands'].append(command)
    env['real_environment_evidence'].update(isolated_runtime2='environment-isolated-runtime2.json',
        connectivity_isolated_runtime2='isolated-probe-runtime2.json',isolated_setup_runtime2='isolated-environment-setup-runtime2.json')
    helper=WORK/'check_progress_inventory.py'
    tree=ast.parse(helper.read_text('utf-8'))
    function=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='summarize_real_validation')
    namespace={'deepcopy':deepcopy,'datetime':datetime,'timezone':timezone,'json':json,'ROOT':ROOT}
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(helper),'exec'),namespace)
    prior=env.get('real_validation_status_at_report',{})
    gate_keys=('trusted_baseline','verified_performance_improvement','phase3_complete')
    gates={key:prior.get(key) for key in gate_keys}
    env['real_validation_status_at_report']=namespace['summarize_real_validation'](WORK,OUT,prior)
    assert {key:env['real_validation_status_at_report'].get(key) for key in gate_keys}==gates
    assert [row for row in env['commands'] if row.get('log')=='existing-mcp-tests.txt']==mcp_before
    replace_if_unchanged(env_path,before,(json.dumps(env,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    print(json.dumps({'archived_log':str(destination.relative_to(ROOT)),'sha256':sha256(raw).hexdigest(),
        'tests':321,'passed':318,'skipped':skip_details,'mcp_entry_unchanged':True,
        'runtime_summary':env['real_validation_status_at_report']['runtime'],'gate_fields_preserved':gates},ensure_ascii=False))


if __name__=='__main__':main()
