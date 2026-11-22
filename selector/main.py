"""Entry point: one JSON line per patch on stdout, everything else on stderr.

Per patch: (1) a candidate set that holds every test the patch can break, from the file map
(selector/filemap.py); anything that cannot be classified with certainty selects every test.
(2) Where it pays, the cheap candidates are run with real pytest in a fresh process against the
patched tree; the answer is the candidates that failed plus every candidate that was not run.
"""
import json
import os
import sys
import threading
import time

from . import pack, patch as patchmod, verify
from .filemap import FileMap

DATA = os.environ.get("RWX_DATA", "/data")
PINNED = os.environ.get("RWX_PINNED", "/opt/sqlglot")
T0 = time.monotonic()

# --- cost model (board seconds) ---------------------------------------------------------------
HEAVY_MS = 2500        # a test this slow is never run here
RUN_START_S = 1.5      # one pytest process: interpreter, pytest, sqlglot, collection
RUN_FACTOR = 1.5       # CPU charged per recorded second of a test that is run
RUN_LEFTOVER_S = 1.2   # expected cost of what still has to be selected after a run
RUN_MARGIN_S = 0.5
COLD_EXTRA_S = 1.0     # a test whose own process costs this much more CPU than an empty test
COLD_EXTRA_X = 3.0     # ... and more than this multiple of its recorded time is not run (pools, class set-up)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def ms_of(seconds):
    return int(round(seconds * 1000))


class Ctx:
    def __init__(self):
        with open(os.path.join(DATA, "tests.json")) as f:
            self.tests = json.load(f)["tests"]
        self.ms = {t["number"]: ms_of(t["seconds"]) for t in self.tests}
        self.id_of = {t["number"]: t["id"] for t in self.tests}
        self.num_of = {t["id"]: t["number"] for t in self.tests}
        self.file_of = {t["number"]: t["file"] for t in self.tests}
        self.all = [t["number"] for t in self.tests]
        self.all_bins = pack.pack([(n, self.ms[n]) for n in self.all])[1]
        self.fm = None
        try:
            self.fm = FileMap()
        except Exception as e:
            log("file map unavailable (%r): every patch selects every test" % (e,))
            return
        fm = self.fm
        self.num_at = [self.num_of.get(i) for i in fm.ids]  # map index -> test number
        mapped = {n for n in self.num_at if n is not None}
        # tests the map does not know, or does not trust, are candidates of every patch
        self.always = {n for n in self.all if n not in mapped}
        self.runnable = set()
        for i, n in enumerate(self.num_at):
            if n is None or i in fm.unknown or fm.cpu[i] is None:
                continue
            extra = fm.cpu[i] - fm.base_cpu
            if self.ms[n] < HEAVY_MS and extra <= max(COLD_EXTRA_S, COLD_EXTRA_X * self.ms[n] / 1000.0):
                self.runnable.add(n)

    def openers(self, path):
        return {self.num_at[i] for i in self.fm.openers(path) if self.num_at[i] is not None} | self.always


class Plan:
    def __init__(self, pid):
        self.pid = pid
        self.everything = None  # reason for selecting every test
        self.cand = set()
        self.new_files = {}     # path -> patched bytes
        self.listed = []        # candidates selected without running
        self.run = []           # candidates to run
        self.notes = []


def read_pinned(path):
    full = os.path.join(PINNED, path)
    if os.path.islink(full) or not os.path.isfile(full):
        return None
    with open(full, "rb") as f:
        return f.read()


def is_test_module(path):
    parts = path.split("/")
    return parts[0] == "tests" and "fixtures" not in parts and parts[-1].startswith("test_") and parts[-1].endswith(".py")


def classify(c, path, new):
    """Candidates of one changed file, or a string: the reason why every test is selected."""
    if path.startswith("tests/fixtures/"):
        return c.openers(path)
    if path.endswith(".py") and (path.startswith("sqlglot/") or is_test_module(path)):
        if b"\0" in new:
            return "NUL byte in %s" % path
        try:
            new.decode("utf-8")
        except UnicodeDecodeError:
            return "%s is not UTF-8" % path
        return c.openers(path)
    return "unclassified file %s" % path


