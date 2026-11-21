"""Run candidate tests against the patched tree in a forked child and report which fail.

Workspace: a tree of symlinks into the pinned checkout where only the directories holding a
changed file are real directories, so building it costs a few dozen syscalls and every
unchanged source file keeps its compiled bytecode.
"""
import os
import select
import shutil
import signal
import sys
import time

from . import runner

PINNED = os.environ.get("RWX_PINNED", "/opt/sqlglot")
WS_BASE = os.environ.get("RWX_WS", "/tmp/rwx-ws")


def build_workspace(new_files, name="ws"):
    """new_files: {relpath: bytes or None (deleted)}.  Returns the workspace root."""
    ws = os.path.join(WS_BASE, name)
    shutil.rmtree(ws, ignore_errors=True)
    os.makedirs(ws)
    dirs = {"": set()}  # real directories -> unused
    for p in new_files:
        parts = p.split("/")
        for i in range(len(parts)):
            dirs[""] = None
            dirs["/".join(parts[:i])] = None
    for d in sorted(dirs, key=lambda x: x.count("/")):
        real = os.path.join(ws, d) if d else ws
        os.makedirs(real, exist_ok=True)
        src = os.path.join(PINNED, d) if d else PINNED
        try:
            names = os.listdir(src)
        except FileNotFoundError:
            names = []
        for n in names:
            rel = (d + "/" + n) if d else n
            if rel in dirs:
                continue
            tgt = os.path.join(real, n)
            if not os.path.lexists(tgt):
                os.symlink(os.path.join(src, n), tgt)
    for p, content in new_files.items():
        tgt = os.path.join(ws, p)
        if os.path.lexists(tgt):
            os.unlink(tgt)
        if content is not None:
            os.makedirs(os.path.dirname(tgt), exist_ok=True)
            with open(tgt, "wb") as f:
                f.write(content)
    return ws


def remove_workspace(ws):
    shutil.rmtree(ws, ignore_errors=True)


def run_tests(ws, ids, warm=(), deadline=60.0, log=None):
    """Run `ids` (test node ids, in suite order) in a child.  Returns {id: failed(bool)}.

    A test that did not report before the deadline counts as failed.
    """
    for m in warm:
        try:
            __import__(m)
        except Exception:
            pass
    keymap = {}
    for i in ids:
        mod, cls, meth = runner.split_id(i)
        keymap[(mod, cls, meth)] = i
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(r)
            os.setsid()
            os.chdir(ws)
            sys.path.insert(0, ws)
            sys.dont_write_bytecode = True
            os.environ["PYTHONPATH"] = ws
            os.environ["SKIP_INTEGRATION"] = "0"
            out = os.fdopen(w, "w", buffering=1)

            def emit(k, failed):
                out.write("%s::%s::%s\t%d\n" % (k[0], k[1], k[2], 1 if failed else 0))

            runner.run_ids(ids, ws, emit)
            out.close()
        except BaseException:
            pass
        finally:
            os._exit(0)
    os.close(w)
    result = {}
    buf = b""
    end = time.time() + deadline
    timed_out = False
    with os.fdopen(r, "rb", buffering=0) as rf:
        while True:
            left = end - time.time()
            if left <= 0:
                timed_out = True
                break
            ready, _, _ = select.select([rf], [], [], min(left, 1.0))
            if not ready:
                continue
            chunk = os.read(rf.fileno(), 65536)
            if not chunk:
                break
            buf += chunk
    if timed_out:
        try:
            os.killpg(pid, signal.SIGKILL)
        except Exception:
            pass
    try:
        os.waitpid(pid, 0)
    except Exception:
        pass
    for line in buf.decode("utf-8", "replace").split("\n"):
        if not line:
            continue
        try:
            name, flag = line.rsplit("\t", 1)
            mod, cls, meth = name.split("::")
        except ValueError:
            continue
        i = keymap.get((mod, cls, meth))
        if i is not None:
            # a test can be reported twice (e.g. subtests): any failure sticks
            result[i] = result.get(i, False) or flag == "1"
    for i in ids:
        if i not in result:
            result[i] = True  # no verdict: treat as failed
    if log:
        log("verify: %d tests, timeout=%s" % (len(ids), timed_out))
    return result
