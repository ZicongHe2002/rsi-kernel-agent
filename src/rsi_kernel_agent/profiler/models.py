"""Reproducible analytical estimates, never hardware measurements.

The four models deliberately describe small, explicit idealizations. Input names
fix units; hardware limits are optional, attributed inputs rather than defaults.
References identify inputs for the caller to validate against its manifest.
"""
from __future__ import annotations

import json
import math

from ..contracts import ValidationError, canonical_json


class ModelError(ValidationError):
    """An analytical model request is malformed or numerically unrepresentable."""


def _require(condition, message):
    if not condition:
        raise ModelError(message)


def _fields(value, required, optional=()):
    _require(type(value) is dict, 'Inputs must be an object')
    _require(set(required) <= value.keys(), 'Missing model input')
    _require(value.keys() <= set(required) | set(optional), 'Unknown model input or unit')


def _integer(value, name, minimum=1):
    _require(type(value) is int and value >= minimum, f'{name} must be an integer >= {minimum}')
    return value


def _number(value, name, *, zero=False):
    _require(type(value) in (int, float) and math.isfinite(value), f'{name} must be finite numeric data')
    _require(value >= 0 if zero else value > 0, f'{name} is outside the model domain')
    return value


def _strings(values, name):
    _require(type(values) in (list, tuple), f'{name} must be an array')
    _require(all(type(item) is str and item.strip() for item in values), f'{name} requires nonblank strings')
    _require(len(set(values)) == len(values), f'{name} cannot contain duplicates')
    return list(values)


def _hardware(inputs, names, references, assumptions):
    if 'hardware_basis' in inputs:
        _require(type(inputs['hardware_basis']) is str and inputs['hardware_basis'].strip(),
                 'hardware_basis must explain the hardware source or assumption')
    present = [name for name in names if inputs.get(name) is not None]
    if present:
        _require('hardware_basis' in inputs, 'Hardware values require hardware_basis')
        _require(bool(references or assumptions), 'Hardware values require an evidence reference or explicit assumption')
    for name in present:
        _number(inputs[name], name)


def _compute(inputs, refs, assumptions):
    _fields(inputs, ('m', 'n', 'k', 'dtype_bytes'),
            ('compute_rate_flops_s', 'bandwidth_bytes_s', 'hardware_basis'))
    m, n, k, width = (_integer(inputs[key], key) for key in ('m', 'n', 'k', 'dtype_bytes'))
    _hardware(inputs, ('compute_rate_flops_s', 'bandwidth_bytes_s'), refs, assumptions)
    work = 2 * m * n * k
    read = (m * k + k * n) * width
    write = m * n * width
    compute = work / inputs['compute_rate_flops_s'] if inputs.get('compute_rate_flops_s') is not None else None
    memory = (read + write) / inputs['bandwidth_bytes_s'] if inputs.get('bandwidth_bytes_s') is not None else None
    bounds = [value for value in (compute, memory) if value is not None]
    outputs = dict(work_flops=work, read_bytes=read, write_bytes=write, total_bytes=read + write,
                   arithmetic_intensity=work / (read + write), compute_bound_s=compute,
                   memory_bound_s=memory, resource_lower_bound_s=max(bounds) if bounds else None)
    unavailable = {}
    if compute is None:
        unavailable['compute_bound_s'] = 'No compute throughput supplied'
    if memory is None:
        unavailable['memory_bound_s'] = 'No memory bandwidth supplied'
    if not bounds:
        unavailable['resource_lower_bound_s'] = 'No hardware rate supplied'
    return dict(
        formula='F=2*m*n*k; R=(m*k+k*n)*d; W=m*n*d; I=F/(R+W); t_compute=F/P; t_memory=(R+W)/B; bound=max(available bounds)',
        input_units=dict(m='elements', n='elements', k='elements', dtype_bytes='byte/element',
                         compute_rate_flops_s='FLOP/s', bandwidth_bytes_s='byte/s', hardware_basis='text'),
        outputs=outputs,
        output_units=dict(work_flops='FLOP', read_bytes='byte', write_bytes='byte', total_bytes='byte',
                          arithmetic_intensity='FLOP/byte', compute_bound_s='s', memory_bound_s='s', resource_lower_bound_s='s'),
        assumptions=['Dense matrix multiplication with one multiply and one add per reduction element',
                     'A and B read once; C written once; no initial C read; all operands use dtype_bytes',
                     'An available single-resource bound is partial; supplied ceilings are not sustained measurements'],
        omitted_costs=['Cache and hierarchy traffic', 'Launch and synchronization', 'Layout conversions',
                       'Non-matmul operations', 'Utilization and contention'], unavailable=unavailable)


