"""Split tests over 1..8 workers: minimise busiest load + 2000 ms per worker (whole milliseconds)."""

WORKER_MS = 2000
MAX_WORKERS = 8


def _lpt(items, k):
    loads = [0] * k
    bins = [[] for _ in range(k)]
    for num, ms in sorted(items, key=lambda x: -x[1]):
        i = loads.index(min(loads))
        bins[i].append((num, ms))
        loads[i] += ms
    return bins, loads


def _improve(bins, loads):
    """Move / swap items off the busiest worker while that lowers the maximum."""
    for _ in range(200):
        m = max(range(len(bins)), key=lambda i: loads[i])
        best = None
        cur = loads[m]
        for j in range(len(bins)):
            if j == m:
                continue
            for a, (na, ma) in enumerate(bins[m]):
                # move
                nm, nj = cur - ma, loads[j] + ma
                if max(nm, nj) < cur and (best is None or max(nm, nj) < best[0]):
                    best = (max(nm, nj), "move", j, a, None)
                for b, (nb, mb) in enumerate(bins[j]):
                    if ma > mb:
                        nm, nj = cur - ma + mb, loads[j] - mb + ma
                        if max(nm, nj) < cur and (best is None or max(nm, nj) < best[0]):
                            best = (max(nm, nj), "swap", j, a, b)
        if best is None:
            break
        _, kind, j, a, b = best
        if kind == "move":
            it = bins[m].pop(a)
            bins[j].append(it)
            loads[m] -= it[1]
            loads[j] += it[1]
        else:
            ia, ib = bins[m][a], bins[j][b]
            bins[m][a], bins[j][b] = ib, ia
            loads[m] += ib[1] - ia[1]
            loads[j] += ia[1] - ib[1]
    return bins, loads


def pack(items):
    """items: list of (number, ms).  Returns (cost_ms, bins) with bins a list of lists of (number, ms)."""
    items = list(items)
    if not items:
        return 0, []
    best = None
    for k in range(1, min(MAX_WORKERS, len(items)) + 1):
        bins, loads = _lpt(items, k)
        if len(items) <= 400:
            bins, loads = _improve(bins, loads)
        cost = max(loads) + WORKER_MS * k
        if best is None or cost < best[0]:
            best = (cost, [b for b in bins if b], max(loads))
    return best[0], best[1]


def cost_of(items):
    return pack(items)[0]


def fill_free(bins, extra):
    """Add `extra` [(number, ms)] (most plausible first) into the idle time of already-used workers.

    The busiest worker sets the price, so tests that fit under that load are free.
    """
    if not bins:
        return bins
    loads = [sum(ms for _, ms in b) for b in bins]
    cap = max(loads)
    for num, ms in extra:
        # best fit: the worker with the least idle time that still fits
        slot = None
        for i, ld in enumerate(loads):
            if ld + ms <= cap and (slot is None or ld > loads[slot]):
                slot = i
        if slot is not None:
            bins[slot].append((num, ms))
            loads[slot] += ms
    return bins