def plan_patch(c, pid, data):
    p = Plan(pid)
    if c.fm is None:
        p.everything = "no file map"
        return p
    try:
        changes = patchmod.parse(data)
    except patchmod.Unparseable as e:
        p.everything = "unparseable patch (%s)" % e
        return p
    seen = set()
    for ch in changes:
        path = ch.path
        if ch.kind != "modify" or ch.binary or not ch.hunks or ch.old_path != ch.new_path or ch.old_mode != ch.new_mode:
            p.everything = "%s of %s" % (ch.kind if ch.kind != "modify" else "unusual change", path)
            return p
        if path in seen or path.startswith("/") or os.path.normpath(path) != path or path.startswith(".."):
            p.everything = "odd path %r" % path
            return p
        seen.add(path)
        old = read_pinned(path)
        if old is None:
            p.everything = "%s is not a file of the pinned tree" % path
            return p
        try:
            new = patchmod.apply_hunks(old, ch.hunks)
        except patchmod.Unparseable as e:
            p.everything = "patch does not apply to %s (%s)" % (path, e)
            return p
        if new == old:
            continue
        got = classify(c, path, new)
        if isinstance(got, str):
            p.everything = got
            return p
        p.new_files[path] = new
        p.cand |= got
    return p


def items(c, nums):
    return [(n, c.ms[n]) for n in nums]


def decide(c, p):
    """Split the candidates into `listed` (selected unrun) and `run`."""
    cand = sorted(p.cand)
    unrun = [n for n in cand if n not in c.runnable]
    rest = [n for n in cand if n in c.runnable]
    # workers that the unrun candidates pay for anyway take other candidates for free
    bins = pack.pack(items(c, unrun))[1]
    before = {n for b in bins for n, _ in b}
    bins = pack.fill_free(bins, sorted(items(c, rest), key=lambda x: -x[1]))
    free = {n for b in bins for n, _ in b} - before
    rest = [n for n in rest if n not in free]
    listed = unrun + sorted(free)
    if rest:
        list_cost = pack.cost_of(items(c, cand)) / 1000.0
        run_cost = (RUN_START_S + RUN_FACTOR * sum(c.ms[n] for n in rest) / 1000.0
                    + pack.cost_of(items(c, listed)) / 1000.0 + RUN_LEFTOVER_S)
        if run_cost + RUN_MARGIN_S < list_cost:
            p.listed, p.run = listed, rest
            return
    p.listed, p.run = cand, []


def fill(c, bins, cand):
    """Free insurance: idle time on workers already paid for takes more tests, likeliest first."""
    if len(bins) < 2:
        return bins
    have = {n for b in bins for n, _ in b}
    files = {c.file_of[n] for n in have}
    tiers = ([n for n in cand if n not in have],
             [n for n in c.all if n not in have and n not in cand and c.file_of[n] in files],
             [n for n in c.all if n not in have and n not in cand and c.file_of[n] not in files])
    extra = []
    for tier in tiers:
        extra.extend(sorted(items(c, tier), key=lambda x: x[1]))
    return pack.fill_free(bins, extra)


class Out:
    def __init__(self):
        self.lock = threading.Lock()
        self.done = set()

    def emit(self, pid, bins):
        with self.lock:
            if pid in self.done:
                return
            self.done.add(pid)
            sys.stdout.write(json.dumps({"commit": pid, "workers": [[n for n, _ in b] for b in bins if b]}) + "\n")
            sys.stdout.flush()


def answer(c, p, selected):
    bins = pack.pack(items(c, sorted(selected)))[1]
    return fill(c, bins, p.cand)


