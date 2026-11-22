"""Pack the comparison profiles into maps/cmp.json.gz (format: selector/cmpproof.py).

    pack.py <out> <cold profile json> [<session profile json>]
A site keeps an operator only if it held in every profile given; a test missing from the session
profile, or failing in any run, is left out (no proof for it).
"""
import gzip
import json
import sys


def main():
    profiles = [json.load(open(p)) for p in sys.argv[2:]]
    tests = {}
    for tid, first in profiles[0].items():
        if any(tid not in p or any(p[tid]["rc"]) for p in profiles):
            continue
        sites = {}
        for site, (op, mask, _) in first["sites"].items():
            for p in profiles[1:]:
                other = p[tid]["sites"].get(site)
                if other is not None:
                    mask &= other[1]
            if mask:
                sites[site] = [op, mask]
        # a site that only a later profile executes has no entry: no proof there
        tests[tid] = sites
    with gzip.GzipFile(sys.argv[1], "wb", mtime=0) as f:
        f.write(json.dumps({"tests": tests, "profiles": len(profiles)}, separators=(",", ":"), sort_keys=True).encode())
    print({t: len(s) for t, s in tests.items()}, file=sys.stderr)


if __name__ == "__main__":
    main()
