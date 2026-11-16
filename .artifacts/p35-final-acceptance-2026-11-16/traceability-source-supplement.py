"""Additional offline source-bundle and publication-order verification."""
from pathlib import Path
import hashlib
import json
import sqlite3

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'.artifacts/phase3-2026-11-12'
OUT=Path(__file__).parent
facts=json.loads((OUT/'traceability-facts.json').read_text(encoding='utf-8'))
db=sqlite3.connect((BASE/'experiments.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
checked={}

def blob(ref):
    d=ref['digest']; p=BASE/'artifacts/sha256'/d[:2]/d
    b=p.read_bytes()
    assert len(b)==ref['size'] and hashlib.sha256(b).hexdigest()==d
    checked[d]=len(b)
    return b

def rec(rid):
    seq,text=db.execute('SELECT seq,payload FROM records WHERE id=?',(rid,)).fetchone()
    return seq,json.loads(text)

for r in facts['rounds']:
    pub_seq,pub=rec(r['publication_id']); in_seq,intake=rec(r['intake_id'])
    assert r['snapshot']['high_watermark']<pub_seq<in_seq
    r['publication_seq']=pub_seq;r['intake_seq']=in_seq
    assert intake['details']['result']==json.loads((ROOT/r['directory']/'intake.json').read_bytes())
    r['intake_items_match_store']=True
    for c in r['candidates']:
        cs,_=rec(c['candidate']['id']);rs,_=rec(c['run']['id'])
        assert in_seq<cs<rs
        c['candidate_record_seq']=cs;c['run_record_seq']=rs
        parent_build=json.loads(blob({'digest':c['parent']['identity']['build_digest'],
                                      'size':(BASE/'artifacts/sha256'/c['parent']['identity']['build_digest'][:2]/c['parent']['identity']['build_digest']).stat().st_size}))
        child_build=json.loads(blob(c['build_artifact']))
        pd=parent_build['source_bundle_digest']; pp=BASE/'artifacts/sha256'/pd[:2]/pd
        parent_bundle=json.loads(blob({'digest':pd,'size':pp.stat().st_size}))
        child_bundle=json.loads(blob(c['source_bundle_ref']))
        before={x['path']:x for x in parent_bundle['files']};after={x['path']:x for x in child_bundle['files']}
        assert set(before)==set(after) and len(after)==375
        changed=[path for path in before if before[path]!=after[path]]
        assert changed in ([],['pallas_kernels/kernels/mla_decode.py'])
        for entry in after.values():blob(entry['artifact_ref'])
        assert child_build['source_bundle_digest']==c['source_bundle_ref']['digest']
        assert {k:v for k,v in parent_build.items() if k!='source_bundle_digest'}=={k:v for k,v in child_build.items() if k!='source_bundle_digest'}
        c['bundle_file_count']=len(after);c['changed_bundle_paths_recomputed']=changed
        c['oracle_runner_build_fields_equal_parent']=True
        if c['label'].startswith(('length-','query-','b1-')):
            p=BASE/'candidates'/('round2-'+c['label'])/'mla_decode.py'
            assert p.read_bytes()==blob(c['source_artifact'])
            c['reviewable_candidate_path']=str(p.relative_to(ROOT)).replace('\\','/')
    r['chronology_verified']=True

facts['source_supplement']={'unique_artifacts_hashed':len(checked),'bytes_hashed_unique':sum(checked.values()),
                            'store_writes':0,'network_calls':0,'publication_before_candidate_verified':True}
(OUT/'traceability-facts.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(facts['source_supplement']))
db.close()
