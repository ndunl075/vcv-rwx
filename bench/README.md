# bench/: labeled patch sets, local scorer, rival ranking

Everything here is ours (MIT). Data lives outside the repo in `~/Developer/vcv-data/rwx/`.

| file | what |
|---|---|
| `labelimg/Dockerfile` | labeling image: the kit's pinned base digest, `kit/requirements.lock`, sqlglot at the pinned commit in `/clean`. `docker build -t rwx-label:arm64 bench/labelimg` (native; the digest has an arm64 variant) |
| `label.py` | applies one patch to a clean copy inside the container, runs the whole suite (`SKIP_INTEGRATION=0`, plugin in `inside/suite_plugin.py`), maps failures to test numbers. `label.py --check-sample` must reproduce `kit/sample/answers.json` |
| `mutate.py`, `mutate_traps.py` | seeded patch generators (`--mix dev|odd|traps`); each patch has a kind/sub-kind |
| `build_set.py`, `build_set.sh` | generate + label sets in the kit's layout (`commits/`, `tests.json`, `answers.json` with `kind`, `sub`); resumable; `DONE` marks a finished set |
| `score_entry.sh` | runs an entry image under the scorer's limits on a set, scores with `kit/score.py`, measures container CPU (cgroup of a per-run parent, read through `colima ssh`), appends a row to `results.jsonl`; takes `~/Developer/vcv-rivals/bench.lock` |
| `rivals.sh` | builds each rival from its bare repo via `git archive` (never unpacked), scores it on every finished set; `--image IMG --name NAME` scores our own |
| `report.py` | writes `REPORT.md` from `results.jsonl` |

Labeling notes: "fails" = failed/error in any phase (SUBFAILED included), a CPU-time limit of 150 s per test
(immune to host load; the normal slowest test is 29 s), a 1500 s wall backstop, or a collection error (every test of that file).
Baseline failures are excluded. Tests that never ran (deleted/renamed files) are listed as `missing`, not `failing`.
