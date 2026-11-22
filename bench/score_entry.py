#!/usr/bin/env python3
"""Run an entry image under the scorer's limits on a set and score it.

  score_entry.py <image> <set-dir> [runs] [--entry NAME] [--commit SHA] [--results FILE]

Output of the entry goes to files (never printed). CPU seconds (user+system of every process in the
container) come from a per-run parent cgroup: the container starts with --cgroup-parent=<parent>; cgroup v2
charges a parent for its dead children, so the parent's cpu.stat usage_usec read after the container exits is
the container's total CPU time. The cgroup tree lives in the Docker engine's VM (colima, Docker Desktop) or on the
host (native Linux), so it is read through a throwaway helper container started with --cgroupns=host and the
engine's /sys/fs/cgroup mounted read-only. cgroup v1 engines fall back to cpuacct.usage. If neither works the
row says cpu_s=null and `cpu_measure` explains why.
On Apple Silicon (Rosetta) the number includes emulation overhead; on x86 it is real.
"""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import slots  # noqa: E402
import vcv  # noqa: E402

KIT = vcv.KIT
DATA = vcv.DATA
RESULTS = DATA / "results.jsonl"
HELPER_IMAGE = "rwx-label:native"  # has cat; built by setup

spec = importlib.util.spec_from_file_location("kit_score", KIT / "score.py")
kit_score = importlib.util.module_from_spec(spec)
sys.modules["kit_score"] = kit_score
spec.loader.exec_module(kit_score)


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def cgroup_driver() -> str:
    return sh(["docker", "info", "--format", "{{.CgroupDriver}}"]).stdout.strip() or "cgroupfs"


def read_cgroup_cpu(parent: str):
    """-> (usage_usec, user, system, note)"""
    script = (f"cat /hostcg/{parent}/cpu.stat 2>/dev/null || "
              f"(echo cpuacct_ns $(cat /hostcg/cpuacct/{parent}/cpuacct.usage 2>/dev/null))")
    p = sh(["docker", "run", "--rm", "--cgroupns=host", "-v", "/sys/fs/cgroup:/hostcg:ro", HELPER_IMAGE, "sh", "-c", script],
           timeout=120)
    usage = user = system = None
    for line in p.stdout.splitlines():
        k, _, v = line.partition(" ")
        v = v.strip()
        if k == "usage_usec" and v.isdigit():
            usage = int(v)
        elif k == "user_usec" and v.isdigit():
            user = int(v)
        elif k == "system_usec" and v.isdigit():
            system = int(v)
        elif k == "cpuacct_ns" and v.isdigit():
            usage = int(v) // 1000
    return usage, user, system, ("ok" if usage is not None else f"unreadable: {p.stderr.strip()[-120:]}")


def snapshot(set_dir: Path, tag: str):
    """A consistent copy of the set (commits named in answers.json only)."""
    ans = json.loads((set_dir / "answers.json").read_text())["commits"]
    snap = DATA / "tmp" / f"snap-{tag}"
    if snap.exists():
        shutil.rmtree(snap)
    (snap / "commits").mkdir(parents=True)
    for cid in ans:
        shutil.copy(set_dir / "commits" / f"{cid}.patch", snap / "commits" / f"{cid}.patch")
    shutil.copy(set_dir / "tests.json", snap / "tests.json")
    os.chmod(snap, 0o755)
    os.chmod(snap / "commits", 0o755)
    return snap, ans


def run_once(image, snap: Path, n: int, outdir: Path, stem: str, cpus="2"):
    sfx = f"{os.getpid()}-{int(time.time())}"
    systemd = cgroup_driver() == "systemd"
    parent = f"rwxscore-{sfx}" + (".slice" if systemd else "")
    cg_parent = parent if systemd else f"/{parent}"
    name = f"rwxscore-{sfx}"
    timeout = max(60.0, 4.5 * n) * float(os.environ.get("VCV_TIMEOUT_MULT", "1.5"))
    cmd = ["docker", "run", "--rm", "--name", name, "--platform", "linux/amd64", "--network", "none",
           "--cpus", cpus, "--memory", "4096m", "--memory-swap", "4096m", "--pids-limit", "256", "--read-only",
           "--tmpfs", "/tmp:rw,size=64m,mode=1777", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
           "--user", "65534:65534", "--env", "HOME=/tmp", f"--cgroup-parent={cg_parent}",
           "-v", f"{snap / 'commits'}:/data/commits:ro", "-v", f"{snap / 'tests.json'}:/data/tests.json:ro", image]
    out = outdir / f"{stem}.out"
    err = outdir / f"{stem}.err"
    t0 = time.time()
    timed_out = False
    with open(out, "wb") as fo, open(err, "wb") as fe:
        p = subprocess.Popen(cmd, stdout=fo, stderr=fe)
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            sh(["docker", "kill", name])
            p.kill()
            rc = -9
            p.wait()
    wall = time.time() - t0
    usage, user, system, note = read_cgroup_cpu(parent)
    return {"rc": rc, "wall_s": round(wall, 2), "timed_out": timed_out, "cpu_usec": usage, "user_usec": user,
            "system_usec": system, "cpu_measure": note, "out": str(out), "err": str(err),
            "stdout_bytes": out.stat().st_size}


