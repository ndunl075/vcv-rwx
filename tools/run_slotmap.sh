#!/bin/sh
# Builds maps/slots.json.gz from the pinned commit, inside the labeling image (it holds the pristine checkout in /clean):
#   1. the slots the tracer can see after importing all of sqlglot            (tracked.txt)
#   2. every test alone in a fresh pytest process, slot reads recorded         (cold-<i>.jsonl.gz)
#   3. the whole suite in one pytest session, reads recorded per test          (session/)
# and packs them.  The state of the pinned import (maps/state.json.gz) is not built here: the Dockerfile builds it
# from the image's own tree (python -m selector.state), so that it is made by the interpreter that compares.
#   tools/run_slotmap.sh [labeling image, default rwx-label:native] [cold workers, default 2]
set -eu
cd "$(dirname "$0")/.."
IMAGE="${1:-rwx-label:native}"; WORKERS="${2:-2}"
OUT="$PWD/.work/slotmap"
mkdir -p "$OUT"
rm -rf "$OUT/session" "$OUT"/cold-*.jsonl.gz
cp kit/sample/tests.json "$OUT/tests.json"
run() { docker run --rm --cpus "${RWX_CPUS:-1.5}" -v "$PWD/tools/slotmap:/tools:ro" -v "$OUT:/out" "$IMAGE" python /tools/trace.py "$@"; }
run tracked /clean /out/tracked.txt
i=0
while [ "$i" -lt "$WORKERS" ]; do
  run cold /clean /out/tests.json /out "$i" "$WORKERS" > "$OUT/log-$i.txt" 2>&1 &
  i=$((i + 1))
done
wait
run session /clean /out/tests.json /out > "$OUT/log-session.txt" 2>&1
python3 tools/slotmap/pack.py "$OUT/tests.json" maps/slots.json.gz "$OUT"
