"""Minimal line/code-object tracer on sys.monitoring (Python 3.12+), used only when building maps.

Records, for files under the given prefixes:
  lines  - every executed (file, line)
  codes  - every code object that was started: (file, co_firstlineno, co_name)
Each location is reported once (the callback returns DISABLE).
"""
import sys

mon = sys.monitoring
TOOL = 3


# Standard-library functions that read or print line numbers of source files / frames.
WATCH = {
    "/traceback.py": None,
    "/linecache.py": None,
    "/inspect.py": {"getsourcelines", "getsource", "findsource", "getframeinfo", "getouterframes",
                    "getinnerframes", "stack", "trace", "currentframe", "getsourcefile", "getblock"},
    "/logging/__init__.py": {"findCaller"},
    "/warnings.py": {"formatwarning", "_formatwarnmsg_impl", "_showwarnmsg_impl", "showwarning"},
}


class Tracer:
    def __init__(self, prefixes):
        self.prefixes = tuple(prefixes)
        self.lines = {}
        self.codes = {}
        self.watch = set()
        self.started = False

    def start(self):
        mon.use_tool_id(TOOL, "rwx")
        ev = mon.events
        mon.register_callback(TOOL, ev.LINE, self._line)
        mon.register_callback(TOOL, ev.PY_START, self._start)
        mon.set_events(TOOL, ev.LINE | ev.PY_START)
        self.started = True

    def stop(self):
        if self.started:
            mon.set_events(TOOL, 0)
            mon.register_callback(TOOL, mon.events.LINE, None)
            mon.register_callback(TOOL, mon.events.PY_START, None)
            mon.free_tool_id(TOOL)
            self.started = False

    def _line(self, code, line):
        fn = code.co_filename
        if fn.startswith(self.prefixes):
            s = self.lines.get(fn)
            if s is None:
                s = self.lines[fn] = set()
            s.add(line)
        return mon.DISABLE

    def _start(self, code, offset):
        fn = code.co_filename
        if fn.startswith(self.prefixes):
            s = self.codes.get(fn)
            if s is None:
                s = self.codes[fn] = set()
            s.add((code.co_firstlineno, code.co_name))
        else:
            for suf, names in WATCH.items():
                if fn.endswith(suf):
                    if names is None or code.co_name in names:
                        self.watch.add(suf[1:] + ":" + code.co_name)
                    break
        return mon.DISABLE

    def dump(self):
        return {
            "lines": {f: sorted(v) for f, v in self.lines.items()},
            "codes": {f: sorted(v) for f, v in self.codes.items()},
            "watch": sorted(self.watch),
        }
