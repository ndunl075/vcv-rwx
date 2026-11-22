"""Pack the traces written by trace.py into maps/files.json.gz (format: selector/filemap.py).

    pack.py <tests.json> <out> <part.jsonl>...
"""
import gzip
import json
import statistics
import sys


def main():
    tests = json.load(open(sys.argv[1]))["tests"]
    ids = [t["id"] for t in tests]
    idx = {i: n for n, i in enumerate(ids)}
    recs = {}
    for p in sys.argv[3:]:
        for line in open(p):
            r = json.loads(line)
            recs[r["id"]] = r  # a later part overrides an earlier one
    files, dirs = {}, {}
    cpu = [None] * len(ids)
    unknown = []
    for i, n in idx.items():
        r = recs.get(i)
        if r is None or r["rc"] != 0 or r["overflow"]:
            unknown.append(n)
            if r is None:
                continue
        cpu[n] = r["cpu"]
        for f in r["files"]:
            files.setdefault(f, []).append(n)
        for f in r["dirs"]:
            dirs.setdefault(f, []).append(n)
    sets, sid = [], {}

    def S(members):
        t = tuple(sorted(members))
        if t not in sid:
            sid[t] = len(sets)
            sets.append(list(t))
        return sid[t]

    known = [c for c in cpu if c is not None]
    out = {
        "ids": ids,
        "sets": sets,
        "files": {f: S(v) for f, v in sorted(files.items())},
        "dirs": {f: S(v) for f, v in sorted(dirs.items())},
        "cpu": cpu,
        "base_cpu": round(statistics.quantiles(known, n=10)[0], 3) if len(known) > 10 else 0.0,
        "unknown": sorted(unknown),
    }
    with gzip.GzipFile(sys.argv[2], "wb", mtime=0) as f:
        f.write(json.dumps(out, separators=(",", ":")).encode())
    print("tests %d, traced %d, unknown %d, files %d, dirs %d, sets %d, base cpu %.3f" % (
        len(ids), len(recs), len(unknown), len(files), len(dirs), len(sets), out["base_cpu"]), file=sys.stderr)


if __name__ == "__main__":
    main()
