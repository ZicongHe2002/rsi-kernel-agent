"""Read-only discovery through the user's installed Colab bridge, no secrets output."""
import contextlib
import io
import json
import logging
import sys

logging.disable(logging.CRITICAL)
captured=io.StringIO()
try:
    with contextlib.redirect_stdout(captured),contextlib.redirect_stderr(captured):
        from colab_cli.common import state
        import colab_cli.client
        assignments=state.client.list_assignments()
        # Official bridge handles local session mapping/auth internally.
        sessions=state.store.list()
    rows=[]
    for item in assignments:
        names=[name for name,s in sessions.items() if s.endpoint==item.endpoint]
        rows.append({'runtime_id':item.endpoint,'session_names':names,
                     'accelerator':str(item.accelerator),'variant':str(item.variant)})
    print(json.dumps({'status':'connected_control_plane','bridge_source':colab_cli.client.__file__,
                      'assignments':rows},ensure_ascii=False))
except BaseException as exc:
    status=getattr(getattr(exc,'response',None),'status_code',None)
    print(json.dumps({'status':'discovery_failed','error_type':type(exc).__name__,'http_status':status,
                      'detail':'No credential, URL, or raw authentication response is included.'}))
    sys.exit(1)
