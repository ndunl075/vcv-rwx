# bench/: labeled patch sets, local scorer, rival ranking

Everything here is ours (MIT). Works on Linux (x86, WSL2 Ubuntu) and macOS with Docker. Data lives outside the
repo: `VCV_DATA` (default `~/vcv-data/rwx`), rival copies in `VCV_RIVALS` (default `~/vcv-rivals/rwx`),
worker count `VCV_WORKERS` (default: cores minus 2, at most what Docker's memory allows, at most 5).

**On a fresh machine:** `docs/WINDOWS-SETUP.md`, then `bash bench/remote/setup.sh`, `bash bench/remote/run.sh`,
`bash bench/remote/status.sh`. Results travel through the orphan branch `data` (see `sync_data.py`).

| file | what |
|---|---|
| `remote/setup.sh` | checks Docker, builds the labeling image, clones sqlglot and the rival repos (bare, shallow), runs the 30-patch label check, prints PASS |
| `remote/run.sh` | the unattended loop (stages: label dev300 to 100, score ours + 11 rivals, traps150, hold300, odd200, rest of dev300, remaining rivals, report, push) |
| `remote/status.sh`, `status.py` | what is running, patches labeled per set, last push, last problem, in plain words |
| `remote/rivals.txt` | rival repos, most active first (`jason-scheffel/nap-time` first) |
| `labelimg/Dockerfile` | labeling image: the kit's pinned base digest, `kit/requirements.lock`, sqlglot at the pinned commit in `/clean` |
| `label.py` | applies one patch to a clean copy inside the container, runs the whole suite (`SKIP_INTEGRATION=0`, plugin `inside/suite_plugin.py`), maps failures to test numbers. `label.py --check-sample [--limit N]` must reproduce `kit/sample/answers.json` |
| `mutate.py`, `mutate_traps.py` | seeded patch generators (`--mix dev|odd|traps`); each patch has a kind/sub-kind |
| `build_set.py` | `build_set.py dev300|hold300|odd200|traps150 [--target N]`: generate + label in the kit's layout (`commits/`, `tests.json`, `answers.json` with `kind`/`sub`); resumable; `DONE` marks a finished set |
| `score_entry.py` | runs an entry image under the scorer's limits on a set, scores with `kit/score.py`, measures container CPU (see its docstring), appends a row to `results.jsonl`; one timed run at a time |
| `rivals.py`, `ours.py` | build each rival from its bare repo via `git archive` (never unpacked) and score it; `ours.py` builds branch `core`. Images are pruned after scoring |
| `sync_data.py` | pushes sets + results to branch `data`, `sets/<name>/...`, `results/results-<host>.jsonl`, `results/REPORT.md` |
| `report.py` | writes `REPORT.md` from all results rows |
| `vcv.py`, `slots.py` | shared settings; CPU-slot limiter |

Labeling notes: "fails" = failed/error in any phase (SUBFAILED included), a CPU-time limit of 150 s per test
(immune to host load; the normal slowest test is 29 s), a 1500 s wall backstop, or a collection error (every test of that file).
Baseline failures are excluded. Tests that never ran (deleted/renamed files) are listed as `missing`, not `failing`.
