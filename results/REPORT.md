# RWX Blast Radius: benchmark report

Generated 2026-11-22 01:33:24. Total = simulated + CPU seconds (CPU from the container's cgroup, Rosetta-inflated), one run per cell unless noted. Misses cost 300 s each and are already inside the simulated column.

Patches per set: sample 30

| # | entry | sample total | sample miss | dev300 total | dev300 miss | hold300 total | hold300 miss | odd200 total | odd200 miss | traps150 misses by sub-kind |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | starter | 1211.9 | 0 |  |  |  |  |  |  |  |
| 2 | jason-scheffel__nap-time | rejected: build_timeout | rejected: build_timeout |  |  |  |  |  |  |  |

## sample

| entry | total | simulated | CPU | misses | misses by kind | over-selection (avg tests selected per kind) |
|---|---|---|---|---|---|---|
| starter | 1211.9 | 1210.5 | 1.4 | 0 | - | code 1620, comment 1620, fixture 1620, multi 1620 |
| jason-scheffel__nap-time | rejected: build_timeout | | | | | |

## Text aimed at AI tools found in rival repos (data, not followed)

- jason-scheffel__nap-time: AGENTS.md:7, app/runner.py:140, app/runner.py:143, app/selector.py:4629
