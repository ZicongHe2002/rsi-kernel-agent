"""Knowledge through the existing MCP tools; source claims are never benchmarks.

Reads do not create records. Method notes carry captured quotes and revisions;
their current source availability is a separate read view. Experiment reference
strings are unverified until the experiment integration validates them.
"""
from dataclasses import asdict, dataclass, fields
from hashlib import sha256
import json
import re

from .contracts import ValidationError, canonical_json


class KnowledgeError(ValueError):
    """Knowledge transport, content, or coverage is unavailable or invalid."""


class RevisionConflict(KnowledgeError):
    """An observed document revision changed or an identity conflicts."""


@dataclass(frozen=True)
class SourceDocument:
    id: str
    title: str
    category: str
    revision: str
    content: str


@dataclass(frozen=True)
class SourceRef:
    document_id: str
    revision: str
    locator: str
    excerpt: str
    availability: str = 'available'
    reason: str | None = None


@dataclass(frozen=True)
class MethodRecord:
    method_id: str
    method_name: str
    description: str
    source_refs: tuple[SourceRef, ...]
    source_revision: dict
    optimization_category: str
    applicable_hardware: tuple[str, ...]
    applicable_workloads: tuple[str, ...]
    preconditions: tuple[str, ...]
    implementation_requirements: tuple[str, ...]
    expected_tradeoffs: tuple[str, ...]
    known_risks: tuple[str, ...]
    unknowns: tuple[str, ...]
    local_experiment_refs: tuple[dict, ...]
    validation_status: str = 'source_supported'
    mechanism: str = 'unknown'


_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z')
_REVISION = re.compile(r'[0-9a-f]{64}\Z')
_BLOCK = re.compile(r'^```rsi-method-v1[ \t]*\r?\n(.*?)^```[ \t]*\r?$', re.M | re.S)
_METHOD_CATEGORY = 'RSI/Methods'
_LIST_FIELDS = ('applicable_hardware', 'applicable_workloads', 'preconditions',
                'implementation_requirements', 'expected_tradeoffs', 'known_risks', 'unknowns')


def _require(condition, message):
    if not condition:
        raise KnowledgeError(message)


def _text(value):
    return type(value) is str and bool(value.strip())


def method_to_dict(method):
    """Validate and detach the portable Knowledge record (not a store entity)."""
    _require(type(method) is MethodRecord, 'Expected MethodRecord')
    _require(type(method.method_id) is str and bool(_ID.fullmatch(method.method_id)), 'Invalid method_id')
    for name in ('method_name', 'description', 'optimization_category', 'mechanism'):
        _require(_text(getattr(method, name)), f'{name} must be nonempty text')
    for name in _LIST_FIELDS:
        value = getattr(method, name)
        _require(type(value) is tuple and all(_text(x) for x in value), f'{name} must be a tuple of text')
    _require(method.validation_status in ('source_supported', 'unverified', 'synthetic_only'),
             'Knowledge alone cannot establish local performance validation')
    _require(type(method.source_refs) is tuple and bool(method.source_refs), 'Method needs captured source references')
    revisions = {}
    for source in method.source_refs:
        _require(type(source) is SourceRef, 'Invalid SourceRef')
        _require(_text(source.document_id) and _text(source.locator) and _text(source.excerpt), 'Source needs ID, locator and quote')
        _require(type(source.revision) is str and bool(_REVISION.fullmatch(source.revision)), 'Source revision must be captured SHA-256')
        _require(source.availability in ('available', 'unavailable', 'stale'), 'Invalid source availability')
        _require(source.reason is None or _text(source.reason), 'Source reason must be text')
        if source.availability != 'available':
            _require(_text(source.reason), 'Unavailable or stale source needs a reason')
        if source.document_id in revisions:
            _require(revisions[source.document_id] == source.revision, 'Conflicting source revisions')
        revisions[source.document_id] = source.revision
    _require(type(method.source_revision) is dict and method.source_revision == revisions, 'source_revision must match every captured reference')
    _require(type(method.local_experiment_refs) is tuple, 'local_experiment_refs must be a tuple')
    for ref in method.local_experiment_refs:
        _require(type(ref) is dict and bool(ref) and set(ref) <= {'plan_id', 'candidate_id', 'run_id', 'evidence_id'},
                 'Experiment references need explicit record ID fields')
        _require(all(type(value) is str and _ID.fullmatch(value) for value in ref.values()), 'Invalid experiment reference ID')
    return json.loads(canonical_json(asdict(method)))


