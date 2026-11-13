"""Admit the explicit conversation repair as a new Candidate and Run."""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.contracts import ArtifactRef,Candidate,CandidateIdentity,ParentCandidate,Run,Decision,canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.execution import make_request,stable_id

directory=Path(__file__).resolve().parent
store=ExperimentStore(directory/'experiments.sqlite3',directory/'artifacts')
old=json.loads((directory/'baseline-job.json').read_text('utf-8'))
result=json.loads((directory/'baseline-result.json').read_text('utf-8'))
assert result['classification']=='compile_failed' and result['run_id']==old['run_id']
repair_dir=directory/'candidates/baseline-repair-1'
response=json.loads((repair_dir/'response.json').read_text('utf-8'))
raw=(repair_dir/'mla_decode.py').read_bytes()
assert response['source_sha256']==sha256(raw).hexdigest()
assert response['parent_source_sha256']==old['variant']['source_ref']['digest']
source=asdict(store.artifacts.put(raw))
assert source!=old['variant']['source_ref']
bundle=json.loads(store.artifacts.read(ArtifactRef(**old['variant']['source_bundle_ref'])))
for item in bundle['files']:
    if item['path']==bundle['entry']['path']:
        item['artifact_ref']=source
bundle_ref=asdict(store.artifacts.put(canonical_json(bundle).encode()))
build=json.loads(store.artifacts.read(ArtifactRef(**old['variant']['build_ref'])))
build['source_bundle_digest']=bundle_ref['digest']
build_ref=asdict(store.artifacts.put(canonical_json(build).encode()))
identity=CandidateIdentity(source['digest'],old['variant']['config_ref']['digest'],build_ref['digest'])
failed=store.get(old['candidate_id'])
parent=ParentCandidate(stable_id('baseline-repair-parent',failed.id),old['task_id'],failed.identity,failed.id)
candidate=Candidate(stable_id('baseline-repair-candidate',failed.id,asdict(identity)),old['plan_id'],parent.id,identity,'repair')
run=Run(stable_id('baseline-repair-run',candidate.id,old['context_id']),candidate.id,old['context_id'])
response_ref=asdict(store.artifacts.put(canonical_json(response).encode()))
diff_ref=asdict(store.artifacts.put((repair_dir/'change.diff').read_bytes()))
intent=Decision(stable_id('baseline-repair-intent',failed.id),old['task_id'],'note',failed.id,
               'Explicit conversation response to real prepare-layout compilation failure',
               {'type':'baseline_repair_intent.v1','parent_run_id':old['run_id'],
                'response_ref':response_ref,'diff_ref':diff_ref,'candidate_id':candidate.id,
                'oracle_unchanged':old['contract']['context']['protocol']['oracle_digest']})
store.append(intent,intent.id)
for record in (parent,candidate,run):store.append(record,record.id)
job=deepcopy(old)
job.update(id=stable_id('mla-job',run.id),parent_id=parent.id,parent_evaluation_id=None,candidate_id=candidate.id,
           run_id=run.id,repair_of=failed.id,repair_depth=1,stage='generate',receipts={},result=None,
           reason='Known prepare-padding compatibility repair, no optimization claim',reserved=False)
job['variant'].update(label='Baseline repair: pad full batch before row selection',source_ref=source,
                      source_bundle_ref=bundle_ref,build_ref=build_ref)
make_request(store,job,'generate')
note=Decision(stable_id('mla-source',candidate.id,run.id),old['task_id'],'note',candidate.id,
              'Immutable baseline repair source artifacts',{'type':'workflow_candidate.v1',
              **{name:job['variant'][name] for name in ('source_ref','source_bundle_ref','config_ref','build_ref')},
              'job_id':job['id'],'provenance':'real'})
store.append(note,note.id)
(directory/'baseline-repair-job.json').write_text(canonical_json(job),encoding='utf-8')
print(json.dumps({'candidate_id':candidate.id,'run_id':run.id,'source_ref':source,'diff_ref':diff_ref}))
