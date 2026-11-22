"""Slot-read traces of the pinned commit, inside the labeling image.

    trace.py cold <clean> <tests.json> <out dir> <worker> <workers> [ids file]   -> <out dir>/cold-<i>.jsonl.gz
    trace.py session <clean> <tests.json> <out dir>                             -> <out dir>/session/*.slots
    trace.py tracked <clean> <out file>     every slot the tracer can see, after importing all of sqlglot
"""
import gzip
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
    return env


def cold(clean, tests_json, out_dir, wi, wn, only=None):
    root = "/c%d" % wi
    env = prepare(clean, root)
    tests = json.load(open(tests_json))["tests"]
    mine = [t for t in tests if t["number"] % wn == wi and (only is None or t["id"] in only)]
    d = "/tmp/slots%d" % wi
    with gzip.open(os.path.join(out_dir, "cold-%d.jsonl.gz" % wi), "at") as f:
        for k, t in enumerate(mine):
            shutil.rmtree(d, ignore_errors=True)
            os.makedirs(d)
            env["RWX_SLOT_DIR"] = d
            t0 = time.time()
            try:
                p = subprocess.run([sys.executable, "-m", "pytest", t["id"], "-q", "-p", "no:cacheprovider"], cwd=root,
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=3600)
                rc, tail = p.returncode, p.stdout[-400:].decode("utf-8", "replace")
            except subprocess.TimeoutExpired:
                rc, tail = -9, "timeout"
            time.sleep(0.02)
            reads = set()
            for n in os.listdir(d):
                with open(os.path.join(d, n), "rb") as g:
                    reads.update(g.read().decode("utf-8", "surrogateescape").split("\n"))
            reads.discard("")
            rec = {"id": t["id"], "rc": rc, "wall": round(time.time() - t0, 3), "reads": sorted(reads)}
            if rc != 0:
                rec["tail"] = tail
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            f.flush()
            if k % 50 == 0:
                print("worker %d: %d/%d %s" % (wi, k, len(mine), t["id"]), file=sys.stderr, flush=True)


def session(clean, tests_json, out_dir):
    env = prepare(clean, "/s")
    d = os.path.join(out_dir, "session")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    env["RWX_SLOT_DIR"] = d
    p = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "-p", "rwx_marks"],
                       cwd="/s", env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    with open(os.path.join(d, "pytest.txt"), "wb") as f:
        f.write(p.stdout[-3000:])
    print("session rc", p.returncode, p.stdout[-200:].decode("utf-8", "replace"), file=sys.stderr)


def tracked(clean, out):
    env = prepare(clean, "/k")
    d = "/tmp/slotsk"
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    env["RWX_SLOT_DIR"] = d
    code = ("import os, sys, importlib\n"
            "for dp, dn, fn in os.walk('sqlglot'):\n"
            "    for f in sorted(fn):\n"
            "        if f.endswith('.py') and f != '__main__.py':\n"
            "            m = os.path.join(dp, f)[:-3].replace('/', '.')\n"
            "            importlib.import_module(m[:-9] if m.endswith('.__init__') else m)\n"
            "print('\\n'.join(sorted(s.key.decode().strip() for _, _, s in sys.rwx_slots)))\n")
    p = subprocess.run([sys.executable, "-c", code], cwd="/k", env=env, stdout=subprocess.PIPE)
    with open(out, "wb") as f:
        f.write(p.stdout)
    print("tracked slots:", p.stdout.count(b"\n"), "rc", p.returncode, file=sys.stderr)


if __name__ == "__main__":
    if sys.argv[1] == "cold":
        only = set(open(sys.argv[7]).read().split()) if len(sys.argv) > 7 else None
        cold(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), int(sys.argv[6]), only)
    elif sys.argv[1] == "session":
        session(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        tracked(sys.argv[2], sys.argv[3])