def method_from_dict(payload):
    _require(type(payload) is dict and set(payload) == {item.name for item in fields(MethodRecord)}, 'Method schema fields must match version 1')
    try:
        values = json.loads(canonical_json(payload))
    except ValidationError as error:
        raise KnowledgeError('Method data must be finite JSON') from error
    try:
        for name in _LIST_FIELDS:
            _require(type(values[name]) is list, f'{name} must be an array')
            values[name] = tuple(values[name])
        _require(type(values['source_refs']) is list and type(values['local_experiment_refs']) is list, 'Reference fields must be arrays')
        values['source_refs'] = tuple(SourceRef(**ref) for ref in values['source_refs'])
        values['local_experiment_refs'] = tuple(values['local_experiment_refs'])
        method = MethodRecord(**values)
    except (TypeError, KeyError) as error:
        raise KnowledgeError('Invalid method source fields') from error
    method_to_dict(method)
    return method


def method_note_id(method_id):
    _require(type(method_id) is str and bool(_ID.fullmatch(method_id)), 'Invalid method ID')
    return f'{_METHOD_CATEGORY}/method-{sha256(method_id.encode("utf-8")).hexdigest()}.md'


def _headings(content):
    """ATX headings outside Markdown fenced code; byte-for-byte excerpts retained."""
    result, offset, fence = [], 0, None
    for line in content.splitlines(keepends=True):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        elif fence is None:
            match = re.match(r'^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*(?:\r?\n)?$', line)
            if match:
                result.append((len(match.group(1)), match.group(2).strip(), offset))
        offset += len(line)
    return result


def extract_method(document, heading, *, optimization_category='unclassified', hardware=(), workloads=(), mechanism='unknown'):
    """Capture a requested section, without inventing applicability or efficacy."""
    _require(type(document) is SourceDocument, 'Expected a completely fetched SourceDocument')
    _require(type(document.revision) is str and bool(_REVISION.fullmatch(document.revision)), 'Invalid source revision')
    headings = _headings(document.content)
    matching = [index for index, (_, title, _) in enumerate(headings) if title == heading]
    _require(len(matching) == 1, 'Requested heading must occur exactly once outside code fences')
    index = matching[0]
    level, _, start = headings[index]
    end = next((position for other_level, _, position in headings[index + 1:] if other_level <= level), len(document.content))
    excerpt = document.content[start:end].strip()
    identifier = sha256(canonical_json({'document_id': document.id, 'heading': heading}).encode()).hexdigest()
    source = SourceRef(document.id, document.revision, f'heading:{heading}', excerpt)
    method = MethodRecord('method-' + identifier, heading, excerpt, (source,), {document.id: document.revision},
                          optimization_category, tuple(hardware), tuple(workloads), (), (), (), (),
                          ('Applicability and local performance require experiment evidence.',), (), 'source_supported', mechanism)
    method_to_dict(method)
    return method


def _method_block(method):
    return '```rsi-method-v1\n' + canonical_json({'schema_version': 1, 'method': method_to_dict(method)}) + '\n```'


def _parse_method(content):
    matches = list(_BLOCK.finditer(content))
    _require(len(matches) == 1, 'Method note must contain exactly one rsi-method-v1 block; preserve human edits')
    try:
        payload = json.loads(matches[0].group(1))
    except (ValueError, TypeError) as error:
        raise KnowledgeError('Method JSON is invalid; preserve human edits') from error
    _require(type(payload) is dict and set(payload) == {'schema_version', 'method'}
             and type(payload['schema_version']) is int and payload['schema_version'] == 1,
             'Unknown method note schema')
    return method_from_dict(payload['method']), matches[0]


