"""Turn the by-test output of build.py into the compact by-site layout (see selector/cmpproof.py).

usage: by_site.py <by-test map.json.gz> <targets file> <out.json.gz>
Every target without a profile (it failed in a recording run) is listed as skipped."""
import gzip
import json
import sys


def encode(xs):
    xs = sorted(xs)
    parts, i = [], 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[j] + 1:
            j += 1
        parts.append(str(xs[i]) if i == j else "%d-%d" % (xs[i], xs[j]))
        i = j + 1
    return ",".join(parts)


def main():
    src, targets, out = sys.argv[1:4]
    with gzip.open(src, "rt") as f:
        tests = json.load(f)["tests"]
    with open(targets) as f:
        ids = [line.strip() for line in f if line.strip()]
    sites = {}
    for i, t in enumerate(ids):
        for site, (op, mask) in tests.get(t, {}).items():
            rec = sites.setdefault(site, [op, {}])
            if rec[0] != op:
                raise SystemExit("two operators at %s" % site)
            rec[1].setdefault(mask, []).append(i)
    for rec in sites.values():
        rec[1] = {str(mask): encode(who) for mask, who in sorted(rec[1].items())}
    skipped = [i for i, t in enumerate(ids) if t not in tests]
    with gzip.GzipFile(out, "wb", mtime=0) as f:
        f.write(json.dumps({"ids": ids, "skipped": skipped, "sites": sites}, separators=(",", ":"), sort_keys=True).encode())
    print("%d tests, %d skipped, %d sites" % (len(ids), len(skipped), len(sites)))


if __name__ == "__main__":
    main()
