"""Archive matching public JAX tag sources for version compatibility audit."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / 'knowledge-site/manifests/phase3-2026-11-12/sources/official'
FILES = {'jax-0.8.1-profiler.py': 'jax/_src/profiler.py',
         'jax-0.8.1-profiling.md': 'docs/profiling.md',
         'jax-0.8.1-pipeline.py': 'jax/_src/pallas/mosaic/pipeline.py',
         'jax-0.8.1-tpu.py': 'jax/experimental/pallas/tpu.py'}

def fetch(item):
    name, relative = item
    url = 'https://raw.githubusercontent.com/jax-ml/jax/jax-v0.8.1/' + relative
    row = {'name': name, 'url': url, 'tag': 'jax-v0.8.1', 'retrieved_utc': datetime.now(timezone.utc).isoformat(),
           'classification': 'Official source audit; not execution or binary capability proof'}
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'RSI-Kernel-Agent/Phase3-source-audit'})
        with urllib.request.urlopen(request, timeout=35) as response:
            data = response.read(5 * 1024 * 1024 + 1)
            assert len(data) <= 5 * 1024 * 1024
            row.update(status=response.status, final_url=response.url, content_type=response.headers.get('Content-Type'))
        path = DEST / name
        path.write_bytes(data)
        row.update(path=path.relative_to(ROOT).as_posix(), sha256=sha256(data).hexdigest(), size=len(data))
    except Exception as error:
        row.update(status='unavailable', error_type=type(error).__name__, error=str(error))
    return row

with ThreadPoolExecutor(max_workers=3) as pool:
    rows = list(pool.map(fetch, FILES.items()))
(DEST / 'jax-0.8.1-source-manifest.json').write_text(json.dumps({'files': rows}, indent=2) + '\n', encoding='utf-8')
print(json.dumps(rows, indent=2))
