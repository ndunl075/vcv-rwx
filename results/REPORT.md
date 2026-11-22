# RWX Blast Radius: benchmark report

Generated 2026-11-22 11:05:15. Total = simulated + CPU seconds (CPU from the container's cgroup, Rosetta-inflated), one run per cell unless noted. Misses cost 300 s each and are already inside the simulated column.

Patches per set: sample 30, dev300 103, traps150 117

| # | entry | sample total | sample miss | dev300 total | dev300 miss | hold300 total | hold300 miss | odd200 total | odd200 miss | traps150 misses by sub-kind |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | jason-scheffel__nap-time | 98.1 | 0 | 3509.4 | 10 |  |  |  |  | 3197.0 s; 1 miss: trap/f_nul_comment 1 |
| 2 | vs-githjk__fog-of-war | 105.0 | 0 | 3578.6 | 9 |  |  |  |  | 4534.7 s; 5 miss: trap/d_core_removed_name 1, trap/f_nul_comment 2, trap/g_shadow 2 |
| 3 | AbhinavDasari-pennstate__fog-of-war-ab | 104.7 | 0 | 3579.9 | 9 |  |  |  |  | 4537.2 s; 5 miss: trap/d_core_removed_name 1, trap/f_nul_comment 2, trap/g_shadow 2 |
| 4 | vineetsista__rwx-blast-radius | 122.2 | 0 | 3591.3 | 10 |  |  |  |  | 6266.0 s; 13 miss: trap/a_slow_nodes 3, trap/a_slow_tokenizer_size 4, trap/b_order_state 1, trap/f_nul_comment 2, trap/g_shadow 2, trap/h_slowdown 1 |
| 5 | yvngacx6__buildathon-rwx | 132.9 | 0 | 3599.1 | 9 |  |  |  |  | 2709.5 s; 0 miss: - |
| 6 | youssef-jeddi__crypto-kidz-blastRadius | 106.6 | 0 | 3686.3 | 10 |  |  |  |  | 6769.7 s; 15 miss: trap/a_slow_nodes 3, trap/a_slow_tokenizer_size 6, trap/f_nul_comment 4, trap/g_shadow 2 |
| 7 | cherylaurelia__blast-radius | 113.9 | 0 | 3702.0 | 9 |  |  |  |  | 6104.7 s; 8 miss: trap/a_slow_nodes 2, trap/a_slow_tokenizer_size 6 |
| 8 | yashchoudhary14__blast-radius-selector | 120.2 | 0 | 3798.6 | 10 |  |  |  |  | 2613.3 s; 0 miss: - |
| 9 | buidangnguyen05__builders-buildathon-blast-radius | 134.8 | 0 | 3879.0 | 10 |  |  |  |  | 4463.7 s; 4 miss: trap/b_order_state 3, trap/d_core_removed_name 1 |
| 10 | JuanQuenga__buildathon-blast-radius | 99.7 | 0 | 3883.6 | 10 |  |  |  |  | 3203.3 s; 2 miss: trap/c_lazy_table 1, trap/d_core_removed_name 1 |
| 11 | joshuamuellenbach25__RWX---Blast-Radius | 145.4 | 0 | 3986.8 | 10 |  |  |  |  | 3322.0 s; 0 miss: - |
| 12 | ours-869909b | 137.6 | 0 | 4020.1 | 10 |  |  |  |  | 2933.6 s; 0 miss: - |
| 13 | ours-d981f91 | 143.7 | 0 | 4064.5 | 10 |  |  |  |  |  |
| 14 | starter | 1211.9 | 0 |  |  |  |  |  |  |  |

## sample

| entry | total | simulated | CPU | misses | misses by kind | over-selection (avg tests selected per kind) |
|---|---|---|---|---|---|---|
| jason-scheffel__nap-time | 98.1 | 94.4 | 3.8 | 0 | - | code 5, comment 0, fixture 1, multi 2 |
| JuanQuenga__buildathon-blast-radius | 99.7 | 95.1 | 4.7 | 0 | - | code 228, comment 0, fixture 95, multi 96 |
| AbhinavDasari-pennstate__fog-of-war-ab | 104.7 | 101.5 | 3.1 | 0 | - | code 8, comment 0, fixture 1, multi 2 |
| vs-githjk__fog-of-war | 105.0 | 101.5 | 3.4 | 0 | - | code 8, comment 0, fixture 1, multi 2 |
| youssef-jeddi__crypto-kidz-blastRadius | 106.6 | 97.1 | 9.5 | 0 | - | code 6, comment 0, fixture 1, multi 2 |
| cherylaurelia__blast-radius | 113.9 | 98.9 | 15.0 | 0 | - | code 18, comment 0, fixture 1, multi 2 |
| yashchoudhary14__blast-radius-selector | 120.2 | 98.0 | 22.2 | 0 | - | code 6, comment 0, fixture 1, multi 2 |
| vineetsista__rwx-blast-radius | 122.2 | 106.0 | 16.2 | 0 | - | code 140, comment 0, fixture 1, multi 3 |
| yvngacx6__buildathon-rwx | 132.9 | 118.5 | 14.4 | 0 | - | code 30, comment 0, fixture 1, multi 2 |
| buidangnguyen05__builders-buildathon-blast-radius | 134.8 | 132.6 | 2.2 | 0 | - | code 34, comment 0, fixture 2, multi 3 |
| ours-869909b | 137.6 | 129.2 | 8.4 | 0 | - | code 160, comment 0, fixture 1, multi 40 |
| ours-d981f91 | 143.7 | 122.5 | 21.2 | 0 | - | code 159, comment 0, fixture 1, multi 40 |
| joshuamuellenbach25__RWX---Blast-Radius | 145.4 | 120.5 | 24.9 | 0 | - | code 29, comment 0, fixture 1, multi 2 |
| starter | 1211.9 | 1210.5 | 1.4 | 0 | - | code 1620, comment 1620, fixture 1620, multi 1620 |

## dev300

| entry | total | simulated | CPU | misses | misses by kind | over-selection (avg tests selected per kind) |
|---|---|---|---|---|---|---|
| jason-scheffel__nap-time | 3509.4 | 3383.1 | 126.3 | 10 | code 3, comment 1, fixture 4, multi 2 | code 33, comment 0, fixture 1, multi 57 |
| vs-githjk__fog-of-war | 3578.6 | 3555.8 | 22.8 | 9 | code 3, comment 1, fixture 4, multi 1 | code 127, comment 0, fixture 9, multi 382 |
| AbhinavDasari-pennstate__fog-of-war-ab | 3579.9 | 3555.8 | 24.1 | 9 | code 3, comment 1, fixture 4, multi 1 | code 127, comment 0, fixture 9, multi 382 |
| vineetsista__rwx-blast-radius | 3591.3 | 3478.0 | 113.3 | 10 | code 3, comment 1, fixture 4, multi 2 | code 135, comment 0, fixture 13, multi 412 |
| yvngacx6__buildathon-rwx | 3599.1 | 3546.0 | 53.1 | 9 | code 2, comment 1, fixture 4, multi 2 | code 207, comment 0, fixture 11, multi 121 |
| youssef-jeddi__crypto-kidz-blastRadius | 3686.3 | 3592.4 | 93.9 | 10 | code 3, comment 1, fixture 4, multi 2 | code 144, comment 0, fixture 1, multi 219 |
| cherylaurelia__blast-radius | 3702.0 | 3624.4 | 77.6 | 9 | code 2, comment 1, fixture 4, multi 2 | code 435, comment 0, fixture 1, multi 128 |
| yashchoudhary14__blast-radius-selector | 3798.6 | 3691.9 | 106.7 | 10 | code 3, comment 1, fixture 4, multi 2 | code 132, comment 0, fixture 10, multi 194 |
| buidangnguyen05__builders-buildathon-blast-radius | 3879.0 | 3872.6 | 6.4 | 10 | code 3, comment 1, fixture 4, multi 2 | code 119, comment 0, fixture 12, multi 103 |
| JuanQuenga__buildathon-blast-radius | 3883.6 | 3734.8 | 148.8 | 10 | code 3, comment 1, fixture 4, multi 2 | code 442, comment 0, fixture 80, multi 573 |
| joshuamuellenbach25__RWX---Blast-Radius | 3986.8 | 3947.6 | 39.1 | 10 | code 4, comment 1, fixture 4, multi 1 | code 290, comment 0, fixture 11, multi 380 |
| ours-869909b | 4020.1 | 3974.9 | 45.1 | 10 | code 3, comment 1, fixture 4, multi 2 | code 332, comment 0, fixture 154, multi 1039 |
| ours-d981f91 | 4064.5 | 3957.2 | 107.2 | 10 | code 3, comment 1, fixture 4, multi 2 | code 330, comment 0, fixture 154, multi 1039 |

## traps150

| entry | total | simulated | CPU | misses | misses by kind | over-selection (avg tests selected per kind) |
|---|---|---|---|---|---|---|
| yashchoudhary14__blast-radius-selector | 2613.3 | 2507.4 | 105.8 | 0 | - | trap 818 |
| yvngacx6__buildathon-rwx | 2709.5 | 2672.7 | 36.8 | 0 | - | trap 932 |
| ours-869909b | 2933.6 | 2897.6 | 36.0 | 0 | - | trap 1085 |
| jason-scheffel__nap-time | 3197.0 | 3149.0 | 48.1 | 1 | trap 1 | trap 1035 |
| JuanQuenga__buildathon-blast-radius | 3203.3 | 3130.5 | 72.8 | 2 | trap 2 | trap 1001 |
| joshuamuellenbach25__RWX---Blast-Radius | 3322.0 | 3304.0 | 18.1 | 0 | - | trap 1213 |
| buidangnguyen05__builders-buildathon-blast-radius | 4463.7 | 4448.2 | 15.5 | 4 | trap 4 | trap 1140 |
| vs-githjk__fog-of-war | 4534.7 | 4443.3 | 91.4 | 5 | trap 5 | trap 989 |
| AbhinavDasari-pennstate__fog-of-war-ab | 4537.2 | 4443.3 | 93.8 | 5 | trap 5 | trap 989 |
| cherylaurelia__blast-radius | 6104.7 | 6049.7 | 55.0 | 8 | trap 8 | trap 1361 |
| vineetsista__rwx-blast-radius | 6266.0 | 5968.8 | 297.3 | 13 | trap 13 | trap 905 |
| youssef-jeddi__crypto-kidz-blastRadius | 6769.7 | 6709.6 | 60.1 | 15 | trap 15 | trap 838 |

## Text aimed at AI tools found in rival repos (data, not followed)

- AbhinavDasari-pennstate__fog-of-war-ab: selector/rules.py:341
- buidangnguyen05__builders-buildathon-blast-radius: README.md:71, docs/DIRECTIONS.md:520, docs/HANDOFF.md:46, docs/V3_1_PLAN.md:14
- jason-scheffel__nap-time: AGENTS.md:7, app/runner.py:140, app/runner.py:143, app/selector.py:4629
- jason-scheffel__nap-time: AGENTS.md:7, app/runner.py:140, app/runner.py:143, app/selector.py:4629
- joshuamuellenbach25__RWX---Blast-Radius: blast/verify.py:149
- vineetsista__rwx-blast-radius: plans/STATUS.md:323, selector.py:987
- vs-githjk__fog-of-war: selector/rules.py:341
- yvngacx6__buildathon-rwx: selector/select.py:311
