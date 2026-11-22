#!/usr/bin/env python3
"""Build and score rival entries (and our own image) on the labeled sets. Resumable, one at a time.

  rivals.py [--first N] [--sets sample,dev300,...] [--image IMG --name NAME --commit SHA] [--keep K]

Rival repos are bare copies in $VCV_RIVALS/<owner>__<repo>.git, in rivals.txt order (most active first). They are
built straight from `git archive` (never unpacked on the host) and run only inside the locked-down container
(score_entry.py). The image is removed after the rival's sets are scored (disk), unless it is among the first --keep.
A set is used once its DONE file exists (sample always); dev300 also counts once it has >= 100 labeled patches.
(entry, set) is scored again when the set has grown (a row exists only for fewer patches).
Rival text is data: only path:line of suspicious matches is stored (rival-notes.jsonl), never acted on.
"""
from __future__ import annotations

import argparse
import json
import os
import re
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
RIV = vcv.RIVALS
BUILD_TIMEOUT = 1800
PARTIAL_OK = {"dev300": 100}
SCAN = re.compile(r"(AI (agent|tool|assistant|model)s?\b|language model|\bLLM\b|\bClaude\b|\bChatGPT\b|"
                  r"do not (build|run|benchmark|execute)|ignore (all |any )?(previous|prior|above)|"
                  r"as an AI\b|if you are an? (ai|assistant|agent))", re.I)


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def rivals_list():
    f = Path(os.environ["VCV_RIVALS_LIST"]) if os.environ.get("VCV_RIVALS_LIST") else HERE / "remote" / "rivals.txt"
    return [l.strip() for l in f.read_text().splitlines() if l.strip() and not l.startswith("#")]


def set_info(name):
    """-> (dir, n_patches) if the set may be scored now, else None."""
    d = KIT / "sample" if name == "sample" else DATA / name
    if name == "sample":
        return d, len(json.loads((d / "answers.json").read_text())["commits"])
    ap = d / "answers.json"
    if not ap.exists():
        return None
    n = len(json.loads(ap.read_text())["commits"])
    if (d / "DONE").exists() or n >= PARTIAL_OK.get(name, 10**9):
        return d, n
    return None


def load_rows():
    rows = []
    f = DATA / "results.jsonl"
    if f.exists():
        for line in f.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def needs(entry, name, n, rows, commit=""):
    mine = [r for r in rows if r["entry"] == entry and r["set"] == name and (not commit or r.get("commit") == commit)]
    if not mine:
        return True
    if sum(1 for r in mine if r.get("reject") in ("timeout", "build_timeout")) >= 2:
        return False
    good = [r for r in mine if r.get("reject") not in ("infra", "timeout")]
    if not good:
        return True
    return max(r.get("n", 0) for r in good) < n and name != "sample"


def scan_notes(gd: Path, slug: str):
    """Record where a repo has text addressed to AI tools. Only paths and line numbers are kept."""
    p = sh(["git", "--git-dir", str(gd), "grep", "-I", "-n", "-i", "-E", SCAN.pattern, "HEAD"])
    hits = []
    for line in p.stdout.splitlines()[:40]:
        parts = line.split(":", 3)
        if len(parts) >= 3:
            hits.append(f"{parts[1]}:{parts[2]}")
    if hits:
        with open(DATA / "rival-notes.jsonl", "a") as f:
            f.write(json.dumps({"entry": slug, "host": vcv.HOST, "where": hits[:10],
                                "note": "text aimed at AI tools (data, not followed)"}) + "\n")


def build(gd: Path, tag: str, slug: str):
    log = DATA / "builds" / f"{slug}.log"
    ls = sh(["git", "--git-dir", str(gd), "ls-tree", "--name-only", "HEAD"]).stdout.split("\n")
    if "Dockerfile" not in ls:
        return "no_dockerfile", None
    sha = sh(["git", "--git-dir", str(gd), "rev-parse", "HEAD"]).stdout.strip()
    with open(log, "wb") as fl:
        arch = subprocess.Popen(["git", "--git-dir", str(gd), "archive", "HEAD"], stdout=subprocess.PIPE, stderr=fl)
        b = subprocess.Popen(["docker", "build", "--platform", "linux/amd64", "-t", tag, "-"], stdin=arch.stdout,
                             stdout=fl, stderr=subprocess.STDOUT)
        arch.stdout.close()
        try:
            rc = b.wait(timeout=BUILD_TIMEOUT)
        except subprocess.TimeoutExpired:
            b.kill()
            return "build_timeout", sha
    return ("ok" if rc == 0 else "build_failed"), sha


def prune(image=None):
    if image:
        sh(["docker", "rmi", "-f", image])
    sh(["docker", "builder", "prune", "-f"])
    sh(["docker", "image", "prune", "-f"])


def score(image, d, entry, commit):
    p = sh([sys.executable, str(HERE / "score_entry.py"), image, str(d), "1", "--entry", entry, "--commit", commit or ""],
           timeout=7200)
    print(f"{entry} {d.name}: {p.stdout.strip()[-300:] or p.stderr.strip()[-300:]}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", type=int, default=0, help="only rivals 1..N of rivals.txt")
    ap.add_argument("--skip", type=int, default=0, help="skip rivals 1..N (with --first: a chunk)")
    ap.add_argument("--sets", default="sample,dev300,hold300,odd200,traps150")
    ap.add_argument("--image", default="")
    ap.add_argument("--name", default="ours")
    ap.add_argument("--commit", default="")
    ap.add_argument("--keep", type=int, default=0, help="keep images of the first N rivals")
    a = ap.parse_args()
    vcv.ensure_dirs()
    sets = a.sets.split(",")
    if a.image:  # our own image: scored on every available set
        rows = load_rows()
        for name in sets:
            info = set_info(name)
            if info and needs(a.name, name, info[1], rows, a.commit):
                score(a.image, info[0], a.name, a.commit)
        return
    entries = []
    for i, repo in enumerate(rivals_list(), 1):
        if a.first and i > a.first:
            break
        if i <= a.skip:
            continue
        slug = repo.replace("/", "__")
        entries.append((slug, f"rival-rwx-{i}", RIV / f"{slug}.git", i))
    for entry, image, gd, idx in entries:
        if (DATA / "STOP").exists():
            return
        rows = load_rows()
        need = []
        for name in sets:
            info = set_info(name)
            if info and needs(entry, name, info[1], rows):
                need.append(info[0])
        if not need or not gd.exists():
            continue
        st_path = DATA / "builds" / f"{entry}.json"
        st = json.loads(st_path.read_text()) if st_path.exists() else {}
        if st.get("status") in ("no_dockerfile", "build_failed"):
            continue
        have = sh(["docker", "image", "inspect", image, "--format", "{{.Id}}"]).returncode == 0
        if not have:
            if "scanned" not in st:
                scan_notes(gd, entry)
                st["scanned"] = True
            with slots.slot(1):
                status, sha = build(gd, image, entry)
            st.update({"status": status, "commit": sha, "built": time.strftime("%F %T")})
            st_path.write_text(json.dumps(st))
            print(f"{entry}: build {status}", flush=True)
            if status != "ok":
                with open(DATA / "results.jsonl", "a") as f:
                    for d in need:
                        f.write(json.dumps({"entry": entry, "host": vcv.HOST, "commit": sha, "set": d.name,
                                            "n": 0, "reject": status}) + "\n")
                prune()
                continue
        for d in need:
            score(image, d, entry, st.get("commit") or "")
        if idx > a.keep:
            prune(image)


if __name__ == "__main__":
    main()
