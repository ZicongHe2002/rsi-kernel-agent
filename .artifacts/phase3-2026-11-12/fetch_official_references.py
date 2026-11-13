"""Read public documentation and archive exact responses; no credentials."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import json
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / 'knowledge-site/manifests/phase3-2026-11-12/sources/official'
DEST.mkdir(parents=True, exist_ok=True)
URLS = {
    'jax-tpu-pipelining': 'https://docs.jax.dev/en/latest/pallas/tpu/pipelining.html',
    'jax-async-dispatch': 'https://docs.jax.dev/en/latest/async_dispatch.html',
    'jax-benchmarking': 'https://docs.jax.dev/en/latest/benchmarking.html',
    'jax-profiling': 'https://docs.jax.dev/en/latest/profiling.html',
    'xprof-kernel-profiling': 'https://openxla.org/xprof/kernel-profiling',
    'xprof-custom-call-profiling': 'https://openxla.org/xprof/custom_call_profiling',
}


class Readable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.skip += 1
        if tag in ('p', 'div', 'br', 'li', 'pre', 'tr', 'h1', 'h2', 'h3', 'h4') and not self.skip:
            self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self.skip = max(0, self.skip - 1)
        if tag in ('p', 'div', 'li', 'pre', 'tr', 'h1', 'h2', 'h3', 'h4') and not self.skip:
            self.parts.append('\n')
    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)
    def text(self):
        lines = [' '.join(line.split()) for line in ''.join(self.parts).splitlines()]
        return '\n'.join(line for line in lines if line) + '\n'


def fetch(item):
    name, url = item
    row = {'name': name, 'requested_url': url, 'retrieved_utc': datetime.now(timezone.utc).isoformat(),
           'classification': 'Source audit of official documentation, not runtime capability validation'}
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'RSI-Kernel-Agent/Phase3-source-audit'})
        with urllib.request.urlopen(request, timeout=35) as response:
            raw = response.read(5 * 1024 * 1024 + 1)
            if len(raw) > 5 * 1024 * 1024:
                raise ValueError('Documentation response exceeds 5 MiB audit limit')
            row.update(status=response.status, final_url=response.url, content_type=response.headers.get('Content-Type'),
                       last_modified=response.headers.get('Last-Modified'), etag=response.headers.get('ETag'))
            charset = response.headers.get_content_charset() or 'utf-8'
        parser = Readable()
        parser.feed(raw.decode(charset, errors='strict'))
        text_bytes = parser.text().encode('utf-8')
        html_path, text_path = DEST / (name + '.html'), DEST / (name + '.txt')
        html_path.write_bytes(raw)
        text_path.write_bytes(text_bytes)
        row.update(raw_path=html_path.relative_to(ROOT).as_posix(), raw_sha256=sha256(raw).hexdigest(), raw_size=len(raw),
                   text_path=text_path.relative_to(ROOT).as_posix(), text_sha256=sha256(text_bytes).hexdigest(),
                   text_size=len(text_bytes), text_derivation='stdlib HTMLParser, script/style/noscript omitted, whitespace normalized')
    except Exception as error:
        row.update(status='unavailable', error_type=type(error).__name__, error=str(error))
    return row


with ThreadPoolExecutor(max_workers=3) as pool:
    rows = list(pool.map(fetch, URLS.items()))
result = {'schema_version': 1, 'retrieval': 'urllib HTTPS GET; no authentication; no execution of page code', 'pages': rows}
(DEST / 'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps([{'name': row['name'], 'status': row['status'], 'bytes': row.get('raw_size'),
                   'error': row.get('error')} for row in rows], indent=2))
