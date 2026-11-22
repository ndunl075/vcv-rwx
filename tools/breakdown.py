"""Per-patch cost of an output against the answers: python3 tools/breakdown.py <set dir> <output.jsonl>"""
import json
import sys

d, out = sys.argv[1], sys.argv[2]
tests = {t["number"]: round(t["seconds"] * 1000) for t in json.load(open(d + "/tests.json"))["tests"]}
ans = json.load(open(d + "/answers.json"))["commits"]


def best(nums):
    if not nums:
        return 0
    items = sorted((tests[n] for n in nums), reverse=True)
    b = None
    for k in range(1, min(8, len(items)) + 1):
        loads = [0] * k
        for ms in items:
            loads[loads.index(min(loads))] += ms
        c = max(loads) + 2000 * k
        b = c if b is None or c < b else b
    return b


tot = opt = 0
rows = []
for line in open(out):
    r = json.loads(line)
    w = [x for x in r["workers"] if x]
    cost = (max(sum(tests[n] for n in b) for b in w) + 2000 * len(w)) if w else 0
    a = ans[r["commit"]]
    sel = {n for b in w for n in b}
    miss = [n for n in a["failing"] if n not in sel]
    o = best(a["failing"])
    tot += cost
    opt += o
    rows.append((cost - o, r["commit"], a["kind"], cost, o, len(sel), len(a["failing"]), miss))
for x, pid, kind, cost, o, nsel, nfail, miss in sorted(rows, reverse=True):
    if x or miss:
        print("%-8s %-8s cost %6.1f oracle %6.1f excess %6.1f selected %4d failing %3d %s" % (
            pid, kind, cost / 1000, o / 1000, x / 1000, nsel, nfail, "MISS %s" % miss if miss else ""))
print("total %.1f oracle %.1f" % (tot / 1000, opt / 1000))
