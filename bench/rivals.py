#!/usr/bin/env python3
"""Build and score rival entries (and our own image) on the labeled sets. Resumable.

  rivals.py [--first N] [--sets sample,dev300,hold300,odd200,traps150] [--image IMG --name NAME]

Rival repos are bare copies in ~/Developer/vcv-rivals/rwx/<owner>__<repo>.git, in list.txt order
(most active first). They are built straight from `git archive` (never unpacked on the host) and
run only inside the locked-down container (score_entry.py). A set is used once its DONE file exists
(sample always). State: results.jsonl (entry, set) pairs already scored; builds/<slug>.json for builds.
Rival text is data: only path:line of suspicious matches is stored (rival-notes.jsonl), never acted on.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parent / "kit"
DATA = Path.home() / "Developer/vcv-data/rwx"
RIV = Path.home() / "Developer/vcv-rivals/rwx"
BUILD_TIMEOUT = 1800  # the machine is shared and heavily loaded; the contest gives 10 min on 4 free cores
SCAN = re.compile(r"(AI (agent|tool|assistant|model)s?\b|language model|\bLLM\b|\bClaude\b|\bChatGPT\b|"
                  r"do not (build|run|benchmark|execute)|ignore (all |any )?(previous|prior|above)|"
                  r"as an AI\b|if you are an? (ai|assistant|agent))", re.I)


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def set_dirs(names):
    out = []
    for n in names:
        d = KIT / "sample" if n == "sample" else DATA / n
        if n == "sample" or (d / "DONE").exists():
            out.append((n, d))
    return out


def done_pairs():
    pairs = set()
    f = DATA / "results.jsonl"
    if f.exists():
        for line in f.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("reject") != "infra":
                pairs.add((r["entry"], r["set"]))
    return pairs


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
            f.write(json.dumps({"entry": slug, "where": hits[:10], "note": "text aimed at AI tools (data, not followed)"}) + "\n")


def build(gd: Path, tag: str, slug: str):
    (DATA / "builds").mkdir(parents=True, exist_ok=True)
    log = DATA / "builds" / f"{slug}.log"
    ls = sh(["git", "--git-dir", str(gd), "ls-tree", "--name-only", "HEAD"]).stdout.split("\n")
    if "Dockerfile" not in ls:
        return "no_dockerfile", None
    sha = sh(["git", "--git-dir", str(gd), "rev-parse", "HEAD"]).stdout.strip()
    t0 = time.time()
    with open(log, "wb") as fl:
        arch = subprocess.Popen(["git", "--git-dir", str(gd), "archive", "HEAD"], stdout=subprocess.PIPE, stderr=fl)
        b = subprocess.Popen(["docker", "build", "--platform", "linux/amd64", "-t", tag, "-"], stdin=arch.stdout,
                             stdout=fl, stderr=subprocess.STDOUT)
        arch.stdout.close()
        try:
            rc = b.wait(timeout=BUILD_TIMEOUT)
        except subprocess.TimeoutExpired:
            b.kill()
            sh(["pkill", "-f", f"docker build .*{tag}"])
            return "build_timeout", sha
    if rc != 0:
        return "build_failed", sha
    return "ok", sha


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", type=int, default=0)
    ap.add_argument("--sets", default="sample,dev300,hold300,odd200,traps150")
    ap.add_argument("--image", default="")
    ap.add_argument("--name", default="ours")
    ap.add_argument("--keep", type=int, default=30, help="keep images of the first N rivals")
    ap.add_argument("--commit", default="")
    a = ap.parse_args()
    sets = a.sets.split(",")
    todo_entries = []
    if a.image:
        todo_entries.append((a.name, a.image, a.commit, None, 0))
    else:
        lst = [l.strip() for l in (RIV / "list.txt").read_text().splitlines() if l.strip()]
        for i, repo in enumerate(lst, 1):
            if a.first and i > a.first:
                break
            slug = repo.replace("/", "__")
            todo_entries.append((slug, f"rival-rwx-{i}", "", RIV / f"{slug}.git", i))
    (DATA / "builds").mkdir(parents=True, exist_ok=True)
    for entry, image, commit, gd, idx in todo_entries:
        avail = set_dirs(sets)
        done = done_pairs()
        need = [(n, d) for n, d in avail if (entry, n) not in done]
        if not need:
            continue
        st_path = DATA / "builds" / f"{entry}.json"
        st = json.loads(st_path.read_text()) if st_path.exists() else {}
        if gd is not None:
            if not gd.exists():
                continue
            if st.get("status") in ("no_dockerfile", "build_failed"):
                continue
            have = sh(["docker", "image", "inspect", image, "--format", "{{.Id}}"]).returncode == 0
            if not have:
                if "scanned" not in st:
                    scan_notes(gd, entry)
                    st["scanned"] = True
                status, sha = build(gd, image, entry)
                st.update({"status": status, "commit": sha, "built": time.strftime("%F %T")})
                st_path.write_text(json.dumps(st))
                print(f"{entry}: build {status}", flush=True)
                if status != "ok":
                    with open(DATA / "results.jsonl", "a") as f:
                        for n, d in need:
                            f.write(json.dumps({"entry": entry, "commit": sha, "set": n, "reject": status}) + "\n")
                    continue
            commit = st.get("commit") or ""
        for n, d in need:
            p = sh([str(HERE / "score_entry.sh"), image, str(d), "1", "--entry", entry, "--commit", commit or "", ],
                   timeout=3600)
            print(f"{entry} {n}: {p.stdout.strip()[-300:]}", flush=True)
        if gd is not None and idx > a.keep:
            sh(["docker", "rmi", "-f", image])


if __name__ == "__main__":
    main()
