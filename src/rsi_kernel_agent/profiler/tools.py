"""Small allowlisted deterministic tools. No shell, network, kernel, or API calls."""
from hashlib import sha256
import json

from .models import calculate
from .outputs import detached, fields, require, references
from ..contracts import ArtifactRef, canonical_json


TOOL_HELP = {
    'source':'{strategy_id}: pinned full source and configuration',
    'history':'{strategy_id}: complete independently bound Memory history',
    'knowledge':'{method_id}: pinned method, source revisions and freshness issues',
    'reference':'{ref}: one immutable input reference including values and availability',
    'profile':'{strategy_id,profile_id}: normalize saved legacy parser artifact for this route',
    'calculate':'{model,inputs,evidence_refs,assumptions}: compute_memory, tile, live_memory, pipeline',
    'skill':'{name}: inspect an explicitly supplied existing skill, with its digest',
}


class AnalysisTools:
    def __init__(self, manifest, artifacts=None, skills=None):
        self.manifest = detached(manifest)
        self.artifacts = artifacts
        self.skills = detached(skills or {})

    def call(self, name, arguments):
        require(name in TOOL_HELP and type(arguments) is dict, 'Unknown tool or arguments')
        m, a = self.manifest, arguments
        if name in ('source','history'):
            fields(a,'strategy_id'); require(a['strategy_id'] in m['strategies'], 'Unknown strategy')
            row = m['strategies'][a['strategy_id']]
            if name == 'history': return detached(row['history'])
            return {'source':row['source'],'config':row['config'],'parent_id':row['parent_id'],
                    'identity':row['parent']['identity'],'ref':'source:'+a['strategy_id']}
        if name == 'knowledge':
            fields(a,'method_id'); require(a['method_id'] in m['knowledge'], 'Knowledge method was not pinned')
            return detached(m['knowledge'][a['method_id']])
        if name == 'reference':
            fields(a,'ref'); require(a['ref'] in m['references'], 'Unknown reference')
            return detached(m['references'][a['ref']])
        if name == 'skill':
            fields(a,'name'); require(a['name'] in self.skills, 'Skill not supplied for this analysis')
            content = self.skills[a['name']]
            return {'name':a['name'],'content':content,'sha256':sha256(content.encode()).hexdigest()}
        if name == 'calculate':
            fields(a,'model inputs evidence_refs assumptions')
            owners = {m['references'][r].get('strategy_id') for r in a['evidence_refs'] if r in m['references']}
            owners.discard(None)
            require(len(owners) <= 1, 'Calculation mixes strategy evidence')
            references(a['evidence_refs'],next(iter(owners),None),m['references'])
            result = calculate(a['model'],a['inputs'],evidence_refs=a['evidence_refs'],assumptions=a['assumptions'])
            result['strategy_id'] = next(iter(owners),None)
            return result
        fields(a,'strategy_id profile_id')
        require(a['strategy_id'] in m['strategies'], 'Unknown strategy')
        row = m['strategies'][a['strategy_id']]
        selected = next((p for p in row['profiles'] if p['id'] == a['profile_id']),None)
        require(selected is not None, 'Profile does not belong to selected strategy')
        require(self.artifacts is not None, 'Artifact reader required')
        run = row['run']
        require(run is not None, 'Measured Profile requires a bound Run')
        from .profiles import normalize_duration_report
        normalized = []
        for eid in selected['evidence_ids']:
            evidence = m['references']['evidence:'+eid]
            if evidence['value']['evidence_kind'] not in ('trace_derived','measured_timing','measured_counter','derived_measurement'):
                normalized.append({'evidence_id':eid,'availability':'unavailable',
                    'reason':'Legacy measured duration parser cannot reinterpret predicted/compiler/source claims',
                    'original_evidence_kind':evidence['value']['evidence_kind']})
                continue
            if evidence.get('availability') != 'available':
                normalized.append({'evidence_id':eid,'availability':'unavailable','reason':'Input manifest could not verify evidence'})
                continue
            for raw in evidence.get('artifacts',[]):
                report = json.loads(self.artifacts.read(ArtifactRef(**raw)))
                binding = dict(strategy_id=a['strategy_id'],parent_id=row['parent_id'],candidate_id=run['candidate_id'],
                               run_id=run['id'],context_id=row['context']['id'],provenance=row['context']['provenance'],
                               declared_identity=row['parent']['identity'])
                normalized.append(normalize_duration_report(report,binding=binding,artifact_refs=[raw]))
        states = [n.get('quality',{}).get('availability',n.get('availability','unavailable')) for n in normalized]
        bundle_state = m['references']['profile:'+a['profile_id']]['availability']
        availability = 'unavailable'
        if states and any(x != 'unavailable' for x in states):
            availability = 'available' if all(x=='available' for x in states) and bundle_state=='available' else 'partial'
        return {'profile_id':a['profile_id'],'reports':normalized,'availability':availability}
