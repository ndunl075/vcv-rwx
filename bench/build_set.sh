#!/bin/sh
# Generate + label patch sets under ~/Developer/vcv-data/rwx/<name>/ . Resumable; sets run one at a time
# (lockf), nice'd, 6 labeling containers at once.
#   nohup caffeinate -is sh bench/build_set.sh dev300 hold300 odd200 > ~/Developer/vcv-data/rwx/build.log 2>&1 &
HERE=$(cd "$(dirname "$0")" && pwd)
DATA="$HOME/Developer/vcv-data/rwx"
mkdir -p "$DATA"
JOBS=${JOBS:-6}
for name in "$@"; do
  case "$name" in
    dev300)    args="--seed 1001 --count 300 --mix dev" ;;
    hold300)   args="--seed 2002 --count 300 --mix dev" ;;
    odd200)    args="--seed 3003 --count 200 --mix odd" ;;
    traps150)  args="--seed 4004 --count 900 --mix traps --target 150" ;;
    *) echo "unknown set $name" >&2; continue ;;
  esac
  echo "== $name $(date)"
  lockf "$DATA/build.lock" nice -n 5 python3 "$HERE/build_set.py" "$DATA/$name" $args --jobs "$JOBS"
done