def main():
    out = Out()
    names = sorted(f for f in os.listdir(os.path.join(DATA, "commits")) if f.endswith(".patch"))
    pids = [f[:-6] for f in names]
    c = Ctx()
    # the whole batch has 3 minutes (4.5 in the final, which has about 60 patches)
    limit = 270.0 if len(pids) > 50 else 180.0
    stop_at = T0 + limit - 70.0   # no verification starts after this
    kill_at = T0 + limit - 45.0   # running verifications are killed at this point
    plans = []
    stats = {"everything": 0, "empty": 0, "listed": 0, "run": 0, "run_clean": 0, "errors": 0}
    for pid in pids:
        try:
            with open(os.path.join(DATA, "commits", pid + ".patch"), "rb") as f:
                data = f.read()
            p = plan_patch(c, pid, data)
            if p.everything is None:
                decide(c, p)
        except Exception as e:
            log("%s: error while planning (%r)" % (pid, e))
            stats["errors"] += 1
            p = Plan(pid)
            p.everything = "error"
        if p.everything is not None:
            log("%s: every test: %s" % (pid, p.everything))
            stats["everything"] += 1
            out.emit(pid, c.all_bins)
        elif not p.run:
            stats["listed" if p.cand else "empty"] += 1
            log("%s: %d candidates, selected unrun" % (pid, len(p.cand)))
            out.emit(pid, answer(c, p, p.listed))
        else:
            plans.append(p)

    def fallback(p):
        out.emit(p.pid, answer(c, p, p.cand))

    def guard():
        # last resort: whatever is still open gets its candidates, and the process ends in time
        delay = kill_at + 8.0 - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        for p in plans:
            fallback(p)
        log("guard: time is up")
        sys.stdout.flush()
        os._exit(0)

    threading.Thread(target=guard, daemon=True).start()
    queue = list(plans)
    qlock = threading.Lock()

    def worker(slot):
        while True:
            with qlock:
                if not queue:
                    return
                p = queue.pop(0)
            try:
                if time.monotonic() > stop_at:
                    log("%s: out of time, %d candidates selected unrun" % (p.pid, len(p.cand)))
                    fallback(p)
                    continue
                ids = [c.id_of[n] for n in p.run]
                budget = min(kill_at - time.monotonic(), 30.0 + 6.0 * sum(c.ms[n] for n in p.run) / 1000.0)
                res = verify.run(slot, p.new_files, ids, {i: c.ms[c.num_of[i]] / 1000.0 for i in ids}, budget, log)
                keep = set(p.listed) | {c.num_of[i] for i in res.unverified}
                if res.teardown_failed:
                    # a failing class or module teardown is charged to the last test of its scope
                    for i in res.teardown_failed:
                        n = c.num_of[i]
                        cls = c.id_of[n].rsplit("::", 1)[0]
                        keep.add(max(m for m in c.all if c.id_of[m].startswith(cls + "::")))
                        keep.add(max(m for m in c.all if c.file_of[m] == c.file_of[n]))
                    keep.add(max(c.all))
                stats["run"] += 1
                stats["run_clean"] += 0 if keep else 1
                log("%s: ran %d of %d candidates in %.1fs (%s), selected %d" % (
                    p.pid, len(ids), len(p.cand), res.wall, res.note, len(keep)))
                out.emit(p.pid, answer(c, p, keep))
            except Exception as e:
                log("%s: error while verifying (%r), candidates selected unrun" % (p.pid, e))
                stats["errors"] += 1
                fallback(p)

    threads = [threading.Thread(target=worker, args=(s,)) for s in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    verify.cleanup()
    log("stats %s wall %.1fs cpu %s" % (json.dumps(stats), time.monotonic() - T0, container_cpu()))
    return 0


def container_cpu():
    """CPU seconds of the whole container so far (what the board charges), for the log only."""
    try:
        with open("/sys/fs/cgroup/cpu.stat") as f:
            for line in f:
                if line.startswith("usage_usec"):
                    return "%.2fs" % (int(line.split()[1]) / 1e6)
    except (OSError, ValueError):
        pass
    t = os.times()
    return "%.2fs (process tree)" % (t.user + t.system + t.children_user + t.children_system)
