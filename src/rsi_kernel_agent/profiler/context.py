"""Lossless shared JSON nodes for model input; stored experiment records stay unchanged."""
from hashlib import sha256
import json
import re

from ..contracts import canonical_json


_CODEC = 'rsi-shared-json.v1'
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')


def _require(value, message):
    if not value:
        raise ValueError(message)


def analysis_context(manifest):
    request = manifest.get('request', {})
    environment = request.get('environment', {})
    if (manifest.get('schema_version') != 2 or request.get('tool_versions', {}).get('runtime_delta') != '1'
            or environment.get('offline_route_id') is None):
        if manifest.get('format_revision', 1) < 6 or request.get('tool_versions', {}).get('route_progress') != '1':
            return manifest
        view = {key: value for key, value in manifest.items() if key not in ('record_refs', 'verified_artifacts')}
        view['references'] = {key: value for key, value in manifest['references'].items() if not key.startswith('candidate:')}
        view['read_scope'] = 'shared_latest_progress_and_complete_own_metrics'
        view['manifest_digest'] = sha256(canonical_json(manifest).encode('utf-8')).hexdigest()
        return json.loads(canonical_json(view))
    _require(environment.get('analysis_phase') == 'execution' and environment.get('preparation_id'),
             'Runtime delta requires a formally bound preparation')
    _require(len(manifest['strategies']) == 1, 'Runtime delta must describe exactly one route')
    sid, current = next(iter(manifest['strategies'].items()))
    draft = manifest['references']['draft:' + sid]['value']
    _require(draft['preparation_id'] == environment['preparation_id']
             and draft['route_id'] == environment['offline_route_id'], 'Runtime delta preparation binding differs')
    binding = draft['delta_binding']
    history = current['history']
    _require(history.get('scope') == 'complete_metric_history' and history['complete'],
             'Runtime delta requires complete structured metric history')
    previous = {(row['candidate_id'], row['run_id']): row['digest'] for row in binding['history_rows']}
    rows = history['rows']
    changed = [row for row in rows if previous.get((row['candidate_id'], row['run_id']))
               != sha256(canonical_json(row).encode('utf-8')).hexdigest()]
    identities = {(row['candidate_id'], row['run_id']) for row in rows}
    checks = draft['checks']
    check_summary = None if checks is None else {
        'report_ref': checks['report_ref'], 'schema_version': checks['schema_version'],
        'ready': checks['ready'], 'candidate_correctness': checks['candidate_correctness'],
        'hardware_executed': checks['hardware_executed'],
        'checks': [{key: row[key] for key in ('kind', 'status', 'reason', 'log_ref')} for row in checks['checks']],
    }
    value = {
        'schema_version': 1, 'read_scope': 'runtime_binding_delta', 'strategy_id': sid,
        'manifest_digest': sha256(canonical_json(manifest).encode('utf-8')).hexdigest(),
        'task': manifest['task'], 'workload': manifest['workload'],
        'current': {'context_id': current['context']['id'], 'context': current['context'],
                    'parent': current['parent'], 'source_artifact': current['source_artifact'],
                    'config_artifact': current['config_artifact'], 'run': current['run'],
                    'source_ref': 'source:' + sid, 'config_ref': 'config:' + sid,
                    'profile_refs': ['profile:' + row['id'] for row in current['profiles']],
                    'benchmark_refs': ['benchmark:' + row['id'] for row in current['benchmarks']]},
        'progress': manifest['references']['progress:all']['value'],
        'history': {'ref': 'history:' + sid, 'scope': 'complete_metric_history_available_on_demand',
                    'snapshot_id': history['snapshot_id'], 'total_rows': history['total'],
                    'sha256': sha256(canonical_json(history).encode('utf-8')).hexdigest(),
                    'previous_sha256': binding['history_digest'], 'changed_rows': changed,
                    'replaced_rows': [{'candidate_id': candidate, 'run_id': run}
                                      for candidate, run in previous if (candidate, run) not in identities]},
        'preparation': {'id': draft['preparation_id'], 'route_id': draft['route_id'],
                        'manifest_ref': draft['manifest'], 'route_result_digest': draft['result_digest'],
                        'draft_ref': 'draft:' + sid, 'plan_set': draft['plan_set'], 'outcome': draft['outcome'],
                        'candidates': draft['drafts'], 'checks': check_summary},
        'knowledge': [{'method_id': key, 'note_id': row['note_id'], 'revision': row['revision'],
                       'availability': row['availability'], 'ref': 'knowledge:' + key}
                      for key, row in manifest['knowledge'].items()],
        'changes': draft['changes'], 'issues': manifest['issues'], 'consistency': manifest['consistency'],
        'reading_claims': {'coverage_scope': 'runtime_binding_delta',
                           'full_history_read_in_this_review': False, 'prior_analysis_inherited_as_reading': False},
    }
    return json.loads(canonical_json(value))


