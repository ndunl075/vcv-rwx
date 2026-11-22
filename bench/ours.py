#!/usr/bin/env python3
"""Build our own entry from branch `core` (re-fetched every call) and score it on the finished sets.

  ours.py [--sets sample,dev300,...] [--branch core]

Skipped (with a line in last_error) when the branch does not exist, has no Dockerfile at its top, or does not build.
The entry is named ours-<sha7>; a new commit on the branch is scored again.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import vcv  # noqa: E402


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def fail(msg):
    print(f"ours: {msg}", flush=True)
    (vcv.DATA / "last_error").write_text(f"{time.strftime('%F %T')} ours: {msg}\n")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", default="sample,dev300,hold300,odd200,traps150")
    ap.add_argument("--branch", default="core")
    a = ap.parse_args()
    vcv.ensure_dirs()
    repo = str(vcv.REPO)
    if sh(["git", "-C", repo, "fetch", "-q", "origin", a.branch]).returncode != 0:
        return fail(f"cannot fetch branch {a.branch} (does it exist and is the sign-in valid?)")
    ref = f"origin/{a.branch}"
    sha = sh(["git", "-C", repo, "rev-parse", ref]).stdout.strip()
    if "Dockerfile" not in sh(["git", "-C", repo, "ls-tree", "--name-only", ref]).stdout.split("\n"):
        return fail(f"{ref} has no Dockerfile at its top yet")
    tag = f"vcv-ours:{sha[:7]}"
    if sh(["docker", "image", "inspect", tag]).returncode != 0:
        log = vcv.DATA / "builds" / f"ours-{sha[:7]}.log"
        with open(log, "wb") as fl:
            arch = subprocess.Popen(["git", "-C", repo, "archive", ref], stdout=subprocess.PIPE, stderr=fl)
            b = subprocess.Popen(["docker", "build", "--platform", "linux/amd64", "-t", tag, "-"], stdin=arch.stdout,
                                 stdout=fl, stderr=subprocess.STDOUT)
            arch.stdout.close()
            try:
                rc = b.wait(timeout=1800)
            except subprocess.TimeoutExpired:
                b.kill()
                rc = -1
        if rc != 0:
            return fail(f"{ref} ({sha[:7]}) does not build (see builds/ours-{sha[:7]}.log)")
    r = subprocess.run([sys.executable, str(HERE / "rivals.py"), "--image", tag, "--name", f"ours-{sha[:7]}",
                        "--commit", sha, "--sets", a.sets])
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
