#!/bin/sh
# Runs a comparison trace in the native labeling image (arm64 build of the kit's pinned image), from the checkout in /clean.
#   tools/cmpmap/run.sh cold <out dir> <worker> <workers> [ids file | -] [seeds]
#   tools/cmpmap/run.sh session <out dir> [seed]
# <out dir> is under the repo (default .work/cmp); outputs are packed by tools/cmpmap/pack.py.
set -eu
cd "$(dirname "$0")/../.."
MODE="$1"; OUT="$2"; shift 2
mkdir -p "$OUT"
IMG="${RWX_LABEL_IMAGE:-rwx-label:native}"
case "$MODE" in
  cold) ARGS="cold /clean /work/kit/sample/tests.json /out $1 $2 ${3:--} ${4:-1,2}" ;;
  session) ARGS="session /clean /work/kit/sample/tests.json /out ${1:-3}" ;;
esac
exec docker run --rm --cpus "${RWX_CPUS:-1}" -v "$PWD":/work:ro -v "$(cd "$OUT" && pwd)":/out \
  -v "$PWD/.work/idsfiles":/idsfiles:ro --user 0 "$IMG" python /work/tools/cmpmap/trace.py $ARGS
