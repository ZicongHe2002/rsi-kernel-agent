"""Link pinned historical Knowledge to the admitted real baseline repair.

Reads the original MCP, then appends through the existing MemoryWriter only.
No knowledge writes, execution, promotion, Frontier or parent selection.
"""
import asyncio
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.associations import MemoryWriter, plan_binding_id
from rsi_kernel_agent.contracts import ArtifactRef, Candidate, Decision, Evidence, ParentCandidate, Run, canonical_json
from rsi_kernel_agent.knowledge import _parse_method, method_note_id, method_to_dict
from rsi_kernel_agent.knowledge_mcp import StdioKnowledge
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.workflow.execution import stable_id

WORK = Path(__file__).resolve().parent
MANIFEST = ROOT / 'knowledge-site/manifests/phase3-2026-11-12'


def read(path):
    return json.loads(path.read_bytes())


async def main():
    store = ExperimentStore(WORK / 'experiments.sqlite3', WORK / 'artifacts')
    base, repair = read(WORK / 'baseline-job.json'), read(WORK / 'baseline-repair-job.json')
    result = read(WORK / 'baseline-result.json')
    response_path = WORK / 'candidates/baseline-repair-1/response.json'
    response = read(response_path)
    if not (base['task_id'] == repair['task_id'] and base['plan_id'] == repair['plan_id']
            and result['classification'] == 'compile_failed' and result['run_id'] == base['run_id']
            and repair['repair_of'] == base['candidate_id']
            and response['history_evidence']['method_id'] == 'hist-decode-prepare-padding'):
        raise ValueError('Unexpected original failure or admitted repair relationship')
    original = store.get(base['candidate_id'])
    candidate, parent, run = (store.get(repair[k]) for k in ('candidate_id', 'parent_id', 'run_id'))
    if not (type(original) is Candidate and type(candidate) is Candidate and type(parent) is ParentCandidate
            and type(run) is Run and candidate.change_kind == 'repair' and run.candidate_id == candidate.id
            and candidate.parent_id == parent.id and parent.candidate_id == original.id and parent.identity == original.identity
            and original.parent_id == base['parent_id'] and original.plan_id == candidate.plan_id == base['plan_id']
            and candidate.identity.source_digest == response['source_sha256']
            and original.identity.source_digest == response['parent_source_sha256']):
        raise ValueError('Store repair ancestry or source identity does not match the response')
    binding = store.get(plan_binding_id(base['plan_id']))
    if binding.details.get('parent_id') != base['parent_id']:
        raise ValueError('Original Plan has a different immutable parent binding')
    compile_ref = ArtifactRef(**result['receipt_refs']['compile'])
    compile_receipt = json.loads(store.artifacts.read(compile_ref))
    if not (compile_receipt['run_id'] == base['run_id'] and compile_receipt['candidate_id'] == original.id
            and compile_receipt['stage'] == 'compile' and compile_receipt['status'] == 'failed'
            and compile_ref.digest == response['current_red_evidence']['receipt_digest']
            and compile_receipt['operation_id'] == response['current_red_evidence']['operation_id']):
        raise ValueError('Original compile-failure receipt differs from the repair response')
    original_evidence = store.get(stable_id('stage-evidence', base['run_id'], 'compile'))
    if type(original_evidence) is not Evidence or original_evidence.run_id != base['run_id']:
        raise ValueError('Original compile failure was not integrated as bound evidence')
    for artifact in original_evidence.artifacts:
        store.artifacts.verify(artifact)
    history = response['history_evidence']
    history_raw = Path(history['path']).read_bytes()
    if sha256(history_raw).hexdigest() != history['sha256']:
        raise ValueError('Historical prepare-padding patch changed')
    history_ref = store.artifacts.put(history_raw)
    intent = store.get(stable_id('baseline-repair-intent', original.id))
    if type(intent) is not Decision or intent.details['candidate_id'] != candidate.id:
        raise ValueError('Missing explicit conversation repair intent')
    response_ref = ArtifactRef(**intent.details['response_ref'])
    if json.loads(store.artifacts.read(response_ref)) != response:
        raise ValueError('Repair response differs from the archived intent')
    store.artifacts.verify(ArtifactRef(**intent.details['diff_ref']))

    method_id = history['method_id']
    note_id = method_note_id(method_id)
    async with StdioKnowledge(ROOT / 'knowledge-site') as knowledge:
        document = await knowledge.fetch_document(note_id)
        method, _ = _parse_method(document.content)
        if method.method_id != method_id:
            raise ValueError('Wrong Knowledge method')
        source_status = await knowledge.source_status(method)
        if any(item['availability'] != 'available' for item in source_status):
            raise ValueError('Historical method source is stale or unavailable')
        # Recheck the same note revision before creating the immutable association.
        await knowledge.fetch_document(note_id, expected_revision=document.revision)

    reason = (
        'Historical Knowledge applied to baseline compatibility repair, not a performance claim. '
        f'Original Run {base["run_id"]} actually failed compilation in decode-boundary; '
        f'bound Evidence {original_evidence.id}, receipt SHA256 {compile_ref.digest}. '
        f'The explicit response {response_ref.digest} cites historical patch {history["path"]} '
        f'SHA256 {history["sha256"]}: pad the full new-KV batch before selecting each row to avoid '
        'the non-concatenation row-offset mismatch. The fixed Oracle, tolerance and benchmark contract are unchanged. '
        'This link records the repair rationale and exact descendant; it does not establish repair correctness, speedup or Best.'
    )
    key = 'phase3:repair-knowledge:' + sha256(canonical_json({
        'candidate': candidate.id, 'method': method_id, 'revision': document.revision}).encode()).hexdigest()
    writer = MemoryWriter(store)
    link = writer.link_method(method, note_id, document.revision, base['plan_id'], base['parent_id'],
        reason, key, candidate_id=candidate.id, run_id=run.id, evidence_ids=())
    second = writer.link_method(method, note_id, document.revision, base['plan_id'], base['parent_id'],
        reason, key, candidate_id=candidate.id, run_id=run.id, evidence_ids=())
    if link != second:
        raise ValueError('The repeated link did not return the same append-only Decision')
    links = [r for r in store.history(base['task_id']) if type(r) is Decision and r.id == link.id]
    if len(links) != 1:
        raise ValueError('Knowledge link was duplicated')
    output = {'schema_version': 1, 'provenance': 'real_compile_failure_plus_sourced_historical_knowledge',
        'decision_id': link.id, 'idempotency_key': key, 'duplicate_call_same_decision': True,
        'task_id': base['task_id'], 'strategy_id': base['strategy_id'], 'plan_id': base['plan_id'],
        'plan_parent_id': base['parent_id'], 'candidate_id': candidate.id, 'candidate_parent_id': parent.id,
        'run_id': run.id, 'original_failed_candidate_id': original.id, 'original_failed_run_id': base['run_id'],
        'original_failure_evidence_id': original_evidence.id, 'original_failure_receipt_ref': asdict(compile_ref),
        'method_id': method_id, 'method_note_id': note_id, 'method_note_revision': document.revision,
        'source_refs': method_to_dict(method)['source_refs'], 'source_status': source_status,
        'historical_patch': {'path': history['path'], 'sha256': history['sha256'], 'artifact_ref': asdict(history_ref)},
        'repair_response_ref': asdict(response_ref), 'repair_diff_ref': intent.details['diff_ref'],
        'repair_intent_id': intent.id, 'reason': reason,
        'repair_evidence_ids': [], 'performance_claim': False, 'promotion_calls': 0,
        'parent_selection_changed': False, 'knowledge_notes_written': 0,
        'limitation': 'The triggering failure belongs to the original Run and is not relabeled as repair-Run Evidence.'}
    (MANIFEST / 'repair-knowledge-link.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: output[k] for k in ('decision_id', 'method_id', 'method_note_revision', 'candidate_id', 'run_id',
        'duplicate_call_same_decision', 'performance_claim')}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
