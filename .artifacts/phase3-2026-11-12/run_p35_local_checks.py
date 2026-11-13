"""Archive scoped local regression output and exit codes; never contacts TPU."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

directory = Path(__file__).resolve().parent
names = [
    'test_p35_confirmation_statistics', 'test_p35_publish_confirmation',
    'test_p35_round2_gate', 'test_p35_control_admission',
    'test_p35_single_tile_diagnostic', 'test_runtime_readmission',
]
started = datetime.now(timezone.utc).isoformat()
command = [sys.executable, '-B', '-X', 'utf8', '-m', 'unittest', '-v', *names]
log = directory / 'p35-local-checks.txt'
with log.open('wb') as stream:
    result = subprocess.run(command, cwd=directory, stdout=stream, stderr=subprocess.STDOUT)
summary = {'started_utc': started, 'finished_utc': datetime.now(timezone.utc).isoformat(),
           'command': command, 'exit_code': result.returncode, 'log': str(log),
           'classification': 'Local CPU protocol fixtures; no TPU execution'}
(directory / 'p35-local-checks-result.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary))
sys.exit(result.returncode)
