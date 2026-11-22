#!/usr/bin/env python3
"""Plain-words status of the benchmark machine. Also writes $VCV_DATA/status.txt (pushed with the results)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vcv  # noqa: E402

D = vcv.DATA
out = []


def say(s=""):
    out.append(s)


def read(p, default=""):
    try:
        return Path(p).read_text().strip()
    except OSError:
        return default


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout


run = sh(["pgrep", "-f", "bench/remote/run.sh"]).split()
say(f"Machine: {vcv.HOST}. Time now: {time.strftime('%F %T')}.")
say("The benchmark loop is RUNNING." if run else "The benchmark loop is NOT running. Start it with: bash bench/remote/run.sh")
stage = read(D / "stage.txt")
if stage:
    say(f"Doing now: {stage}")
cont = [l for l in sh(["docker", "ps", "--format", "{{.Image}}"]).splitlines() if l.startswith(("rwx-label", "rival-rwx", "vcv-ours"))]
say(f"Containers busy right now: {len(cont)} ({sum(1 for c in cont if c.startswith('rwx-label'))} labeling, "
    f"{sum(1 for c in cont if not c.startswith('rwx-label'))} scoring).")
say("")
say("Labeled patches per set:")
for name in ("dev300", "hold300", "odd200", "traps150"):
    d = D / name
    if not d.exists():
        say(f"  {name}: not started")
        continue
    acc = len(list((d / "commits").glob("*.patch")))
    labs = len([p for p in (d / "labels").glob("*.json") if not p.name.startswith("_")])
    cand = len(list((d / ".pending").glob("*.patch")))
    say(f"  {name}: {acc} kept, {labs} checked of {cand} made" + (" (finished)" if (d / "DONE").exists() else ""))
rows = []
f = D / "results.jsonl"
if f.exists():
    for line in f.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
good = [r for r in rows if not r.get("reject")]
say("")
say(f"Scores: {len(good)} good runs, {len(rows) - len(good)} rejected/failed, {len({r['entry'] for r in rows})} entries seen.")
say(f"Last push to GitHub (branch data): {read(D / 'last_push', 'never')}")
err = read(D / "last_error")
say(f"Last problem: {err or 'none'}")
du = shutil.disk_usage(str(D if D.exists() else Path.home()))
say(f"Disk free: {du.free / 2**30:.0f} GB.")
text = "\n".join(out)
print(text)
try:
    (D / "status.txt").write_text(text + "\n")
except OSError:
    pass
