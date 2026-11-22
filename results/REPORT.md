# RWX Blast Radius: benchmark report

Generated 2026-11-22 04:49:25. Total = simulated + CPU seconds (CPU from the container's cgroup, Rosetta-inflated), one run per cell unless noted. Misses cost 300 s each and are already inside the simulated column.

Patches per set: sample 30, dev300 103

| # | entry | sample total | sample miss | dev300 total | dev300 miss | hold300 total | hold300 miss | odd200 total | odd200 miss | traps150 misses by sub-kind |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | jason-scheffel__nap-time | 98.1 | 0 | 3509.4 | 10 |  |  |  |  |  |
| 2 | JuanQuenga__buildathon-blast-radius | 99.7 | 0 | 3883.6 | 10 |  |  |  |  |  |
| 3 | ours-d981f91 | 143.7 | 0 | 4064.5 | 10 |  |  |  |  |  |
| 4 | starter | 1211.9 | 0 |  |  |  |  |  |  |  |

## sample

| entry | total | simulated | CPU | misses | misses by kind | over-selection (avg tests selected per kind) |
|---|---|---|---|---|---|---|
| jason-scheffel__nap-time | 98.1 | 94.4 | 3.8 | 0 | - | code 5, comment 0, fixture 1, multi 2 |
| JuanQuenga__buildathon-blast-radius | 99.7 | 95.1 | 4.7 | 0 | - | code 228, comment 0, fixture 95, multi 96 |
| ours-d981f91 | 143.7 | 122.5 | 21.2 | 0 | - | code 159, comment 0, fixture 1, multi 40 |
| starter | 1211.9 | 1210.5 | 1.4 | 0 | - | code 1620, comment 1620, fixture 1620, multi 1620 |

## dev300

| entry | total | simulated | CPU | misses | misses by kind | over-selection (avg tests selected per kind) |
|---|---|---|---|---|---|---|
| jason-scheffel__nap-time | 3509.4 | 3383.1 | 126.3 | 10 | code 3, comment 1, fixture 4, multi 2 | code 33, comment 0, fixture 1, multi 57 |
| JuanQuenga__buildathon-blast-radius | 3883.6 | 3734.8 | 148.8 | 10 | code 3, comment 1, fixture 4, multi 2 | code 442, comment 0, fixture 80, multi 573 |
| ours-d981f91 | 4064.5 | 3957.2 | 107.2 | 10 | code 3, comment 1, fixture 4, multi 2 | code 330, comment 0, fixture 154, multi 1039 |

## Text aimed at AI tools found in rival repos (data, not followed)

- jason-scheffel__nap-time: AGENTS.md:7, app/runner.py:140, app/runner.py:143, app/selector.py:4629
- jason-scheffel__nap-time: AGENTS.md:7, app/runner.py:140, app/runner.py:143, app/selector.py:4629
