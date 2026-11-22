"""Record, for every test, the files and directories of the sqlglot checkout that its process tree opens.

Runs inside the labeling image.  Each test runs alone in a fresh pytest process (cold), against a
private copy of the pinned checkout that an inotify instance watches, so opens by native code
(DuckDB reading a CSV) and by spawned pool workers are seen as well as Python's own.

    trace.py <clean checkout> <tests.json> <out.jsonl> <worker index> <worker count>

One JSON line per test: {"id", "rc", "cpu", "wall", "files": [...], "dirs": [...], "overflow": bool}
A module that is imported shows up as its .py or its cached .pyc (reported as the .py).
"""
import ctypes
import json
import os
import resource
import shutil
import struct
import subprocess
import sys
import threading
import time

IN_OPEN = 0x20
IN_ISDIR = 0x40000000
IN_Q_OVERFLOW = 0x4000
libc = ctypes.CDLL(None, use_errno=True)


class Watch:
    def __init__(self, root):
        self.root = root
        self.fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1")
        self.wd = {}
        for d, subdirs, _ in os.walk(root):
            w = libc.inotify_add_watch(self.fd, d.encode(), IN_OPEN)
            if w < 0:
                raise OSError(ctypes.get_errno(), "inotify_add_watch " + d)
            self.wd[w] = os.path.relpath(d, root)
        self.files = set()
        self.dirs = set()
        self.overflow = False
        self.lock = threading.Lock()
        self.stop = False
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()

    def _drain(self):
        got = False
        while True:
            try:
                buf = os.read(self.fd, 1 << 16)
            except BlockingIOError:
                return got
            if not buf:
                return got
            got = True
            off = 0
            with self.lock:
                while off < len(buf):
                    wd, mask, _, ln = struct.unpack_from("iIII", buf, off)
                    name = buf[off + 16: off + 16 + ln].split(b"\0", 1)[0].decode("utf-8", "surrogateescape")
                    off += 16 + ln
                    if mask & IN_Q_OVERFLOW:
                        self.overflow = True
                        continue
                    base = self.wd.get(wd)
                    if base is None:
                        continue
                    p = os.path.normpath(os.path.join(base, name)) if name else base
                    (self.dirs if mask & IN_ISDIR else self.files).add(p)

    def _loop(self):
        while not self.stop:
            if not self._drain():
                time.sleep(0.005)

    def take(self):
        time.sleep(0.03)
        self._drain()
        with self.lock:
            f, d, o = self.files, self.dirs, self.overflow
            self.files, self.dirs, self.overflow = set(), set(), False
        return f, d, o


def src_of(p):
    """__pycache__/x.cpython-313.pyc -> x.py"""
    d, n = os.path.split(p)
    if os.path.basename(d) == "__pycache__" and n.endswith(".pyc"):
        return os.path.normpath(os.path.join(os.path.dirname(d), n.split(".")[0] + ".py"))
    return p


def main():
    clean, tests_json, out, wi, wn = sys.argv[1:6]
    wi, wn = int(wi), int(wn)
    root = "/w%d%s" % (wi, "r" if os.environ.get("TRACE_REVERSE") else "")
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(clean, root, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    env = dict(os.environ, PYTHONPATH=root, SKIP_INTEGRATION="0")
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    subprocess.run([sys.executable, "-m", "compileall", "-q", "sqlglot", "tests", "sqlglotc"], cwd=root, env=env)
    # warm the remaining caches once so that every traced run sees the same tree
    subprocess.run([sys.executable, "-m", "pytest", "tests/test_time.py", "-q", "-p", "no:cacheprovider"], cwd=root, env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    tests = json.load(open(tests_json))["tests"]
    only = None
    if len(sys.argv) > 6:
        only = set(open(sys.argv[6]).read().split())
    mine = [t for t in tests if t["number"] % wn == wi and (only is None or t["id"] in only)]
    if os.environ.get("TRACE_REVERSE"):
        mine.reverse()
    skip_from = os.environ.get("TRACE_SKIP_DONE", "").split()
    w = Watch(root)
    w.take()
    with open(out, "a") as f:
        for k, t in enumerate(mine):
            done = set()
            for sp in skip_from:  # another worker walks the same list from the other end
                try:
                    done.update(json.loads(line)["id"] for line in open(sp))
                except (OSError, ValueError):
                    pass
            if t["id"] in done:
                continue
            r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
            t0 = time.time()
            try:
                p = subprocess.run([sys.executable, "-m", "pytest", t["id"], "-q", "-p", "no:cacheprovider"], cwd=root, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900)
                rc, tail = p.returncode, p.stdout[-300:].decode("utf-8", "replace")
            except subprocess.TimeoutExpired:
                rc, tail = -9, "timeout"
            r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
            files, dirs, overflow = w.take()
            rec = {"id": t["id"], "rc": rc, "wall": round(time.time() - t0, 3),
                   "cpu": round(r1.ru_utime + r1.ru_stime - r0.ru_utime - r0.ru_stime, 3),
                   "files": sorted({src_of(p) for p in files}), "dirs": sorted(dirs), "overflow": overflow}
            if rc != 0:
                rec["tail"] = tail
            f.write(json.dumps(rec) + "\n")
            f.flush()
            if k % 50 == 0:
                print("worker %d: %d/%d %s" % (wi, k, len(mine), t["id"]), file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
