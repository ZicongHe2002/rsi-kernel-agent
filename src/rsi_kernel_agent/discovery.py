"""Bounded host-assisted discovery and a separate, auditable mechanism review."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
import ipaddress
import json
import re
from urllib.parse import urlsplit

from .contracts import ArtifactRef, canonical_json, record_to_dict
from .knowledge import (KnowledgeClient, KnowledgeError, RevisionConflict, SourceDocument,
                        _parse_method, _require, _text, method_from_dict, method_note_id,
                        method_to_dict)

_MAX_QUERIES = 8
_MAX_SOURCE_CHARS = 240000
_STRUCTURES = {'decomposition', 'reduction', 'reuse', 'layout_lifetime', 'movement_wait', 'fusion_boundary'}


def _digest(value):
    return sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _public_url(value):
    _require(_text(value), 'Public source URL required')
    parsed = urlsplit(value)
    _require(parsed.scheme in ('http', 'https') and parsed.hostname and
             not parsed.username and not parsed.password, 'Only public HTTP(S) source URLs are accepted')
    host = parsed.hostname.lower().rstrip('.')
    _require(host != 'localhost' and not host.endswith(('.localhost', '.local', '.internal')),
             'Private source URL is not allowed')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        _require('.' in host and not re.fullmatch(r'(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*', host),
                 'Public source hostname required; noncanonical numeric hosts are not allowed')
    else:
        _require(address.is_global, 'Private source address is not allowed')
    return value


def _coverage(content, supplied=None):
    supplied = {} if supplied is None else supplied
    _require(type(supplied) is dict, 'Invalid source coverage')
    total = supplied.get('expected_chars', len(content))
    read = supplied.get('read_chars', len(content))
    complete = supplied.get('complete', True)
    _require(type(total) is int and total >= len(content) and type(read) is int and
             0 <= read <= len(content) and type(complete) is bool, 'Invalid source coverage counts')
    return {'scope': supplied.get('scope', 'returned_content'), 'expected_chars': total,
            'read_chars': read, 'complete': complete and total == read == len(content)}


async def search_sources(knowledge, queries, *, mode='exploratory', external=None, max_reads=8):
    _require(mode in ('directed', 'exploratory'), 'Invalid search mode')
    _require(type(queries) in (list, tuple) and 1 <= len(queries) <= _MAX_QUERIES and
             all(type(q) is str and len(q) <= 1000 for q in queries), 'Discovery allows 1..8 public queries')
    _require(type(max_reads) is int and 1 <= max_reads <= 32, 'Discovery read limit must be 1..32')
    _require(external is None or callable(external), 'External tools must be supplied by the trusted host')
    log, unknowns, deferred, documents = [], [], [], []
    found = {'knowledge': {}, 'external': {}}
    availability = {'knowledge': 'available' if knowledge is not None else 'unavailable',
                    'external': 'available' if external is not None else 'unavailable'}

    async def local_call(name, args):
        entry = {'channel': 'knowledge', 'operation': name, 'request': args,
                 'at': datetime.now(timezone.utc).isoformat()}
        log.append(entry)
        try:
            result = await knowledge._call(name, args)
        except KnowledgeError as error:
            entry.update(status='unavailable', reason=str(error))
            raise
        summary = {k: v for k, v in result.items() if k not in ('content', 'results')}
        if 'content' in result:
            summary['content_sha256'] = sha256(result['content'].encode('utf-8')).hexdigest()
        if 'results' in result:
            summary['results'] = [{k: v for k, v in row.items() if k != 'content'} for row in result['results']]
        entry.update(status='available', response=summary,
                     locator=f"chars:{args['offset']}:{args['offset'] + len(result.get('content', ''))}" if name == 'fetch' else None)
        return result

    async def external_call(name, args):
        entry = {'channel': 'external', 'operation': name, 'request': args,
                 'at': datetime.now(timezone.utc).isoformat()}
        log.append(entry)
        try:
            result = await external(name, args)
            _require(type(result) is dict and result.get('status') in ('available', 'unavailable'),
                     'External response must declare availability')
            if result['status'] == 'unavailable':
                _require(_text(result.get('reason')), 'Unavailable external tool requires a reason')
            entry.update(status=result['status'], response={k: v for k, v in result.items() if k != 'content'})
            if type(result.get('content')) is str:
                entry['response']['content_sha256'] = sha256(result['content'].encode('utf-8')).hexdigest()
                entry['response']['read_chars'] = len(result['content'])
            return result
        except Exception as error:
            entry.update(status='unavailable', reason='External tool failed: ' + type(error).__name__)
            return {'status': 'unavailable', 'reason': entry['reason']}

    local = KnowledgeClient(local_call) if knowledge is not None else None
    if external is None:
        unknowns.append('External search/read tools are unavailable; local retrieval is not open-web discovery.')
    if local is None:
        unknowns.append('Knowledge search transport is unavailable.')
    for query in dict.fromkeys(queries):
        if local is not None:
            try:
                result = await local.search(query, max_results=max_reads)
                for row in result['results']:
                    found['knowledge'].setdefault(row['id'], row)
                if not result['complete']:
                    deferred.append({'channel': 'knowledge', 'query': query, 'reason': 'Search result limit',
                                     'total': result['total'], 'returned': len(result['results'])})
            except KnowledgeError as error:
                availability['knowledge'] = 'partial'
                unknowns.append(str(error))
        if external is not None:
            result = await external_call('search', {'query': query, 'limit': max_reads})
            if result['status'] != 'available':
                availability['external'] = 'unavailable'
                unknowns.append(result['reason'])
                continue
            if type(result.get('results')) is not list:
                unknowns.append('External search returned no result array.')
                availability['external'] = 'partial'
                continue
            for row in result['results'][:max_reads]:
                try:
                    _require(type(row) is dict and _text(row.get('title')), 'Invalid external source metadata')
                    url = _public_url(row.get('url'))
                    found['external'].setdefault(url, row)
                except (KnowledgeError, ValueError) as error:
                    unknowns.append(str(error))
            if len(result['results']) > max_reads:
                deferred.append({'channel': 'external', 'query': query, 'reason': 'Search result limit',
                                 'total': len(result['results']), 'returned': max_reads})
    pending = []
    local_rows, external_rows = list(found['knowledge'].values()), list(found['external'].values())
    for index in range(max(len(local_rows), len(external_rows))):
        if index < len(local_rows):
            pending.append(('knowledge', local_rows[index]))
        if index < len(external_rows):
            pending.append(('external', external_rows[index]))
    for channel, item in pending[max_reads:]:
        deferred.append({'channel': channel, 'id': item.get('id', item.get('url')), 'reason': 'Source read limit'})
    for channel, item in pending[:max_reads]:
        try:
            if channel == 'knowledge':
                doc = await local.fetch_document(item['id'], expected_revision=item['revision'], max_chars=_MAX_SOURCE_CHARS)
                capture = {**asdict(doc), 'url': None, 'channel': channel, 'upstream_revision': doc.revision,
                           'locator': f'chars:0:{len(doc.content)}', 'coverage': _coverage(doc.content, {'scope': 'document'})}
            else:
                result = await external_call('read', {'url': item['url']})
                _require(result['status'] == 'available', result.get('reason', 'External source unavailable'))
                url = _public_url(result.get('url'))
                content = result.get('content')
                _require(_text(content) and len(content) <= _MAX_SOURCE_CHARS and _text(result.get('title')),
                         'External source is empty, oversized or missing title')
                coverage = _coverage(content, result.get('coverage'))
                if result.get('truncated'):
                    coverage['complete'] = False
                capture = {'id': url, 'url': url, 'requested_url': item['url'], 'title': result['title'],
                           'category': 'external', 'revision': sha256(content.encode('utf-8')).hexdigest(),
                           'upstream_revision': result.get('revision', item.get('revision')),
                           'content': content, 'channel': channel, 'locator': result.get('locator', 'returned-content'),
                           'coverage': coverage}
            capture.update(content_sha256=sha256(capture['content'].encode('utf-8')).hexdigest(),
                           read_at=datetime.now(timezone.utc).isoformat())
            documents.append(capture)
            if not capture['coverage']['complete']:
                unknowns.append(capture['id'] + ': source reading is incomplete')
        except (KnowledgeError, ValueError) as error:
            unknowns.append(str(error))
            availability[channel] = 'partial'
    return {'schema_version': 1, 'mode': mode, 'queries': list(queries), 'documents': documents,
            'request_log': log, 'availability': availability, 'unknowns': unknowns, 'deferred': deferred,
            'limits': {'max_queries': _MAX_QUERIES, 'max_reads': max_reads, 'max_source_chars': _MAX_SOURCE_CHARS},
            'knowledge_corpus_snapshot_consistent': False, 'combined_atomic_snapshot': False}


async def archive_source(knowledge, capture):
    _require(type(capture) is dict and _text(capture.get('content')), 'Read source content required')
    content = capture['content']
    identity = sha256(content.encode('utf-8')).hexdigest()
    _require(capture.get('content_sha256') == identity, 'Captured source identity differs from content')
    if capture.get('channel') == 'knowledge':
        document = await knowledge.fetch_document(capture['id'], expected_revision=capture['revision'])
        _require(document.content == content, 'Captured local source differs from document')
        return document
    _require(capture.get('channel') == 'external' and _text(capture.get('title')) and _text(capture.get('locator')),
             'External source needs title and reading locator')
    metadata = {'schema_version': 1, 'url': _public_url(capture.get('url')), 'title': capture['title'],
                'upstream_revision': capture.get('upstream_revision'), 'content_sha256': identity,
                'locator': capture['locator'], 'coverage': _coverage(content, capture.get('coverage'))}
    note_id = 'RSI/Sources/source-' + _digest(metadata) + '.md'
    body = '# Captured source\n\n```rsi-source-v1\n' + canonical_json(metadata) + '\n```\n\n' + content
    try:
        existing = await knowledge.fetch_document(note_id)
    except KnowledgeError:
        existing = None
    if existing is None:
        try:
            await knowledge._call('create_note', {'title': note_id.rsplit('/', 1)[1][:-3],
                                                 'category': 'RSI/Sources', 'content': body})
        except KnowledgeError as error:
            try:
                existing = await knowledge.fetch_document(note_id)
            except KnowledgeError:
                raise error
            if existing.content != body:
                raise RevisionConflict('Source identity conflicts with existing note; preserve its contents') from error
        if existing is None:
            existing = await knowledge.fetch_document(note_id)
    if existing.content != body:
        raise RevisionConflict('Source note changed; archive never overwrites existing source evidence')
    return existing


def _seal_inventory(inventory):
    result = json.loads(canonical_json({k: v for k, v in inventory.items() if k != 'digest'}))
    result['digest'] = _digest(result)
    return result


def _check_inventory(inventory):
    _require(type(inventory) is dict and inventory.get('schema_version') == 1 and
             inventory.get('context_kind') == 'cross_route_method_review', 'Invalid mechanism review inventory')
    _require(inventory.get('digest') == _digest({k: v for k, v in inventory.items() if k != 'digest'}),
             'Mechanism review inventory identity changed')


def build_review_inventory(store, view, context_id, methods):
    _require(type(methods) is dict and type(methods.get('methods')) in (list, tuple), 'Method index required')
    cards = view.status_cards(context_id, recent_plan_limit=0)
    ids = [card['strategy_id'] for card in cards]
    _require(set(ids) == set(view.snapshot['strategy_counts']), 'Strategy index coverage differs from snapshot')
    selected = view.search_history(strategy_ids=ids, kinds=('Plan', 'Candidate', 'Run', 'RunEvent', 'EvaluationBundle', 'Decision'))
    rows = selected['records']
    plans = {row['payload']['id']: row['payload'] for row in rows if row['kind'] == 'Plan'}
    candidates = {row['payload']['id']: row['payload'] for row in rows if row['kind'] == 'Candidate'}
    runs = {row['payload']['id']: row['payload'] for row in rows if row['kind'] == 'Run'}
    refs = {'strategy:' + card['strategy_id']: {
        'kind': 'Strategy', 'record_id': card['strategy_id'], 'strategy_id': card['strategy_id'],
        'summary': card, 'availability': 'available', 'read': True} for card in cards}
    for pid, plan in plans.items():
        refs['plan:' + pid] = {'kind': 'Plan', 'record_id': pid, 'strategy_id': plan['strategy_id'],
                               'summary': plan['description'], 'availability': 'available', 'read': True}
    for cid, candidate in candidates.items():
        sid = plans[candidate['plan_id']]['strategy_id']
        digest = candidate['identity']['source_digest']
        entry = {'kind': 'Source', 'record_id': cid, 'strategy_id': sid, 'candidate_id': cid,
                 'source_digest': digest, 'artifact_ref': None, 'availability': 'unavailable', 'read': False}
        try:
            path = store.artifacts.path_for(ArtifactRef(digest, 0))
            entry.update(artifact_ref=asdict(ArtifactRef(digest, path.stat().st_size)), availability='available')
        except (ValueError, OSError):
            entry['reason'] = 'Pinned source artifact is unavailable'
        refs['source:' + cid] = entry
    for card in cards:
        parent = card['current_parent_candidate']
        if parent is None or parent['candidate_id'] in candidates:
            continue
        digest = parent['identity']['source_digest']
        entry = {'kind': 'Source', 'record_id': parent['parent_id'], 'strategy_id': card['strategy_id'],
                 'candidate_id': parent['candidate_id'], 'source_digest': digest,
                 'artifact_ref': None, 'availability': 'unavailable', 'read': False}
        try:
            path = store.artifacts.path_for(ArtifactRef(digest, 0))
            entry.update(artifact_ref=asdict(ArtifactRef(digest, path.stat().st_size)), availability='available')
        except (ValueError, OSError):
            entry['reason'] = 'Pinned parent source artifact is unavailable'
        refs['source:' + parent['parent_id']] = entry
    links, admissions = {}, {}
    for row in rows:
        payload = row['payload']
        if row['kind'] == 'Decision' and payload['details'].get('type') == 'exploration_admission.v1':
            admissions[payload['id']] = {'strategy_id': payload['target_id'], **payload['details']}
        if row['kind'] == 'Decision' and payload['details'].get('type') == 'knowledge_link.v1':
            link = payload['details']
            links.setdefault(link['method_id'], set()).add(link['strategy_id'])
        if row['kind'] in ('Run', 'RunEvent', 'EvaluationBundle'):
            run = payload if row['kind'] == 'Run' else runs[payload['run_id']]
            candidate = candidates[run['candidate_id']]
            refs['feedback:' + payload['id']] = {'kind': row['kind'], 'record_id': payload['id'],
                'strategy_id': plans[candidate['plan_id']]['strategy_id'], 'run_id': run['id'],
                'summary': {k: v for k, v in payload.items() if k in ('id', 'execution_status', 'scheduling_status',
                            'reason', 'correctness', 'timing_validity', 'latency_ms', 'confirmed')},
                'availability': 'available', 'read': False}
    method_index = {method.method_id: method_to_dict(method) for method in methods['methods']}
    pins = methods.get('pins', {})
    missing = list(methods.get('unavailable', []))
    if methods.get('index_scope') != 'all_methods':
        missing.append({'id': 'method-index', 'reason': 'Method index is filtered or its scope is unknown'})
    for mid in method_index:
        if mid not in pins:
            missing.append({'id': mid, 'reason': 'Method note revision was not pinned'})
    coverage = {'strategy_count': len(ids), 'expected_strategy_count': len(view.snapshot['strategy_counts']),
                'method_count': len(method_index), 'plan_count': len(plans), 'candidate_count': len(candidates),
                'index_complete': bool(methods.get('complete')) and not missing,
                'source_read_count': 0, 'source_count': sum(r['kind'] == 'Source' for r in refs.values()),
                'missing': missing}
    return _seal_inventory({'schema_version': 1, 'context_kind': 'cross_route_method_review',
        'task_id': view.snapshot['task_id'], 'context_id': context_id, 'snapshot': view.snapshot,
        'strategies': {card['strategy_id']: card for card in cards}, 'methods': method_index,
        'method_pins': pins, 'method_sources': methods.get('sources', {}),
        'method_strategies': {mid: sorted(sids) for mid, sids in links.items()},
        'candidates': candidates, 'admissions': admissions, 'references': refs, 'coverage': coverage,
        'knowledge_corpus_snapshot_consistent': False, 'combined_atomic_snapshot': False})


def expand_review_inventory(store, inventory, refs):
    _check_inventory(inventory)
    _require(type(refs) in (tuple, list) and len(refs) == len(set(refs)), 'Distinct review references required')
    result = json.loads(canonical_json(inventory))
    for ref in refs:
        _require(ref in result['references'], 'Unknown review reference: ' + str(ref))
        entry = result['references'][ref]
        try:
            if entry['kind'] == 'Source':
                _require(entry['artifact_ref'] is not None, 'Source artifact is unavailable')
                raw = store.artifacts.read(ArtifactRef(**entry['artifact_ref']))
                content = raw.decode('utf-8-sig')
                entry.update(content=content, locator=f'chars:0:{len(content)}',
                             coverage=_coverage(content, {'scope': 'source_artifact'}))
            else:
                entry['record'] = record_to_dict(store.get(entry['record_id']))
            entry.update(read=True, availability='available')
        except (ValueError, OSError) as error:
            entry.update(read=False, availability='unavailable', reason=type(error).__name__)
    result['coverage']['source_read_count'] = sum(r['kind'] == 'Source' and r['read'] for r in result['references'].values())
    return _seal_inventory(result)


def _review_reference(inventory, ref):
    _require(_text(ref), 'Review reference must be text')
    entry = inventory['references'].get(ref)
    if entry is None and ref in inventory.get('admissions', {}):
        entry = {**inventory['admissions'][ref], 'kind': 'Admission', 'read': True, 'availability': 'available'}
    _require(entry is not None, 'Review reference is outside the pinned inventory')
    _require(entry.get('read') and entry['availability'] == 'available', 'Review reference has not been read')
    return entry


def validate_method_review(proposed, review, inventory):
    method_to_dict(proposed)
    _check_inventory(inventory)
    names = {'inventory_digest', 'closest_method_id', 'mechanism_difference', 'structural_change', 'code_refs', 'plan_refs',
             'global_relation', 'task_relation', 'route_action', 'strategy_id', 'reviewed_strategy_ids', 'reviewed_method_ids', 'lineage'}
    optional = {'admission_comparisons', 'coverage_rationale'}
    _require(type(review) is dict and names <= set(review) <= names | optional,
             'Mechanism review fields differ from version 1')
    _require(review['inventory_digest'] == inventory['digest'], 'Review references another inventory')
    _require(review['global_relation'] in ('same', 'new', 'unknown') and
             review['task_relation'] in ('already_implemented', 'tried', 'new_to_task', 'unknown') and
             review['route_action'] in ('reuse', 'retry', 'fork', 'none', 'unknown'), 'Invalid mechanism classification')
    _require(_text(review['mechanism_difference']), 'Nearest mechanism comparison is required')
    _require(review['structural_change'] in _STRUCTURES | {'same', 'parameter', 'unknown'}, 'Invalid structure comparison')
    mid = review['closest_method_id']
    _require(mid is None or mid in inventory['methods'], 'Nearest method is outside the pinned index')
    _require(not inventory['methods'] or mid is not None or review['global_relation'] == 'unknown',
             'Compare the nearest existing method before declaring novelty')
    _require(review['global_relation'] != 'same' or mid is not None, 'Same mechanism must reuse an existing method ID')
    if review['global_relation'] == 'new':
        _require(proposed.method_id not in inventory['methods'], 'Existing method ID cannot be registered as globally new')
        _require(review['structural_change'] in _STRUCTURES, 'New mechanism requires substantive structural differences')
    for field, kind in (('code_refs', 'Source'), ('plan_refs', 'Plan')):
        values = review[field]
        _require(type(values) is list and len(values) == len(set(values)), 'Review references must be distinct arrays')
        for ref in values:
            entry = inventory['references'].get(ref)
            _require(entry is not None and entry['kind'] == kind, 'Review reference is outside the pinned inventory')
            _require(entry.get('read') and entry['availability'] == 'available', 'Review reference has not been read completely')
    for field, known in (('reviewed_strategy_ids', inventory['strategies']), ('reviewed_method_ids', inventory['methods'])):
        _require(type(review[field]) is list and len(set(review[field])) == len(review[field]) and
                 set(review[field]) <= set(known), 'Invalid review coverage')
    sid = review['strategy_id']
    if review['route_action'] == 'fork':
        _require(type(sid) is str and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', sid)),
                 'Fork requires a new stable strategy ID')
        _require(not any(r['kind'] == 'Plan' and r['strategy_id'] == sid for r in inventory['references'].values()) and
                 not any(a['strategy_id'] == sid for a in inventory.get('admissions', {}).values()),
                 'An existing route cannot receive a fresh identity through fork admission')
    else:
        _require(sid is None or sid in inventory['strategies'], 'Selected strategy is outside the snapshot')
    if review['route_action'] in ('reuse', 'retry'):
        _require(sid is not None, 'Existing route action requires its stable strategy ID')
    if review['route_action'] == 'fork':
        _require(review['task_relation'] == 'new_to_task' and review['structural_change'] in _STRUCTURES,
                 'Independent route requires a new task structure, not a parameter or synonym')
        lineage = review['lineage']
        _require(type(lineage) is dict and set(lineage) == {'strategy_id', 'parent_candidate_id'}, 'Fork needs exact lineage')
        candidate = inventory['candidates'].get(lineage['parent_candidate_id'])
        _require(candidate is not None and inventory['references']['plan:' + candidate['plan_id']]['strategy_id'] == lineage['strategy_id'],
                 'Fork parent does not belong to declared strategy')
    else:
        _require(review['lineage'] is None, 'Lineage belongs to a proposed structural fork')
    reused = mid if review['global_relation'] == 'same' else proposed.method_id
    if review['task_relation'] == 'new_to_task':
        _require(not inventory['method_strategies'].get(reused), 'Known task application cannot be declared new-to-task')
    limitations = []
    rationale = review.get('coverage_rationale', [])
    _require(type(rationale) is list, 'Route review rationale must be an array')
    explained = set()
    for item in rationale:
        _require(type(item) is dict and set(item) == {'strategy_id', 'mechanism_difference', 'refs'},
                 'Invalid route review rationale')
        route = item['strategy_id']
        _require(route in review['reviewed_strategy_ids'] and route not in explained,
                 'Route rationale is outside review coverage or duplicated')
        _require(_text(item['mechanism_difference']) and type(item['refs']) is list and
                 item['refs'] and len(item['refs']) == len(set(item['refs'])),
                 'Route review requires a mechanism comparison and distinct references')
        entries = [_review_reference(inventory, ref) for ref in item['refs']]
        _require(all(entry['strategy_id'] == route for entry in entries),
                 'Route review references another strategy')
        explained.add(route)
        if all(entry['kind'] == 'Strategy' for entry in entries) and (
                any(entry['kind'] != 'Strategy' and entry['strategy_id'] == route
                    for entry in inventory['references'].values()) or
                any(entry['strategy_id'] == route for entry in inventory.get('admissions', {}).values())):
            limitations.append('Route mechanism was not compared beyond its status card: ' + route)
        if any(entry['kind'] == 'Source' and not entry.get('coverage', {}).get('complete') for entry in entries):
            limitations.append('Route comparison cites incomplete source reading: ' + route)
    if explained != set(inventory['strategies']):
        limitations.append('Per-route mechanism comparisons are incomplete')
    comparisons = review.get('admission_comparisons', [])
    _require(type(comparisons) is list, 'Admission comparisons must be an array')
    compared = set()
    for comparison in comparisons:
        _require(type(comparison) is dict and set(comparison) ==
                 {'admission_id', 'relation', 'mechanism_difference', 'refs'}, 'Invalid admission comparison')
        admission_id = comparison['admission_id']
        _require(admission_id in inventory['admissions'] and admission_id not in compared,
                 'Admission comparison is outside the pinned inventory or duplicated')
        compared.add(admission_id)
        _require(comparison['relation'] in ('same', 'different', 'unknown') and _text(comparison['mechanism_difference']),
                 'Admission comparison requires a mechanism relation and difference')
        _require(type(comparison['refs']) is list and admission_id in comparison['refs'] and
                 len(comparison['refs']) == len(set(comparison['refs'])),
                 'Admission comparison must cite the existing admission and distinct pinned inputs')
        for ref in comparison['refs']:
            entry = _review_reference(inventory, ref)
            if entry['kind'] == 'Source' and not entry.get('coverage', {}).get('complete'):
                limitations.append('Admission comparison cites incomplete implementation reading')
        if review['route_action'] == 'fork' and comparison['relation'] != 'different':
            limitations.append('Existing admission has the same or unresolved mechanism: ' + admission_id)
    if review['route_action'] == 'fork' and compared != set(inventory['admissions']):
        limitations.append('Existing exploration admissions were not all compared')
    if not inventory['coverage']['index_complete']:
        limitations.append('Global method index is incomplete')
    if any(not inventory.get('method_sources', {}).get(method_id) or
           any(source.get('availability') != 'available' or source.get('coverage_complete') is False
               for source in inventory['method_sources'][method_id])
           for method_id in inventory['methods']):
        limitations.append('Indexed method source reading or revision is incomplete')
    if review['route_action'] == 'fork' and review['global_relation'] == 'unknown':
        limitations.append('Global mechanism equivalence is unresolved')
    if set(review['reviewed_strategy_ids']) != set(inventory['strategies']):
        limitations.append('Not all task strategies were reviewed')
    if set(review['reviewed_method_ids']) != set(inventory['methods']):
        limitations.append('Not all indexed methods were reviewed')
    if not review['code_refs']:
        limitations.append('Actual implementation source was not read')
    if any(r['kind'] == 'Source' and r['availability'] != 'available' for r in inventory['references'].values()):
        limitations.append('Historical or current implementation source is unavailable')
    for ref in review['code_refs']:
        if not inventory['references'][ref].get('coverage', {}).get('complete'):
            limitations.append('Cited implementation reading is incomplete')
    checked = json.loads(canonical_json(review))
    checked['admission_comparisons'] = comparisons
    checked['coverage_rationale'] = rationale
    if limitations:
        if checked['global_relation'] == 'new':
            checked['global_relation'] = 'unknown'
        if checked['task_relation'] == 'new_to_task':
            checked['task_relation'] = 'unknown'
        if checked['route_action'] == 'fork':
            checked['route_action'] = 'unknown'
    return {**checked, 'schema_version': 1, 'validation_status': 'validated_mechanism_review',
            'task_id': inventory['task_id'], 'snapshot_id': inventory['snapshot']['id'],
            'coverage': {**inventory['coverage'], 'complete': not limitations}, 'limitations': limitations,
            'correctness': 'unknown', 'performance': 'unknown'}


async def classify_method(knowledge, proposed, review, *, inventory):
    checked = validate_method_review(proposed, review, inventory)
    source_complete = True
    for source in proposed.source_refs:
        try:
            document = await knowledge.fetch_document(source.document_id, expected_revision=source.revision)
            _require(source.availability == 'available' and source.excerpt in document.content, 'Source excerpt is not available at its pinned revision')
            if document.id.startswith('RSI/Sources/'):
                from .profiler.inputs import _source_provenance
                source_complete &= _source_provenance({'id': document.id, 'content': document.content})['coverage_complete']
        except (KnowledgeError, ValueError):
            source_complete = False
    if not source_complete:
        checked['coverage']['complete'] = False
        checked['limitations'].append('Proposed source reading or revision is incomplete')
        if checked['global_relation'] == 'new':
            checked['global_relation'] = 'unknown'
        if checked['task_relation'] == 'new_to_task':
            checked['task_relation'] = 'unknown'
        if checked['route_action'] == 'fork':
            checked['route_action'] = 'unknown'
    target = checked['closest_method_id'] if checked['global_relation'] == 'same' else proposed.method_id
    existing = inventory['methods'].get(target)
    new_source = True
    if existing is not None:
        pin = inventory['method_pins'].get(target)
        _require(pin is not None, 'Existing method revision is not pinned')
        current = await knowledge.fetch_document(pin['note_id'], expected_revision=pin['revision'])
        old, _ = _parse_method(current.content)
        _require(method_to_dict(old) == existing, 'Method content differs from the review inventory')
        refs = list(old.source_refs)
        revisions = dict(old.source_revision)
        new_source = False
        for source in proposed.source_refs:
            if source.document_id in revisions and revisions[source.document_id] != source.revision:
                raise RevisionConflict('Archive distinct source revisions before merging them into one method')
            if source not in refs:
                refs.append(source)
                revisions[source.document_id] = source.revision
                new_source = True
        method = replace(old, source_refs=tuple(refs), source_revision=revisions)
        saved = await knowledge.save_method(method, expected_revision=current.revision)
    else:
        status = 'unverified' if checked['global_relation'] == 'unknown' or not source_complete else proposed.validation_status
        method = replace(proposed, validation_status=status)
        saved = await knowledge.save_method(method)
    relation, task = checked['global_relation'], checked['task_relation']
    classification = {'global_relation': relation, 'task_relation': task,
        'new_source': new_source, 'new_method': True if relation == 'new' else False if relation == 'same' else None,
        'new_task_application': True if task == 'new_to_task' else None if task == 'unknown' else False,
        'application_status': 'proposed', 'new_strategy': False, 'route_action': checked['route_action'],
        'strategy_id': checked['strategy_id'], 'method_id': method.method_id,
        'correctness': 'unknown', 'performance': 'unknown', 'limitations': checked['limitations']}
    return {'schema_version': 1, 'method': method_to_dict(method), 'method_ref': saved,
            'classification': classification, 'review': checked, 'inventory_digest': inventory['digest']}
