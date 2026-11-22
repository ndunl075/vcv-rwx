"""Run tests against the patched tree with real pytest in a fresh process, the way the labels
were made (pytest, SKIP_INTEGRATION=0), and report which of them are verified to pass.

The patched tree is a private directory under /tmp: every file of the pinned checkout is a hard
link to one copy made at start-up (so unchanged modules keep their bytecode), and the patched
files are written as new files.
"""
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

PINNED = os.environ.get("RWX_PINNED", "/opt/sqlglot")
TMP = os.environ.get("RWX_TMP", "/tmp/rwx")
PLUG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plug")
TEST_TIMEOUT_S = 40          # pytest-timeout per test: a hanging test fails instead of blocking the run
SLOW_S, SLOW_X = 5.0, 10.0   # a test slower than SLOW_S + SLOW_X * its recorded time is not "verified"

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


def base():
    """The private copy of the pinned checkout; rebuilt if a test run changed a file in it."""
    global _base
    with _base_lock:
        if _base is not None and _stat_tree(_base[0]) == _base[1]:
            return _base[0]
        root = os.path.join(TMP, "base")
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(TMP, exist_ok=True)
        shutil.copytree(PINNED, root, symlinks=True, ignore=shutil.ignore_patterns(".git"))
        _base = (root, _stat_tree(root))
        return root


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


class Result:
    def __init__(self):
        self.unverified = []       # ids that failed, or have no complete clean verdict
        self.teardown_failed = []  # ids with a failing teardown report
        self.wall = 0.0
        self.note = ""


def run(slot, new_files, ids, recorded, budget, log=None):
    """Run `ids` (suite order) against the tree with `new_files`.  recorded: id -> seconds."""
    res = Result()
    res.unverified = list(ids)
    t0 = time.monotonic()
    root = build_tree(slot, new_files)
    want = os.path.join(TMP, "want%d" % slot)
    outp = os.path.join(TMP, "out%d" % slot)
    with open(want, "w") as f:
        f.write("\n".join(ids))
    if os.path.exists(outp):
        os.unlink(outp)
    files = []
    for i in ids:
        f = i.split("::", 1)[0]
        if f not in files:
            files.append(f)
    env = dict(os.environ)
    env.update(PYTHONPATH=PLUG + os.pathsep + root, SKIP_INTEGRATION="0", PYTHONDONTWRITEBYTECODE="1",
               RWX_WANT=want, RWX_OUT=outp)
    cmd = [sys.executable, "-m", "pytest", "-p", "rwx_verdicts", "-p", "no:cacheprovider", "-q",
           "--continue-on-collection-errors", "--timeout=%d" % TEST_TIMEOUT_S] + files
    proc = subprocess.Popen(cmd, cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    timed_out = False
    try:
        proc.wait(timeout=max(1.0, budget))
    except subprocess.TimeoutExpired:
        timed_out = True
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # also ends anything the tests left running
    except OSError:
        pass
    proc.wait()
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
    shutil.rmtree(root, ignore_errors=True)
    if timed_out or ended not in ("0", "1"):
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
    res.note = "pytest status %s" % ended
    return res


def cleanup():
    shutil.rmtree(TMP, ignore_errors=True)
