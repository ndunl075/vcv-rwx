"""Warm processes that fork one child per verification run.

`python -m selector.zygote <base tree> <cold fds> <hot fds> <libs: 0|1> <test files of the hot server>`
starts the *cold* server: pytest is loaded (and, if asked, the third-party libraries that some test
files import at the top), nothing of sqlglot.  It forks the *hot* server, which imports sqlglot and
collects the given test files from the pinned base tree: what a pytest session over those files has
done when its first test starts.

A job is one JSON line on the server's socket; the server forks, the child runs the job's tests with
pytest and streams the reports to the job's file (see Verdicts); the server answers when the child
is gone.

  cold child: chdir to the job's patched tree and run pytest on the job's test files there.  Nothing
      of sqlglot or of the tests was imported before: this is a fresh pytest process, minus start-up.
  hot child: the job's patch must leave everything untouched that ran while the suite was imported
      and collected (the caller checks: no changed module- or class-level code in a loaded module, no
      changed line that ran during collection).  The child gives every function of a changed
      function body the patched code object, serves the patched source for modules that are not
      imported yet, and runs the job's tests out of the collected session.
"""
import gc
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
import select
import signal
import socket
import sys
import types

import pytest


class Verdicts:
    """pytest plugin: stream every report to the job's file, written at once so that a killed run
    keeps the verdicts it reached.

        R <when> <outcome> <duration> <nodeid>     a test report (subtest reports included)
        C <nodeid>                                 a collector that failed
        K <count>                                  tests kept
        E <exitstatus>                             session end
    """

    def __init__(self):
        self.fd = None
        self.want = None

    def open(self, path, want):
        self.fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        self.want = want

    def put(self, line):
        if self.fd is not None:
            os.write(self.fd, (line + "\n").encode("utf-8", "backslashreplace"))

    def pytest_collectreport(self, report):
        if report.failed:
            self.put("C %s" % report.nodeid)

    def pytest_collection_modifyitems(self, session, config, items):
        if self.want is not None:
            items[:] = [i for i in items if i.nodeid in self.want]
            self.put("K %d" % len(items))

    def pytest_runtest_logreport(self, report):
        self.put("R %s %s %.3f %s" % (report.when, report.outcome, getattr(report, "duration", 0.0) or 0.0, report.nodeid))

    def pytest_sessionfinish(self, session, exitstatus):
        self.put("E %d" % int(exitstatus))


def serve(socks, announce):
    """Fork a child per job.  Returns the job in the child; never returns in the server."""
    for s in socks:
        s.sendall((json.dumps(announce) + "\n").encode())
    live = {}   # read end of the child's pipe -> (pid, socket)
    bufs = {s: b"" for s in socks}
    socks = list(socks)
    while True:
        if not socks and not live:
            os._exit(0)
        ready, _, _ = select.select(socks + list(live), [], [])
        for x in ready:
            if x in live:
                pid, s = live.pop(x)
                os.close(x)
                _, status = os.waitpid(pid, 0)
                try:
                    s.sendall((json.dumps({"done": pid, "status": status}) + "\n").encode())
                except OSError:
                    pass
                continue
            try:
                data = x.recv(1 << 20)
            except OSError:
                data = b""
            if not data:
                socks.remove(x)
                for fd, (pid, s) in live.items():
                    if s is x:
                        _kill(pid)
                continue
            bufs[x] += data
            while b"\n" in bufs[x]:
                line, bufs[x] = bufs[x].split(b"\n", 1)
                job = json.loads(line)
                if job.get("kill"):
                    for fd, (pid, s) in live.items():
                        if s is x:
                            _kill(pid)
                    continue
                rfd, wfd = os.pipe()
                pid = os.fork()
                if pid == 0:
                    os.close(rfd)
                    for fd in live:
                        os.close(fd)
                    for s in bufs:
                        s.close()
                    os.setsid()
                    return job   # wfd stays open until this process and what it forked are gone
                os.close(wfd)
                live[rfd] = (pid, x)


