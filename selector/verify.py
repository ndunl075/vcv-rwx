"""Run tests against the patched code with real pytest and report which are verified to pass.

The runs happen in children of two warm servers (selector/zygote.py):
  cold  a fresh pytest run in a private patched tree under /tmp (every file a hard link to one copy
        of the pinned checkout made at start-up, patched files written anew), minus start-up cost;
  hot   the whole suite already imported and collected from the pinned copy, patched function
        bodies swapped in, patched source served for modules that are not imported yet.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time

PINNED = os.environ.get("RWX_PINNED", "/opt/sqlglot")
TMP = os.environ.get("RWX_TMP", "/tmp/rwx")
SLOW_S, SLOW_X = 5.0, 10.0   # a test slower than SLOW_S + SLOW_X * its recorded time is not "verified"
SLOTS = 2

_base_lock = threading.Lock()
_base = None      # (root, {relative path: (mtime_ns, size)})


def _stat_tree(root):
    st = {}
    for d, _, names in os.walk(root):
        for n in names:
            full = os.path.join(d, n)
            s = os.lstat(full)
            st[os.path.relpath(full, root)] = (s.st_mtime_ns, s.st_size)
    return st


def base_root():
    return os.path.join(TMP, "base")


def base(check=True):
    """The private copy of the pinned checkout; `check` tells whether a test run changed a file in it."""
    global _base
    with _base_lock:
        if _base is None:
            root = base_root()
            shutil.rmtree(root, ignore_errors=True)
            os.makedirs(TMP, exist_ok=True)
            shutil.copytree(PINNED, root, symlinks=True, ignore=shutil.ignore_patterns(".git"))
            _base = (root, _stat_tree(root))
        elif check and _stat_tree(_base[0]) != _base[1]:
            raise RuntimeError("a test run changed the copy of the pinned tree")
        return _base[0]


def build_tree(slot, new_files):
    src = base()
    root = os.path.join(TMP, "tree%d" % slot)
    shutil.rmtree(root, ignore_errors=True)
    for d, subdirs, names in os.walk(src):
        rel = os.path.relpath(d, src)
        dst = root if rel == "." else os.path.join(root, rel)
        os.mkdir(dst)
        for n in names:
            os.link(os.path.join(d, n), os.path.join(dst, n), follow_symlinks=False)
    for path, content in new_files.items():
        full = os.path.join(root, path)
        os.unlink(full)
        with open(full, "wb") as f:
            f.write(content)
        # the bytecode of the pinned file must not be used for the patched one
        d, n = os.path.split(full)
        cache = os.path.join(d, "__pycache__")
        if n.endswith(".py") and os.path.isdir(cache):
            for c in os.listdir(cache):
                if c.startswith(n[:-3] + "."):
                    os.unlink(os.path.join(cache, c))
    return root


class Channel:
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    def send(self, obj):
        self.sock.sendall((json.dumps(obj) + "\n").encode())

    def recv(self, timeout):
        """One JSON message, or None after `timeout` seconds or when the server is gone."""
        end = time.monotonic() + timeout
        while b"\n" not in self.buf:
            left = end - time.monotonic()
            if left <= 0:
                return None
            self.sock.settimeout(left)
            try:
                data = self.sock.recv(1 << 16)
            except (socket.timeout, OSError):
                return None
            if not data:
                return None
            self.buf += data
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line)


class Servers:
    """The warm servers, one channel per slot and mode."""

    def __init__(self, log, libs=True, hot_files=("tests",)):
        self.log = log
        self.cold = self.hot = None
        self.loaded = None      # files of the modules the hot server has imported
        self.ready = {}
        root = base(check=False)
        pairs = [[socket.socketpair() for _ in range(SLOTS)] for _ in range(2)]
        theirs = [[b for _, b in mode] for mode in pairs]
        # one hash seed for the build-time snapshot of the import state and for everything the servers run
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "selector.zygote", root,
             ",".join(str(s.fileno()) for s in theirs[0]), ",".join(str(s.fileno()) for s in theirs[1]),
             "1" if libs else "0", ",".join(hot_files)],
            pass_fds=[s.fileno() for mode in theirs for s in mode], env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=None if os.environ.get("RWX_DEBUG") else subprocess.DEVNULL,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for mode in theirs:
            for s in mode:
                s.close()
        self.cold = [Channel(a) for a, _ in pairs[0]]
        self.hot = [Channel(a) for a, _ in pairs[1]]

    def wait(self, mode, slot, timeout):
        """True when the server of `mode` is ready for `slot`."""
        key = (mode, slot)
        if key not in self.ready:
            ch = (self.hot if mode == "hot" else self.cold)[slot]
            msg = ch.recv(timeout)
            self.ready[key] = bool(msg and msg.get("ready") == mode)
            if msg and mode == "hot":
                self.loaded = set(msg["loaded"])
        return self.ready[key]

    def close(self):
        for ch in (self.cold or []) + (self.hot or []):
            try:
                ch.sock.close()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


class Result:
    def __init__(self):
        self.unverified = []       # ids that failed, or have no complete clean verdict
        self.teardown_failed = []  # ids with a failing teardown report
        self.wall = 0.0
        self.note = ""
        self.hot_refused = False   # the hot server could not hold the patch exactly


def run(servers, slot, mode, new_files, changes, ids, recorded, budget):
    """Run `ids` (suite order) with the patch.  recorded: id -> seconds.
    changes (hot): [{"path", "swaps"}] for the patched Python files."""
    res = Result()
    res.unverified = list(ids)
    t0 = time.monotonic()
    work = os.path.join(TMP, "job%d" % slot)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    outp = os.path.join(work, "out")
    files = []
    for i in ids:
        f = i.split("::", 1)[0]
        if f not in files:
            files.append(f)
    root = None
    if mode == "hot":
        job = {"out": outp, "ids": ids, "changes": []}
        for k, ch in enumerate(changes):
            src = os.path.join(work, "src%d" % k)
            with open(src, "wb") as f:
                f.write(new_files[ch["path"]])
            job["changes"].append({"path": ch["path"], "source": src, "swaps": ch["swaps"]})
        ch = servers.hot[slot]
    else:
        root = build_tree(slot, new_files)
        job = {"out": outp, "ids": ids, "files": files, "root": root}
        ch = servers.cold[slot]
    ch.send(job)
    msg = ch.recv(max(1.0, budget))
    timed_out = msg is None
    if timed_out:
        try:
            ch.send({"kill": True})
        except OSError:
            pass
        msg = ch.recv(10.0)
    res.wall = time.monotonic() - t0
    reports = {}
    ended = None
    try:
        with open(outp, "rb") as f:
            lines = f.read().decode("utf-8", "replace").split("\n")
    except OSError:
        lines = []
    for line in lines:
        if line.startswith("R "):
            parts = line.split(" ", 4)
            if len(parts) == 5:
                reports.setdefault(parts[4], []).append((parts[1], parts[2], float(parts[3])))
        elif line.startswith("E "):
            ended = line[2:].strip()
        elif line.startswith("H "):
            res.hot_refused = True
            res.note = line[2:]
    if root is not None:
        shutil.rmtree(root, ignore_errors=True)
    if res.hot_refused:
        return res
    if timed_out or msg is None or ended not in ("0", "1"):
        res.note = "timeout" if timed_out else "pytest ended with %r" % (ended,)
        return res
    unverified = []
    for i in ids:
        reps = reports.get(i, [])
        whens = {w for w, _, _ in reps}
        bad = any(o == "failed" for _, o, _ in reps)
        skipped_in_setup = any(w == "setup" and o == "skipped" for w, o, _ in reps)
        complete = "setup" in whens and "teardown" in whens and ("call" in whens or skipped_in_setup)
        slow = sum(d for _, _, d in reps) > SLOW_S + SLOW_X * recorded.get(i, 0.0)
        if bad or not complete or slow:
            unverified.append(i)
        if any(w == "teardown" and o == "failed" for w, o, _ in reps):
            res.teardown_failed.append(i)
    res.unverified = unverified
    res.note = "%s, pytest status %s" % (mode, ended)
    return res


def cleanup():
    shutil.rmtree(TMP, ignore_errors=True)
