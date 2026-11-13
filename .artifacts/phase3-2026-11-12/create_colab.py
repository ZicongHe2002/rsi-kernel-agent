"""Create the authorized, dedicated Phase3 runtime through existing CLI code."""
import contextlib
import io
import json
import logging
import sys
logging.disable(logging.CRITICAL)
name='rsi-p3-20261112'
try:
    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
        from colab_cli.commands.session import new
        from colab_cli.common import state
        new(session=name,tpu='v6e1',gpu=None,high_mem=False)
        session=state.get_session(name)
    print(json.dumps({'status':'allocated','session':name,'runtime_id':session.endpoint}))
except BaseException as exc:
    print(json.dumps({'status':'allocation_not_confirmed','session':name,
                      'error_type':type(exc).__name__,'http_status':getattr(getattr(exc,'response',None),'status_code',None)}))
    sys.exit(1)
