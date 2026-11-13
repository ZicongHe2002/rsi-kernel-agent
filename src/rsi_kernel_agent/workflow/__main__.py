"""One entrypoint for fixed Tasks, conversation sessions and explicit execution."""
import argparse
import asyncio
import json
from pathlib import Path

from ..contracts import Decision, Task, Workload, EvaluationContext, Strategy, canonical_json, record_from_dict
from ..knowledge_mcp import StdioKnowledge
from ..memory import MemoryReader
from ..profiler.inputs import AnalysisRequest, capture_inputs, load_manifest
from ..profiler.session import start_session, submit_response
from ..profiler.outputs import publish_result
from ..contracts import ArtifactRef
from ..store import ExperimentStore
from .controller import Controller, stable
from .execution import SyntheticBackend
from .intake import intake_publication


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def create_task(store, spec):
    if set(spec)!={'task','workload','context','strategies'}:
        raise ValueError('Task specification requires task/workload/context/strategies')
    records=[record_from_dict(kind,spec[key]) for kind,key in
             [('Task','task'),('Workload','workload'),('EvaluationContext','context')]]
    records += [record_from_dict('Strategy',value) for value in spec['strategies']]
    if records[2].provenance not in ('synthetic', 'real'):
        raise ValueError('A saved context is historical evidence, not an executable Task specification')
    if records[2].provenance == 'real':
        from .real_protocol import validate_context
        validate_context(store, records[2], records[1])
    if any(record.task_id!=records[0].id for record in records[1:]):
        raise ValueError('Task specification mixes Tasks')
    if records[2].workload_id!=records[1].id:
        raise ValueError('Context belongs to another Workload')
    for record in records:
        store.append(record,stable('wf-create',record.id))
    return {'task_id':records[0].id,'context_id':records[2].id,'provenance':records[2].provenance}


def colab_remote(config_path):
    if not config_path:
        raise ValueError('Real execution requires --colab-config for an existing pinned Runtime')
    config = read_json(config_path)
    names = {'python', 'session', 'runtime_id', 'nonce', 'remote_root', 'cache'}
    if type(config) is not dict or not names <= set(config) or set(config) - names - {'remote_python'} or any(type(config[k]) is not str or not config[k].strip() for k in names):
        raise ValueError('Colab configuration requires python/session/runtime_id/nonce/remote_root/cache')
    from .colab_bridge import ColabBridge
    from .colab_remote import ColabRemote
    bridge = ColabBridge(config['python'], config['session'], config['runtime_id'])
    return ColabRemote(bridge, config['remote_root'], config['nonce'], Path(__file__).with_name('colab_worker.py'),
                       Path(__file__).with_name('mla_runner.py'), config['cache'], remote_python=config.get('remote_python'))


def make_backend(store, args, task_id):
    contexts = [r for r in store.history(task_id) if type(r) is EvaluationContext]
    provenance = {c.provenance for c in contexts}
    if provenance == {'synthetic'}:
        return SyntheticBackend(store, args.workspaces or str(Path(args.db).parent / 'workspaces'))
    if provenance != {'real'}:
        raise ValueError('Execution needs one explicit real or synthetic provenance per Task; saved/mixed contexts are not executable')
    from .colab import ColabBackend
    remote = colab_remote(args.colab_config)
    if any(c.environment['runtime_session_id'] != remote.runtime_id for c in contexts):
        raise ValueError('Colab configuration Runtime differs from immutable Task contexts')
    return ColabBackend(store, remote)