class KnowledgeClient:
    def __init__(self, call_tool):
        self.call_tool = call_tool

    async def _call(self, name, arguments):
        try:
            result = await self.call_tool(name, arguments)
        except KnowledgeError:
            raise
        except Exception as error:
            # Keep cancellation propagation and avoid exposing transport config
            # or credentials in error text. A timed-out write may have committed.
            raise KnowledgeError(f'MCP {name} unavailable ({type(error).__name__}); outcome may be unknown') from error
        _require(type(result) is dict, 'MCP tool did not return an object')
        return result

    async def search(self, query='', page_size=50):
        _require(type(query) is str and len(query) <= 1000, 'Invalid search query')
        _require(type(page_size) is int and 1 <= page_size <= 50, 'Search page size must be 1..50')
        results, seen, offset, total = [], set(), 0, None
        while True:
            page = await self._call('search', {'query': query, 'offset': offset, 'limit': page_size})
            count = page.get('total')
            _require(type(count) is int and count >= 0 and type(page.get('results')) is list, 'Invalid search page')
            if total is None:
                total = count
            _require(count == total, 'Search corpus changed during pagination; restart the search')
            items = page['results']
            _require(len(items) == min(page_size, max(total - offset, 0)), 'Search response has a coverage gap')
            for item in items:
                _require(type(item) is dict and _text(item.get('id')) and item['id'] not in seen, 'Duplicate or invalid search result; corpus may have changed')
                _require(type(item.get('revision')) is str and bool(_REVISION.fullmatch(item['revision'])), 'Search result needs revision')
                results.append(item)
                seen.add(item['id'])
            end = offset + len(items)
            expected = end if end < total else None
            _require(page.get('next_offset') == expected, 'Search next_offset does not cover the requested range')
            if expected is None:
                break
            offset = expected
        # The existing tool has no corpus snapshot token. Even unchanged counts
        # cannot prove one point-in-time view when documents are concurrently edited.
        return {'results': results, 'total': total, 'complete': True, 'snapshot_consistent': False}

    async def fetch_document(self, note_id, expected_revision=None, page_size=24000):
        _require(_text(note_id), 'Document ID required')
        _require(type(page_size) is int and 1 <= page_size <= 24000, 'Fetch page size must be 1..24000')
        if expected_revision is not None:
            _require(type(expected_revision) is str and bool(_REVISION.fullmatch(expected_revision)), 'Invalid expected revision')
        content, offset, first = [], 0, None
        while True:
            page = await self._call('fetch', {'id': note_id, 'offset': offset, 'limit': page_size})
            _require(page.get('id') == note_id and page.get('offset') == offset, 'Fetch response bound to wrong document or offset')
            _require(type(page.get('total_chars')) is int and page['total_chars'] >= 0 and type(page.get('content')) is str, 'Invalid fetch page')
            _require(type(page.get('revision')) is str and bool(_REVISION.fullmatch(page['revision'])), 'Fetch revision missing')
            if expected_revision is not None and page['revision'] != expected_revision:
                raise RevisionConflict('Document differs from the expected revision')
            if first is None:
                first = page
                _require(type(page.get('title')) is str and type(page.get('category')) is str, 'Fetch metadata missing')
            elif any(page.get(field) != first.get(field) for field in ('revision', 'total_chars', 'title', 'category')):
                raise RevisionConflict('Document changed while reading; restart complete fetch')
            _require(len(page['content']) == min(page_size, max(page['total_chars'] - offset, 0)), 'Fetch page has a coverage gap')
            content.append(page['content'])
            end = offset + len(page['content'])
            expected_next = end if end < page['total_chars'] else None
            _require(page.get('next_offset') == expected_next, 'Invalid fetch continuation')
            if expected_next is None:
                break
            offset = expected_next
        # Revisions hash original file bytes, which may include a UTF-8 BOM.
        # The decoded content hash is therefore not a substitute for revision.
        return SourceDocument(note_id, first['title'], first['category'], first['revision'], ''.join(content))

    async def save_method(self, method, expected_revision=None):
        method_to_dict(method)
        note_id = method_note_id(method.method_id)
        if expected_revision is not None:
            current = await self.fetch_document(note_id, expected_revision=expected_revision)
            old, block = _parse_method(current.content)
            _require(old.method_id == method.method_id, 'Stored method identity mismatch')
            content = current.content[:block.start()] + _method_block(method) + current.content[block.end():]
            if content == current.content:
                return {'id': current.id, 'revision': current.revision}
            return await self._call('update_note', {'id': note_id, 'revision': current.revision, 'content': content})
        try:
            current = await self.fetch_document(note_id)
        except KnowledgeError:
            current = None
        if current is not None:
            old, _ = _parse_method(current.content)
            if old != method:
                raise RevisionConflict('Method already exists; fetch its revision before updating')
            return {'id': current.id, 'revision': current.revision}
        content = '# ' + method.method_name + '\n\n' + _method_block(method) + '\n'
        try:
            return await self._call('create_note', {'title': note_id.rsplit('/', 1)[1][:-3], 'category': _METHOD_CATEGORY, 'content': content})
        except KnowledgeError as error:
            # create is not transport-idempotent: a timeout may follow a commit.
            # Reconcile only an exact existing payload, never blind overwrite.
            try:
                current = await self.fetch_document(note_id)
                old, _ = _parse_method(current.content)
            except KnowledgeError:
                raise error
            if old == method:
                return {'id': current.id, 'revision': current.revision}
            raise RevisionConflict('Conflicting method already exists') from error

    async def get_method(self, method_id):
        current = await self.fetch_document(method_note_id(method_id))
        method, _ = _parse_method(current.content)
        _require(method.method_id == method_id, 'Stored method ID does not match requested ID')
        return method

    async def source_status(self, method):
        method_to_dict(method)
        result = []
        for source in method.source_refs:
            status = {'document_id': source.document_id, 'captured_revision': source.revision,
                      'current_revision': None, 'availability': 'unavailable', 'reason': None}
            try:
                current = await self.fetch_document(source.document_id)
                status['current_revision'] = current.revision
                status['availability'] = 'available' if current.revision == source.revision else 'stale'
                if status['availability'] == 'stale':
                    status['reason'] = 'Current source differs; captured excerpt remains available in MethodRecord'
            except KnowledgeError as error:
                status['reason'] = str(error)
            result.append(status)
        return result

    async def find_methods(self, keyword='', category=None, hardware=None, mechanism=None, applicability=None):
        for value in (keyword, category, hardware, mechanism, applicability):
            _require(value is None or type(value) is str, 'Method filters must be text')
        listing = await self.search(_METHOD_CATEGORY)
        methods, unavailable, sources = [], [], {}
        for item in listing['results']:
            if item.get('category') != _METHOD_CATEGORY:
                continue
            try:
                document = await self.fetch_document(item['id'], expected_revision=item['revision'])
                method, _ = _parse_method(document.content)
                _require(method_note_id(method.method_id) == item['id'], 'Method note path/identity mismatch')
            except KnowledgeError as error:
                unavailable.append({'id': item['id'], 'reason': str(error)})
                continue
            tests = ((keyword, canonical_json(method_to_dict(method))),
                     (category, method.optimization_category), (hardware, ' '.join(method.applicable_hardware)),
                     (mechanism, method.mechanism), (applicability, ' '.join((*method.applicable_workloads, *method.preconditions))))
            if all(not needle or needle.casefold() in haystack.casefold() for needle, haystack in tests):
                methods.append(method)
                sources[method.method_id] = await self.source_status(method)
        return {'methods': methods, 'sources': sources, 'unavailable': unavailable,
                'complete': not unavailable, 'snapshot_consistent': False}

    async def discover(self, queries=('',), page_size=50):
        _require(type(queries) in (tuple, list) and bool(queries) and all(type(q) is str for q in queries), 'Discovery requires search queries')
        found = {}
        for query in queries:
            for item in (await self.search(query, page_size))['results']:
                found[item['id']] = item
        documents, sections, unavailable = [], {}, []
        for item in found.values():
            try:
                document = await self.fetch_document(item['id'], expected_revision=item['revision'])
                documents.append(document)
                sections[document.id] = [title for _, title, _ in _headings(document.content)]
            except KnowledgeError as error:
                unavailable.append({'id': item['id'], 'reason': str(error)})
        return {'documents': documents, 'sections': sections, 'unavailable': unavailable,
                'complete': not unavailable, 'snapshot_consistent': False}
