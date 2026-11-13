"""Standalone child, run by the existing google-colab-cli interpreter.

Do not invoke CLI callback: it enables sensitive DEBUG logs and auto-updates.
All credential handling remains inside the user's original bridge.
"""
import contextlib
import io
import json
import logging
from pathlib import Path
import sys


def call(request):
    from colab_cli.common import state
    from colab_cli.contents import ContentsClient
    from colab_cli.runtime import ColabRuntime

    session = state.get_session(request['session'])
    if session.endpoint != request['expected_runtime_id']:
        raise ValueError('RuntimeChanged')
    action = request['action']
    if action in ('get', 'put'):
        client = ContentsClient(session)
        if action == 'put':
            client.upload(request['local_path'], request['remote_path'])
        else:
            target = Path(request['local_path'])
            pending = target.with_name(target.name + '.partial')
            client.download(request['remote_path'], str(pending))
            pending.replace(target)
        return None
    if action != 'execute':
        raise ValueError('UnknownBridgeAction')

    def kernel_started(kernel_id):
        session.kernel_id = kernel_id
        state.store.add(session)

    def session_started(session_id):
        session.session_id = session_id
        state.store.add(session)

    runtime = ColabRuntime(session.url, session.token, kernel_id=session.kernel_id,
                           session_id=session.session_id, on_kernel_started=kernel_started,
                           on_session_started=session_started)
    try:
        return runtime.execute_code(request['code'], timeout=request['timeout'], allow_stdin=False)
    finally:
        runtime.stop(shutdown_kernel=False)


def main():
    logging.disable(logging.CRITICAL)
    try:
        request = json.load(sys.stdin)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = call(request)
        print(json.dumps({'status': 'ok', 'result': result}, ensure_ascii=False))
    except BaseException as exc:
        # Class only: exception messages can include authenticated URLs.
        print(json.dumps({'status': 'error', 'error_type': type(exc).__name__}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
