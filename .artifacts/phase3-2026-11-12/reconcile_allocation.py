"""Bounded allocation using the original bridge and a persisted notebook ID.

Only sanitized phase/method/status are emitted. No URL/header/token is logged.
"""
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import sys
import uuid

logging.disable(logging.CRITICAL)
ledger = Path(__file__).with_name('allocation-intent.json')
if ledger.exists():
    intent = json.loads(ledger.read_text('utf-8'))
else:
    intent = {'session': 'rsi-p3-20261112', 'notebook_id': str(uuid.uuid4()),
              'status': 'prepared', 'prior_discovery': 'three empty assignment queries'}

def save():
    pending = ledger.with_suffix('.pending')
    with pending.open('w', encoding='utf-8') as stream:
        json.dump(intent, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(ledger)

phase = 'get_assignment'
try:
    save()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from colab_cli.common import state
        from colab_cli.client import Assignment, Variant, Accelerator
        from colab_cli.state import SessionState
        allocation_id = uuid.UUID(intent['notebook_id'])
        reply = state.client._get_assignment(allocation_id, Variant.TPU, Accelerator.V6E1, None)
        if not isinstance(reply, Assignment):
            retry_authorized = '--retry-authorized' in sys.argv
            active = state.client.list_assignments() if retry_authorized else []
            # An unrelated CPU notebook is not an orphan of this explicit TPU
            # request. Preserve it and let the service enforce allocation limits.
            possible_tpu = [item for item in active if item.accelerator != Accelerator.NONE]
            if possible_tpu:
                print_result = {'status': 'reconciliation_required', 'reason': 'an accelerated assignment exists; ownership must be reconciled',
                                'runtime_ids': [item.endpoint for item in possible_tpu]}
            elif intent['status'] not in ('prepared', 'get_failed') and not retry_authorized:
                # An unconfirmed POST cannot be resubmitted automatically.
                print_result = {'status': 'unknown', 'reason': 'prior POST not confirmed; GET has no assignment'}
            else:
                phase = 'post_assignment'
                intent['status'] = 'post_pending'
                intent['attempts'] = intent.get('attempts', 0) + 1
                intent['retry_authorization'] = 'User explicitly requested continued TPU v6e allocation on 2026-11-12'
                save()
                reply = state.client._post_assignment(allocation_id, reply.token, Variant.TPU, Accelerator.V6E1, None)
                print_result = None
        else:
            print_result = None
        if print_result is None:
            phase = 'register_session'
            session = SessionState(name=intent['session'], token=reply.runtime_proxy_info.token,
                                   url=reply.runtime_proxy_info.url, endpoint=reply.endpoint,
                                   token_expires_at=reply.runtime_proxy_info.expires_at(),
                                   variant=Variant.TPU.value, accelerator=Accelerator.V6E1.value,
                                   machine_shape='STANDARD')
            state.store.add(session)
            state.history.log_event(session.name, 'session_created',
                                    {'endpoint': session.endpoint, 'variant': session.variant,
                                     'accelerator': session.accelerator, 'machine_shape': session.machine_shape})
            intent.update(status='allocated', runtime_id=session.endpoint)
            save()
            print_result = {'status': 'allocated', 'session': session.name, 'runtime_id': session.endpoint}
    print(json.dumps(print_result))
except BaseException as exc:
    if phase == 'get_assignment' and intent['status'] == 'prepared':
        intent['status'] = 'get_failed'
    intent['last_error'] = {'phase': phase, 'error_type': type(exc).__name__,
                            'http_status': getattr(getattr(exc, 'response', None), 'status_code', None),
                            'method': getattr(getattr(exc, 'request', None), 'method', None)}
    save()
    print(json.dumps({'status': 'allocation_not_confirmed', **intent['last_error']}))
    sys.exit(1)
