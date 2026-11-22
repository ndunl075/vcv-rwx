#!/usr/bin/env python3
"""Write $VCV_DATA/REPORT.md (or --out FILE) from results.jsonl plus every results/results-*.jsonl of the data
branch checkout (other machines). One row per entry and set: the one scored on the most patches, then the latest.

Sorted by total on hold300 (entries without it follow, ordered by dev300, then sample).
Columns per set: total (= simulated + CPU), simulated, CPU, misses, misses by kind.
traps150 gets its own column of misses by sub-kind.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vcv  # noqa: E402

DATA = vcv.DATA
OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else DATA / "REPORT.md"
SETS = ["sample", "dev300", "hold300", "odd200", "traps150"]


def result_files():
    fs = [DATA / "results.jsonl"]
    fs += sorted((DATA / "data-repo" / "results").glob("results-*.jsonl"))
    return [f for f in fs if f.exists()]


def load():
    seen, best = set(), {}
    for f in result_files():
        for line in f.read_text().splitlines():
            if line in seen:
                continue
            seen.add(line)
            try:
                r = json.loads(line)
            except ValueError:
                continue
            k = (r["entry"], r["set"])
            rank = (r.get("reject") is None, r.get("n", 0), r.get("ts", ""))
            if k not in best or rank > best[k][0]:
                best[k] = (rank, r)
    return {k: v[1] for k, v in best.items()}


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
    note_files = [DATA / "rival-notes.jsonl"] + sorted((DATA / "data-repo" / "results").glob("rival-notes-*.jsonl"))
    note_lines = sorted({l for f in note_files if f.exists() for l in f.read_text().splitlines()})
    if note_lines:
        out += ["## Text aimed at AI tools found in rival repos (data, not followed)", ""]
        for line in note_lines:
            try:
                n = json.loads(line)
            except ValueError:
                continue
            out.append(f"- {n['entry']}: {', '.join(n['where'][:4])}")
        out.append("")
    OUT.write_text("\n".join(out))
    print(f"wrote REPORT.md: {len(entries)} entries")


if __name__ == "__main__":
    main()