def _tile(inputs, refs, assumptions):
    names = ('m', 'n', 'k', 'tile_m', 'tile_n', 'tile_k', 'dtype_bytes')
    _fields(inputs, names)
    m, n, k, tm, tn, tk, width = (_integer(inputs[key], key) for key in names)
    im, jn, rk = (m + tm - 1) // tm, (n + tn - 1) // tn, (k + tk - 1) // tk
    pm, pn, pk = im * tm, jn * tn, rk * tk
    iterations = im * jn * rk
    reads = iterations * (tm * tk + tk * tn) * width
    writes = im * jn * tm * tn * width
    outputs = dict(padded_m=pm, padded_n=pn, padded_k=pk, output_tiles=im * jn,
                   reduction_steps=rk, tile_iterations=iterations, useful_flops=2 * m * n * k,
                   padded_flops=2 * pm * pn * pk, padding_flops=2 * (pm * pn * pk - m * n * k),
                   tiled_read_bytes=reads, tiled_write_bytes=writes, tiled_total_bytes=reads + writes,
                   unique_bytes=(m * k + k * n + m * n) * width,
                   a_tile_reuse=tn, b_tile_reuse=tm, work_efficiency=(m * n * k) / (pm * pn * pk))
    units = {key: 'elements' for key in ('padded_m', 'padded_n', 'padded_k')}
    units.update({key: 'count' for key in ('output_tiles', 'reduction_steps', 'tile_iterations', 'a_tile_reuse', 'b_tile_reuse')})
    units.update({key: 'FLOP' for key in ('useful_flops', 'padded_flops', 'padding_flops')})
    units.update({key: 'byte' for key in ('tiled_read_bytes', 'tiled_write_bytes', 'tiled_total_bytes', 'unique_bytes')})
    units['work_efficiency'] = 'ratio'
    return dict(
        formula='i=ceil(m/tm); j=ceil(n/tn); r=ceil(k/tk); padded=(i*tm,j*tn,r*tk); reads=i*j*r*(tm*tk+tk*tn)*d; writes=i*j*tm*tn*d',
        input_units={key: ('byte/element' if key == 'dtype_bytes' else 'elements') for key in names},
        outputs=outputs, output_units=units,
        assumptions=['Dense matmul with rectangular padding to full tiles',
                     'Each reduction tile reloads A and B; accumulator retained through reduction; output written once',
                     'Tile traffic counts full padded transfers; masked edges may move fewer bytes',
                     'A/B reuse counts padded multiply uses per tile element; this is not observed cache reuse'],
        omitted_costs=['Register and accumulator spills', 'DMA startup', 'Vector alignment requirements',
                       'Cache reuse between output tiles', 'Compiler mapping and scheduling'], unavailable={})


