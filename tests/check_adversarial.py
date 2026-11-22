"""Compare the selector's output on tests/patches/ with the labels from tools/label.py.

    python3 tests/check_adversarial.py <selector output.jsonl> <tests.json> [labels.jsonl]

Exit code 1 when any patch misses a failing test.  Also prints the cost of every answer.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    out, tj = sys.argv[1], sys.argv[2]
    labels_path = sys.argv[3] if len(sys.argv) > 3 else os.path.join(HERE, "labels.jsonl")
    tests = json.load(open(tj))["tests"]
    num = {t["id"]: t["number"] for t in tests}
    ms = {t["number"]: round(t["seconds"] * 1000) for t in tests}
    labels = {}
    for line in open(labels_path):
        r = json.loads(line)
        labels[r["patch"][:-6]] = r
    answers = {}
    for line in open(out):
        r = json.loads(line)
        answers[r["commit"]] = r["workers"]
    bad = 0
    total = 0.0
    for name in sorted(answers):
        w = answers[name]
        sel = {n for b in w for n in b}
        cost = (max(sum(ms[n] for n in b) for b in w if b) + 2000 * len([b for b in w if b])) / 1000 if any(w) else 0.0
        total += cost
        lab = labels.get(name)
        if lab is None:
            print("%-30s cost %6.1f  (no label)" % (name, cost))
            continue
        fail = {num[i] for i in lab["failing"]}
        miss = fail - sel
        flag = "MISS %d" % len(miss) if miss else "ok"
        if miss:
            bad += 1
        print("%-30s cost %6.1f  failing %3d  %s" % (name, cost, len(fail), flag))
    print("total cost %.1f, patches with misses: %d" % (total, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
