"""Turn .work/cold_raw.json.gz (output of build_maps.py) into the committed maps in maps/.

maps/map.json.gz:
  ids      test ids, index = position in the list
  sets     deduplicated sorted lists of test indexes
  lines    {file: [[lo, hi, set], ...]}      consecutive executed lines with the same set of tests
  codes    {file: {"firstlineno:name": set}} code objects started
  reads    {path: set}                       files a test opens (not through the import system)
  listed   {dir: set}                        directories a test lists
  cold     {"cpu": [...], "failed": [idx...]}  per-test CPU seconds in the cold run, cold failures
"""
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main():
    raw = json.load(gzip.open(os.path.join(ROOT, ".work", "cold_raw.json.gz"), "rt"))
    fix = os.path.join(ROOT, ".work", "cold_fix.json.gz")
    if os.path.exists(fix):
        raw.update(json.load(gzip.open(fix, "rt")))
    ids = [t["id"] for t in json.load(open(os.path.join(ROOT, "kit", "sample", "tests.json")))["tests"]]
    idx = {i: n for n, i in enumerate(ids)}
    sets = []
    sid = {}

    def S(members):
        t = tuple(sorted(members))
        if t not in sid:
            sid[t] = len(sets)
            sets.append(list(t))
        return sid[t]

    by_line = {}
    by_code = {}
    reads = {}
    listed = {}
    suspects = []
    mods = {}
    cpu = [0.0] * len(ids)
    failed = []
    crashes = {}
    for nid, r in raw.items():
        n = idx[nid]
        cpu[n] = round(r.get("cpu", 0), 3)
        if r.get("failed"):
            failed.append(n)
        if r.get("crash") or r.get("timeout") or r.get("childerr"):
            crashes[nid] = {k: r.get(k) for k in ("crash", "timeout", "childerr")}
        for f, ls in r["lines"].items():
            d = by_line.setdefault(f, {})
            for l in ls:
                d.setdefault(l, []).append(n)
        for f, cs in r["codes"].items():
            d = by_code.setdefault(f, {})
            for fl, name in cs:
                d.setdefault("%d:%s" % (fl, name), []).append(n)
        if r.get("watch"):
            suspects.append(n)
        for m_ in r.get("mods", []):
            mods.setdefault(m_, []).append(n)
        for p in r["reads"]:
            reads.setdefault(p, []).append(n)
        for p in r["listed"]:
            listed.setdefault(p, []).append(n)
    lines = {}
    for f, d in by_line.items():
        ranges = []
        for l in sorted(d):
            s = S(d[l])
            if ranges and ranges[-1][1] == l - 1 and ranges[-1][2] == s:
                ranges[-1][1] = l
            else:
                ranges.append([l, l, s])
        lines[f] = ranges
    codes = {f: {k: S(v) for k, v in d.items()} for f, d in by_code.items()}
    # tests whose outcome can depend on process state, memory, timing or line numbers: never decided by running
    import ast

    src_root = os.path.join(ROOT, ".work", "sqlglot-src")
    watch = {"tracemalloc", "gc", "weakref", "resource", "psutil", "getsizeof", "perf_counter",
             "process_time", "sleep", "monotonic", "timeout", "getrefcount", "_getframe", "signal",
             "setrecursionlimit", "getrecursionlimit"}
    fragile = set()
    for path in sorted({i.split("::")[0] for i in ids}):
        try:
            tree = ast.parse(open(os.path.join(src_root, path)).read())
        except Exception:
            continue
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            for fn in [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                nid = "%s::%s::%s" % (path, cls.name, fn.name)
                if nid not in idx:
                    continue
                names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)} | {
                    n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
                if names & watch or "memory" in fn.name or "leak" in fn.name or "retain" in fn.name:
                    fragile.add(idx[nid])
    out = {
        "ids": ids,
        "sets": sets,
        "lines": lines,
        "codes": codes,
        "reads": {p: S(v) for p, v in reads.items()},
        "listed": {p: S(v) for p, v in listed.items()},
        "extra": {"fragile": sorted(fragile), "suspects": S(suspects), "mods": {k: S(v) for k, v in mods.items()}},
        "cold": {"cpu": cpu, "failed": sorted(failed), "crashes": crashes},
    }
    os.makedirs(os.path.join(ROOT, "maps"), exist_ok=True)
    path = os.path.join(ROOT, "maps", "map.json.gz")
    with gzip.open(path, "wt", compresslevel=9) as f:
        json.dump(out, f, separators=(",", ":"))
    print("wrote", path, os.path.getsize(path), "bytes;", len(sets), "sets;", len(failed), "cold failures", file=sys.stderr)


if __name__ == "__main__":
    main()
