"""Verify archived public bytes and report links without network or runtime."""
from pathlib import Path
from hashlib import sha256
import json
import re

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / 'knowledge-site/manifests/phase3-2026-11-12/sources/official'
manifest = json.loads((DEST / 'manifest.json').read_bytes())
files = []
for page in manifest['pages']:
    assert page['status'] == 200
    for prefix in ('raw', 'text'):
        path = ROOT / page[prefix + '_path']
        raw = path.read_bytes()
        assert len(raw) == page[prefix + '_size']
        assert sha256(raw).hexdigest() == page[prefix + '_sha256']
        files.append(str(path.relative_to(ROOT)))
for row in json.loads((DEST / 'jax-0.8.1-source-manifest.json').read_bytes())['files']:
    assert row['status'] == 200
    path = ROOT / row['path']
    raw = path.read_bytes()
    assert len(raw) == row['size'] and sha256(raw).hexdigest() == row['sha256']
    files.append(str(path.relative_to(ROOT)))
report = ROOT / 'docs/phase3/phase3-official-reference-audit.md'
text = report.read_text('utf-8')
links = []
for target in re.findall(r'\]\(([^)]+)\)', text):
    if target.startswith(('https://', 'http://')):
        continue
    path = (report.parent / target).resolve()
    assert path.is_file(), target
    links.append(target)
imports = json.loads((ROOT / 'knowledge-site/manifests/phase3-2026-11-12/official-knowledge-import.json').read_bytes())
assert imports['status'] == 'saved_and_refetched' and len(imports['methods']) == 2
for method in imports['methods']:
    assert method['validation_status'] == 'source_supported' and method['local_experiment_refs'] == []
    assert all(source['availability'] == 'available' for source in method['source_status'])
result = {'classification': 'Source audit / local verification only', 'archived_files_verified': len(files),
          'local_report_links_verified': len(links), 'broken_links': [], 'official_methods_saved_and_refetched': 2,
          'report_sha256': sha256(report.read_bytes()).hexdigest(), 'runtime_execution': False}
(ROOT / 'knowledge-site/manifests/phase3-2026-11-12/official-reference-check.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
print(json.dumps(result, indent=2))
