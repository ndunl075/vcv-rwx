"""Build the maps from sqlglot at the pinned commit.  Runs inside the dev image:

    tools/run_maps.sh            (docker run ... python tools/build_maps.py)

For every test, in a fresh forked process (so import-time lines of lazily loaded modules
count for exactly the tests that load them) it records:
  * every sqlglot / tests / sqlglotc line executed (sys.monitoring tracer, also in child processes),
  * every file the test opens and every directory it lists,
  * the outcome and the CPU seconds,
then writes compact files to maps/ (see tools/README.md for the format).
"""
import gzip
import json
import multiprocessing
import os
import shutil
import sys
import tempfile
import time

ROOT = os.environ.get("RWX_ROOT", "/opt/sqlglot")
WORK = os.environ.get("RWX_WORK", "/work")
OUT = os.path.join(WORK, os.environ.get("RWX_MAPS", "maps"))
COLLECT = os.path.join(WORK, "tools", "collect")
SRC = [ROOT + "/sqlglot", ROOT + "/tests", ROOT + "/sqlglotc"]
TIMEOUT = float(os.environ.get("RWX_TIMEOUT", "3000"))

os.environ["SKIP_INTEGRATION"] = "0"
sys.path.insert(0, os.path.join(WORK))


def _rel(path):
    try:
        p = os.path.abspath(os.fsdecode(path))
    except Exception:
        return None
    return p[len(ROOT) + 1:] if p.startswith(ROOT + "/") else None


def _from_import_machinery():
    f = sys._getframe(3)
    n = 0
    while f is not None and n < 40:
        nm = f.f_globals.get("__name__", "")
        if nm.startswith("importlib") or nm in ("pkgutil", "zipimport"):
            return True
        f = f.f_back
        n += 1
    return False


def child(nodeid, wfd):
    sys.path.insert(0, COLLECT)
    from rwx_tracer import Tracer

    from selector import runner

    tmp = tempfile.mkdtemp(prefix="cov")
    os.environ["RWX_CHILD_COV"] = tmp
    os.environ["PYTHONPATH"] = ROOT + os.pathsep + COLLECT
    os.chdir(ROOT)
    sys.path.insert(0, ROOT)
    reads, listed = set(), set()

    def audit(event, args):
        if event == "open":
            p = args[0]
            if isinstance(p, int):
                return
            r = _rel(p)
            if r and not r.endswith(".pyc") and "__pycache__" not in r and not _from_import_machinery():
                reads.add(r)
        elif event in ("os.listdir", "os.scandir"):
            r = _rel(args[0] if args[0] is not None else ".")
            if r is not None and not _from_import_machinery():
                listed.add(r)

    result = {}
    tr = Tracer([ROOT + "/"])
    tr.start()
    sys.addaudithook(audit)
    t0 = time.process_time()
    failed = []

    def emit(k, f):
        failed.append(bool(f))

    try:
        runner.run_ids([nodeid], ROOT, emit)
    except BaseException as e:  # noqa
        result["crash"] = repr(e)
        failed.append(True)
    cpu = time.process_time() - t0
    # pool workers write their trace at exit: wait for them before collecting
    import multiprocessing

    for proc in multiprocessing.active_children():
        proc.join(30)
    end = time.time() + 5
    while time.time() < end and not os.listdir(tmp):
        time.sleep(0.1)
    tr.stop()
    own = tr.dump()
    lines = {}
    codes = {}

    def merge(d):
        for f, v in d["lines"].items():
            r = _rel(f)
            if r:
                lines.setdefault(r, set()).update(v)
        for f, v in d["codes"].items():
            r = _rel(f)
            if r:
                codes.setdefault(r, set()).update(tuple(x) for x in v)

    merge(own)
    watch = set(own.get("watch", []))
    nchild = 0
    for x in os.listdir(tmp):
        try:
            d = json.load(open(os.path.join(tmp, x)))
            merge(d)
            watch.update(d.get("watch", []))
            nchild += 1
        except Exception as e:  # noqa
            result.setdefault("childerr", []).append(repr(e))
    shutil.rmtree(tmp, ignore_errors=True)
    result.update(
        failed=any(failed) or not failed,
        cpu=cpu,
        lines={f: sorted(v) for f, v in lines.items()},
        codes={f: sorted(v) for f, v in codes.items()},
        reads=sorted(reads),
        listed=sorted(listed),
        children=nchild,
        watch=sorted(watch),
        mods=[m for m in ("numpy", "pandas", "duckdb") if m in sys.modules],
    )
    with os.fdopen(wfd, "w") as w:
        json.dump(result, w)


def cold(nodeid):
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        try:
            child(nodeid, w)
        finally:
            os._exit(0)
    os.close(w)
    import select

    buf = []
    deadline = time.time() + TIMEOUT
    with os.fdopen(r) as rf:
        while True:
            left = deadline - time.time()
            if left <= 0:
                os.kill(pid, 9)
                os.waitpid(pid, 0)
                return nodeid, {"failed": True, "timeout": True, "lines": {}, "codes": {}, "reads": [], "listed": [], "cpu": TIMEOUT}
            ready, _, _ = select.select([rf], [], [], min(left, 5))
            if ready:
                chunk = rf.read(1 << 20) if False else os.read(rf.fileno(), 1 << 20)
                if not chunk:
                    break
                buf.append(chunk)
    os.waitpid(pid, 0)
    try:
        return nodeid, json.loads(b"".join(buf))
    except Exception:
        return nodeid, {"failed": True, "crash": "no result", "lines": {}, "codes": {}, "reads": [], "listed": [], "cpu": 0}


def main():
    ids_file = os.path.join(WORK, "kit", "sample", "tests.json")
    ids = [t["id"] for t in json.load(open(ids_file))["tests"]]
    only = os.environ.get("RWX_ONLY")
    if only:
        ids = [i for i in ids if only in i]
    idsf = os.environ.get("RWX_IDS_FILE")
    if idsf:
        want = set(open(idsf).read().split())
        ids = [i for i in ids if i in want]
    jobs = int(os.environ.get("RWX_JOBS", "2"))
    # warm imports shared by every fork: third-party libraries the tests import
    import unittest  # noqa
    for m in ("numpy", "pandas", "duckdb", "pytz", "dateutil"):
        try:
            __import__(m)
        except Exception:
            pass
    from concurrent.futures import ThreadPoolExecutor

    results = {}
    t0 = time.time()
    # threads only supervise: every test runs in its own forked process (which may itself spawn pools)
    if jobs == 1:  # plain loop, no threads at all around fork()
        for n, nid in enumerate(ids):
            results[nid] = cold(nid)[1]
            print(f"{n+1}/{len(ids)} {time.time()-t0:.0f}s {nid}", file=sys.stderr, flush=True)
    else:
        with ThreadPoolExecutor(jobs) as ex:
            for n, (nid, res) in enumerate(ex.map(cold, ids)):
                results[nid] = res
                if n % 50 == 0:
                    print(f"{n}/{len(ids)} {time.time()-t0:.0f}s", file=sys.stderr, flush=True)
    os.makedirs(OUT, exist_ok=True)
    name = os.environ.get("RWX_RAW", "cold_raw.json.gz")
    with gzip.open(os.path.join(WORK, ".work", name), "wt") as f:
        json.dump(results, f)
    print("raw saved", file=sys.stderr)


if __name__ == "__main__":
    main()
