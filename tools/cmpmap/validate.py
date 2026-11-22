"""Check the proof against real runs: swap one comparison operator of the pinned source, run the whole suite, and
check that no test the map claims to run as before fails.

    validate.py pick <map> <pinned source root> <out dir> <n> <seed>     writes <out>/<name>/{file.py,meta.json}
    validate.py run  <out dir> <name> [<name> ...]                       runs each in the labeling image (docker, native)
    validate.py check <map> <out dir> <pinned source root>               prints claimed / failing per swap

The swapped source goes through selector.cmpproof.single_compare_swap, so the refusals are exercised too.
"""
import ast
import gzip
import json
import os
import random
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from selector import cmpproof  # noqa: E402

SYM = {"Eq": "==", "NotEq": "!=", "Lt": "<", "LtE": "<=", "Gt": ">", "GtE": ">=", "Is": "is", "IsNot": "is not"}


def swap_text(text, site, old, new):
    path, _, key = site.partition(":")
    line, col, eline, ecol = map(int, key.split(":"))
    tree = ast.parse(text)
    for n in ast.walk(tree):
        if isinstance(n, ast.Compare) and len(n.ops) == 1 and (n.lineno, n.col_offset, n.end_lineno, n.end_col_offset) == (line, col, eline, ecol):
            lines = text.split("\n")
            # offsets are utf-8 bytes
            def off(l, c):
                return sum(len(x.encode()) + 1 for x in lines[:l - 1]) + c
            b = text.encode()
            a0, a1 = off(n.left.end_lineno, n.left.end_col_offset), off(n.comparators[0].lineno, n.comparators[0].col_offset)
            seg = b[a0:a1].decode()
            pat = {"Is": r"\bis\b(?!\s+not\b)", "IsNot": r"\bis\s+not\b"}.get(old, re.escape(SYM[old]))
            m = re.search(pat, seg)
            if not m:
                return None
            seg2 = seg[:m.start()] + SYM[new] + seg[m.end():]
            return (b[:a0].decode() + seg2 + b[a1:].decode())
    return None


def pick(mp, root, out, n, seed):
    d = json.load(gzip.open(mp, "rt"))
    rnd = random.Random(seed)
    cands = []
    for path, sites in d["sites"].items():
        for key, e in sites.items():
            for k, sid in enumerate(e[1:]):
                alt = cmpproof.ALTS[e[0]][k]
                unproven = len(d["sets"][sid]) if sid >= 0 else len(d["ids"])
                cands.append((path + ":" + key, e[0], alt, unproven))
    full = [c for c in cands if c[3] == 0]
    part = [c for c in cands if 0 < c[3] < len(d["ids"])]
    nothing = [c for c in cands if c[3] == len(d["ids"])]
    chosen = rnd.sample(full, min(n // 2, len(full))) + rnd.sample(part, min(n // 4, len(part))) + rnd.sample(nothing, min(n - n // 2 - n // 4, len(nothing)))
    os.makedirs(out, exist_ok=True)
    k = 0
    for site, old, new, unproven in chosen:
        path = site.partition(":")[0]
        text = open(os.path.join(root, path), encoding="utf-8").read()
        patched = swap_text(text, site, old, new)
        if patched is None:
            continue
        got = cmpproof.single_compare_swap(text, patched, path)
        name = "s%03d" % k
        k += 1
        os.makedirs(os.path.join(out, name), exist_ok=True)
        open(os.path.join(out, name, "file.py"), "w", encoding="utf-8").write(patched)
        json.dump({"site": site, "old": old, "new": new, "path": path, "unproven": unproven, "selector_swap": got}, open(os.path.join(out, name, "meta.json"), "w"))
        print(name, site, old, "->", new, "unproven for", unproven, "selector:", got is not None)


def run(out, names):
    for name in names:
        meta = json.load(open(os.path.join(out, name, "meta.json")))
        script = ("set -e; rm -rf /v; cp -a /clean /v; cp /out/%s/file.py /v/%s; cd /v; "
                  "SKIP_INTEGRATION=0 PYTHONDONTWRITEBYTECODE=1 python -m pytest tests -q -p no:cacheprovider -rfE --maxfail=100000 "
                  "> /out/%s/pytest.txt 2>&1 || true" % (name, meta["path"], name))
        subprocess.run(["docker", "run", "--rm", "--cpus", "1", "--user", "0", "-v", os.path.abspath(out) + ":/out", "rwx-label:native", "sh", "-c", script])
        print("ran", name, flush=True)


def failing_tests(txt, cm):
    """The tests that failed in a pytest -rfE summary.  A collection error (`ERROR tests/x.py`) names a file, not a test:
    it fails every test of the file."""
    failing = set()
    for line in txt.splitlines():
        if line.startswith("SUBFAIL"):
            # `SUBFAILED[label with spaces] tests/x.py::Class::test` (the label comes first)
            got = re.findall(r"\S+\.py::\S+", line)
            if got:
                failing.add(got[-1].split(" - ")[0])
            continue
        m = re.match(r"(?:FAILED|ERROR) (\S+)", line)
        if not m:
            continue
        name = m.group(1)
        if "::" in name:
            failing.add(name)
        else:
            failing |= {t for t in cm.tests if t.split("::")[0] == name}
            failing.add(name)
    return failing


def finished(txt):
    lines = [l for l in txt.splitlines() if l.strip()]
    return bool(lines) and bool(re.search(r"\b(passed|failed|error|errors)\b.* in [\d.]+s", lines[-1]))


def check(mp, out, root):
    """`root` is the pinned source tree: the swap is derived from the real patched file by selector.cmpproof, exactly
    as the selector does it, and the map is queried with what it returns."""
    cm = cmpproof.CmpMap(mp)
    bad = 0
    for name in sorted(os.listdir(out)):
        p = os.path.join(out, name, "pytest.txt")
        if not os.path.exists(p):
            continue
        meta = json.load(open(os.path.join(out, name, "meta.json")))
        txt = open(p, errors="replace").read()
        if not txt.strip():
            print(name, meta["site"], "no pytest output (unfinished)")
            continue
        with open(os.path.join(root, meta["path"]), encoding="utf-8") as f:
            old = f.read()
        with open(os.path.join(out, name, "file.py"), encoding="utf-8") as f:
            new = f.read()
        swap = cmpproof.single_compare_swap(old, new, meta["path"])
        if swap is None:
            print(name, meta["site"], "the selector refuses this swap: nothing to check")
            continue
        if swap[0] != meta["site"] or (swap[1], swap[2]) != (meta["old"], meta["new"]):
            bad += 1
            print(name, meta["site"], "MISMATCH: single_compare_swap says", swap)
            continue
        failing = failing_tests(txt, cm)
        claimed = sum(1 for t in cm.tests if cm.same_outcome(t, *swap))
        wrong = sorted(f for f in failing if cm.same_outcome(f, *swap))
        print(name, swap[0], swap[1], "->", swap[2], "claimed", claimed, "failing", len(failing), "MISSED" if wrong else "ok",
              "" if finished(txt) else "(unfinished)", wrong[:3])
        bad += bool(wrong)
    print("missed swaps:", bad)


if __name__ == "__main__":
    c = sys.argv[1]
    if c == "pick":
        pick(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), int(sys.argv[6]))
    elif c == "run":
        run(sys.argv[2], sys.argv[3:])
    else:
        check(sys.argv[2], sys.argv[3], sys.argv[4])
