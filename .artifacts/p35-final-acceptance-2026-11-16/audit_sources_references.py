"""Read-only source and archived official-reference integrity check."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
MANIFEST = ROOT / 'knowledge-site/manifests/phase3-2026-11-12'

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

inventory = read(MANIFEST / 'pallaskernels-source-inventory.json')
kernel = Path(inventory['root'])
checks = []
for entry in inventory['files']:
    path = kernel / entry['path']
    actual = sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    checks.append({'path': entry['path'], 'expected': entry['sha256'], 'actual': actual,
                   'matches': actual == entry['sha256']})

references = []
for page in read(MANIFEST / 'sources/official/manifest.json')['pages']:
    for kind in ('raw', 'text'):
        path = ROOT / page[kind + '_path']
        digest = sha256(path.read_bytes()).hexdigest()
        references.append({'path': page[kind + '_path'], 'url': page['requested_url'],
                           'retrieved_utc': page['retrieved_utc'], 'actual': digest,
                           'expected': page[kind + '_sha256'],
                           'matches': digest == page[kind + '_sha256']})
for entry in read(MANIFEST / 'sources/official/jax-0.8.1-source-manifest.json')['files']:
    digest = sha256((ROOT / entry['path']).read_bytes()).hexdigest()
    references.append({'path': entry['path'], 'url': entry['url'], 'tag': entry['tag'],
                       'actual': digest, 'expected': entry['sha256'],
                       'matches': digest == entry['sha256']})

result = {'checked_utc': datetime.now(timezone.utc).isoformat(),
          'network_calls': 0, 'runtime_calls': 0, 'protected_writes': 0,
          'kernel_inventory_files': len(checks), 'kernel_checks': checks,
          'kernel_mismatches': [x for x in checks if not x['matches']],
          'official_archive_files': len(references), 'official_checks': references,
          'official_mismatches': [x for x in references if not x['matches']],
          'scope': 'Historical manifest entries only; no assertion about unlisted files or Git cleanliness.'}
(OUT / 'source-reference-audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in result.items() if k not in ('kernel_checks', 'official_checks')}, ensure_ascii=False))
