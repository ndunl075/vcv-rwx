#!/usr/bin/env python3
"""Label patches: apply each to a clean pinned sqlglot inside the labeling
container, run the whole suite, and map failures to test numbers.

  label.py PATCH [PATCH...] [--tests tests.json] [--platform native|amd64]
  label.py --check-sample [--platform ...]   # must equal kit/sample/answers.json

"Fails" = failed/error report in any phase (SUBFAILED included), a timeout
(pytest-timeout), or the test's file cannot be imported (every test of that file).
Tests of the pinned set that never ran (file deleted/renamed) are returned as
`missing`, separately from `failing`.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parent / "kit"
IMAGES = {"native": "rwx-label:arm64", "amd64": "rwx-label:amd64"}
DOCKER_TIMEOUT = 4000  # seconds for one whole-suite container run


def load_tests(path):
    return json.loads(Path(path).read_text())["tests"]


def docker_run(patch, platform, test_timeout=1500, nice=True, only=None):
    """Returns (result_json_dict, stderr_tail)."""
    image = IMAGES[platform]
    with tempfile.TemporaryDirectory(prefix="lbl-", dir=os.environ.get("LABEL_TMP", str(Path.home() / "Developer/vcv-data/rwx/tmp"))) as td:
        out = Path(td)
        os.chmod(out, 0o777)
        cmd = ["docker", "run", "--rm", "--network", "none", "--memory", "3g",
               "--tmpfs", "/w:exec,size=500m", "--tmpfs", "/tmp:exec,size=200m",
               "-e", f"TEST_TIMEOUT={test_timeout}",
               "-v", f"{HERE / 'inside'}:/bench:ro", "-v", f"{out}:/out"]
        if patch is not None:
            cmd += ["-v", f"{Path(patch).resolve()}:/in/patch:ro"]
        if only:
            (out / "only.txt").write_text("\n".join(only) + "\n")
            cmd += ["-v", f"{out / 'only.txt'}:/in/only.txt:ro"]
        if platform == "amd64":
            cmd += ["--platform", "linux/amd64"]
        cmd += [image, "/bench/run_one.sh", "/in/patch" if patch is not None else "-", "/out/result.json"]
        if nice:
            cmd = ["nice", "-n", "10"] + cmd
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=DOCKER_TIMEOUT)
        rj = out / "result.json"
        if not rj.exists():
            return {"no_result": True, "rc": p.returncode}, p.stderr[-800:]
        return json.loads(rj.read_text()), p.stderr[-800:]


def map_result(res, tests):
    if res.get("apply_error") or res.get("no_result"):
        return {"error": res}
    failed = set(res["failed"])
    seen = set(res["seen"])
    bad = set(n.split("::")[0] for n in res["collect_failed"])
    bad_dirs = [b.rstrip("/") + "/" for b in bad if not b.endswith(".py")]
    failing, missing = [], []
    for t in tests:
        tid, f = t["id"], t["file"]
        if tid in failed or f in bad or any(f.startswith(d) for d in bad_dirs):
            failing.append(t["number"])
        elif tid not in seen:
            missing.append(t["number"])
    return {"failing": failing, "missing": missing, "collect_failed": sorted(bad),
            "exitstatus": res.get("exitstatus")}


def label_patch(patch, tests, platform="native", only=None, **kw):
    res, err = docker_run(patch, platform, only=only, **kw)
    out = map_result(res, tests)
    if "error" in out:
        out["stderr"] = err
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("patches", nargs="*")
    ap.add_argument("--tests", default=str(KIT / "sample" / "tests.json"))
    ap.add_argument("--platform", default="native", choices=["native", "amd64"])
    ap.add_argument("--check-sample", action="store_true")
    ap.add_argument("--jobs", type=int, default=3)
    a = ap.parse_args()
    tests = load_tests(a.tests)
    if a.check_sample:
        from concurrent.futures import ThreadPoolExecutor
        ans = json.loads((KIT / "sample" / "answers.json").read_text())["commits"]
        ids = sorted(ans)

        def one(i):
            return i, label_patch(KIT / "sample" / "commits" / f"{i}.patch", tests, a.platform)

        ok = 0
        with ThreadPoolExecutor(a.jobs) as ex:
            for i, r in ex.map(one, ids):
                want = ans[i]["failing"]
                good = r.get("failing") == want and not r.get("missing")
                ok += good
                print(i, "OK" if good else f"DIFF got={r.get('failing', r)} want={want} missing={r.get('missing')}", flush=True)
        print(f"{ok}/{len(ids)} match ({a.platform})")
        sys.exit(0 if ok == len(ids) else 1)
    for p in a.patches:
        r = label_patch(Path(p), tests, a.platform)
        print(json.dumps({"patch": p, **r}))


if __name__ == "__main__":
    main()
