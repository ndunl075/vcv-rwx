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

from . import cmpproof, codediff, pack, patch as patchmod, verify
from .filemap import FileMap
from .linemap import LineMap
from .slotmap import SlotMap

DATA = os.environ.get("RWX_DATA", "/data")
PINNED = os.environ.get("RWX_PINNED", "/opt/sqlglot")
T0 = time.monotonic()

# --- cost model (board seconds) ---------------------------------------------------------------
HEAVY_MS = 2500        # a test this slow is never run here
RUN_START_S = {"hot": 0.5, "cold": 3.0}   # fixed CPU of one run in a child of the warm servers (board seconds)
RUN_FACTOR = 1.3       # CPU charged per recorded second of a test that is run
RUN_LEFTOVER_S = 0.8   # expected cost of what still has to be selected after a run
RUN_MARGIN_S = 1e9      # probe: nothing is run, every candidate is listed
COLD_EXTRA_S = 4.0     # a test whose own process costs this much more CPU than an empty test is not run (spawn pools)
EXECUTOR_SETUP_S = 2.5 # class set-up of tests/test_executor.py, paid once by a run that holds one of its tests


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
            if self.ms[n] < HEAVY_MS and extra <= COLD_EXTRA_S:
                self.runnable.add(n)

        self.lm = None
        try:
            self.lm = LineMap()
        except Exception as e:
            log("line map unavailable (%r): Python files fall back to the file map" % (e,))
            return
        lm = self.lm
        self.lnum_at = [self.num_of.get(i) for i in lm.ids]
        self.always |= {n for n in self.all if n not in {m for m in self.lnum_at if m is not None}}
        # a test that starts worker processes pays for every worker's start-up: never run here
        self.runnable -= {self.lnum_at[i] for i in lm.pool if self.lnum_at[i] is not None}
        # children of a hot run would import the pinned tree: tests that start processes run cold only
        self.spawn = {self.lnum_at[i] for i in lm.spawn if self.lnum_at[i] is not None} | self.always

        self.sm = None
        self.state_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "maps", "state.json.gz")
        try:
            sm = SlotMap()
            if sm.ids == lm.ids and sm.complete and os.path.exists(self.state_path):
                self.sm = sm
                self.snum_at = self.lnum_at
            else:
                log("slot map not usable: import-time edits select every importer")
        except Exception as e:
            log("slot map unavailable (%r): import-time edits select every importer" % (e,))

        self.cmp = None
        try:
            if os.environ.get("RWX_CMP_PROOF", "1") == "1":
                self.cmp = cmpproof.CmpMap()
        except Exception as e:
            log("no comparison profiles (%r): slow tests are selected whenever they are candidates" % (e,))

    def openers(self, path):
        return {self.num_at[i] for i in self.fm.openers(path) if self.num_at[i] is not None} | self.always

    def nums(self, indexes):
        return {self.lnum_at[i] for i in indexes if self.lnum_at[i] is not None}


class Plan:
    def __init__(self, pid):
        self.pid = pid
        self.everything = None  # reason for selecting every test
        self.cand = set()
        self.new_files = {}     # path -> patched bytes
        self.unrun = set()      # candidates that a run in a fresh process cannot settle
        self.diffs = {}         # patched Python file -> codediff.Diff, or None if it does not compile
        self.base = set()       # candidates that do not depend on what the import-time edits change
        self.importers = set()  # tests that run the changed import-time code: the fallback for those edits
        self.slots = False      # import-time edits whose effect the slot comparison can narrow down
        self.text_readers = set()  # tests that read a patched Python file as data
        self.fixture = False    # a fixture file changed: its readers are selected, not run
        self.mode = "cold"
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


def is_python(path):
    return path.endswith(".py") and (path.startswith("sqlglot/") or path.startswith("tests/")) and "/fixtures/" not in path


