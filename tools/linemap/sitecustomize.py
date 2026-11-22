"""Line tracer, loaded by every Python process of a traced run (it sits on PYTHONPATH, so
spawned pool workers load it too).  Active only when RWX_COV_DIR is set.

Appends to $RWX_COV_DIR/<pid>.cov, one record per line, written at once (a worker that ends with
os._exit loses nothing):
    S <time>          process start
    F <n> <path>      file number n is <path>, relative to $RWX_COV_ROOT
    <n> <line>        first execution of a line of file n (since the last restart of events)
    O <path>          a file of the checkout opened by Python code (data reads; imports open .pyc)
    T <time> <id>     written by the pytest plugin: test <id> starts (whole-suite mode)
    X <time>          process end
"""
import os
import sys

_dir = os.environ.get("RWX_COV_DIR")
if _dir and hasattr(sys, "monitoring"):
    import time as _time

    _root = os.path.join(os.environ["RWX_COV_ROOT"], "")
    _fd = os.open(os.path.join(_dir, "%d.cov" % os.getpid()), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o666)
    _write = os.write
    _files = {}
    _mon = sys.monitoring
    _tool = _mon.COVERAGE_ID
    _DISABLE = _mon.DISABLE
    _write(_fd, b"S %.3f\n" % _time.time())

    def _line(code, lineno):
        fn = code.co_filename
        n = _files.get(fn)
        if n is None:
            if fn.startswith(_root):
                n = len(_files) + 1
                _write(_fd, ("F %d %s\n" % (n, fn[len(_root):])).encode("utf-8", "surrogateescape"))
            else:
                n = 0
            _files[fn] = n
        if n:
            _write(_fd, b"%d %d\n" % (n, lineno))
        return _DISABLE

    def _audit(event, args):
        if event == "open":
            p = args[0]
            if isinstance(p, bytes):
                p = os.fsdecode(p)
            if isinstance(p, str):
                if not os.path.isabs(p):
                    p = os.path.abspath(p)
                if p.startswith(_root):
                    _write(_fd, ("O %s\n" % p[len(_root):]).encode("utf-8", "surrogateescape"))

    def _end(*a):
        try:
            _write(_fd, b"X %.3f\n" % _time.time())
        except OSError:
            pass

    _mon.use_tool_id(_tool, "rwx")
    _mon.register_callback(_tool, _mon.events.LINE, _line)
    _mon.set_events(_tool, _mon.events.LINE)
    sys.addaudithook(_audit)
    import atexit

    atexit.register(_end)
    _os_exit = os._exit

    def _exit(code):
        _end()
        _os_exit(code)

    os._exit = _exit

    def rwx_mark(nodeid):
        """Whole-suite mode: a test starts; every line reports again."""
        _write(_fd, ("T %.3f %s\n" % (_time.time(), nodeid)).encode("utf-8", "surrogateescape"))
        _mon.restart_events()

    sys.rwx_mark = rwx_mark
