"""Entry point: one JSON line per patch on stdout, everything else on stderr."""
import json
import os
import sys
import time

from . import pack, plan as planmod, verify

DATA = os.environ.get("RWX_DATA", "/data")
PINNED = os.environ.get("RWX_PINNED", "/opt/sqlglot")

# --- tunables (see docs/DESIGN.md) -------------------------------------------------------------
HEAVY_MS = 2500           # a candidate this slow is never run by us: it is selected
VERIFY_OVERHEAD_S = 0.40  # fixed CPU of one verification (fork, import sqlglot from the patched tree)
LIB_IMPORT_S = {"numpy": 0.12, "pandas": 0.45, "duckdb": 0.10}
P_FAIL = 0.5              # prior that a candidate set holds a failing test (only used for the choice)
FAIL_COST_S = 2.0         # extra cost when something fails and must be selected
VERIFY_MARGIN_S = 0.3
VERIFY = os.environ.get("RWX_VERIFY", "0") == "1"  # running candidates is off until the image carries the test dependencies
BATCH_BUDGET_S = 200.0    # stop verifying after this much wall time and just select
VERIFY_DEADLINE_MIN_S = 20.0


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def read_old(path):
    try:
        with open(os.path.join(PINNED, path), "rb") as f:
            return f.read()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError, PermissionError):
        return None


class Ctx:
    pass


def load_ctx():
    c = Ctx()
    with open(os.path.join(DATA, "tests.json")) as f:
        tj = json.load(f)["tests"]
    c.tests = tj
    c.ms = {t["number"]: _ms(t["seconds"]) for t in tj}
    c.num_by_id = {t["id"]: t["number"] for t in tj}
    c.id_by_num = {t["number"]: t["id"] for t in tj}
    c.all_nums = [t["number"] for t in tj]
    c.maps = None
    try:
        from .maps import Maps

        c.maps = Maps()
    except Exception as e:  # no maps: everything is selected
        log("maps unavailable: %r" % (e,))
        return c
    m = c.maps
    c.idx_num = [c.num_by_id.get(i) for i in m.ids]  # map index -> test number
    mapped = {n for n in c.idx_num if n is not None}
    c.unmapped = [n for n in c.all_nums if n not in mapped]
    ex = m.extra
    c.suspects = m.mask(ex["suspects"]) if "suspects" in ex else 0
    c.fragile = set(ex.get("fragile", []))  # map indexes that must never be verified by running
    c.cold_failed = set(m.cold.get("failed", []))
    c.libs = {k: m.mask(v) for k, v in ex.get("mods", {}).items()}
    return c


def _ms(seconds):
    import math

    return int(math.floor(seconds * 1000 + 0.5))


def nums_of(c, mask):
    out = []
    for i in c.maps.indexes(mask):
        n = c.idx_num[i]
        if n is not None:
            out.append(n)
    return out


def select_all(c):
    return pack.pack([(n, c.ms[n]) for n in c.all_nums])[1]


def sel_cost_s(c, nums):
    return pack.cost_of([(n, c.ms[n]) for n in nums]) / 1000.0


def decide(c, cand, unsafe_nums):
    """Return (verify?, W, U): verify W by running, U is selected without running."""
    U = [n for n in cand if n in unsafe_nums or c.ms[n] >= HEAVY_MS]
    W = [n for n in cand if n not in set(U)]
    if not W:
        return False, W, U
    base = sel_cost_s(c, cand)
    cpu = sum(c.ms[n] for n in W) / 1000.0 + VERIFY_OVERHEAD_S
    exp_verify = cpu + sel_cost_s(c, U) + P_FAIL * FAIL_COST_S
    return exp_verify + VERIFY_MARGIN_S < base, W, U


