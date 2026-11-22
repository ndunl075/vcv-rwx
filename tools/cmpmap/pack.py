"""Pack the comparison profiles into maps/cmp.json.gz (what it holds and how it is read: selector/cmpproof.py).

    pack.py [--scope file|all] <tests.json> <out> <profile dir> [<profile dir> ...]

A profile dir holds cold-*.jsonl / executed-*.json (tools/cmpmap/trace.py cold) and session-<seed>/ (trace.py session).

--scope says whom a disagreement in the whole-suite session is charged to (the test that was running may have changed
state, a registry, a cache or a session fixture, that another test consumes):
    file   every test of the file of the running test (class and module fixtures are shared by the file)
    all    the (site, alternative) is unproven for every test
Anything that happened while a module was being imported or outside a test is charged to every test in both.
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
    argv = sys.argv[1:]
    scope = "file"
    if argv and argv[0] == "--scope":
        scope = argv[1]
        argv = argv[2:]
    if scope not in ("file", "all"):
        raise SystemExit("--scope file|all")
    tests = json.load(open(argv[0]))["tests"]
    out = argv[1]
    dirs = argv[2:]
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
    session_runs = []                # per session: {"counts": {(test, site): n}, "drops": {(test, site): {alt}}}
    raised = {}                      # site -> op: the original comparison raised in a session
    cold_nondet = []                 # (test, site): the operator may only be known after the sessions are read
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
            cold_nondet.append((t, site))
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
            run = {"counts": {}, "drops": {}, "sdir": sdir, "rc": rc}
            session_runs.append(run)
            for p in sorted(glob.glob(os.path.join(sdir, "*.cmp"))):
                with open(p, "rb") as fh:
                    for raw in fh:
                        rec = raw.decode("utf-8", "replace").rstrip("\n").split("\t")
                        if rec[0] == "P":
                            mains.add(rec[1])
                        elif rec[0] == "C":
                            _, pid, cur, site, op, n = rec
                            executed[site] = op
                            run["counts"][(cur, site)] = run["counts"].get((cur, site), 0) + int(n)
                        elif rec[0] in ("F", "A", "D", "R"):
                            recs.append(rec)
            for rec in recs:
                k = rec[0]
                if k == "F":
                    t = idx.get(rec[2])
                    if t is not None:
                        bad.add(t)
                        notes["bad"].setdefault("failed in the session", []).append(rec[2])
                elif k == "A" and "UNTRACED" in rec[2]:
                    # which test started it is not recorded: nothing it runs is in the profile
                    raise SystemExit("session: a process was started that does not trace (%s): no sound map" % rec[2])
                elif k == "R":
                    _, pid, imp, cur, site, op = rec
                    executed[site] = op
                    raised[site] = op
                elif k == "D":
                    _, pid, imp, cur, site, op, alt = rec
                    executed[site] = op
                    run["drops"].setdefault((cur, site), set()).add(alt)
                    cfile = cur.split("::")[0]
                    if scope == "all":
                        who = ALL
                    elif pid in mains:
                        who = ALL if (imp == "1" or cur == "-") else by_file.get(cfile, ALL)
                    elif cur != "-" and cfile in by_file and spawn:
                        who = set(spawn) | by_file[cfile]
                    elif cur == "-" and spawn:
                        who = set(spawn)
                    else:
                        who = ALL
                    add(site, alt, who)
    if notes["sessions"] == 0:
        raise SystemExit("no finished session profile: the record would not hold in the whole-suite session")

    # a site where the original comparison raised: the alternative could have answered, so it is unproven for every test
    for site, op in raised.items():
        for alt in ALTS.get(op, ()):
            add(site, alt, ALL)
    notes["raised"] = len(raised)

    # whole-suite runs under different hash seeds must execute every comparison equally often, in every test, and
    # the alternatives must disagree in the same tests: any difference makes the site unproven for every test
    # (the disagreements of both sessions are charged above, so the record is their union)
    nondet = set()
    for other in session_runs[1:]:
        a, b = session_runs[0], other
        for key in set(a["counts"]) | set(b["counts"]):
            if a["counts"].get(key, 0) != b["counts"].get(key, 0):
                nondet.add(key[1])
        for key in set(a["drops"]) | set(b["drops"]):
            if a["drops"].get(key, set()) != b["drops"].get(key, set()):
                nondet.add(key[1])
        tot = [{}, {}]
        for i, run in enumerate((a, b)):
            for (cur, site), n in run["counts"].items():
                tot[i][site] = tot[i].get(site, 0) + n
        for site in set(tot[0]) | set(tot[1]):
            if tot[0].get(site, 0) != tot[1].get(site, 0):
                nondet.add(site)
    for site in nondet:
        for alt in ALTS.get(executed.get(site), ()):
            add(site, alt, ALL)
    notes["session_nondet"] = len(nondet)

    # a nondeterministic site of a cold run: its operator is known by now (every recorded site has one); a site with no
    # operator is not in the map at all, which means unproven
    unknown = 0
    for t, site in cold_nondet:
        op = executed.get(site)
        if op not in ALTS:
            unknown += 1
            continue
        for alt in ALTS[op]:
            add(site, alt, {t})
    notes["cold_nondet_unknown"] = unknown

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
    print("  scope %s; spawn tests: %d; sites whose counts or disagreements differ between sessions: %d; sites where the original raised: %d; "
          "cold nondeterministic sites with no known operator (absent from the map): %d"
          % (scope, len(spawn), notes["session_nondet"], notes["raised"], notes["cold_nondet_unknown"]), file=sys.stderr)


if __name__ == "__main__":
    main()
