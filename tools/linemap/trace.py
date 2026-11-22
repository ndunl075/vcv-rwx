"""Line-level traces of the pinned commit, inside the labeling image.

    trace.py cold <clean checkout> <tests.json> <out dir> <worker index> <worker count> [ids file]
        every test alone in a fresh pytest process; writes <out dir>/cold-<i>.jsonl, one line per test:
        {"id", "rc", "cpu", "wall", "lines": {file: [line, ...]}, "opens": [...], "children": n}
    trace.py session <clean checkout> <tests.json> <out dir>
        the whole suite in one pytest process; keeps the raw trace files in <out dir>/session/
"""
import json
import os
import resource
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def prepare(clean, root):
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(clean, root, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    env = dict(os.environ, PYTHONPATH=root, SKIP_INTEGRATION="0")
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    subprocess.run([sys.executable, "-m", "compileall", "-q", "sqlglot", "tests"], cwd=root, env=env)
    subprocess.run([sys.executable, "-m", "pytest", "tests", "--collect-only", "-q", "-p", "no:cacheprovider"],
                   cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = HERE + os.pathsep + root
    env["RWX_COV_ROOT"] = root
    return env


def read_cov(path):
    """-> ({file: set(lines)}, set(opens)) of one trace file without test marks."""
    files, lines, opens = {}, {}, set()
    with open(path, "rb") as f:
        for raw in f:
            rec = raw.decode("utf-8", "surrogateescape").rstrip("\n")
            c = rec[:1]
            if c == "F":
                _, n, p = rec.split(" ", 2)
                files[n] = p
            elif c == "O":
                opens.add(rec[2:])
            elif c.isdigit():
                n, ln = rec.split(" ")
                lines.setdefault(files[n], set()).add(int(ln))
    return lines, opens


def cold(clean, tests_json, out_dir, wi, wn, only=None):
    root = "/c%d" % wi
    env = prepare(clean, root)
    tests = json.load(open(tests_json))["tests"]
    mine = [t for t in tests if t["number"] % wn == wi and (only is None or t["id"] in only)]
    cov = "/tmp/cov%d" % wi
    with open(os.path.join(out_dir, "cold-%d.jsonl" % wi), "a") as f:
        for k, t in enumerate(mine):
            shutil.rmtree(cov, ignore_errors=True)
            os.makedirs(cov)
            env["RWX_COV_DIR"] = cov
            r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
            t0 = time.time()
            try:
                p = subprocess.run([sys.executable, "-m", "pytest", t["id"], "-q", "-p", "no:cacheprovider"], cwd=root,
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1800)
                rc, tail = p.returncode, p.stdout[-300:].decode("utf-8", "replace")
            except subprocess.TimeoutExpired:
                rc, tail = -9, "timeout"
            r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
            time.sleep(0.02)
            lines, opens = {}, set()
            names = os.listdir(cov)
            for n in names:
                ls, os_ = read_cov(os.path.join(cov, n))
                for fn, s in ls.items():
                    lines.setdefault(fn, set()).update(s)
                opens |= os_
            rec = {"id": t["id"], "rc": rc, "wall": round(time.time() - t0, 3),
                   "cpu": round(r1.ru_utime + r1.ru_stime - r0.ru_utime - r0.ru_stime, 3),
                   "lines": {fn: sorted(s) for fn, s in sorted(lines.items())},
                   "opens": sorted(opens), "children": len(names) - 1}
            if rc != 0:
                rec["tail"] = tail
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            f.flush()
            if k % 50 == 0:
                print("worker %d: %d/%d %s" % (wi, k, len(mine), t["id"]), file=sys.stderr, flush=True)


def session(clean, tests_json, out_dir):
    root = "/s"
    env = prepare(clean, root)
    cov = os.path.join(out_dir, "session")
    shutil.rmtree(cov, ignore_errors=True)
    os.makedirs(cov)
    env["RWX_COV_DIR"] = cov
    p = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "-p", "rwx_marks"],
                       cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    with open(os.path.join(cov, "pytest.txt"), "wb") as f:
        f.write(p.stdout[-3000:])
    with open(os.path.join(cov, "main.txt"), "w") as f:
        f.write("%d\n" % p.returncode)
    print("session rc", p.returncode, p.stdout[-200:].decode("utf-8", "replace"), file=sys.stderr)


if __name__ == "__main__":
    if sys.argv[1] == "cold":
        only = set(open(sys.argv[7]).read().split()) if len(sys.argv) > 7 else None
        cold(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), int(sys.argv[6]), only)
    else:
        session(sys.argv[2], sys.argv[3], sys.argv[4])
