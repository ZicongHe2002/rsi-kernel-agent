"""Read saved real baseline through original session tools; no Plan or execution."""
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rsi_kernel_agent.contracts import canonical_json
from rsi_kernel_agent.store import ExperimentStore
from rsi_kernel_agent.knowledge import method_note_id
from rsi_kernel_agent.knowledge_mcp import StdioKnowledge
from rsi_kernel_agent.workflow.rounds import prepare_round
from rsi_kernel_agent.profiler.session import start_session,submit_response
d=Path(__file__).resolve().parent
async def main():
    s=ExperimentStore(d/'experiments.sqlite3',d/'artifacts')
    job=json.loads((d/'baseline-repair-job.json').read_text('utf-8'))
    result=json.loads((d/'baseline-repair-result.json').read_text('utf-8'))
    async with StdioKnowledge(ROOT/'knowledge-site') as knowledge:
        mid='hist-profile-accounting';doc=await knowledge.fetch_document(method_note_id(mid))
        prepared=await prepare_round(s,knowledge,job['task_id'],job['context_id'],[job['strategy_id']],
            'baseline-runtime1-profile-inspection',[{'method_id':mid,'note_id':doc.id,'revision':doc.revision}])
    directory=d/'baseline-analysis-inspection';directory.mkdir(exist_ok=True)
    if not (directory/'session.json').exists():
        start_session(s,prepared['manifest'],directory,model_label='current conversation model; baseline inspection only')
    state=json.loads((directory/'session.json').read_text('utf-8'))
    if not state['responses']:
        calls=[{'id':'complete-history','name':'history','arguments':{'strategy_id':job['strategy_id']}},
               {'id':'real-profile','name':'profile','arguments':{'strategy_id':job['strategy_id'],'profile_id':result['profile_id']}},
               {'id':'accounting','name':'knowledge','arguments':{'method_id':mid}}]
        response=directory/'inspection-response.json'
        response.write_text(canonical_json({'tool_calls':calls}),encoding='utf-8')
        submit_response(s,directory,response)
    packet=json.loads((directory/'packet.json').read_text('utf-8'))
    rows=[]
    for tool in packet['tool_results']:
        if 'error' in tool:raise RuntimeError(tool['error'])
        if tool['name']=='history':
            value=tool['result'];rows.append({'tool':'history','complete':value['complete'],'records':len(value['records'])})
            (directory/'complete-history.json').write_text(canonical_json(value),encoding='utf-8')
        if tool['name']=='profile':
            value=tool['result'];rows.append({'tool':'profile','availability':value['availability'],
                'reports':[{'quality':r['quality'],'events':len(r['events']),'operation_stats':r['operation_stats'],
                            'capabilities':r['capabilities']} for r in value['reports']]})
            (directory/'normalized-profile.json').write_text(canonical_json(value),encoding='utf-8')
    report={'classification':'Real saved Profile integration, no optimization Plan or new hardware operation',
        'manifest_ref':asdict(prepared['manifest'].artifact_ref),'cards':len(prepared['cards']),
        'session_path':str(directory.relative_to(ROOT)),'tools':rows,'plan_publication':None,
        'oracle_negative_gate':'pending','source_run_id':job['run_id']}
    (ROOT/'knowledge-site/manifests/phase3-2026-11-12/real-profile-analysis-smoke.json').write_text(canonical_json(report),encoding='utf-8')
    print(canonical_json(report))
asyncio.run(main())
