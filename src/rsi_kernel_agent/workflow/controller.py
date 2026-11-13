"""Deterministic single-host orchestration over the original ExperimentStore.

Every effect is preceded by a journal intent. Replaying an interrupted prefix
uses identical IDs, transition predecessors and idempotency keys. A backend's
unknown outcome blocks the job; it is never silently retried as new work.
"""
from hashlib import sha256
import json

from ..contracts import (ArtifactRef, Candidate, CandidateIdentity, Decision,
    ParentCandidate, Run, RunEvent, Strategy, canonical_json, record_to_dict)
from .journal import WorkflowJournal
from .scheduling import choose_jobs, validate_limits, remaining_budget
from .execution import make_request, stages_for
from .evaluation import integrate_result


def stable(prefix, value):
    return prefix + ':' + sha256(value.encode('utf-8')).hexdigest()[:32]


def detached(value):
    return json.loads(canonical_json(value))


def require(condition, message):
    if not condition:
        raise ValueError(message)


class Controller:
    def __init__(self, store, task_id, backend, *, limits=None):
        self.store, self.task_id, self.backend = store, task_id, backend
        self.journal = WorkflowJournal(store, task_id)
        with self.journal.lock():
            state = self.journal.load()
            if state is None:
                self.journal.save(dict(task_id=task_id, jobs={}, limits=validate_limits(limits or {}),
                    spent=dict(executions=0,tokens=0,time_s=0), served={}, paused=[],
                    cancelled=False, intakes={}, commands={}, control_events=[]), 'Initialize workflow')
            elif limits is not None:
                require(state['limits']==validate_limits(limits), 'Existing workflow limits are immutable')

    def status(self):
        state=self.journal.load()
        state['remaining_budget']=remaining_budget(state)
        pending={j['state'] for j in state['jobs'].values()}
        state['workflow_state']=('running' if pending & {'active','evaluating'} else
            'waiting' if pending & {'queued','deferred','blocked'} else
            'cancelled' if state['cancelled'] else 'completed' if pending else 'idle')
        return state

    def _save(self, state, reason, job=None):
        return self.journal.save(state, reason, target_id=job['plan_id'] if job else None)

    def _ensure_records(self, job):
        variant = job['variant']
        provenance = job['contract']['context']['provenance']
        for name in ('source_ref','config_ref','build_ref'):
            self.store.artifacts.read(ArtifactRef(**variant[name]))
        if job.get('repair_of'):
            failed = self.store.get(job['repair_of'])
            parent = ParentCandidate(job['parent_id'],self.task_id,failed.identity,failed.id)
            self.store.append(parent,parent.id)
        candidate = Candidate(job['candidate_id'],job['plan_id'],job['parent_id'],
            CandidateIdentity(*(variant[name]['digest'] for name in ('source_ref','config_ref','build_ref'))),
            'repair' if job.get('repair_of') else 'proposal')
        self.store.append(candidate,candidate.id)
        self.store.append(Run(job['run_id'],candidate.id,job['context_id']),job['run_id'])
        details = {'type':'workflow_candidate.v1',
                'source_ref':variant['source_ref'],'config_ref':variant['config_ref'],
                'build_ref':variant['build_ref'],'job_id':job.get('origin_job_id',job['id']),'provenance':provenance}
        if provenance == 'real':
            details['source_bundle_ref'] = variant['source_bundle_ref']
        note = Decision(stable('wf-source',candidate.id),self.task_id,'note',candidate.id,
            'Immutable ' + provenance + ' candidate inputs', details)
        self.store.append(note,note.id)

    def accept_intake(self, intake):
        with self.journal.lock():
            state = self.journal.load()
            require(intake['task_id']==self.task_id,'Intake belongs to another Task')
            # Intake must be a persisted outcome; callers cannot edit accepted jobs.
            record = self.store.get(intake['id'])
            require(isinstance(record,Decision) and record.kind=='note'
                and record.task_id==record.target_id==self.task_id
                and set(record.details)=={'type','request_digest','result'}
                and record.details['type']=='workflow_intake.v1',
                'Expected a validated workflow intake completion marker')
            stored = record.details['result']
            require(canonical_json(stored)==canonical_json(intake),'Intake differs from persisted validation')
            digest = sha256(canonical_json(intake).encode()).hexdigest()
            old = state['intakes'].get(intake['id'])
            if old is not None:
                require(old==digest,'Changed intake under existing identity')
                return state
            require(not state['cancelled'],'Workflow has been cancelled')
            for item in intake['items']:
                if item['status']!='accepted': continue
                for raw in item['jobs']:
                    job = detached(raw)
                    require(job['id'] not in state['jobs'],'Duplicate job')
                    job.update(candidate_id=stable('wf-c',job['id']),run_id=stable('wf-r',job['id']+':0'),
                        state='queued',stage='generate',receipts={},attempt=0,repair_depth=0,
                        result=None,reason='Accepted Plan variant',reserved=False)
                    if job['variant'].get('scenario')=='deferred':
                        job.update(state='deferred',reason='Synthetic backend capacity deferred')
                    state['jobs'][job['id']] = job
            state['intakes'][intake['id']] = digest
            state = self._save(state,'Accepted validated PlanSet')
            for job in state['jobs'].values():
                self._ensure_records(job)
                self._record_deferred(job)
            return state

    def _record_deferred(self, job):
        if job['state']!='deferred': return
        run_state = self.store.run_state(job['run_id'])
        if run_state['execution_status']=='not_run' and run_state['scheduling_status']=='ready':
            event = RunEvent(job['run_id']+':defer',job['run_id'],'not_run','deferred',job['reason'])
            self.store.transition(event,None,event.id)

    def _start(self, job):
        # Original predecessor is pinned in the journal before any transition.
        label = 'Synthetic' if job['contract']['context']['provenance'] == 'synthetic' else 'Real'
        event = RunEvent(job['run_id']+':start',job['run_id'],'running','active',label + ' workflow attempt')
        self.store.transition(event,job.get('start_predecessor'),event.id)

    def _sync_controls(self, state):
        for event in state.get('control_events',[]):
            record=Decision(event['id'],self.task_id,event['kind'],event['strategy_id'],event['reason'])
            self.store.append(record,record.id)

    def step(self):
        with self.journal.lock():
            state = self.journal.load()
            self._sync_controls(state)
            for job in state['jobs'].values():
                self._ensure_records(job)
                self._record_deferred(job)
            before = canonical_json(state)
            admitted = choose_jobs(state)
            for job_id in admitted:
                job = state['jobs'][job_id]
                job['start_predecessor'] = self.store.run_state(job['run_id'])['event_id']
            if canonical_json(state)!=before:
                state = self._save(state,'Reserve attempt budgets and concurrency')
            for job in state['jobs'].values():
                self._record_deferred(job)
            # Stable ordering is deterministic, but pending receipts may finish out of order.
            for job_id in list(state['jobs']):
                job=state['jobs'][job_id]
                if job['state'] not in ('active','evaluating','blocked'): continue
                if job['state']=='blocked':
                    if not job.get('operation'): continue
                    found = self.backend.lookup(job['operation']['operation_id'])
                    if found['status']=='unknown': continue
                    job['state']='active'
                    state=self._save(state,'Reconciled backend operation',job)
                    job=state['jobs'][job['id']]
                self._start(job)
                if job['stage']=='evaluate':
                    result = integrate_result(self.store,job,job['receipts'],cancelled=job.get('cancel_requested',False))
                    job['result'] = result
                    classification = result['classification']
                    job['state'] = ('completed' if classification.startswith('correct_') else
                                    'cancelled' if classification=='cancelled' else 'failed')
                    job['reason'] = classification
                    if job['state']=='completed':
                        self.store.set_frontier(job['candidate_id'],job['context_id'],True,
                            'Preserve correctly evaluated exploration route',job['run_id']+':frontier')
                    job.pop('operation',None)
                    state=self._save(state,'Integrated fixed evaluation',job)
                    continue
                if state['cancelled'] and not job.get('operation'):
                    job['stage']='evaluate'
                    state=self._save(state,'Cancelled before next execution stage',job)
                    continue
                if job['stage']=='benchmark' and not job.get('operation'):
                    busy = any(other['id']!=job['id'] and other.get('operation',{}).get('stage')=='benchmark'
                        and other['variant']['device']==job['variant']['device']
                        for other in state['jobs'].values())
                    if busy: continue
                if not job.get('operation'):
                    job['operation']=make_request(self.store,job,job['stage'])
                    state=self._save(state,'Persist execution intent and device reservation',job)
                    job=state['jobs'][job['id']]
                request=job['operation']
                found=self.backend.lookup(request['operation_id'])
                if found['status']=='definitely_not_started':
                    if job.get('cancel_requested'):
                        job.pop('operation',None)
                        job.update(stage='evaluate',state='evaluating')
                        state=self._save(state,'Cancel confirmed unstarted operation',job)
                        continue
                    found=self.backend.submit(request)
                elif found['status']=='running':
                    found=self.backend.poll(request['operation_id'])
                if found['status']=='unknown':
                    job.update(state='blocked',reason='Backend outcome unknown; explicit reconciliation required')
                    state=self._save(state,job['reason'],job)
                    continue
                if found['status']!='completed': continue
                receipt=found['receipt']
                job['receipts'][job['stage']]=receipt
                job.pop('operation',None)
                # Evaluator is the authority; any non-success stage ends execution.
                status=receipt.get('status',receipt.get('stage_status'))
                stages=(*stages_for(job['contract']['context']['provenance']), 'evaluate')
                job['stage']=stages[stages.index(job['stage'])+1] if status=='succeeded' else 'evaluate'
                if job.get('cancel_requested'):job['stage']='evaluate'
                if job['stage']=='evaluate': job['state']='evaluating'
                state=self._save(state,'Persist backend receipt and next stage',job)
            return state

    def run_until_idle(self, max_steps=1000):
        require(type(max_steps) is int and max_steps>0,'Positive step bound required')
        for _ in range(max_steps):
            before=self.journal.load()
            after=self.step()
            active=any(j['state'] in ('queued','active','evaluating') for j in after['jobs'].values())
            if not active: return self.status()
            if canonical_json(before)==canonical_json(after) and not any(
                    j['state']=='active' and j.get('operation') for j in after['jobs'].values()): return self.status()
        return self.status()

    def pause(self, strategy_id, reason):
        return self._pause(strategy_id,reason,True)

    def resume(self, strategy_id, reason):
        return self._pause(strategy_id,reason,False)

    def _pause(self, sid, reason, paused):
        strategy=self.store.get(sid)
        require(isinstance(strategy,Strategy) and strategy.task_id==self.task_id,'Invalid Strategy')
        require(type(reason) is str and reason.strip(),'Reason required')
        with self.journal.lock():
            state=self.journal.load()
            self._sync_controls(state)
            if (sid in state['paused'])==paused: return state
            if paused: state['paused'].append(sid)
            else: state['paused'].remove(sid)
            identity=stable('wf-pause',sid+canonical_json(state))
            state.setdefault('control_events',[]).append({'id':identity,'kind':'paused' if paused else 'resumed',
                'strategy_id':sid,'reason':reason})
            state=self._save(state,reason)
            self._sync_controls(state)
            return state

    def resume_job(self, job_id, reason):
        with self.journal.lock():
            state=self.journal.load(); job=state['jobs'][job_id]
            require(job['state']=='deferred' and not state['cancelled'],'Only deferred work can resume')
            job.update(state='queued',reason=reason)
            return self._save(state,'Resume deferred job',job)

    def cancel(self, reason):
        with self.journal.lock():
            state=self.journal.load(); state['cancelled']=True
            for job in state['jobs'].values():
                if job['state'] in ('queued','deferred'):
                    job.update(state='cancelled',reason=reason)
                elif job['state'] in ('active','evaluating','blocked'):
                    job['cancel_requested']=True
            return self._save(state,reason)

    def repair_packet(self, job_id):
        job=self.status()['jobs'][job_id]
        require(job['state']=='failed','Repair requires a failed attempt')
        return dict(schema_version=1,job_id=job_id,candidate_id=job['candidate_id'],run_id=job['run_id'],
            contract_digest=job['contract_digest'],failure=job['result'],provenance=job['contract']['context']['provenance'],
            parent_identity=record_to_dict(self.store.get(job['candidate_id']).identity))

    def submit_repair(self, job_id, response, key):
        with self.journal.lock():
            state=self.journal.load(); failed=state['jobs'][job_id]
            fingerprint=sha256(canonical_json(response).encode()).hexdigest()
            command='repair:'+key
            if command in state['commands']:
                old=state['commands'][command]
                require(old['digest']==fingerprint,'Repair key reused with different response')
                return old['job_id']
            require(not state['cancelled'],'Workflow cancelled')
            packet=self.repair_packet(job_id)
            require(set(response)==set(packet)|{'variant','reason'},'Unexpected repair response fields')
            require(all(response[k]==v for k,v in packet.items()),'Repair binding or contract changed')
            require(type(response['reason']) is str and response['reason'].strip(),'Repair rationale required')
            require(failed['repair_depth']<state['limits']['max_repairs'],'Repair limit exhausted')
            require(not any(j.get('repair_of')==failed['candidate_id'] for j in state['jobs'].values()),
                    'A repair already exists for this failed candidate')
            from .intake import validate_variant
            variant=validate_variant(self.store,response['variant'],failed['variant']['label'],
                provenance=failed['contract']['context']['provenance'])
            if variant is None: variant=detached(response['variant'])
            source_key = 'source_bundle_ref' if failed['contract']['context']['provenance'] == 'real' else 'source_ref'
            require(variant[source_key]['digest']!=failed['variant'][source_key]['digest'],
                    'Repair must produce new source; use retry for unchanged code')
            identity=stable('wf-repair',job_id+':'+key)
            job=detached(failed)
            job.update(id=identity,candidate_id=stable('wf-c',identity),run_id=stable('wf-r',identity+':0'),
                parent_id=stable('wf-parent',identity),parent_evaluation_id=None,repair_of=failed['candidate_id'],
                repair_depth=failed['repair_depth']+1,variant=variant,state='queued',stage='generate',
                attempt=0,receipts={},result=None,reason=response['reason'],reserved=False,origin_job_id=identity)
            job.pop('operation',None); job.pop('start_predecessor',None)
            state['jobs'][identity]=job
            state['commands'][command]={'digest':fingerprint,'job_id':identity}
            self._save(state,'Accepted conversation repair response',job)
            self._ensure_records(job)
            return identity

    def retry(self, job_id, key):
        with self.journal.lock():
            state=self.journal.load(); old=state['jobs'][job_id]
            command='retry:'+key
            if command in state['commands']:
                require(state['commands'][command]['source']==job_id,'Retry key conflict')
                return state['commands'][command]['job_id']
            require(old['state']=='failed' and old['result']['classification'] in ('incomplete','execution_failed'),
                    'Only execution failure or incomplete attempts can retry')
            require(old['attempt']<state['limits']['max_retries'],'Retry limit exhausted')
            require(not any(j.get('retry_of')==job_id for j in state['jobs'].values()),'Attempt already retried')
            require(not state['cancelled'],'Workflow cancelled')
            identity=stable('wf-retry',job_id+':'+key)
            job=detached(old)
            job.update(id=identity,run_id=stable('wf-r',identity),state='queued',stage='generate',receipts={},
                result=None,attempt=old['attempt']+1,retry_of=job_id,reserved=False,
                origin_job_id=old.get('origin_job_id',old['id']))
            job.pop('operation',None);job.pop('start_predecessor',None)
            state['jobs'][identity]=job
            state['commands'][command]={'source':job_id,'job_id':identity}
            self._save(state,'Explicit bounded retry with a new Run',job)
            self._ensure_records(job)
            return identity