def encode_analysis_context(manifest):
    return encode_context(analysis_context(manifest))


def encode_context(value):
    original = canonical_json(value).encode('utf-8')
    candidates, counts, sizes = {}, {}, {}

    def encode(item):
        kind = type(item)
        if kind is dict:
            children = {key: encode(child) for key, child in sorted(item.items())}
            node = {'type': 'object', 'value': {key: child[0] for key, child in children.items()}}
            size = 2 + max(0, len(children) - 1) + sum(
                len(canonical_json(key).encode('utf-8')) + 1 + child[1] for key, child in children.items())
        elif kind is list:
            children = [encode(child) for child in item]
            node = {'type': 'array', 'value': [child[0] for child in children]}
            size = 2 + max(0, len(children) - 1) + sum(child[1] for child in children)
        elif kind is str:
            node = {'type': 'string', 'value': item}
            size = len(canonical_json(item).encode('utf-8'))
        else:
            return item, len(canonical_json(item).encode('utf-8'))
        key = sha256(canonical_json(node).encode('utf-8')).hexdigest()
        candidates.setdefault(key, node)
        counts[key] = counts.get(key, 0) + 1
        sizes[key] = size
        return {'type': 'ref', 'digest': key}, size

    root, _ = encode(value)
    selected = {key for key in candidates if counts[key] > 1 and sizes[key] >= 512}
    if type(root) is dict:
        selected.add(root['digest'])
    pool = {}

    def compact(item):
        if type(item) is not dict:
            return item
        key = item['digest']
        node = candidates[key]
        if key in selected and key in pool:
            return {'type': 'ref', 'digest': pool[key][0]}
        if node['type'] == 'object':
            result = {'type': 'object', 'value': {name: compact(child) for name, child in node['value'].items()}}
        elif node['type'] == 'array':
            result = {'type': 'array', 'value': [compact(child) for child in node['value']]}
        else:
            result = node
        if key not in selected:
            return result
        digest = sha256(canonical_json(result).encode('utf-8')).hexdigest()
        pool[key] = (digest, result)
        return {'type': 'ref', 'digest': digest}

    root = compact(root)
    return {'codec': _CODEC, 'lossless': True, 'root': root,
            'nodes': {digest: node for digest, node in sorted(pool.values())},
            'original_sha256': sha256(original).hexdigest(), 'original_bytes': len(original)}


def decode_context(encoded):
    _require(type(encoded) is dict and set(encoded) == {
        'codec', 'lossless', 'root', 'nodes', 'original_sha256', 'original_bytes'}, 'Invalid shared context fields')
    _require(encoded['codec'] == _CODEC and encoded['lossless'] is True, 'Unsupported or lossy context')
    _require(type(encoded['original_sha256']) is str and _DIGEST.fullmatch(encoded['original_sha256'])
             and type(encoded['original_bytes']) is int and encoded['original_bytes'] >= 0, 'Invalid original context identity')
    nodes = encoded['nodes']
    _require(type(nodes) is dict, 'Shared context nodes must be an object')
    for key, node in nodes.items():
        _require(type(key) is str and _DIGEST.fullmatch(key)
                 and sha256(canonical_json(node).encode('utf-8')).hexdigest() == key, 'Shared node SHA256 mismatch')
    cache, visiting, used = {}, set(), set()

    def decode(item):
        if type(item) in (int, float, bool) or item is None:
            canonical_json(item)
            return item
        _require(type(item) is dict and type(item.get('type')) is str, 'Shared value requires a type tag')
        kind = item['type']
        if kind == 'ref':
            _require(set(item) == {'type', 'digest'} and type(item['digest']) is str
                     and item['digest'] in nodes, 'Missing shared context node')
            key = item['digest']
            _require(key not in visiting, 'Cyclic shared context reference')
            used.add(key)
            if key not in cache:
                visiting.add(key)
                cache[key] = decode(nodes[key])
                visiting.remove(key)
            return cache[key]
        _require(set(item) == {'type', 'value'}, 'Invalid shared value fields')
        value = item['value']
        if kind == 'object':
            _require(type(value) is dict and all(type(key) is str for key in value), 'Invalid shared object')
            return {key: decode(child) for key, child in value.items()}
        if kind == 'array':
            _require(type(value) is list, 'Invalid shared array')
            return [decode(child) for child in value]
        _require(kind == 'string' and type(value) is str, 'Invalid shared scalar')
        return value

    result = decode(encoded['root'])
    _require(used == set(nodes), 'Unreachable shared nodes are not part of the context')
    raw = canonical_json(result).encode('utf-8')
    _require(len(raw) == encoded['original_bytes'] and sha256(raw).hexdigest() == encoded['original_sha256'],
             'Reconstructed context identity differs')
    return json.loads(raw)