def classify(c, p, path, old, new):
    """Add the candidates of one changed file to the plan, or return the reason why every test is selected."""
    if path.startswith("tests/fixtures/"):
        got = c.openers(path)
        if c.lm is not None:
            got |= c.nums(c.lm.readers(path))
        p.cand |= got
        # an edited fixture nearly always fails the tests that read it: running them first only adds CPU
        p.unrun |= got
        return None
    if not is_python(path):
        return "unclassified file %s" % path
    if c.lm is None:
        if b"\0" in new:
            return "NUL byte in %s" % path
        p.cand |= c.openers(path)
        return None
    # whoever reads the source as data sees any change of its text
    p.text_readers |= c.nums(c.lm.readers(path)) | c.always
    p.cand |= p.text_readers
    try:
        d = codediff.diff(old, new, path, codediff.pinned_code(PINNED, path, old))
    except codediff.Broken as e:
        # the module no longer compiles: every process that imports it is affected
        p.notes.append("%s does not compile (%s)" % (path, e))
        p.cand |= c.openers(path)
        p.diffs[path] = None
        return None
    p.diffs[path] = d
    lines = d.lines
    if d.import_level and c.sm is not None and path.startswith("sqlglot/"):
        # Import-time code changed.  Whoever runs changed function code is a candidate as always; the
        # effect of the import-time part is the set of slots it changes, known after a comparison of
        # the two import states: until then (and if that fails) every test that runs the changed
        # import-time lines, i.e. every importer, stands in.
        imp, _ = c.lm.executors(path, lines - d.func_lines)
        p.importers |= c.nums(imp)
        p.slots = True
        lines = d.func_lines
    cold, sess = c.lm.executors(path, lines)
    p.notes.append("%s: lines %s%s" % (path, sorted(d.lines)[:8], " (import-time)" if d.import_level else ""))
    p.cand |= c.nums(cold) | c.nums(sess)
    # reached only after earlier tests of the whole suite: a run of the candidates alone proves nothing
    p.unrun |= c.nums(sess)
    return None


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
        why = classify(c, p, path, old, new)
        if why is not None:
            p.everything = why
            return p
        p.new_files[path] = new
    p.base = set(p.cand)
    p.cand |= p.importers
    return p


_LIBS = {}


def imports_libs(test_file, depth=0):
    """True if collecting `test_file` imports numpy, pandas or duckdb (directly or through a test module)."""
    import re

    if test_file not in _LIBS:
        _LIBS[test_file] = False
        try:
            with open(os.path.join(PINNED, test_file), "rb") as f:
                src = f.read().decode("utf-8", "replace")
        except OSError:
            src = "import numpy"
        if re.search(r"^\s*(?:import|from)\s+(?:numpy|pandas|duckdb)\b", src, re.M):
            _LIBS[test_file] = True
        elif depth < 4:
            for m in re.finditer(r"^\s*(?:from|import)\s+(tests(?:\.\w+)*)", src, re.M):
                mod = m.group(1).replace(".", "/")
                for cand in (mod + ".py", mod + "/__init__.py"):
                    if os.path.isfile(os.path.join(PINNED, cand)) and imports_libs(cand, depth + 1):
                        _LIBS[test_file] = True
    return _LIBS[test_file]


def function_readers(c, where):
    """Tests that run the code of a function-like slot ("file:first-last" of the pinned code)."""
    path, _, span = where.rpartition(":")
    lo, _, hi = span.partition("-")
    cold, sess = c.lm.executors(path, range(int(lo), int(hi) + 1))
    return c.nums(cold) | c.nums(sess)


def global_readers(c, modname, name):
    """Tests that run a line of the module's own functions that mentions the global `name`."""
    import ast

    for path in (modname.replace(".", "/") + ".py", modname.replace(".", "/") + "/__init__.py"):
        full = os.path.join(PINNED, path)
        if os.path.isfile(full):
            break
    else:
        return None
    with open(full, "rb") as f:
        tree = ast.parse(f.read())
    lines = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Name) and node.id == name:
                    lines.update(range(node.lineno, node.end_lineno + 1))
                elif isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
                    lines.update(range(fn.lineno, fn.end_lineno + 1))
    cold, sess = c.lm.executors(path, lines)
    return c.nums(cold) | c.nums(sess)


MAX_CHANGED_SLOTS = 400


def slot_readers(c, changed):
    """Tests that can see any of the changed slots, or (None, reason) when that cannot be told."""
    if len(changed) > MAX_CHANGED_SLOTS:
        return None, "%d slots changed" % len(changed)
    out = set()
    for key, (a, b) in changed.items():
        if key[0] == "E" or a is None or b is None or key.endswith("<structure>"):
            return None, "%s %s" % (key.replace("\t", " "), "appears or disappears" if key[0] != "E" else "fails to import")
        ka, kb = a[0], b[0]
        if ka != kb or ka == "x":
            return None, "%s holds another kind of value" % key.replace("\t", " ")
        parts = key.split("\t")
        if ka == "f":
            if "@" not in a:
                return None, "%s: code of unknown place" % key.replace("\t", " ")
            got = function_readers(c, a.split("@", 1)[1])
        elif parts[0] == "C":
            got = c.sm.class_readers(key)
            if got is None:
                return None, "%s is not traced" % key.replace("\t", " ")
            got = {c.snum_at[i] for i in got if c.snum_at[i] is not None}
        else:
            inner = global_readers(c, parts[1], parts[2])
            if inner is None:
                return None, "%s: no source" % key.replace("\t", " ")
            got = inner | {c.snum_at[i] for i in c.sm.module_readers(key) if c.snum_at[i] is not None}
        out |= got
    return out, "%d slots" % len(changed)


