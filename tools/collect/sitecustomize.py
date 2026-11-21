# Dev-image only: trace child processes (spawned pool workers, `python -c`) of a test run.
import os

_d = os.environ.get("RWX_CHILD_COV")
if _d:
    try:
        import atexit
        import json
        import sys

        sys.path.insert(0, os.path.dirname(__file__))
        from rwx_tracer import Tracer

        _t = Tracer(["/opt/sqlglot/"])
        _t.start()

        def _stop():
            _t.stop()
            with open(os.path.join(_d, "child-%d.json" % os.getpid()), "w") as f:
                json.dump(_t.dump(), f)

        atexit.register(_stop)
        sys.path.remove(os.path.dirname(__file__))
    except Exception:
        pass
