"""Observe one pinned original worker operation during local CPU analysis only.

Stop this foreground observer before any formal hardware driver. It never calls
Controller, Backend, submit, bootstrap, allocation, or credential cleanup. The
original bridge still owns authentication and its normal session handling.
One poll is the default; --max-polls 0 explicitly requests continuous observation.
An observation is not a keepalive guarantee or proof of future TPU availability.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from rsi_kernel_agent.workflow.__main__ import colab_remote

STATUSES = ('completed', 'running', 'unknown', 'definitely_not_started')


def monitor(generation, operation_id, *, interval=60, max_polls=1, directory=None):
    if (type(generation) is not str or not re.fullmatch(r'runtime[0-9]+', generation)
            or int(generation[7:]) < 2 or type(operation_id) is not str
            or not re.fullmatch(r'operation:[a-f0-9]{64}', operation_id)
            or type(interval) is not int or interval != 60
            or type(max_polls) is not int or max_polls < 0):
        raise ValueError('Require runtime2+, original operation ID, interval 60, and nonnegative max-polls')
    directory = Path(directory) if directory is not None else Path(__file__).resolve().parent
    config_path = directory / ('colab-config-' + generation + '.json')
    frozen_config = config_path.read_bytes()
    config = json.loads(frozen_config)
    runtime_id = config.get('runtime_id') if type(config) is dict else None
    if type(runtime_id) is not str or not runtime_id.strip():
        raise ValueError('A pinned existing Runtime configuration is required')
    remote = colab_remote(config_path)
    if remote.runtime_id != runtime_id or config_path.read_bytes() != frozen_config:
        raise ValueError('Runtime configuration changed while opening the original bridge')
    count, consecutive_unknown = 0, 0
    stop_reason = 'max_polls'
    while max_polls == 0 or count < max_polls:
        started = datetime.now(timezone.utc).isoformat()
        status, error_type, config_changed = 'unknown', None, False
        try:
            if config_path.read_bytes() != frozen_config:
                error_type, config_changed = 'ConfigChanged', True
            else:
                reply = remote.lookup(operation_id)
                if (type(reply) is not dict or reply.get('status') not in STATUSES
                        or (reply['status'] == 'completed' and
                            (type(reply.get('receipt')) is not dict
                             or reply['receipt'].get('operation_id') != operation_id))):
                    error_type = 'InvalidLookupReply'
                else:
                    status = reply['status']
        except Exception as exc:
            # Never emit exception text, raw reply, paths, URLs, cookies, or tokens.
            error_type = type(exc).__name__
        count += 1
        consecutive_unknown = consecutive_unknown + 1 if status == 'unknown' else 0
        print(json.dumps({'generation': generation, 'runtime_id': runtime_id,
            'operation_id': operation_id, 'poll': count, 'started_at_utc': started,
            'finished_at_utc': datetime.now(timezone.utc).isoformat(), 'status': status,
            'error_type': error_type, 'consecutive_unknown': consecutive_unknown}), flush=True)
        if config_changed:
            stop_reason = 'config_changed'
            break
        if max_polls == 0 or count < max_polls:
            time.sleep(interval)
    return {'polls': count, 'stop_reason': stop_reason}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generation', required=True)
    parser.add_argument('--operation-id', required=True)
    parser.add_argument('--interval', type=int, default=60, choices=(60,))
    parser.add_argument('--max-polls', type=int, default=1,
                        help='Default 1; explicit 0 observes until interrupted')
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    try:
        monitor(args.generation, args.operation_id, interval=args.interval,
                max_polls=args.max_polls, directory=args.directory)
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        print(json.dumps({'status': 'observer_failed', 'error_type': type(exc).__name__,
                          'at_utc': datetime.now(timezone.utc).isoformat()}), flush=True)
        raise SystemExit(1) from None
