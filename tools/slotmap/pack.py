"""Pack the slot-read traces into maps/slots.json.gz (format: selector/slotmap.py).

    pack.py <tests.json> <out> <trace dir>     (cold-*.jsonl.gz, tracked.txt and, if present, session/)
"""
import glob
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "linemap"))
from pack import encode  # noqa: E402


def main():
    tests = json.load(open(sys.argv[1]))["tests"]
    ids = [t["id"] for t in tests]
    idx = {i: n for n, i in enumerate(ids)}
    d = sys.argv[3]
    reads = {}
    unknown, seen = set(), set()
    for p in sorted(glob.glob(os.path.join(d, "cold-*.jsonl.gz"))):
        for line in gzip.open(p, "rt"):
            r = json.loads(line)
            n = idx[r["id"]]
            seen.add(n)
            if r["rc"] != 0:
                unknown.add(n)
            for k in r["reads"]:
                reads[k] = reads.get(k, 0) | (1 << n)
    unknown |= set(range(len(ids))) - seen
    # whole-suite session: reads after a test mark count for that test; a pool worker's reads before its
    # first tagged task count for nobody (start-up imports)
    session = False
    for p in glob.glob(os.path.join(d, "session", "*.slots")):
        cur = None
        with open(p, "rb") as f:
            for raw in f:
                rec = raw.decode("utf-8", "surrogateescape").rstrip("\n")
                if rec.startswith("T\t"):
                    cur = idx.get(rec[2:])
                    session = True
                elif cur is not None and rec:
                    reads[rec] = reads.get(rec, 0) | (1 << cur)
    tracked = [k for k in open(os.path.join(d, "tracked.txt")).read().split("\n") if k]
    sets, sid = [], {}

    def S(mask):
        s = sid.get(mask)
        if s is None:
            s = sid[mask] = len(sets)
            sets.append(encode([i for i in range(len(ids)) if mask >> i & 1]))
        return s

    slots = {k: S(m) for k, m in sorted(reads.items())}
    for k in tracked:
        slots.setdefault(k, -1)   # seen by the tracer, read by no test
    out = {"ids": ids, "sets": sets, "slots": slots, "unknown": sorted(unknown), "session": session}
    with gzip.GzipFile(sys.argv[2], "wb", mtime=0) as f:
        f.write(json.dumps(out, separators=(",", ":")).encode())
    print("tests %d traced %d unknown %d slots read %d tracked %d sets %d session %s" % (
        len(ids), len(seen), len(unknown), len(reads), len(tracked), len(sets), session), file=sys.stderr)


if __name__ == "__main__":
    main()
