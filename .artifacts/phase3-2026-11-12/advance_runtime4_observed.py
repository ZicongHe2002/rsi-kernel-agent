"""Same original driver with sanitized per-call transport diagnostics only."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
from advance_runtime4 import advance, runtime4_guard

directory = Path(__file__).resolve().parent
original_run = subprocess.run


def observed_run(*args, **kwargs):
    command = args[0] if args else kwargs.get('args')
    is_bridge = isinstance(command, (list, tuple)) and any(
        str(value).endswith('colab_bridge_call.py') for value in command)
    if not is_bridge:
        return original_run(*args, **kwargs)
    request = json.loads(kwargs.get('input', '{}'))
    event = {'action': request.get('action'), 'started_at_utc': datetime.now(timezone.utc).isoformat()}
    started = time.monotonic()
    try:
        result = original_run(*args, **kwargs)
        event['returncode'] = result.returncode
        try:
            reply = json.loads(result.stdout)
            event.update(status=reply.get('status'), error_type=reply.get('error_type'))
        except (ValueError, TypeError):
            event['parse_error'] = True
        return result
    except BaseException as error:
        event['error_type'] = type(error).__name__
        raise
    finally:
        event.update(elapsed_s=time.monotonic() - started, finished_at_utc=datetime.now(timezone.utc).isoformat())
        with (directory / 'runtime4-transport-diagnostics.jsonl').open('a', encoding='utf-8') as out:
            out.write(json.dumps(event) + '\n')
        print(json.dumps({'transport_diagnostic': event}), flush=True)


if __name__ == '__main__':
    subprocess.run = observed_run
    try:
        advance(directory, 'runtime4', guard=runtime4_guard)
    finally:
        subprocess.run = original_run
