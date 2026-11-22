"""One whole-suite run in a single process (the way pytest runs it), per-test line sets.

Used to (1) check the cold per-test maps are supersets of what a test executes inside the suite and
(2) detect order effects: outcome in the suite vs outcome alone.  Output: .work/insuite_raw.json.gz
Runs inside the dev image:  python /work/tools/insuite.py
"""
import gzip
import json
import os
import sys
import time

ROOT = os.environ.get("RWX_ROOT", "/opt/sqlglot")
WORK = os.environ.get("RWX_WORK", "/work")
sys.path.insert(0, WORK)
sys.path.insert(0, os.path.join(WORK, "tools", "collect"))
os.environ["SKIP_INTEGRATION"] = "0"
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from rwx_tracer import Tracer, mon  # noqa: E402
from selector import runner  # noqa: E402


def rel(f):
    return f[len(ROOT) + 1:] if f.startswith(ROOT + "/") else None


def main():
    ids = [t["id"] for t in json.load(open(os.path.join(WORK, "kit", "sample", "tests.json")))["tests"]]
    only = os.environ.get("RWX_ONLY")
    if only:
        ids = [i for i in ids if only in i]
    tr = Tracer([ROOT + "/"])
    tr.start()
    per = {}
    current = [None]
    outcome = {}
    t0 = time.time()
    orig = runner._Result

    class R(orig):
        def startTest(self, test):
            super().startTest(test)
            tr.lines = {}
            tr.codes = {}
            mon.restart_events()
            current[0] = test

        def stopTest(self, test):
            d = tr.dump()
            k = "%s::%s::%s" % (type(test).__module__.replace(".", "/") + ".py", type(test).__qualname__, test._testMethodName)
            per[k] = {
                "lines": {rel(f): v for f, v in d["lines"].items() if rel(f)},
                "codes": {rel(f): v for f, v in d["codes"].items() if rel(f)},
            }
            super().stopTest(test)

    runner._Result = R

    def emit(k, failed):
        outcome["%s::%s::%s" % (k[0].replace(".", "/") + ".py", k[1], k[2])] = bool(failed)

    runner.run_ids(ids, ROOT, emit)
    tr.stop()
    print("suite took %.0fs; %d tests; %d failed" % (time.time() - t0, len(outcome), sum(outcome.values())), file=sys.stderr)
    with gzip.open(os.path.join(WORK, ".work", "insuite_raw.json.gz"), "wt") as f:
        json.dump({"outcome": outcome, "per": per}, f)


if __name__ == "__main__":
    main()