def resolve_slots(c, p, servers, slot, budget):
    """Replace "every importer" by the readers of the slots that the import-time edits change."""
    memo = {}
    translate = {}
    for d in p.diffs.values():
        if d is not None:
            for old, new in d.pairs:
                translate[codediff.code_hash(new, memo)] = codediff.code_hash(old, memo)
    changed = verify.changed_slots(servers, slot, p.new_files, translate, c.state_path, budget)
    readers, note = slot_readers(c, changed)
    if readers is None:
        p.notes.append("slots: %s -> every importer" % note)
        return
    p.notes.append("slots: %s, %d readers instead of %d importers" % (note, len(readers), len(p.importers)))
    p.cand = p.base | readers | c.always


def items(c, nums):
    return [(n, c.ms[n]) for n in nums]


def hot_ok(c, p, loaded):
    """True if the hot server can hold this patch exactly (see selector/zygote.py)."""
    if loaded is None or c.lm is None or not p.new_files:
        return False
    for path in p.new_files:
        if not (path.startswith("sqlglot/") and path.endswith(".py")) or path not in p.diffs:
            return False
        if path not in loaded:
            continue   # imported later, from the patched source
        d = p.diffs[path]
        if d is None or d.import_level or c.lm.ran_at_collection(path, d.lines):
            return False
    return True


def prove_slow(c, p):
    """Drop slow tests that provably run as before (selector/cmpproof.py)."""
    if c.cmp is None or len(p.new_files) != 1:
        return
    (path, new), = p.new_files.items()
    if p.diffs.get(path) is None:
        return
    slow = [n for n in p.cand if c.id_of[n] in c.cmp.tests and n not in p.text_readers and n not in p.unrun]
    if not slow:
        return
    swap = cmpproof.single_compare_swap(read_pinned(path), new, path)
    if swap is None:
        return
    proven = [n for n in slow if c.cmp.same_outcome(c.id_of[n], *swap)]
    for n in proven:
        p.cand.discard(n)
        p.base.discard(n)
    if proven:
        p.notes.append("%d of %d candidates run as before (%s -> %s at %s)" % (len(proven), len(slow), swap[1], swap[2], swap[0]))