def cost_by_commit(stdout: bytes, tests, ans):
    dur = {t["number"]: kit_score._duration_milliseconds(t["seconds"]) for t in tests}
    cost, sel = {}, {}
    for line in stdout.decode("utf-8").split("\n"):
        if not line.strip():
            continue
        o = json.loads(line)
        ws = [w for w in o["workers"] if w]
        sel[o["commit"]] = sum(len(w) for w in ws)
        cost[o["commit"]] = (max(sum(dur[x] for x in w) for w in ws) + 2000 * len(ws)) if ws else 0
    return cost, sel


def score_run(res, tests, ans):
    stdout = Path(res["out"]).read_bytes()
    if res["timed_out"] or res["rc"] != 0:
        return {"reject": "timeout" if res["timed_out"] else f"exit_{res['rc']}"}
    if res["stdout_bytes"] > 2 * 1024 * 1024:
        return {"reject": "stdout_too_big"}
    ev = kit_score.evaluate_run(stdout, tests, ans)
    if not ev["ok"]:
        return {"reject": ev["code"] + (f"@{ev['line']}" if ev.get("line") else "")}
    cost, sel = cost_by_commit(stdout, tests, ans)
    return {"reject": None, "sim_ms": ev["simulated_milliseconds"], "missed": ev["missed_commits"],
            "cost_ms": cost, "selected": sel}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("set_dir")
    ap.add_argument("runs", nargs="?", type=int, default=1)
    ap.add_argument("--entry", default="")
    ap.add_argument("--commit", default="")
    ap.add_argument("--results", default=str(RESULTS))
    a = ap.parse_args()
    vcv.ensure_dirs()
    S = Path(a.set_dir).resolve()
    set_name = S.name
    entry = a.entry or a.image
    tag = f"{os.getpid()}"
    if S.name == "sample":
        snap, ans = S, json.loads((S / "answers.json").read_text())["commits"]
    else:
        snap, ans = snapshot(S, tag)
    tests = json.loads((snap / "tests.json").read_text())["tests"]
    n = len(ans)
    ts = time.strftime("%Y%m%d-%H%M%S")
    runs = []
    vcv.BENCH_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(vcv.BENCH_LOCK, "w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)  # one timed run at a time, across all our processes
        for i in range(a.runs):
            stem = f"{entry.replace('/', '_').replace(':', '_')}__{set_name}__{ts}__{i}"
            with slots.slot(2):
                res = run_once(a.image, snap, n, DATA / "runs", stem)
            sc = score_run(res, tests, ans)
            res.update(sc)
            if not sc["reject"]:
                res["cpu_s"] = res["cpu_usec"] / 1e6 if res["cpu_usec"] is not None else None
                res["total_usec"] = res["sim_ms"] * 1000 + (res["cpu_usec"] or 0)
            runs.append(res)
    ok = [r for r in runs if not r["reject"]]
    row = {"ts": ts, "host": vcv.HOST, "entry": entry, "commit": a.commit, "set": set_name, "n": n, "runs": len(runs)}
    if len(ok) != len(runs):
        row["reject"] = next(r["reject"] for r in runs if r["reject"])
        row["wall_s_all"] = [r["wall_s"] for r in runs]
    else:
        mid = sorted(ok, key=lambda r: r["total_usec"])[len(ok) // 2]
        row["reject"] = None
        row["simulated_s"] = mid["sim_ms"] / 1000
        row["cpu_s"] = round(mid["cpu_usec"] / 1e6, 2) if mid["cpu_usec"] is not None else None
        row["cpu_measure"] = mid["cpu_measure"]
        row["total_s"] = (mid["total_usec"] + 50000) // 100000 / 10
        row["misses"] = len(mid["missed"])
        row["missed"] = mid["missed"]
        kinds = {c: v["kind"] for c, v in ans.items()}
        subs = {c: v.get("sub", "") for c, v in ans.items()}
        row["missed_kinds"], row["missed_subs"] = {}, {}
        for c in mid["missed"]:
            row["missed_kinds"][kinds[c]] = row["missed_kinds"].get(kinds[c], 0) + 1
            k = f"{kinds[c]}/{subs[c]}"
            row["missed_subs"][k] = row["missed_subs"].get(k, 0) + 1
        by_kind = {}
        for c, ms in mid["cost_ms"].items():
            d = by_kind.setdefault(kinds[c], {"n": 0, "cost_s": 0.0, "selected": 0, "failing": 0})
            d["n"] += 1
            d["cost_s"] += ms / 1000
            d["selected"] += mid["selected"][c]
            d["failing"] += len(ans[c]["failing"])
        row["by_kind"] = {k: {"n": d["n"], "cost_s": round(d["cost_s"], 1), "avg_selected": round(d["selected"] / d["n"], 1),
                              "avg_failing": round(d["failing"] / d["n"], 1)} for k, d in by_kind.items()}
        row["wall_s"] = mid["wall_s"]
        row["wall_s_all"] = [r["wall_s"] for r in runs]
        if len(ok) > 1:
            row["total_spread_s"] = round((max(r["total_usec"] for r in ok) - min(r["total_usec"] for r in ok)) / 1e6, 2)
        cpus = [r["cpu_usec"] for r in runs if r.get("cpu_usec")]
        if cpus:
            row["over_limit_by_cpu"] = max(cpus) / 1e6 / 2 > max(60.0, 4.5 * n)  # more than 2 cores x the nominal limit
    with open(a.results, "a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps({k: row.get(k) for k in ("entry", "set", "n", "total_s", "simulated_s", "cpu_s", "misses", "reject")}))
    if snap != S:
        shutil.rmtree(snap, ignore_errors=True)


if __name__ == "__main__":
    main()
