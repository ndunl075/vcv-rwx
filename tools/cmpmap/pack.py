"""Pack the comparison profiles into maps/cmp.json.gz (what it holds and how it is read: selector/cmpproof.py).

    pack.py <tests.json> <out> <profile dir> [<profile dir> ...]

A profile dir holds cold-*.jsonl / executed-*.json (tools/cmpmap/trace.py cold) and session-<seed>/ (trace.py session).
"""
import glob
import gzip
import json
import os
import sys

ALTS = {"Eq": ["Is"], "NotEq": ["IsNot"], "Is": ["Eq"], "IsNot": ["NotEq"],
        "Lt": ["LtE", "Gt"], "LtE": ["Lt", "GtE"], "Gt": ["GtE", "Lt"], "GtE": ["Gt", "LtE"]}
ALL = "all"


def main():
    tests = json.load(open(sys.argv[1]))["tests"]
    out = sys.argv[2]
    dirs = sys.argv[3:]
    ids = [t["id"] for t in tests]
    idx = {i: n for n, i in enumerate(ids)}
    by_file = {}
    for i, n in idx.items():
        by_file.setdefault(i.split("::")[0], set()).add(n)

    bad = set()
    notes = {"cold": 0, "sessions": 0, "bad": {}}
    executed = {}                    # site -> op
    unproven = {}                    # (site, alt) -> set of tests, or ALL
    spawn = set()                    # tests that started processes

    def add(site, alt, who):
        cur = unproven.get((site, alt))
        if cur == ALL:
            return
        if who == ALL:
            unproven[(site, alt)] = ALL
        else:
            if cur is None:
                cur = unproven[(site, alt)] = set()
            cur |= who

    seen = set()
    cold_recs = []
    for d in dirs:
        for p in sorted(glob.glob(os.path.join(d, "executed-*.json"))):
            for site, op in json.load(open(p)).items():
                executed[site] = op
        for p in sorted(glob.glob(os.path.join(d, "cold-*.jsonl"))):
            for line in open(p):
                cold_recs.append(json.loads(line))
    for r in cold_recs:
        t = idx.get(r["id"])
        if t is None:
            continue
        seen.add(t)
        notes["cold"] += 1
        why = None
        if any(rc != 0 for rc in r["rc"]):
            why = "failed in a profile run"
        elif r.get("untraced"):
            why = "started a process that does not trace"
        if why:
            bad.add(t)
            notes["bad"].setdefault(why, []).append(r["id"])
        if any(n > 1 for n in r["procs"]):
            spawn.add(t)
        for site, (op, alts) in r["drops"].items():
            executed[site] = op
            for alt in alts:
                add(site, alt, {t})
        for site in r["nondet"]:
            op = executed.get(site)
            for alt in ALTS.get(op, ()):
                add(site, alt, {t})
    for n in range(len(ids)):
        if n not in seen:
            bad.add(n)
            notes["bad"].setdefault("no cold profile", []).append(ids[n])

    for d in dirs:
        for sdir in sorted(glob.glob(os.path.join(d, "session-*"))):
            rc = open(os.path.join(sdir, "rc.txt")).read().strip() if os.path.exists(os.path.join(sdir, "rc.txt")) else None
            if rc is None:
                continue          # unfinished
            notes["sessions"] += 1
            mains, recs = set(), []
            for p in sorted(glob.glob(os.path.join(sdir, "*.cmp"))):
                for raw in open(p, "rb"):
                    rec = raw.decode("utf-8", "replace").rstrip("\n").split("\t")
                    if rec[0] == "P":
                        mains.add(rec[1])
                    recs.append(rec)
            for rec in recs:
                k = rec[0]
                if k == "F":
                    t = idx.get(rec[2])
                    if t is not None:
                        bad.add(t)
                        notes["bad"].setdefault("failed in the session", []).append(rec[2])
                elif k == "C":
                    executed[rec[2]] = rec[3]
                elif k == "A" and "UNTRACED" in rec[2]:
                    print("session: process start that does not trace:", rec[2], file=sys.stderr)
                    notes.setdefault("session_untraced", []).append(rec[2])
                elif k == "D":
                    _, pid, imp, cur, site, op, alt = rec
                    executed[site] = op
                    if pid in mains:
                        if imp == "1" or cur == "-":
                            who = ALL
                        else:
                            who = by_file.get(cur.split("::")[0], ALL)
                    else:
                        who = set(spawn) | by_file.get(cur.split("::")[0], set()) if cur != "-" else set(spawn)
                        if not spawn:
                            who = ALL
                    add(site, alt, who)
    if notes["sessions"] == 0:
        raise SystemExit("no finished session profile: the record would not hold in the whole-suite session")

    sets, sid = [], {}

    def S(members):
        if members == ALL:
            return -1
        key = tuple(sorted(members))
        if key not in sid:
            sid[key] = len(sets)
            sets.append(list(key))
        return sid[key]

    sites = {}
    for site, op in sorted(executed.items()):
        if op not in ALTS:
            continue
        path, _, key = site.partition(":")
        sites.setdefault(path, {})[key] = [op] + [S(unproven.get((site, alt), set())) for alt in ALTS[op]]
    data = {"ids": ids, "bad": sorted(bad), "sets": sets, "sites": sites}
    with gzip.GzipFile(out, "wb", mtime=0, compresslevel=9) as f:
        f.write(json.dumps(data, separators=(",", ":"), sort_keys=True).encode())
    ns = sum(len(v) for v in sites.values())
    allset = sum(1 for v in sites.values() for e in v.values() for s in e[1:] if s == -1)
    print("wrote %s: %d bytes, %d sites, %d sets, %d bad tests, %d cold profiles, %d sessions, %d (site, alt) unproven for all"
          % (out, os.path.getsize(out), ns, len(sets), len(bad), notes["cold"], notes["sessions"], allset), file=sys.stderr)
    for why, lst in notes["bad"].items():
        print("  bad (%s): %d  %s" % (why, len(lst), sorted(lst)[:4]), file=sys.stderr)
    print("  spawn tests: %d" % len(spawn), file=sys.stderr)


if __name__ == "__main__":
    main()