def decide(c, p, loaded):
    """Split the candidates into `listed` (selected unrun) and `run`."""
    prove_slow(c, p)
    cand = sorted(p.cand)
    p.mode = "hot" if hot_ok(c, p, loaded) else "cold"
    unrun = [n for n in cand if n not in c.runnable or n in p.unrun]
    rest = [n for n in cand if n in c.runnable and n not in p.unrun]
    # workers that the unrun candidates pay for anyway take other candidates for free
    bins = pack.pack(items(c, unrun))[1]
    before = {n for b in bins for n, _ in b}
    bins = pack.fill_free(bins, sorted(items(c, rest), key=lambda x: -x[1]))
    free = {n for b in bins for n, _ in b} - before
    rest = [n for n in rest if n not in free]
    listed = unrun + sorted(free)
    if p.mode == "hot" and any(n in c.spawn for n in rest):
        p.mode = "cold"
    if rest:
        list_cost = pack.cost_of(items(c, cand)) / 1000.0
        run_cost = (RUN_START_S[p.mode] + RUN_FACTOR * sum(c.ms[n] for n in rest) / 1000.0
                    + (EXECUTOR_SETUP_S if any(c.file_of[n] == "tests/test_executor.py" for n in rest) else 0.0)
                    + pack.cost_of(items(c, listed)) / 1000.0 + (RUN_LEFTOVER_S if not listed else 0.0))
        p.notes.append("list %.1f run %.1f (%s)" % (list_cost, run_cost, p.mode))
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
    todo = []
    stats = {"everything": 0, "empty": 0, "listed": 0, "run": 0, "hot": 0, "run_clean": 0, "errors": 0}
    for pid in pids:
        try:
            with open(os.path.join(DATA, "commits", pid + ".patch"), "rb") as f:
                data = f.read()
            p = plan_patch(c, pid, data)
        except Exception as e:
            log("%s: error while planning (%r)" % (pid, e))
            stats["errors"] += 1
            p = Plan(pid)
            p.everything = "error"
        if p.everything is not None:
            log("%s: every test: %s" % (pid, p.everything))
            stats["everything"] += 1
            out.emit(pid, c.all_bins)
        elif not p.cand:
            stats["empty"] += 1
            log("%s: no candidates %s" % (pid, "; ".join(p.notes)))
            out.emit(pid, [])
        else:
            todo.append(p)
    # which modules a collected suite has imported: taken from the whole-suite collection of the pinned
    # commit (a superset of what the hot server, which collects fewer files, will have imported)
    loaded = c.lm.loaded if c.lm is not None and c.lm.loaded else None
    for p in todo:
        if p.slots:
            plans.append(p)   # its candidates are settled by a comparison of import states first
            continue
        try:
            decide(c, p, loaded)
        except Exception as e:
            log("%s: error while deciding (%r)" % (p.pid, e))
            p.listed, p.run = sorted(p.cand), []
        if not p.run:
            stats["listed"] += 1
            log("%s: %d candidates, selected unrun %s" % (p.pid, len(p.cand), "; ".join(p.notes)))
            out.emit(p.pid, answer(c, p, p.cand))
        else:
            plans.append(p)
    servers = None
    if plans:
        hot_files, may_run = [], set()
        for p in plans:
            may_run |= {c.file_of[n] for n in (p.cand if p.slots else p.run)}
            if not p.slots and p.mode == "hot":
                for n in p.run:
                    if c.file_of[n] not in hot_files:
                        hot_files.append(c.file_of[n])
        hot_files.sort(key=lambda f: min(n for n in c.all if c.file_of[n] == f))
        try:
            servers = verify.Servers(log, libs=any(imports_libs(f) for f in may_run), hot_files=hot_files)
        except Exception as e:
            log("no warm servers (%r): nothing is run" % (e,))
            for p in plans:
                out.emit(p.pid, answer(c, p, p.cand))
            plans = []
    log("planned %d patches in %.1fs, %d to run" % (len(pids), time.monotonic() - T0, len(plans)))

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
                if p.slots:
                    if not servers.wait("cold", slot, 60.0):
                        raise RuntimeError("no server")
                    try:
                        resolve_slots(c, p, servers, slot, min(40.0, kill_at - time.monotonic()))
                    except Exception as e:
                        p.notes.append("slots: %r -> every importer" % (e,))
                    decide(c, p, None)   # no hot run: the hot server was started without this patch's test files
                    if not p.run:
                        stats["listed" if p.cand else "empty"] += 1
                        log("%s: %d candidates, selected unrun %s" % (p.pid, len(p.cand), "; ".join(p.notes)))
                        out.emit(p.pid, answer(c, p, p.cand))
                        continue
                ids = [c.id_of[n] for n in p.run]
                recorded = {i: c.ms[c.num_of[i]] / 1000.0 for i in ids}
                budget = min(kill_at - time.monotonic(), 30.0 + 6.0 * sum(c.ms[n] for n in p.run) / 1000.0)
                mode = p.mode if servers.wait(p.mode, slot, 60.0) else "cold"
                if not servers.wait(mode, slot, 60.0):
                    raise RuntimeError("no server")
                changes = [{"path": path, "swaps": d.swaps if d is not None else None} for path, d in p.diffs.items()]
                res = verify.run(servers, slot, mode, p.new_files, changes, ids, recorded, budget)
                if res.hot_refused:
                    log("%s: hot server refused (%s), running cold" % (p.pid, res.note))
                    if not servers.wait("cold", slot, 60.0):
                        raise RuntimeError("no server")
                    res = verify.run(servers, slot, "cold", p.new_files, changes, ids, recorded,
                                     min(budget, kill_at - time.monotonic()))
                elif mode == "hot":
                    stats["hot"] += 1
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
                log("%s: ran %d of %d candidates in %.1fs (%s), selected %d %s" % (
                    p.pid, len(ids), len(p.cand), res.wall, res.note, len(keep), "; ".join(p.notes)))
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
    if servers is not None:
        servers.close()
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