def process(c, patch_id, data, t_start, stats):
    m = c.maps
    if m is None:
        stats["nomaps"] += 1
        return select_all(c)
    pl = planmod.plan_patch(data, m, read_old, c.suspects)
    for n in pl.notes:
        log("  note:", n)
    if pl.everything:
        stats["everything"] += 1
        return select_all(c)
    c.last_related = nums_of(c, pl.related)
    cand = nums_of(c, pl.mask) + [n for n in c.unmapped]
    if not cand:
        stats["empty_static"] += 1
        return []
    unsafe = set()
    for i in c.fragile | c.cold_failed:
        if c.idx_num[i] is not None:
            unsafe.add(c.idx_num[i])
    do_verify, W, U = decide(c, cand, unsafe)
    final = list(cand)
    if VERIFY and do_verify and not pl.cannot_run and time.time() - t_start < BATCH_BUDGET_S:
        stats["verified"] += 1
        ids = [m.ids[i] for i in range(m.n) if c.idx_num[i] in set(W)]
        warm = [lib for lib, mk in c.libs.items() if mk & pl.mask]
        ws = verify.build_workspace(pl.new_files, "w")
        try:
            budget = max(VERIFY_DEADLINE_MIN_S, 3.0 * sum(c.ms[n] for n in W) / 1000.0 + 10)
            res = verify.run_tests(ws, ids, warm=warm, deadline=budget, log=log)
        finally:
            verify.remove_workspace(ws)
        failing = [c.num_by_id[i] for i, f in res.items() if f]
        final = U + failing
        stats["verified_clean"] += 0 if final else 1
    else:
        stats["selected_candidates"] += 1
    return final


def emit_line(pid, bins):
    workers = [[n for n, _ in b] for b in bins if b]
    sys.stdout.write(json.dumps({"commit": pid, "workers": workers}) + "\n")
    sys.stdout.flush()


def main():
    t_start = time.time()
    data_dir = DATA
    patches = sorted(f for f in os.listdir(os.path.join(data_dir, "commits")) if f.endswith(".patch"))
    try:
        c = load_ctx()
    except Exception as e:
        log("fatal context error %r: selecting everything" % (e,))
        c = None
    stats = {k: 0 for k in ("nomaps", "everything", "empty_static", "verified", "verified_clean", "selected_candidates", "errors")}
    for fn in patches:
        pid = fn[:-6]
        try:
            with open(os.path.join(data_dir, "commits", fn), "rb") as f:
                data = f.read()
            nums = process(c, pid, data, t_start, stats)
            if nums is None:
                nums = []
            if nums and isinstance(nums[0], list):
                bins = nums
            else:
                items = [(n, c.ms[n]) for n in nums]
                cost, bins = pack.pack(items)
                bins = fill(c, bins, nums, getattr(c, 'last_related', ()))
        except Exception as e:
            log("error on %s: %r -> selecting everything" % (pid, e))
            stats["errors"] += 1
            try:
                bins = select_all(c)
            except Exception:
                bins = []
        emit_line(pid, bins)
    log("stats", json.dumps(stats), "wall %.1fs" % (time.time() - t_start))
    return 0


def fill(c, bins, nums, related_nums=()):
    """Free insurance: idle time of already paid-for workers takes the next most plausible tests.

    Tiers: tests touching the changed files, then tests of the same test files as the selection,
    then everything else; cheapest first inside a tier so that the most tests fit.
    """
    if len(bins) < 2:
        return bins
    have = {n for b in bins for n, _ in b}
    files = {c.tests[n]["file"] for n in have}
    rel = set(related_nums)
    tier1 = [n for n in c.all_nums if n in rel and n not in have]
    tier2 = [t["number"] for t in c.tests if t["file"] in files and t["number"] not in have and t["number"] not in rel]
    seen = set(tier1) | set(tier2) | have
    tier3 = [n for n in c.all_nums if n not in seen]
    extra = []
    for tier in (tier1, tier2, tier3):
        extra.extend(sorted(((n, c.ms[n]) for n in tier), key=lambda x: x[1]))
    return pack.fill_free(bins, extra)
