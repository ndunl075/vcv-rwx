#!/bin/sh
# score_entry.sh <image> <set-dir> [runs] [--entry NAME --commit SHA]
# Takes the shared benchmark lock around the timed runs (machine must be quiet).
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$HOME/Developer/vcv-rivals"
exec lockf "$HOME/Developer/vcv-rivals/bench.lock" python3 "$HERE/score_entry.py" "$@"