class ContextReader:
    def __init__(self, encoded):
        decode_context(encoded)
        self.encoded = json.loads(canonical_json(encoded))
        self.nodes = {key: canonical_json(node) for key, node in sorted(self.encoded['nodes'].items())}
        self.ranges = {key: [] for key in self.nodes}
        self.read_log = []

    def index(self, offset=0, limit=50):
        keys = list(self.nodes)
        _require(type(offset) is int and 0 <= offset <= len(keys) and type(limit) is int and 1 <= limit <= 100,
                 'Invalid context index page')
        end = min(offset + limit, len(keys))
        rows = [{'digest': key, 'type': self.encoded['nodes'][key]['type'],
                 'total_chars': len(self.nodes[key]), 'bytes': len(self.nodes[key].encode('utf-8'))}
                for key in keys[offset:end]]
        return {'codec': _CODEC, 'root': self.encoded['root'], 'original_sha256': self.encoded['original_sha256'],
                'nodes': rows, 'offset': offset, 'total': len(keys), 'next_offset': end if end < len(keys) else None}

    def read(self, digest, offset=0, limit=24000):
        _require(type(digest) is str and digest in self.nodes, 'Unknown context node')
        content = self.nodes[digest]
        _require(type(offset) is int and 0 <= offset <= len(content) and type(limit) is int and 1 <= limit <= 24000,
                 'Invalid context node read range')
        end = min(offset + limit, len(content))
        part = content[offset:end]
        entry = {'node_digest': digest, 'offset': offset, 'count': len(part),
                 'page_sha256': sha256(part.encode('utf-8')).hexdigest()}
        self.read_log.append(entry)
        merged = []
        for start, stop in sorted([*self.ranges[digest], [offset, end]]):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], stop)
            else:
                merged.append([start, stop])
        self.ranges[digest] = merged
        return {**entry, 'content': part, 'total_chars': len(content),
                'next_offset': end if end < len(content) else None,
                'complete': offset == 0 and end == len(content)}

    def coverage(self):
        missing, read_chars = [], 0
        for key, content in self.nodes.items():
            count = sum(end - start for start, end in self.ranges[key])
            read_chars += count
            if count != len(content):
                missing.append(key)
        return {'codec': _CODEC, 'original_sha256': self.encoded['original_sha256'],
                'expected_nodes': len(self.nodes), 'read_nodes': len(self.nodes) - len(missing),
                'expected_chars': sum(len(content) for content in self.nodes.values()), 'read_chars': read_chars,
                'missing_nodes': missing, 'complete': not missing, 'read_log': list(self.read_log),
                'semantic_comprehension_verified': False}

    def page(self, offset=0, limit=24000):
        """Read a bounded character window across canonical nodes in digest order."""
        total = sum(len(content) for content in self.nodes.values())
        _require(type(offset) is int and 0 <= offset <= total and type(limit) is int and 1 <= limit <= 24000,
                 'Invalid context page range')
        end = min(offset + limit, total)
        position, segments = 0, []
        for digest, content in self.nodes.items():
            node_end = position + len(content)
            start, stop = max(offset, position), min(end, node_end)
            if start < stop:
                segments.append(self.read(digest, offset=start - position, limit=stop - start))
            position = node_end
            if position >= end:
                break
        return {'codec': _CODEC, 'root': self.encoded['root'],
                'original_sha256': self.encoded['original_sha256'], 'segments': segments,
                'offset': offset, 'count': end - offset, 'total_chars': total,
                'next_offset': end if end < total else None, 'complete': offset == 0 and end == total}
