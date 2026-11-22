#!/bin/sh
# Build the image and run it once on a set under the scorer's limits; print the score and the container's CPU.
#   tools/gate.sh [amd64|arm64] [set dir, default kit/sample] [tag]
set -eu
ARCH="${1:-amd64}"; SET="${2:-$PWD/kit/sample}"; TAG="${3:-rwx-core:$ARCH}"
mkdir -p .work
docker build -q --platform "linux/$ARCH" --tag "$TAG" . >/dev/null
START=$(date +%s)
docker run --rm --platform "linux/$ARCH" --network none --cpus 2 --memory 4096m --memory-swap 4096m \
  --pids-limit 256 --read-only --tmpfs /tmp:rw,size=64m,mode=1777 --cap-drop ALL \
  --security-opt no-new-privileges --user 65534:65534 --env HOME=/tmp \
  --volume "$SET/commits:/data/commits:ro" --volume "$SET/tests.json:/data/tests.json:ro" \
  "$TAG" > ".work/out-$ARCH.jsonl" 2> ".work/err-$ARCH.txt"
echo "wall $(( $(date +%s) - START )) s"
grep '^stats' ".work/err-$ARCH.txt" || tail -3 ".work/err-$ARCH.txt"
python3 kit/score.py "$SET" "$SET/answers.json" ".work/out-$ARCH.jsonl" | head -2
