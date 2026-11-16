"""Offline read-only audit of retained Analyzer inputs, exchanges and source lineage."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import difflib
import gc
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / '.artifacts/phase3-2026-11-12'
OUT = Path(__file__).parent
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.profiler.tools import AnalysisTools

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

class ReadOnlyArtifacts:
    def path(self, ref):
        d = ref['digest'] if isinstance(ref, dict) else ref.digest
        return BASE / 'artifacts/sha256' / d[:2] / d

    def read(self, ref):
        d = ref['digest'] if isinstance(ref, dict) else ref.digest
        size = ref['size'] if isinstance(ref, dict) else ref.size
        data = self.path(ref).read_bytes()
        assert len(data) == size and hashlib.sha256(data).hexdigest() == d
        return data

    def check(self, ref):
        p = self.path(ref)
        assert p.stat().st_size == ref['size'] and file_hash(p) == ref['digest']
        return ref

ART = ReadOnlyArtifacts()
DB = sqlite3.connect((BASE / 'experiments.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
DB.execute('PRAGMA query_only=ON')
DB.execute('BEGIN')

def record(rid):
    row = DB.execute('SELECT kind,payload,digest,seq FROM records WHERE id=?', (rid,)).fetchone()
    assert row, rid
    assert hashlib.sha256(row[1].encode()).hexdigest() == row[2], rid
    return json.loads(row[1])

START = time.perf_counter()
result = {'started_at_utc': datetime.now(timezone.utc).isoformat(), 'offline': True,
          'store_writes': 0, 'network_calls': 0, 'new_runs': 0, 'rounds': [], 'errors': []}
ANALYSIS_ONLY = '--original-analysis-only' in sys.argv
if ANALYSIS_ONLY:
    result = json.loads((OUT/'traceability-facts.json').read_text(encoding='utf-8'))
    result['rounds'] = [r for r in result['rounds'] if r['directory'] != '.artifacts/phase3-2026-11-12/real-round-2']
rows = DB.execute('SELECT seq,id,kind,strategy_id,digest FROM records WHERE task_id=? ORDER BY seq', ('mla-decode-v1',)).fetchall()
META = {r[1]: dict(seq=r[0], kind=r[2], owner=r[3], digest=r[4]) for r in rows}
LINKS = defaultdict(list)
for rid, field, target in DB.execute('SELECT record_id,field,target_id FROM links'):
    if rid in META:
        LINKS[rid].append(target)
SMALL = {}
for rid, kind in [(r[1], r[2]) for r in rows if r[2] in ('Run', 'Candidate', 'Decision')]:
    text = DB.execute('SELECT payload FROM records WHERE id=?', (rid,)).fetchone()[0]
    if kind != 'Decision':
        SMALL[rid] = json.loads(text)
    elif any('"type":"' + name + '"' in text for name in ('parent_selection.v1','plan_parent.v1','knowledge_link.v1')):
        p = json.loads(text); d = p['details']; refs = [d['strategy_id'], d['parent_id']]
        refs += [d[k] for k in ('context_id','plan_id','candidate_id','candidate_parent_id','run_id') if d.get(k)]
        refs += d.get('evidence_ids', [])
        LINKS[rid].extend(refs)

def expected_history(sid, high):
    allowed = {rid for rid,m in META.items() if m['seq'] <= high}
    owned = {rid for rid in allowed if META[rid]['owner'] == sid}
    included = owned | {'mla-decode-v1'} | {rid for rid in allowed if META[rid]['kind'] in ('Workload','EvaluationContext')}
    reverse = defaultdict(list)
    for rid in allowed:
        for target in LINKS[rid]: reverse[target].append(rid)
    todo = list(included)
    while todo:
        rid = todo.pop(); kind = META[rid]['kind']
        related = list(LINKS[rid])
        for dep in reverse[rid]:
            dk = META[dep]['kind']
            if ((kind == 'Candidate' and rid not in owned and dk == 'Run')
                or (kind == 'Run' and any(field == 'run_id' and target == rid for field,target in DB.execute('SELECT field,target_id FROM links WHERE record_id=?',(dep,))))
                or (kind not in ('Task','Strategy','Workload','EvaluationContext') and dk == 'Decision'
                    and DB.execute('SELECT 1 FROM links WHERE record_id=? AND field=? AND target_id=?',(dep,'target_id',rid)).fetchone())):
                related.append(dep)
        for dep in related:
            assert dep in allowed, (rid, dep, high)
            if dep not in included: included.add(dep); todo.append(dep)
    ordered = [r[1] for r in rows if r[1] in included]
    return ordered, [rid for rid in ordered if rid in owned], [rid for rid in ordered if rid not in owned]

def source_change(parent, child):
    before = ART.read(parent).decode('utf-8').splitlines()
    after = ART.read(child).decode('utf-8').splitlines()
    opcodes = difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes()
    return [{'kind':tag, 'parent_lines':[a+1,b], 'candidate_lines':[c+1,d],
             'removed':before[a:b], 'added':after[c:d]}
            for tag,a,b,c,d in opcodes if tag != 'equal']

def audit_round(directory):
    p = BASE / directory
    session = json.loads((p/'session.json').read_bytes())
    ART.check(session['manifest'])
    m = json.loads(ART.read(session['manifest']))
    prepared = json.loads((p/'prepared.json').read_bytes())
    completed = json.loads((p/'completed.json').read_bytes()) if (p/'completed.json').exists() else None
    intake = json.loads((p/'intake.json').read_bytes())
    report_hash = file_hash(p/'result.json')
    assert report_hash == session['result_digest']
    r = json.loads((p/'result.json').read_bytes())
    assert r['manifest_digest'] == session['manifest']['digest']
    assert r['exchanges'] == session['responses']
    assert r['proposal'] == session['responses'][-1]['final']
    assert r['winner_selected'] is False and r['review_enabled'] is True
    assert r['analysis']['review'] == []
    assert prepared['request'] == m['request']
    high = prepared['snapshot']['high_watermark']
    expected_refs = {rid for rid,x in META.items() if x['seq'] <= high}
    assert set(m['record_refs']) == expected_refs
    for rid,ref in m['record_refs'].items():
        assert ref['kind'] == META[rid]['kind'] and ref['digest'] == META[rid]['digest'], rid
    out = {'directory':str(p.relative_to(ROOT)).replace('\\','/'), 'round_id':intake['round_id'],
           'manifest':session['manifest'], 'manifest_hash_verified': True,
           'result_digest':report_hash, 'result_hash_verified':True, 'snapshot':prepared['snapshot'],
           'all_snapshot_record_digests_match_store':len(expected_refs), 'status_cards':len(prepared['cards']),
           'input_issues':m['issues'], 'consistency':m['consistency'],
           'histories':{}, 'knowledge':{}, 'observations':r['proposal']['observations'],
           'hypotheses':r['proposal']['hypotheses'], 'plans':r['plan_set']['plans'],
           'unknowns':r['proposal']['unknowns'], 'tool_calls':[], 'calculations':r['calculations'],
           'responses':len(session['responses']), 'review':r['analysis']['review'],
           'winner_selected':r['winner_selected'], 'model_transport':r['model_transport'],
           'candidates':[], 'completed_snapshot':completed['snapshot'] if completed else None,
           'hardware_completed_in_this_directory':completed is not None}
    for sid, route in m['strategies'].items():
        h = route['history']; ids = [x['payload']['id'] for x in h['records']]
        want,owned,deps = expected_history(sid,high)
        assert ids == want and h['owned_ids'] == owned and h['dependency_ids'] == deps, sid
        assert h['complete'] and h['offset'] == 0 and h['next_offset'] is None and len(ids) == h['total']
        assert h == prepared['histories'][sid]
        for row in h['records']:
            assert digest(row['payload']) == META[row['payload']['id']]['digest']
        out['histories'][sid] = {'total':h['total'], 'owned_total':h['owned_total'],
            'dependency_total':h['dependency_total'], 'complete_independently_reconstructed':True,
            'record_ids':ids, 'parent':route['parent'], 'run':route['run'], 'context_id':route['context']['id']}
        assert ART.read(route['source_artifact']).decode() == route['source']
        assert ART.read(route['config_artifact']).decode() == route['config']
    del prepared
    for mid,k in m['knowledge'].items():
        ART.check(k['artifact'])
        assert k['artifact']['digest'] == k['revision']
        assert ART.read(k['artifact']).decode() == k['document']['content']
        for src in k['sources']:
            assert src['availability']=='available' and src['current_revision']==src['revision']
            ART.check(src['artifact'])
            assert src['artifact']['digest']==src['revision']
        out['knowledge'][mid] = {'note_id':k['note_id'], 'revision':k['revision'], 'availability':k['availability'],
                                 'sources':k['sources'], 'method':k['method']}
    tools = AnalysisTools.__new__(AnalysisTools)
    tools.manifest,tools.artifacts,tools.skills = m,ART,r['skill_captures']
    for call in r['tool_calls']:
        assert 'error' not in call, call
        actual = tools.call(call['name'],call['arguments'])
        assert actual == call['result'], call['id']
        out['tool_calls'].append({'id':call['id'],'name':call['name'],'arguments':call['arguments'],
                                  'recomputed_equal':True})
        del actual
    out['tool_counts']=dict(Counter(c['name'] for c in r['tool_calls']))
    catalog=m['references'] | {'calculation:'+k:{'value':v} for k,v in r['calculations'].items()}
    for obs in out['observations']:
        pred=obs['predicate']
        if pred:
            v=catalog[pred['ref']]['value']
            referenced_record=None
            if len(pred['path']) >= 2 and pred['path'][0]=='records':
                referenced_record=v['records'][pred['path'][1]]['payload']['id']
            for step in pred['path']: v=v[step]
            assert type(v)==type(pred['value']) and v==pred['value'],obs['id']
            obs['audit_predicate_equal']=True
            obs['audit_history_record_id']=referenced_record
            if referenced_record:
                rr=record(referenced_record)
                obs['audit_referenced_result']=rr.get('details',{}).get('result')
    publication=record(intake['publication_id'])
    assert publication['details']['manifest']==session['manifest']
    assert publication['details']['result']['digest']==report_hash
    ART.check(publication['details']['result'])
    out['publication_id']=intake['publication_id']; out['intake_id']=intake['id']
    assert high < META[intake['publication_id']]['seq'] < META[intake['id']]['seq']
    out['publication_seq']=META[intake['publication_id']]['seq']
    out['intake_seq']=META[intake['id']]['seq']
    intake_record=record(intake['id'])
    out['intake_store_record_type']=intake_record['details']['type']
    plan_map={x['id']:x for x in r['plan_set']['plans']}
    for item in intake['items']:
        plan=plan_map[item['proposal_id']]; plan_record=record(item['plan_id'])
        assert plan_record['description']==plan['modification']
        assert plan_record['strategy_id']==plan['strategy_id']
        assert item['status']=='accepted' and item['parent_id']==plan['parent_id']
        if completed is None:
            continue
        for job in item['jobs']:
            jid=job['id']; outcome=completed['results'][jid]
            cand=record(outcome['candidate_id']); run=record(outcome['run_id'])
            assert cand['plan_id']==item['plan_id'] and cand['parent_id']==item['parent_id']
            assert run['candidate_id']==cand['id'] and run['context_id']==item['context_id']
            route=m['strategies'][plan['strategy_id']]
            assert item['context_id']==route['context']['id']
            variant=job['variant']; ident=cand['identity']
            for a,b in [('source_ref','source_digest'),('config_ref','config_digest'),('build_ref','build_digest')]:
                ART.check(variant[a]); assert variant[a]['digest']==ident[b]
            source_diff=source_change(route['source_artifact'],variant['source_ref'])
            evaluation=record(outcome['evaluation_id']); promotion=record(outcome['promotion_id'])
            assert evaluation['run_id']==run['id'] and evaluation['latency_ms']==outcome['latency_ms']
            out['candidates'].append({'label':variant['label'],'proposal_id':plan['id'],'plan_id':item['plan_id'],
                'candidate':cand,'run':run,'source_changes':source_diff, 'source_artifact':variant['source_ref'],
                'source_bundle_ref':variant.get('source_bundle_ref'), 'config_artifact':variant['config_ref'],
                'build_artifact':variant['build_ref'], 'parent':route['parent'], 'parent_run':route['run'],
                'result':outcome, 'promotion':promotion})
    if completed is None:
        original_artifacts=json.loads((p/'candidate-artifacts.json').read_bytes())
        for entry in original_artifacts:
            ART.check(entry['diff_ref'])
            ART.check(entry['variant']['source_ref'])
            match=next(c for rr in result['rounds'] for c in rr['candidates']
                       if c['label']==entry['label'])
            assert match['source_artifact']==entry['variant']['source_ref']
            assert match['build_artifact']==entry['variant']['build_ref']
            assert match['parent']['id']==entry['parent_id']
        out['original_four_source_identities_match_runtime7']=True
        out['candidate_artifacts']=original_artifacts
    result['rounds'].append(out)
    (OUT/'traceability-facts.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'round':out['round_id'],'histories':{k:v['total'] for k,v in out['histories'].items()},
                      'tools':out['tool_counts'],'candidates':len(out['candidates']),'status':'verified'},ensure_ascii=False),flush=True)
    del m,r,tools,catalog
    gc.collect()

try:
    directories = ['real-round-2'] if ANALYSIS_ONLY else ['real-round-1','real-round-2-runtime7','continuation-e4b6a0fcf34a6c37d083']
    for directory in directories:
        audit_round(directory)
    result['status']='verified'
except Exception as exc:
    result['status']='audit_failed'; result['errors'].append(repr(exc))
    raise
finally:
    result['elapsed_seconds']=time.perf_counter()-START
    result['completed_at_utc']=datetime.now(timezone.utc).isoformat()
    (OUT/'traceability-facts.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    DB.close()
