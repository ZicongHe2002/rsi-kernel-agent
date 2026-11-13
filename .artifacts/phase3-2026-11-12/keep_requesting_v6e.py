"""User-authorized continued v6e allocation; reconcile the same ID each time."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

root = Path(__file__).resolve().parent
while True:
    # This child already suppresses all bridge/auth logging. Preserve sanitized JSON only.
    process = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(root / 'reconcile_allocation.py'),
                              '--retry-authorized'], text=True, encoding='utf-8', capture_output=True)
    try:
        reply = json.loads(process.stdout)
    except ValueError:
        reply = {'status': 'allocation_not_confirmed', 'error_type': 'MalformedSanitizedReply'}
    event = {'utc': datetime.now(timezone.utc).isoformat(), **reply}
    with (root / 'allocation-attempts.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(event) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps(event), flush=True)
    if reply.get('status') in ('allocated', 'reconciliation_required'):
        break
    time.sleep(30)
