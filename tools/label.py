"""Label a patch by really running the whole suite on the patched tree (dev image only).

    python tools/label.py <patch> [<patch> ...]      ->  prints one JSON line per patch

The tree is a full copy of /opt/sqlglot with the patch applied by selector/patch.py (the strict
applier); the suite runs under pytest with SKIP_INTEGRATION=0 and xdist.  A test file that cannot
be imported fails every test of that file (as the track defines "fails").
"""
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from selector import patch as P  # noqa: E402

PINNED = "/opt/sqlglot"


def apply(patch_bytes, dest):
    for c in P.parse(patch_bytes):
        if c.binary:
            raise SystemExit("binary patches are labeled by hand")
        if c.kind in ("delete",):
            os.unlink(os.path.join(dest, c.old_path))
            continue
        if c.kind == "add":
            data = b"\n".join(b for h in c.hunks for op, b in h.lines if op == "+")
            data += b"\n" if data and not (c.hunks and c.hunks[-1].new_noeol) else b""
            os.makedirs(os.path.dirname(os.path.join(dest, c.new_path)) or dest, exist_ok=True)
            open(os.path.join(dest, c.new_path), "wb").write(data)
            continue
        old = open(os.path.join(dest, c.old_path), "rb").read()
        new = P.apply_hunks(old, c.hunks) if c.hunks else old
        if c.kind == "rename":
            os.unlink(os.path.join(dest, c.old_path))
        os.makedirs(os.path.dirname(os.path.join(dest, c.new_path)) or dest, exist_ok=True)
        open(os.path.join(dest, c.new_path), "wb").write(new)
        if c.new_mode and c.kind in ("mode", "modify"):
            os.chmod(os.path.join(dest, c.new_path), int(c.new_mode.decode(), 8) & 0o777)


def label(patch_path, tests_json, jobs):
    dest = "/tmp/lab"
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(PINNED, dest, symlinks=True)
    apply(open(patch_path, "rb").read(), dest)
    env = dict(os.environ, SKIP_INTEGRATION="0", PYTHONPATH=dest, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--continue-on-collection-errors",
         "-n", str(jobs), "-rfEA", "--no-header", "--tb=no", "tests"],
        cwd=dest, env=env, capture_output=True, text=True)
    out = p.stdout
    ids = [t["id"] for t in json.load(open(tests_json))["tests"]]
    failing = set()
    collection = set()
    for line in out.splitlines():
        m = re.match(r"^(FAILED|ERROR|SUBFAILED)\b[^\n]*?(tests/\S+\.py)(::\S+)?", line)
        if m and line.startswith(("FAILED", "ERROR", "SUBFAILED")):
            f = m.group(2)
            if m.group(3):
                nid = f + m.group(3)
                nid = re.sub(r"\[.*$", "", nid)
                parts = nid.split("::")
                failing.add("::".join(parts[:3]))
            else:
                collection.add(f)
    for i in ids:
        if i.split("::")[0] in collection:
            failing.add(i)
    return {"failing": sorted(i for i in failing if i in set(ids)), "collection_errors": sorted(collection),
            "tail": out.strip().splitlines()[-3:], "rc": p.returncode}


if __name__ == "__main__":
    tj = os.environ.get("RWX_TESTS", "/work/kit/sample/tests.json")
    jobs = int(os.environ.get("RWX_JOBS", "4"))
    for pp in sys.argv[1:]:
        r = label(pp, tj, jobs)
        r["patch"] = os.path.basename(pp)
        print(json.dumps(r), flush=True)
