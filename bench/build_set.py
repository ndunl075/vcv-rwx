#!/usr/bin/env python3
"""Generate and label a patch set in the kit's layout. Resumable.

  build_set.py <name> [--target K] [--jobs N] [--platform native]     name: dev300 hold300 odd200 traps150
  build_set.py <set-dir> --seed N --count C --mix dev|odd|traps ...     (explicit form)

<set-dir>/commits/<id>.patch   labeled patches only
<set-dir>/tests.json           copy of the kit's tests.json
<set-dir>/answers.json         {"commits": {id: {"failing": [...], "kind": ..., "sub": ..., "missing": [...]}}}
<set-dir>/labels/<id>.json     raw label records (kept; resume skips these)
<set-dir>/.pending/            generated patches + kinds.json, not yet labeled

A candidate with a `require` predicate (traps) is kept only if the labeler
confirms it; others are moved to <set-dir>/.rejected/.
Baseline (no patch) failures are recorded in labels/_baseline.json and
removed from every label (they are environment flakes, not the patch).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import label as L  # noqa: E402
import vcv  # noqa: E402

SPECS = {
    "dev300": {"seed": 1001, "count": 300, "mix": "dev"},
    "hold300": {"seed": 2002, "count": 300, "mix": "dev"},
    "odd200": {"seed": 3003, "count": 200, "mix": "odd"},
    "traps150": {"seed": 4004, "count": 280, "mix": "traps"},
}

SLOW = {1490, 1106, 1224}  # test_max_nodes, test_execute_tpcds, test_generator_memory


def py312() -> str:
    """Patch generation is deterministic per Python version; use 3.12 everywhere."""
    if sys.version_info[:2] == (3, 12):
        return sys.executable
    for c in (shutil.which("python3.12"), "/opt/homebrew/bin/python3.12"):
        if c and os.path.exists(c):
            return c
    return sys.executable


def atomic_write(path: Path, text: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def accept(require, failing, missing, extra) -> bool:
    if not require:
        return True
    if require == "nonempty":
        return bool(failing)
    if require == "only_slow":
        return bool(failing) and set(failing) <= SLOW
    if require == "order_dependent":
        return bool(extra.get("order_dependent"))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("set_dir")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--count", type=int)
    ap.add_argument("--mix", default="dev")
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument("--target", type=int, default=0, help="stop after this many accepted patches")
    ap.add_argument("--platform", default="native")
    ap.add_argument("--no-baseline", action="store_true")
    a = ap.parse_args()
    if a.set_dir in SPECS:
        sp = SPECS[a.set_dir]
        a.seed, a.count, a.mix = sp["seed"], (2 if vcv.SMOKE else sp["count"]), sp["mix"]
        S = vcv.DATA / a.set_dir
    else:
        S = Path(a.set_dir)
    a.jobs = a.jobs or vcv.workers()
    vcv.ensure_dirs()
    lockf = open(vcv.DATA / "build.lock", "w")
    fcntl.flock(lockf, fcntl.LOCK_EX)  # one set builder at a time
    for d in ("commits", "labels", ".pending", ".rejected"):
        (S / d).mkdir(parents=True, exist_ok=True)
    tests_src = L.KIT / "sample" / "tests.json"
    shutil.copy(tests_src, S / "tests.json")
    tests = L.load_tests(tests_src)

    # 1. generate
    kinds_path = S / ".pending" / "kinds.json"
    have = len(list((S / "labels").glob("*.json"))) + len(list((S / ".pending").glob("*.patch")))
    if have < a.count:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(HERE)
        subprocess.run([py312(), str(HERE / "mutate.py"), "--seed", str(a.seed), "--count", str(a.count),
                        "--out", str(S / ".pending"), "--mix", a.mix, "--work", str(S / ".gen-work")],
                       check=True, env=env)
    kinds = json.loads(kinds_path.read_text()) if kinds_path.exists() else {}

    # 2. baseline
    base_path = vcv.DATA / "baseline.json"  # shared by all sets: failures of the unpatched suite on this machine
    flaky = set()
    if not a.no_baseline:
        if not base_path.exists():
            r = L.label_patch(None, tests, a.platform)
            atomic_write(base_path, json.dumps(r))
        flaky = set(json.loads(base_path.read_text()).get("failing", []))
        if flaky:
            print(f"baseline fails {sorted(flaky)}: excluded from labels", file=sys.stderr)

    lock = threading.Lock()
    accepted = {}

    def reload_answers():
        ans = {}
        for lp in sorted((S / "labels").glob("*.json")):
            if lp.stem.startswith("_"):
                continue
            r = json.loads(lp.read_text())
            if r.get("accepted"):
                ans[lp.stem] = {"failing": r["failing"], "kind": r["kind"], "sub": r.get("sub", ""),
                                "missing": r.get("missing", [])}
        return ans

    def write_answers(ans):
        atomic_write(S / "answers.json", json.dumps({"commits": ans}, indent=0, sort_keys=True))

    accepted = reload_answers()
    write_answers(accepted)

    todo = sorted(p.stem for p in (S / ".pending").glob("*.patch") if not (S / "labels" / f"{p.stem}.json").exists())
    todo = [t for t in todo if t in kinds]
    print(f"{len(accepted)} accepted already, {len(todo)} to label", file=sys.stderr)

    def work(pid):
        if a.target and len(accepted) >= a.target:
            return
        patch = S / ".pending" / f"{pid}.patch"
        t0 = time.time()
        try:
            r = L.label_patch(patch, tests, a.platform)
        except Exception as ex:  # docker hiccup: leave unlabeled for the next resume
            print(f"{pid}: {ex!r}", file=sys.stderr)
            return
        meta = kinds[pid]
        rec = {"kind": meta["kind"], "sub": meta.get("sub", ""), "desc": meta.get("desc", ""),
               "secs": round(time.time() - t0, 1)}
        if "error" in r:
            rec.update({"accepted": False, "error": r["error"], "stderr": r.get("stderr", "")[-300:]})
        else:
            failing = [n for n in r["failing"] if n not in flaky]
            rec.update({"failing": failing, "missing": r["missing"], "collect_failed": r["collect_failed"],
                        "exitstatus": r["exitstatus"]})
            extra = dict(meta)
            if meta.get("require") == "order_dependent" and failing:
                byn = {t["number"]: t["id"] for t in tests}
                head = failing[:6]
                r2 = L.label_patch(patch, tests, a.platform, only=[byn[n] for n in head])
                still = set(r2.get("failing", []))
                extra["order_dependent"] = bool(set(head) - still) if "error" not in r2 else False
                rec["isolated_still_failing"] = sorted(still)
            rec["accepted"] = accept(meta.get("require"), failing, r["missing"], extra)
        atomic_write(S / "labels" / f"{pid}.json", json.dumps(rec))
        with lock:
            if rec["accepted"]:
                shutil.copy(patch, S / "commits" / f"{pid}.patch")
                accepted[pid] = {"failing": rec["failing"], "kind": rec["kind"], "sub": rec["sub"],
                                 "missing": rec["missing"]}
                write_answers(accepted)
            else:
                shutil.copy(patch, S / ".rejected" / f"{pid}.patch")
            print(f"{pid} {rec['kind']}/{rec['sub']} failing={len(rec.get('failing', []))} "
                  f"acc={rec['accepted']} n={len(accepted)} {rec['secs']}s", flush=True)

    with ThreadPoolExecutor(a.jobs) as ex:
        list(ex.map(work, todo))
    write_answers(accepted)
    unlabeled = [t for t in todo if not (S / "labels" / f"{t}.json").exists()]
    if not unlabeled:
        (S / "DONE").write_text(f"{len(accepted)} patches, {time.strftime('%F %T')}\n")
    print(f"done: {len(accepted)} accepted in {S}", flush=True)


if __name__ == "__main__":
    main()
