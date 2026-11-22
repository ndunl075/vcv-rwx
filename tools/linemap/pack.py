"""Pack the line traces into maps/lines.json.gz (format: selector/linemap.py).

    pack.py <tests.json> <out> <trace dir>      (cold-*.jsonl and, if present, session/)
"""
import glob
import gzip
import json
import os
import sys


def read_session(d, idx, pool_users):
    """Whole-suite trace -> {(file, line): bitmask of tests}.  A line run by a pool worker counts for
    every pool-using test that ran while that worker lived (its lines cannot be told apart by test)."""
    out = {}
    main = None
    procs = []
    for p in glob.glob(os.path.join(d, "*.cov")):
        marks, files, recs = [], {}, []
        start = end = None
        cur = None
        with open(p, "rb") as f:
            for raw in f:
                rec = raw.decode("utf-8", "surrogateescape").rstrip("\n")
                c = rec[:1]
                if c == "F":
                    _, n, path = rec.split(" ", 2)
                    files[n] = path
                elif c == "T":
                    _, t, nid = rec.split(" ", 2)
                    cur = nid
                    marks.append((float(t), nid))
                elif c == "S":
                    start = float(rec[2:])
                elif c == "X":
                    end = float(rec[2:])
                elif c.isdigit():
                    n, ln = rec.split(" ")
                    recs.append((cur, files[n], int(ln)))
        procs.append((marks, recs, start, end if end is not None else os.path.getmtime(p)))
        if marks:
            main = procs[-1]
    if main is None:
        return out
    marks = main[0]
    for nid, fn, ln in main[1]:
        if nid in idx:
            out[(fn, ln)] = out.get((fn, ln), 0) | (1 << idx[nid])
    for pr in procs:
        if pr is main:
            continue
        _, recs, start, end = pr
        mask = 0
        for k, (t, nid) in enumerate(marks):
            t_end = marks[k + 1][0] if k + 1 < len(marks) else float("inf")
            if t <= end + 1 and t_end >= start - 1 and nid in idx and idx[nid] in pool_users:
                mask |= 1 << idx[nid]
        for _, fn, ln in recs:
            out[(fn, ln)] = out.get((fn, ln), 0) | mask
    return out


def main():
    tests = json.load(open(sys.argv[1]))["tests"]
    ids = [t["id"] for t in tests]
    idx = {i: n for n, i in enumerate(ids)}
    d = sys.argv[3]
    cold = {}     # (file, line) -> bitmask
    reads = {}    # path opened by Python code -> bitmask
    cpu = [None] * len(ids)
    unknown, pool_users, seen = [], set(), set()
    for p in sorted(glob.glob(os.path.join(d, "cold-*.jsonl"))):
        for line in open(p):
            r = json.loads(line)
            n = idx[r["id"]]
            seen.add(n)
            if r["rc"] != 0:
                unknown.append(n)
            cpu[n] = r["cpu"]
            if r["children"] >= 2:  # one child is the resource tracker or a plain subprocess
                pool_users.add(n)
            bit = 1 << n
            for fn, ls in r["lines"].items():
                for ln in ls:
                    k = (fn, ln)
                    cold[k] = cold.get(k, 0) | bit
            for o in r["opens"]:
                if not o.endswith(".pyc"):
                    reads[o] = reads.get(o, 0) | bit
    unknown += [n for n in range(len(ids)) if n not in seen]
    sess = read_session(os.path.join(d, "session"), idx, pool_users)
    sets, sid = [], {}

    def S(mask):
        s = sid.get(mask)
        if s is None:
            s = sid[mask] = len(sets)
            sets.append([i for i in range(len(ids)) if mask >> i & 1])
        return s

    lines = {}
    extra = 0
    for (fn, ln) in sorted(set(cold) | set(sess)):
        c = cold.get((fn, ln), 0)
        e = sess.get((fn, ln), 0) & ~c   # tests that reach the line only inside the whole-suite session
        extra += bool(e)
        rows = lines.setdefault(fn, [])
        cs, es = S(c), (S(e) if e else -1)
        if rows and rows[-1][1] == ln - 1 and rows[-1][2] == cs and rows[-1][3] == es:
            rows[-1][1] = ln
        else:
            rows.append([ln, ln, cs, es])
    out = {"ids": ids, "sets": sets, "lines": lines, "reads": {p: S(m) for p, m in sorted(reads.items())},
           "cpu": cpu, "unknown": sorted(set(unknown)), "pool": sorted(pool_users), "session": bool(sess)}
    with gzip.GzipFile(sys.argv[2], "wb", mtime=0) as f:
        f.write(json.dumps(out, separators=(",", ":")).encode())
    print("tests %d traced %d unknown %d files %d sets %d lines with session-only tests %d pool users %d" % (
        len(ids), len(seen), len(set(unknown)), len(lines), len(sets), extra, len(pool_users)), file=sys.stderr)


if __name__ == "__main__":
    main()
