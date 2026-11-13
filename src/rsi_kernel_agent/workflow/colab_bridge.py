"""Bounded calls to the user's installed Colab bridge, with no model API.

The bridge owns authentication. This module never opens credential files and
does not allocate, restart, upgrade, or terminate a runtime. A local timeout
means the remote outcome is unknown, not that remote execution was cancelled.
"""
import json
import os
from pathlib import Path
import subprocess


class BridgeUnavailable(RuntimeError):
    """Transport failure; remote execution may still be active."""


def extract_json_result(outputs):
    if not isinstance(outputs, list) or any(
            not isinstance(item, dict) or item.get('output_type') == 'error' for item in outputs):
        raise BridgeUnavailable('Remote reply contains an execution error; outcome unknown')
    # Notebook output can be split into multiple stream messages.
    streams = ''.join(item.get('text', '') for item in outputs if item.get('output_type') == 'stream')
    marked = [line[len('RSI_RESULT:'):] for line in streams.splitlines() if line.startswith('RSI_RESULT:')]
    if len(marked) != 1:
        raise BridgeUnavailable('Expected exactly one remote result; outcome unknown')
    try:
        result = json.loads(marked[0])
    except (TypeError, ValueError) as exc:
        raise BridgeUnavailable('Malformed remote result; outcome unknown') from None
    if type(result) is not dict:
        raise BridgeUnavailable('Remote result is not an object; outcome unknown')
    return result


class ColabBridge:
    def __init__(self, python, session, expected_runtime_id, *, timeout=60):
        if not all(type(value) is str and value for value in (str(python), session, expected_runtime_id)):
            raise ValueError('Bridge requires an interpreter, session, and pinned runtime ID')
        if type(timeout) not in (int, float) or not 0 < timeout <= 3600:
            raise ValueError('Invalid bridge timeout')
        self.python = str(python)
        self.session = session
        self.runtime_id = expected_runtime_id
        self.timeout = timeout

    def _call(self, action, **payload):
        request = {'action': action, 'session': self.session,
                   'expected_runtime_id': self.runtime_id, 'timeout': self.timeout, **payload}
        options = {'input': json.dumps(request), 'text': True, 'encoding': 'utf-8',
                   'capture_output': True, 'timeout': self.timeout + 10,
                   'env': {**os.environ, 'PYTHONUTF8': '1', 'COLAB_CLI_REQUEST_TIMEOUT': str(self.timeout)}}
        if os.name == 'nt':
            options['creationflags'] = subprocess.CREATE_NO_WINDOW
        try:
            result = subprocess.run([self.python, '-B', '-X', 'utf8',
                                     str(Path(__file__).with_name('colab_bridge_call.py'))], **options)
        except subprocess.TimeoutExpired:
            raise BridgeUnavailable('Bridge deadline exceeded; remote outcome unknown') from None
        except OSError:
            raise BridgeUnavailable('Bridge process unavailable; remote outcome unknown') from None
        # Never include raw transport stdout/stderr or exception URLs in errors.
        if result.returncode != 0:
            raise BridgeUnavailable('Bridge call failed; remote outcome unknown')
        try:
            response = json.loads(result.stdout)
        except (TypeError, ValueError):
            raise BridgeUnavailable('Malformed bridge reply; remote outcome unknown') from None
        if type(response) is not dict or response.get('status') != 'ok' or 'result' not in response:
            raise BridgeUnavailable('Bridge rejected the request; remote outcome unknown')
        return response['result']

    def execute(self, code):
        return self._call('execute', code=code)

    def put(self, local_path, remote_path):
        return self._call('put', local_path=str(Path(local_path).absolute()), remote_path=remote_path)

    def get(self, remote_path, local_path):
        return self._call('get', local_path=str(Path(local_path).absolute()), remote_path=remote_path)