def _kill(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


PYTEST_ARGS = ["-p", "no:cacheprovider", "-q", "--continue-on-collection-errors"]


def state_child(job):
    """Import all of sqlglot from the job's patched tree and write the slots that differ from the pinned state."""
    out = {}
    try:
        root = job["root"]
        os.chdir(root)
        sys.path.insert(0, root)
        from . import state

        snap = state.snapshot(root, job["translate"])
        out["changed"] = {k: list(v) for k, v in state.compare(state.load(job["pinned"]), snap).items()}
    except BaseException as e:
        out = {"error": "%s: %s" % (type(e).__name__, e)}
    with open(job["out"], "w") as f:
        json.dump(out, f)
    os._exit(0)


def cold_child(job, verdicts):
    if job.get("state"):
        state_child(job)
    root = job["root"]
    os.chdir(root)
    sys.path.insert(0, root)
    os.environ["PYTHONPATH"] = root   # as in the labeling runs: subprocesses of the tests import the patched tree
    verdicts.open(job["out"], set(job["ids"]))
    sys.stdout.flush()
    code = pytest.main(PYTEST_ARGS + job["files"], plugins=[verdicts])
    os._exit(int(code) & 0x7F)


class Overlay(importlib.abc.MetaPathFinder):
    """Serve the patched source of modules that were not imported when the suite was collected."""

    def __init__(self, base, sources):
        self.base = base
        self.sources = sources   # relative path -> patched source bytes

    def find_spec(self, name, path=None, target=None):
        rel = name.replace(".", "/")
        for cand, pkg in ((rel + ".py", False), (rel + "/__init__.py", True)):
            if cand in self.sources:
                full = os.path.join(self.base, cand)
                loader = _PatchedLoader(name, full, self.sources[cand])
                return importlib.util.spec_from_file_location(
                    name, full, loader=loader, submodule_search_locations=[os.path.dirname(full)] if pkg else None)
        return None


class _PatchedLoader(importlib.machinery.SourceFileLoader):
    def __init__(self, name, path, source):
        super().__init__(name, path)
        self._source = source

    def get_data(self, path):
        if path == self.path:
            return self._source
        raise OSError("no bytecode for a patched module")

    def path_stats(self, path):
        raise OSError("no bytecode for a patched module")

    def set_data(self, path, data, *, _mode=0o666):
        pass


def code_tree(code, out):
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            out.setdefault((c.co_qualname, c.co_firstlineno), []).append(c)
            code_tree(c, out)
    return out


def hot_apply(job, base, index, loaded):
    """Make this process hold the patched code.  Raises if that cannot be done exactly."""
    overlay = {}
    for ch in job["changes"]:
        rel = ch["path"]
        with open(ch["source"], "rb") as f:
            source = f.read()
        if rel not in loaded:
            overlay[rel] = source
            continue
        if ch.get("swaps") is None:
            raise RuntimeError("%s is loaded and cannot be swapped" % rel)
        new = code_tree(compile(source, os.path.join(base, rel), "exec", dont_inherit=True), {})
        for oq, ol, nq, nl in ch["swaps"]:
            got = new.get((nq, nl), [])
            if len(got) != 1:
                raise RuntimeError("%s: %d code objects for %s:%d" % (rel, len(got), nq, nl))
            olds = index.get((rel, oq, ol), {})
            if len(olds) > 1:
                raise RuntimeError("%s: %s:%d is not one code object" % (rel, oq, ol))
            for funcs in olds.values():
                for fn in funcs:
                    fn.__code__ = got[0]
    if overlay:
        sys.meta_path.insert(0, Overlay(base, overlay))
        importlib.invalidate_caches()


class Hot:
    """pytest plugin of the hot server: when collection is done, serve; a child runs its job's tests."""

    def __init__(self, socks, base, verdicts):
        self.socks, self.base, self.verdicts = socks, base, verdicts

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtestloop(self, session):
        base = os.path.join(self.base, "")
        index = {}   # (relative path, qualname, first line) -> {id(code): [functions]}
        for obj in gc.get_objects():
            if isinstance(obj, types.FunctionType):
                code = obj.__code__
                if code.co_filename.startswith(base):
                    key = (code.co_filename[len(base):], code.co_qualname, code.co_firstlineno)
                    index.setdefault(key, {}).setdefault(id(code), []).append(obj)
        loaded = set()
        for m in list(sys.modules.values()):
            f = getattr(m, "__file__", None)
            if f and f.startswith(base):
                loaded.add(f[len(base):])
        gc.freeze()
        job = serve(self.socks, {"ready": "hot", "loaded": sorted(loaded), "items": len(session.items)})
        try:
            hot_apply(job, self.base, index, loaded)
        except BaseException as e:
            with open(job["out"], "a") as f:
                f.write("H %s: %s\n" % (type(e).__name__, e))
            os._exit(3)
        want = set(job["ids"])
        session.items[:] = [i for i in session.items if i.nodeid in want]
        self.verdicts.open(job["out"], None)
        self.verdicts.put("K %d" % len(session.items))
        return None   # pytest's own loop now runs the remaining items


def main():
    base = sys.argv[1]
    cold_socks = [socket.socket(fileno=int(fd)) for fd in sys.argv[2].split(",")]
    hot_socks = [socket.socket(fileno=int(fd)) for fd in sys.argv[3].split(",")]
    os.environ["SKIP_INTEGRATION"] = "0"
    sys.dont_write_bytecode = True
    hot_files = [f for f in sys.argv[5].split(",") if f] if len(sys.argv) > 5 else ["tests"]
    if sys.argv[4:5] != ["0"]:
        # test files that will be collected import them at the top: every session over those files has
        # them loaded before its first test
        for lib in ("numpy", "pandas", "duckdb"):
            try:
                __import__(lib)
            except Exception:
                pass
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        if fd != 2 or not os.environ.get("RWX_DEBUG"):
            os.dup2(devnull, fd)
    verdicts = Verdicts()
    if hot_files and os.fork() == 0:
        for s in cold_socks:
            s.close()
        try:
            os.chdir(base)
            sys.path.insert(0, base)
            code = pytest.main(PYTEST_ARGS + hot_files, plugins=[verdicts, Hot(hot_socks, base, verdicts)])
        finally:
            os._exit(0)
    for s in hot_socks:
        s.close()
    gc.freeze()
    job = serve(cold_socks, {"ready": "cold"})
    cold_child(job, verdicts)


if __name__ == "__main__":
    main()