def _live(inputs, refs, assumptions):
    _fields(inputs, ('buffers',), ('capacity_bytes', 'hardware_basis'))
    _require(type(inputs['buffers']) is list, 'buffers must be an array')
    _hardware(inputs, ('capacity_bytes',), refs, assumptions)
    capacity = inputs.get('capacity_bytes')
    if capacity is not None:
        _integer(capacity, 'capacity_bytes')
    changes, names = {}, set()
    for buffer in inputs['buffers']:
        _fields(buffer, ('name', 'elements', 'dtype_bytes', 'copies', 'start', 'end'))
        name = buffer['name']
        _require(type(name) is str and name.strip() and name not in names, 'Buffer names must be nonblank and distinct')
        names.add(name)
        size = (_integer(buffer['elements'], 'elements') * _integer(buffer['dtype_bytes'], 'dtype_bytes')
                * _integer(buffer['copies'], 'copies'))
        start, end = _integer(buffer['start'], 'start', 0), _integer(buffer['end'], 'end', 0)
        _require(end > start, 'Buffer lifetime must have end > start')
        changes[start] = changes.get(start, 0) + size
        changes[end] = changes.get(end, 0) - size
    current = peak = 0
    peak_step = None
    for step in sorted(changes):
        current += changes[step]
        if current > peak:
            peak, peak_step = current, step
    outputs = dict(peak_bytes=peak, peak_step=peak_step,
                   capacity_fraction=peak / capacity if capacity is not None else None,
                   headroom_bytes=capacity - peak if capacity is not None else None)
    unavailable = {} if capacity is not None else {
        'capacity_fraction': 'No memory capacity supplied', 'headroom_bytes': 'No memory capacity supplied'}
    if peak_step is None:
        unavailable['peak_step'] = 'Empty buffer schedule'
    return dict(
        formula='size_i=elements_i*dtype_bytes_i*copies_i; live(t)=sum(size_i for start_i<=t<end_i); peak=max(live(t)); pressure=peak/capacity',
        input_units={'buffers[].name': 'text', 'buffers[].elements': 'elements', 'buffers[].dtype_bytes': 'byte/element',
                     'buffers[].copies': 'count', 'buffers[].start': 'schedule_step', 'buffers[].end': 'schedule_step',
                     'capacity_bytes': 'byte', 'hardware_basis': 'text'},
        outputs=outputs, output_units=dict(peak_bytes='byte', peak_step='schedule_step', capacity_fraction='ratio', headroom_bytes='byte'),
        assumptions=['Buffer lifetimes are half-open logical schedule intervals, not elapsed clock measurements',
                     'Listed buffers occupy one shared memory resource and copies coexist throughout each lifetime',
                     'Distinct buffers do not alias; an empty list means explicitly no modeled buffers'],
        omitted_costs=['Compiler temporaries and spills', 'Allocation alignment and fragmentation',
                       'Hidden runtime reservations', 'Unlisted memories and buffers'], unavailable=unavailable)


def _pipeline(inputs, refs, assumptions):
    _fields(inputs, ('stages', 'transfer_s', 'compute_s'))
    count = _integer(inputs['stages'], 'stages')
    transfer, compute = (_number(inputs[key], key, zero=True) for key in ('transfer_s', 'compute_s'))
    serial = count * (transfer + compute)
    ideal = transfer + compute + (count - 1) * max(transfer, compute)
    return dict(
        formula='serial=N*(transfer+compute); ideal=transfer+compute+(N-1)*max(transfer,compute); speedup=serial/ideal',
        input_units=dict(stages='count', transfer_s='s', compute_s='s'),
        outputs=dict(serial_s=serial, ideal_pipeline_s=ideal, saved_s=serial - ideal,
                     ideal_speedup=serial / ideal if ideal else None),
        output_units=dict(serial_s='s', ideal_pipeline_s='s', saved_s='s', ideal_speedup='ratio'),
        assumptions=['stages counts repeated work items through one transfer engine and one compute engine',
                     'Transfer and compute use independent resources, with sufficient buffers and legal dependencies',
                     'Identical per-item durations; first transfer fills and final compute drains the pipeline'],
        omitted_costs=['Synchronization and DMA startup', 'Bandwidth contention', 'Buffer allocation',
                       'Uneven work items', 'Additional transfer directions and pipeline engines'],
        unavailable={} if ideal else {'ideal_speedup': 'Zero modeled time makes a speedup ratio undefined'})


_MODELS = dict(compute_memory=_compute, tile=_tile, live_memory=_live, pipeline=_pipeline)


def calculate(model, inputs, *, evidence_refs=(), assumptions=()):
    """Execute one version-1 model and return a detached, replayable JSON record.

    Inputs use the units in their field names and the returned input_units map.
    Unavailable hardware bounds are null, with a reason in unavailable. The
    caller must validate evidence_refs; arbitrary citation text is not evidence.
    """
    _require(type(model) is str and model in _MODELS, 'Unknown analytical model')
    refs = _strings(evidence_refs, 'evidence_refs')
    supplied_assumptions = _strings(assumptions, 'assumptions')
    try:
        detached = json.loads(canonical_json(inputs))
        result = _MODELS[model](detached, refs, supplied_assumptions)
        result.update(schema_version=1, model=model, model_revision='1', classification='model_predicted',
                      inputs=detached, evidence_refs=refs)
        result['assumptions'] = list(dict.fromkeys(result['assumptions'] + supplied_assumptions))
        return json.loads(canonical_json(result))
    except (ValueError, TypeError, OverflowError) as error:
        if isinstance(error, ModelError):
            raise
        raise ModelError(f'Model cannot represent these inputs: {error}') from error
