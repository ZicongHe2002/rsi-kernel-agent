"""Write an explicitly in-progress SHA inventory; no historical bytes invented.

This is a manifest helper, not a verifier of Phase 3 completion. It never opens
authentication files, Git internals, experiment databases, blobs or caches.
"""
from collections import Counter
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / '.artifacts/phase3-2026-11-12'
MANIFEST = ROOT / 'knowledge-site/manifests/phase3-2026-11-12'
OUTPUT = MANIFEST / 'changes.json'
BLOCKED_DIRS = {'.git', '.auth', '.aws', '__pycache__', 'node_modules', '.venv', '.venv-mcp',
                'artifacts', 'downloads', 'transfer-cache', 'transfer_cache', 'transfercache', 'parts'}
BLOCKED_NAMES = {'.env', 'credentials', 'credentials.json', 'token.json', 'cookies', 'cookies.json',
                 'id_rsa', 'id_ed25519'}
ALLOWED_AUX = {'.py', '.md', '.json', '.jsonl', '.txt', '.diff', '.patch', '.ps1', '.cmd', '.sh'}


def relative(path):
    return path.relative_to(ROOT).as_posix()


def safe_file(path):
    path = Path(path).absolute()
    if not path.is_relative_to(ROOT):
        raise ValueError('Inventory input escapes project root')
    for item in (path, *path.parents):
        if item == ROOT.parent:
            break
        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
            raise ValueError('Inventory refuses links/junctions: ' + relative(path))
    if path.name.lower() in BLOCKED_NAMES or any(part.lower() in {'.git', '.auth', '.aws'} for part in path.relative_to(ROOT).parts):
        raise ValueError('Excluded sensitive path')
    return path


def fingerprint(path):
    path = safe_file(path)
    for _ in range(3):
        before = path.stat()
        digest = sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns):
            return {'sha256': digest.hexdigest(), 'size': after.st_size,
                    'observed_at_utc': datetime.now(timezone.utc).isoformat(),
                    'stable_during_hash': True}
    return {'sha256': None, 'size': after.st_size, 'observed_at_utc': datetime.now(timezone.utc).isoformat(),
            'stable_during_hash': False, 'reason': 'File changed during all three bounded hash attempts'}


def walk(base):
    if not base.exists():
        return
    for directory, names, files in os.walk(base, followlinks=False):
        names[:] = sorted(name for name in names if name.lower() not in BLOCKED_DIRS
                          and not (Path(directory) / name).is_symlink()
                          and not (hasattr(Path(directory) / name, 'is_junction') and (Path(directory) / name).is_junction()))
        for name in sorted(files):
            path = Path(directory) / name
            if name.lower() in BLOCKED_NAMES or path.is_symlink():
                continue
            yield path


def read_json(path):
    return json.loads(safe_file(path).read_text('utf-8-sig'))


def atomic_write(value):
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    fd, name = tempfile.mkstemp(prefix='.phase3-changes-', dir=MANIFEST)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, OUTPUT)
    finally:
        Path(name).unlink(missing_ok=True)


