"""Deterministic admission, weighted fairness and conservative cost bounds.

Reservations charge an attempt exactly once; they are not refunded after early
failure. Declared token/time bounds are budgets, not measured model usage.
"""
from fractions import Fraction
import math


_DEFAULTS = dict(max_concurrency=2,executions=100,tokens=100000,time_s=10000,
                 max_repairs=1,max_retries=1,weights={})
_COSTS = ('executions','tokens','time_s')


def _number(value, label, minimum=0, integer=False):
    valid = type(value) is int if integer else type(value) in (int,float)
    if not valid or not math.isfinite(value) or value < minimum:
        raise ValueError(f'Invalid {label}')


def validate_limits(limits):
    if type(limits) is not dict or set(limits) - set(_DEFAULTS):
        raise ValueError('Unknown workflow limit fields')
    result = {**_DEFAULTS,**limits}
    for key in ('max_concurrency','executions','tokens','max_repairs','max_retries'):
        _number(result[key],key,minimum=1 if key=='max_concurrency' else 0,integer=True)
    _number(result['time_s'],'time_s')
    if type(result['weights']) is not dict:
        raise ValueError('Strategy weights must be an object')
    result['weights'] = dict(result['weights'])
    for sid,weight in result['weights'].items():
        if type(sid) is not str or not sid.strip():
            raise ValueError('Strategy weight needs an ID')
        _number(weight,'strategy weight')
        if weight == 0:
            raise ValueError('Strategy weight must be positive')
    return result


def remaining_budget(state):
    limits = validate_limits(state['limits'])
    spent = state['spent']
    if type(spent) is not dict or set(spent) != set(_COSTS):
        raise ValueError('Invalid workflow spending fields')
    for name in _COSTS:
        _number(spent[name],name+' spending',integer=name!='time_s')
        if spent[name] > limits[name]:
            raise ValueError('Workflow spending exceeds budget')
    return {name:limits[name]-spent[name] for name in _COSTS}


def _cost(job):
    cost = job['variant']['cost']
    if type(cost) is not dict or set(cost) != set(_COSTS):
        raise ValueError('Invalid attempt cost fields')
    for name in _COSTS:
        _number(cost[name],name+' cost',minimum=1 if name=='executions' else 0,
                integer=name!='time_s')
    return cost


def reserve(state, job):
    """Charge all cost bounds together or defer; the caller persists the state."""
    cost, remaining = _cost(job), remaining_budget(state)
    if type(job.get('reserved')) is not bool:
        raise ValueError('Attempt reservation flag must be boolean')
    if job['reserved']:
        return True
    exceeded = [name for name in _COSTS if cost[name] > remaining[name]]
    if exceeded:
        job.update(state='deferred',reason='Budget exhausted: ' + ', '.join(exceeded))
        return False
    for name in _COSTS:
        state['spent'][name] += cost[name]
    job['reserved'] = True
    return True


def choose_jobs(state):
    """Mutate one admission batch; pending/unknown jobs retain their slots."""
    limits = validate_limits(state['limits'])
    remaining_budget(state)
    for value in state['served'].values():
        _number(value,'served attempts',integer=True)
    # Validate before changing any job or budget; malformed late items cannot
    # leave a partly admitted in-memory batch.
    for job in state['jobs'].values():
        _cost(job)
        _number(job['variant']['priority'],'priority',minimum=-math.inf,integer=True)
        if type(job.get('reserved')) is not bool:
            raise ValueError('Attempt reservation flag must be boolean')
    if state['cancelled']:
        return []
    active = sum(job['state'] in ('active','evaluating','blocked') for job in state['jobs'].values())
    admitted = []
    while active < limits['max_concurrency']:
        queued = [job for job in state['jobs'].values()
                  if job['state']=='queued' and job['strategy_id'] not in state['paused']]
        if not queued:
            break
        strategies = {job['strategy_id'] for job in queued}
        sid = min(strategies,key=lambda sid:(
            Fraction(state['served'].get(sid,0)) / Fraction(limits['weights'].get(sid,1)),sid))
        selected = min((job for job in queued if job['strategy_id']==sid),
                       key=lambda job:(-job['variant']['priority'],job['id']))
        if not reserve(state,selected):
            continue
        selected.update(state='active',reason='Admitted under workflow budget')
        state['served'][sid] = state['served'].get(sid,0)+1
        admitted.append(selected['id'])
        active += 1
    return admitted
