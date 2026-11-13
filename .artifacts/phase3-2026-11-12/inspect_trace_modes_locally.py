"""Read archived diagnostic/negative-control bytes; no remote/Store writes."""
from datetime import datetime,timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

ROOT=Path(__file__).resolve().parents[2]
WORK=Path(__file__).resolve().parent
OUT=ROOT/'knowledge-site/manifests/phase3-2026-11-12'
verified={}


def blob(ref):
    path=WORK/'artifacts/sha256'/ref['digest'][:2]/ref['digest']
    raw=path.read_bytes()
    if len(raw)!=ref['size'] or sha256(raw).hexdigest()!=ref['digest']:
        raise ValueError('Archived artifact identity mismatch')
    verified[ref['digest']]=len(raw)
    return raw


def main():
    request=json.loads((WORK/'trace-mode-diagnostic-request.json').read_bytes())
    collection=json.loads((WORK/'trace-mode-diagnostic-collected.json').read_bytes())
    receipt=json.loads(blob(collection['receipt_ref']))
    body={k:v for k,v in receipt.items() if k!='receipt_digest'}
    canonical=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()
    assert sha256(canonical).hexdigest()==receipt['receipt_digest']
    assert collection['request_digest']==request['request_digest']==receipt['request_digest']
    assert receipt['source_digest']==request['source_digest'] and receipt['formal_run_id'] is None
    source=blob(request['source_ref']).decode('utf-8')
    assert "jax.device_get(result[0, 0])" in source
    rows=[]
    for artifact in collection['artifacts']:
        raw=blob(artifact['artifact_ref'])
        if artifact['relative_path'].endswith('/mode-result.json'):
            mode=json.loads(raw)
            planes=[plane for inventory in mode['event_inventory'] for plane in inventory['planes']]
            device=[plane for plane in planes if plane['name'].startswith('/device:TPU:')]
            names={}
            for plane in device:
                for name,count in plane['event_names'].items():names[name]=names.get(name,0)+count
            rows.append({'mode':mode['mode'],'api_status':mode['api_status'],'synchronized':mode['synchronized'],
                'result_0_0':mode['result_0_0'],'errors':mode['errors'],
                'device_plane_count':len(device),'device_event_count':sum(p['event_count'] for p in device),
                'device_event_names':names,'mode_artifact_ref':artifact['artifact_ref'],
                'raw_xplane_refs':[item['artifact_ref'] for item in collection['artifacts']
                    if item['relative_path'].startswith(mode['mode']+'/') and item['relative_path'].endswith('.xplane.pb')]})
    idle_raw=(WORK/'runtime2-idle-after-trace.json').read_bytes()
    idle=json.loads(idle_raw.decode('utf-16') if idle_raw[:2] in (bytes([255,254]),bytes([254,255])) else idle_raw.decode('utf-8-sig'))
    assert idle['request_digest']==request['request_digest'] and idle['receipt_digest']==receipt['receipt_digest']
    assert idle['status']=='clear_visible_processes_after_diagnostic_exit' and idle['guard_cleared'] and idle['diagnostic_pid_exited']
    negatives=[]
    database=sqlite3.connect((WORK/'experiments.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    database.execute('PRAGMA query_only=ON')
    for name in ('oracle-negative-attention','oracle-negative-cache'):
        result_path=WORK/(name+'-runtime2-result.json')
        result=json.loads(result_path.read_bytes())
        job=json.loads((WORK/(name+'-runtime2-job.json')).read_bytes())
        assert result['run_id']==job['run_id'] and result['provenance']=='real'
        assert result['classification']=='incorrect' and result['correctness']=='failed' and not result['confirmed']
        assert set(result['receipt_refs'])=={'generate','compile','correctness'} and result['latency_ms'] is None
        stages={stage:json.loads(blob(ref)) for stage,ref in result['receipt_refs'].items()}
        for stage,value in stages.items():
            assert value['run_id']==job['run_id'] and value['candidate_id']==job['candidate_id']
            for ref in value.get('artifact_refs',[]):blob(ref)
        correct=stages['correctness']
        assert correct['status']=='failed' and correct['outputs']['environment']['runtime_session_id']==request['runtime_id']
        promotion=json.loads(database.execute('SELECT payload FROM records WHERE id=?',(result['promotion_id'],)).fetchone()[0])
        assert promotion['details']['eligible'] is False and promotion['details']['branch_promoted'] is False and promotion['details']['task_promoted'] is False
        negatives.append({'name':name,'run_id':job['run_id'],'candidate_id':job['candidate_id'],
            'context_id':job['context_id'],'classification':result['classification'],'correctness':result['correctness'],
            'receipt_stages':sorted(stages),'benchmark_executed':False,'profile_executed':False,
            'cases':correct['outputs']['cases'],'promotion_decision':promotion,
            'result_file_sha256':sha256(result_path.read_bytes()).hexdigest()})
    database.close()
    report={'schema_version':1,'verified_at_utc':datetime.now(timezone.utc).isoformat(),'network_calls':0,
        'store_writes':0,'scope':'Local SHA/size and saved event inventory verification; raw XPlane protobuf not reparsed',
        'request_digest':request['request_digest'],'diagnostic_source_digest':request['source_digest'],
        'receipt_ref':collection['receipt_ref'],'receipt_digest':receipt['receipt_digest'],
        'runtime_id':request['runtime_id'],'environment':request['expected_environment'],
        'modes':sorted(rows,key=lambda row:request['modes'].index(row['mode'])),'idle_observation':idle,
        'idle_file_sha256':sha256(idle_raw).hexdigest(),'oracle_negative_controls':negatives,
        'verified_unique_artifacts':len(verified),'verified_bytes':sum(verified.values()),
        'limitations':['API success does not establish full mode semantics or hardware counter support.',
            'Scalar extraction device_get(result[0,0]) occurred before stop_trace; dynamic_slice/squeeze are additional traced work.',
            'The 134 AND_SYNC events cannot all be assigned to the five matrix computations.',
            'This diagnostic is not an MLA Run/ProfileBundle, a DMA timing breakdown or a performance experiment.',
            'Negative correctness receipt bindings and all raw artifact hashes verified; numerical comparisons not recomputed here.']}
    (OUT/'trace-mode-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'modes':[{'mode':row['mode'],'api_status':row['api_status'],'result':row['result_0_0'],
        'device_event_count':row['device_event_count']} for row in report['modes']],
        'negative_controls':[{'name':row['name'],'classification':row['classification']} for row in negatives],
        'verified_unique_artifacts':len(verified),'verified_bytes':sum(verified.values()),
        'idle_status':idle['status']},ensure_ascii=False))


if __name__=='__main__':main()