async def dispatch(args):
    if args.command=='demo':
        from .demo import run_demo
        return await run_demo(args.output)
    if args.command == 'colab-bootstrap':
        return colab_remote(args.colab_config).bootstrap()
    store=ExperimentStore(args.db,args.artifacts)
    if args.command=='create': return create_task(store,read_json(args.spec))
    if args.command=='round':
        request=AnalysisRequest.from_dict(read_json(args.request))
        async with StdioKnowledge(args.website) as knowledge:
            manifest=await capture_inputs(store,knowledge,request)
        packet=start_session(store,manifest,args.session,model_label='current conversation model')
        return {'packet':str(packet),'manifest':{'digest':manifest.artifact_ref.digest,'size':manifest.artifact_ref.size}}
    if args.command=='next-round':
        from .rounds import prepare_round
        async with StdioKnowledge(args.website) as knowledge:
            prepared=await prepare_round(store,knowledge,args.task,args.context,args.strategies.split(','),
                args.round_id,read_json(args.knowledge_refs),parent_policy='branch_best')
        packet=start_session(store,prepared['manifest'],args.session,model_label='current conversation model')
        return {'packet':str(packet),'snapshot':prepared['snapshot'],'cards':prepared['cards']}
    if args.command=='respond':
        result=submit_response(store,args.session,args.response)
        if result is None:return {'status':'awaiting conversation response'}
        session=read_json(Path(args.session)/'session.json')
        manifest=load_manifest(store,ArtifactRef(**session['manifest']))
        marker=publish_result(store,manifest,result,args.key)
        return {'status':'published','publication_id':marker.id}
    if args.command=='intake':
        async with StdioKnowledge(args.website) as knowledge:
            result=await intake_publication(store,args.publication,args.round_id,read_json(args.variants),knowledge=knowledge)
        controller=Controller(store,result['task_id'],None)
        controller.accept_intake(result)
        return result
    if args.command=='cards':
        snapshot=store.create_snapshot(args.task,args.key or stable('wf-view',str(len(store.history(args.task)))))
        view=MemoryReader(store).open_snapshot(snapshot['id'])
        return {'snapshot':snapshot,'cards':view.status_cards(args.context)}
    backend = make_backend(store, args, args.task) if args.command in ('run', 'recover') else None
    controller=Controller(store,args.task,backend)
    if args.command in ('run','recover'): return controller.run_until_idle(args.max_steps)
    if args.command=='pause': return controller.pause(args.strategy,args.reason)
    if args.command=='resume': return controller.resume(args.strategy,args.reason)
    if args.command=='resume-job': return controller.resume_job(args.job,args.reason)
    if args.command=='cancel': return controller.cancel(args.reason)
    if args.command=='repair-packet': return controller.repair_packet(args.job)
    if args.command=='repair': return {'job_id':controller.submit_repair(args.job,read_json(args.response),args.key)}
    if args.command=='retry': return {'job_id':controller.retry(args.job,args.key)}
    state=controller.status()
    if args.command=='queue':return [j for j in state['jobs'].values() if j['state'] in ('queued','active','evaluating','deferred','blocked')]
    if args.command=='results':return [j for j in state['jobs'].values() if j['result']]
    if args.command=='failures':return [j for j in state['jobs'].values() if j['state']=='failed' or j.get('repair_of')]
    return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default='rsi.sqlite3')
    parser.add_argument('--artifacts',default='rsi-artifacts')
    parser.add_argument('--workspaces')
    parser.add_argument('--colab-config', help='Pinned existing Runtime bridge configuration; never allocates a Runtime')
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('create');p.add_argument('--spec',required=True)
    p=sub.add_parser('demo');p.add_argument('--output',required=True)
    sub.add_parser('colab-bootstrap')
    p=sub.add_parser('round')
    for name in ('request','website','session'):p.add_argument('--'+name,required=True)
    p=sub.add_parser('next-round')
    for name in ('task','context','strategies','round-id','knowledge-refs','website','session'):p.add_argument('--'+name,required=True)
    p=sub.add_parser('respond')
    for name in ('session','response','key'):p.add_argument('--'+name,required=True)
    p=sub.add_parser('intake')
    for name in ('publication','round-id','variants','website'):p.add_argument('--'+name,required=True)
    p=sub.add_parser('cards');p.add_argument('--task',required=True);p.add_argument('--context',required=True);p.add_argument('--key')
    for name in ('status','queue','results','failures','run','recover','pause','resume','resume-job','cancel','repair-packet','repair','retry'):
        p=sub.add_parser(name);p.add_argument('--task',required=True)
        if name in ('run','recover'):p.add_argument('--max-steps',type=int,default=1000)
        if name in ('pause','resume'):p.add_argument('--strategy',required=True)
        if name in ('pause','resume','resume-job','cancel'):p.add_argument('--reason',required=True)
        if name in ('resume-job','repair-packet','repair','retry'):p.add_argument('--job',required=True)
        if name in ('repair','retry'):p.add_argument('--key',required=True)
        if name=='repair':p.add_argument('--response',required=True)
    args=parser.parse_args()
    try:
        result=asyncio.run(dispatch(args))
    except (ValueError,KeyError,OSError,RuntimeError) as error:
        parser.exit(2,str(error)+'\n')
    print(canonical_json(result))


if __name__=='__main__':main()