def main():
    started = datetime.now(timezone.utc)
    runtime_manifest = MANIFEST / 'runtime-baseline.json'
    runtime_before = {row['path']: row['sha256'] for row in read_json(runtime_manifest)}
    docs_progress = MANIFEST / 'current-docs-progress.json'
    docs_before = {row['path']: row['before_sha256'] for row in read_json(docs_progress)['changes']}
    snapshots = {}
    for path in walk(MANIFEST / 'before'):
        identity = fingerprint(path)
        if identity['sha256'] is not None:
            snapshots.setdefault(identity['sha256'], []).append(relative(path))

    selected = {}
    def add(path, category, selection_source):
        path = safe_file(path)
        rel = relative(path)
        if path == OUTPUT:
            return
        selected.setdefault(rel, {'path': path, 'category': category, 'selection_source': selection_source})

    for path in walk(ROOT / 'src/rsi_kernel_agent'):
        if path.suffix == '.py':
            add(path, 'runtime_source', 'current src/rsi_kernel_agent Python inventory')
    for path in walk(ROOT / 'tests'):
        if path.suffix == '.py':
            add(path, 'test_source', 'current tests Python inventory; absence of before hash is not proof of creation')
    for path in walk(ROOT / 'docs/phase3'):
        add(path, 'phase3_documentation', 'explicit Phase 3 documentation directory')
    for path in (ROOT / 'docs/superpowers/plans').glob('*phase3*'):
        if path.is_file():
            add(path, 'phase3_plan', 'explicit phase3 plan filename')
    for path in walk(MANIFEST):
        category = ('before_snapshot' if 'before' in path.relative_to(MANIFEST).parts else
                    'auxiliary_script' if path.suffix == '.py' else 'phase3_manifest_or_evidence')
        add(path, category, 'explicit current Phase 3 manifest directory')
    for path in walk(WORK):
        if path.suffix.lower() in ALLOWED_AUX:
            parts = path.relative_to(WORK).parts
            category = ('auxiliary_script' if path.suffix == '.py' and ('candidates' not in parts or path.name.startswith('prepare'))
                        else 'candidate_source_or_evidence' if 'candidates' in parts
                        else 'auxiliary_note' if path.suffix == '.md' else 'auxiliary_execution_evidence')
            add(path, category, 'task-owned auxiliary files; blobs/downloads/databases excluded')
    for rel in docs_before:
        add(ROOT / rel, 'current_index_roadmap_or_weekly_record', relative(docs_progress))

    # Add only knowledge files explicitly returned by this phase's original MCP
    # import receipts, not the entire user knowledge corpus.
    for name in ('history-knowledge-import.json', 'official-knowledge-import.json'):
        source = MANIFEST / name
        def visit(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in ('id', 'document_id') and isinstance(child, str) and child.startswith('RSI/') and child.endswith('.md'):
                        path = ROOT / 'knowledge-site/content' / child
                        if path.is_file():
                            add(path, 'phase3_knowledge_note', relative(source))
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(read_json(source))

    historical = []
    prior_hashes = {}
    for phase in ('phase2a-2026-11-11', 'phase2b-2026-11-11', 'phase2c-2026-11-11', 'phase2d-2026-11-11'):
        old_manifest = ROOT / 'knowledge-site/manifests' / phase / 'changes.json'
        if old_manifest.exists():
            for row in read_json(old_manifest).get('files', []):
                if row.get('after_sha256'):
                    prior_hashes[row['path']] = {'sha256': row['after_sha256'], 'source': relative(old_manifest)}
        old_report = ROOT / 'knowledge-site/manifests' / phase / 'implementation-report.md'
        if old_report.exists():
            historical.append(old_report)

    rows = []
    for rel, item in sorted(selected.items()):
        path = item['path']
        if not path.is_file():
            rows.append({'path': rel, 'category': item['category'], 'present': False,
                         'before_sha256': runtime_before.get(rel, docs_before.get(rel)), 'after_sha256': None})
            continue
        current = fingerprint(path)
        before = runtime_before.get(rel, docs_before.get(rel))
        snapshot = snapshots.get(before, []) if before else []
        before_source = relative(runtime_manifest) if rel in runtime_before else relative(docs_progress) if rel in docs_before else None
        row = {'path': rel, 'category': item['category'], 'selection_source': item['selection_source'],
               'present': True, 'before_sha256': before, 'before_hash_source': before_source,
               'before_snapshot': snapshot[0] if len(snapshot) == 1 else None,
               'before_snapshot_candidates': snapshot if len(snapshot) > 1 else [],
               'before_bytes_available': bool(snapshot), 'after_sha256': current['sha256'],
               'size': current['size'], 'observed_at_utc': current['observed_at_utc'],
               'stable_during_hash': current['stable_during_hash'],
               'comparison': ('before_not_captured' if before is None else
                              'unavailable_during_concurrent_write' if current['sha256'] is None else
                              'unchanged_from_recorded_before_hash' if current['sha256'] == before else
                              'changed_from_recorded_before_hash')}
        if before is None and rel in prior_hashes:
            row['historical_manifest_reference'] = {**prior_hashes[rel],
                'matches_current': prior_hashes[rel]['sha256'] == current['sha256'],
                'is_phase3_start_baseline': False}
        rows.append(row)

    checks = []
    for path in historical:
        rel = relative(path)
        current = fingerprint(path)
        reference = prior_hashes.get(rel)
        checks.append({'path': rel, 'current_sha256': current['sha256'], 'size': current['size'],
                       'observed_at_utc': current['observed_at_utc'], 'written_by_this_tool': False,
                       'historical_reference': reference,
                       'matches_historical_reference': None if reference is None else reference['sha256'] == current['sha256'],
                       'scope': 'read-only current fingerprint; no unchanged claim without historical hash'})

    result = {'schema_version': 1, 'phase': 'Phase 3', 'status': 'in_progress', 'phase_complete': False,
              'generated_at_utc': datetime.now(timezone.utc).isoformat(),
              'generated_at_asia_shanghai': datetime.now(timezone(timedelta(hours=8))).isoformat(),
              'started_at_utc': started.isoformat(), 'generator': relative(Path(__file__).resolve()),
              'self_excluded': True, 'source_of_truth': 'Current byte fingerprints plus explicitly captured historical hashes/snapshots',
              'completeness': {'whole_repository_diff': False, 'atomic_multi_file_snapshot': False,
                 'historical_before_bytes_complete': False, 'tests_finished': 'not inferred',
                 'git_clean_or_committed': 'not checked or claimed'},
              'scope_notes': ['This is a scoped live inventory, not proof of every file changed in Phase 3.',
                 'before_sha256=null means no Phase 3 before hash was captured; it does not prove a new file.',
                 'runtime-baseline.json contains hashes only; before_bytes_available requires an existing matching snapshot.',
                 'Archived previous-phase hashes are separately labeled and never substituted for a Phase 3 start snapshot.',
                 'Each file is checked for stability while hashing; other files may change before or after it.',
                 'Auxiliary scripts and validation helpers are listed as auxiliary, not runtime modules.',
                 'Active test logs and reports can change; regenerate after all work settles.'],
              'exclusions': ['Authentication/credential files and directories', '.git internals',
                 '.artifacts/phase3-2026-11-12/artifacts (content-addressed blobs)',
                 '.artifacts/phase3-2026-11-12/downloads and transfer caches',
                 'SQLite database/WAL/SHM, lock files, binary transfers and partial downloads',
                 'External Kernel/history/bridge repositories (their source audits have separate inventories)',
                 'Unrelated user knowledge notes, dependencies and environments', relative(OUTPUT)],
              'counts': {'files': len(rows), 'by_category': dict(sorted(Counter(row['category'] for row in rows).items())),
                 'before_hashes': sum(row.get('before_sha256') is not None for row in rows),
                 'before_byte_snapshots': sum(row.get('before_bytes_available', False) for row in rows),
                 'unstable_during_hash': sum(row.get('stable_during_hash') is False for row in rows)},
              'files': rows, 'historical_reports_read_only_checks': checks}
    atomic_write(result)
    print(json.dumps({'status': result['status'], 'output': relative(OUTPUT), 'counts': result['counts'],
                      'historical_reports_checked': len(checks)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
