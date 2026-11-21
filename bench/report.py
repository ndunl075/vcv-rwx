#!/usr/bin/env python3
"""Write ~/Developer/vcv-data/rwx/REPORT.md from results.jsonl (latest row per entry and set).

Sorted by total on hold300 (entries without it follow, ordered by dev300, then sample).
Columns per set: total (= simulated + CPU), simulated, CPU, misses, misses by kind.
traps150 gets its own column of misses by sub-kind.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

DATA = Path.home() / "Developer/vcv-data/rwx"
SETS = ["sample", "dev300", "hold300", "odd200", "traps150"]


def load():
    rows = {}
    f = DATA / "results.jsonl"
    if not f.exists():
        return rows
    for line in f.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        rows[(r["entry"], r["set"])] = r  # later lines win
    return rows


def kinds_str(d):
    return ", ".join(f"{k} {v}" for k, v in sorted(d.items())) if d else "-"


def cell(r, field, fmt="{:.1f}"):
    if r is None:
        return ""
    if r.get("reject"):
        return f"rejected: {r['reject']}"
    v = r.get(field)
    return fmt.format(v) if v is not None else ""


def main():
    rows = load()
    entries = sorted({e for e, _ in rows})

    def key(e):
        for s in ("hold300", "dev300", "sample"):
            r = rows.get((e, s))
            if r and not r.get("reject"):
                return (["hold300", "dev300", "sample"].index(s), r["total_s"])
        return (9, 0)

    entries.sort(key=key)
    out = ["# RWX Blast Radius: benchmark report", "",
           f"Generated {time.strftime('%F %T')}. Total = simulated + CPU seconds (CPU from the container's cgroup, Rosetta-inflated), "
           "one run per cell unless noted. Misses cost 300 s each and are already inside the simulated column.", ""]
    ns = {s: next((r["n"] for (e, ss), r in rows.items() if ss == s and r.get("n")), 0) for s in SETS}
    out.append("Patches per set: " + ", ".join(f"{s} {n}" for s, n in ns.items() if n))
    out.append("")
    hdr = "| # | entry | " + " | ".join(f"{s} total | {s} miss" for s in SETS[:4]) + " | traps150 misses by sub-kind |"
    out += [hdr, "|" + "---|" * (2 + 2 * 4 + 1)]
    for i, e in enumerate(entries, 1):
        cells = []
        for s in SETS[:4]:
            r = rows.get((e, s))
            cells += [cell(r, "total_s"), cell(r, "misses", "{}")]
        t = rows.get((e, "traps150"))
        tc = ""
        if t:
            tc = f"rejected: {t['reject']}" if t.get("reject") else f"{t['total_s']:.1f} s; {t['misses']} miss: " + kinds_str(t.get("missed_subs"))
        out.append(f"| {i} | {e} | " + " | ".join(cells) + f" | {tc} |")
    out.append("")
    for s in SETS:
        srows = [(e, rows[(e, s)]) for e in entries if (e, s) in rows]
        if not srows:
            continue
        out += [f"## {s}", "", "| entry | total | simulated | CPU | misses | misses by kind | over-selection (avg tests selected per kind) |",
                "|---|---|---|---|---|---|---|"]
        srows.sort(key=lambda x: (x[1].get("reject") is not None, x[1].get("total_s", 0)))
        for e, r in srows:
            if r.get("reject"):
                out.append(f"| {e} | rejected: {r['reject']} | | | | | |")
                continue
            sel = ", ".join(f"{k} {v['avg_selected']:.0f}" for k, v in sorted(r.get("by_kind", {}).items()))
            out.append(f"| {e} | {r['total_s']:.1f} | {r['simulated_s']:.1f} | {r['cpu_s']:.1f} | {r['misses']} | "
                       f"{kinds_str(r.get('missed_kinds'))} | {sel} |")
        out.append("")
    notes = DATA / "rival-notes.jsonl"
    if notes.exists():
        out += ["## Text aimed at AI tools found in rival repos (data, not followed)", ""]
        for line in notes.read_text().splitlines():
            try:
                n = json.loads(line)
            except ValueError:
                continue
            out.append(f"- {n['entry']}: {', '.join(n['where'][:4])}")
        out.append("")
    (DATA / "REPORT.md").write_text("\n".join(out))
    print(f"wrote REPORT.md: {len(entries)} entries")


if __name__ == "__main__":
    main()
