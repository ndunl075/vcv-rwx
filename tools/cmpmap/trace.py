"""Comparison profiles of the pinned commit, inside the labeling image (see sitecustomize.py).

    trace.py cold <clean checkout> <tests.json> <out dir> <worker> <workers> [ids file | -] [seeds, default 1,2]
        every test alone in a fresh pytest process, once per hash seed; writes <out dir>/cold-<worker>.jsonl,
        one line per test:  {"id", "rc": [..], "procs": [..], "drops": {site: [op, [alt, ..]]},
                             "nondet": [site, ..], "untraced": bool, "spawn": [..]}
        and <out dir>/executed-<worker>.json: {site: op} of every comparison executed with an alternative alive
    trace.py session <clean checkout> <tests.json> <out dir> [seed]
        the whole suite in one pytest process; keeps the raw record files in <out dir>/session-<seed>/
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = "/tmp/cmpcache"


def prepare(clean, root):
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(clean, root, symlinks=True, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    env = dict(os.environ, PYTHONPATH=root, SKIP_INTEGRATION="0")
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    # pytest's rewritten bytecode of the test modules only: sqlglot is compiled from rewritten sources
    subprocess.run([sys.executable, "-m", "pytest", "tests", "--collect-only", "-q", "-p", "no:cacheprovider"],
                   cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for top in ("sqlglot", "sqlglotc"):
        for d, dirs, _ in os.walk(os.path.join(root, top)):
            if "__pycache__" in dirs:
                shutil.rmtree(os.path.join(d, "__pycache__"))
    os.makedirs(CACHE, exist_ok=True)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = HERE + os.pathsep + root
    env["RWX_CMP_ROOT"] = root
    env["RWX_CMP_CACHE"] = CACHE
    return env


def read_records(d):
    """-> drops {site: (op, set(alt))}, counts {site: (op, n)}, number of processes, process-start notes,
    failed tests (whole-suite runs)"""
    drops, counts, procs, spawns, failed = {}, {}, 0, [], []
    for name in sorted(os.listdir(d)):
        with open(os.path.join(d, name), "rb") as f:
            for raw in f:
                rec = raw.decode("utf-8", "replace").rstrip("\n").split("\t")
                k = rec[0]
                if k == "D":
                    _, pid, imp, cur, site, op, alt = rec
                    drops.setdefault(site, (op, set()))[1].add(alt)
                elif k == "C":
                    _, pid, cur, site, op, n = rec
                    o, c = counts.get(site, (op, 0))
                    counts[site] = (op, c + int(n))
                elif k == "S":
                    procs += 1
                elif k == "A":
                    spawns.append(rec[2])
                elif k == "F":
                    failed.append(rec[2])
    return drops, counts, procs, spawns, failed


def cold(clean, tests_json, out_dir, wi, wn, only=None, seeds=("1", "2")):
    root = "/c%d" % wi
    env = prepare(clean, root)
    tests = json.load(open(tests_json))["tests"]
    mine = [t for t in tests if t["number"] % wn == wi and (only is None or t["id"] in only)]
    executed = {}
    d = "/tmp/cmp%d" % wi
    with open(os.path.join(out_dir, "cold-%d.jsonl" % wi), "a") as f:
        for k, t in enumerate(mine):
            runs = []
            for seed in seeds:
                shutil.rmtree(d, ignore_errors=True)
                os.makedirs(d)
                env["RWX_CMP_DIR"] = d
                env["PYTHONHASHSEED"] = seed
                try:
                    p = subprocess.run([sys.executable, "-m", "pytest", t["id"], "-q", "-p", "no:cacheprovider"], cwd=root,
                                       env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1800)
                    rc, tail = p.returncode, p.stdout[-300:].decode("utf-8", "replace")
                except subprocess.TimeoutExpired:
                    rc, tail = -9, "timeout"
                runs.append((rc, tail) + read_records(d))
            drops, nondet = {}, set()
            for r in runs:
                for site, (op, alts) in r[2].items():
                    drops.setdefault(site, (op, set()))[1].update(alts)
                    executed[site] = op
                for site, (op, n) in r[3].items():
                    executed[site] = op
            first = runs[0]
            for r in runs[1:]:
                for site in set(first[3]) | set(r[3]):
                    if first[3].get(site, (0, 0))[1] != r[3].get(site, (0, 0))[1]:
                        nondet.add(site)
                for site in set(first[2]) | set(r[2]):
                    if first[2].get(site, (0, set()))[1] != r[2].get(site, (0, set()))[1]:
                        nondet.add(site)
            rec = {"id": t["id"], "rc": [r[0] for r in runs], "procs": [r[4] for r in runs],
                   "drops": {s: [op, sorted(a)] for s, (op, a) in drops.items()}, "nondet": sorted(nondet),
                   "untraced": any("UNTRACED" in x for r in runs for x in r[5]),
                   "spawn": sorted({x.split(" ")[0] for r in runs for x in r[5]})}
            if any(r[0] != 0 for r in runs):
                rec["tail"] = [r[1][-200:] for r in runs if r[0] != 0][0]
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            f.flush()
            if k % 20 == 0:
                print("worker %d: %d/%d %s" % (wi, k, len(mine), t["id"]), file=sys.stderr, flush=True)
                with open(os.path.join(out_dir, "executed-%d.json" % wi), "w") as g:
                    json.dump(executed, g, separators=(",", ":"))
    with open(os.path.join(out_dir, "executed-%d.json" % wi), "w") as g:
        json.dump(executed, g, separators=(",", ":"))
    with open(os.path.join(out_dir, "done-%d" % wi), "w") as g:
        g.write("done\n")


def session(clean, tests_json, out_dir, seed="3"):
    root = "/s"
    env = prepare(clean, root)
    rec = os.path.join(out_dir, "session-%s" % seed)
    shutil.rmtree(rec, ignore_errors=True)
    os.makedirs(rec)
    env["RWX_CMP_DIR"] = rec
    env["PYTHONHASHSEED"] = seed
    p = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "-p", "rwx_cmpmarks"],
                       cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    with open(os.path.join(rec, "pytest.txt"), "wb") as f:
        f.write(p.stdout[-3000:])
    with open(os.path.join(rec, "rc.txt"), "w") as f:
        f.write("%d\n" % p.returncode)
    print("session rc", p.returncode, p.stdout[-200:].decode("utf-8", "replace"), file=sys.stderr)


if __name__ == "__main__":
    if sys.argv[1] == "cold":
        only = set(open(sys.argv[7]).read().split()) if len(sys.argv) > 7 and sys.argv[7] != "-" else None
        seeds = tuple(sys.argv[8].split(",")) if len(sys.argv) > 8 else ("1", "2")
        cold(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), int(sys.argv[6]), only, seeds)
    else:
        session(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5] if len(sys.argv) > 5 else "3")
