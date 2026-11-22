#!/bin/sh
# Generate + label patch sets under ~/Developer/vcv-data/rwx/<name>/ . Resumable. CPU budget: 2 cores total
# for everything (bench/slots.py), so at most 2 labeling containers, fewer while a rival is being scored.
#   nohup caffeinate -is sh bench/build_set.sh --check dev300:100 traps150 dev300 hold300 odd200 > ~/Developer/vcv-data/rwx/build.log 2>&1 &
# name:N stops after N accepted patches (the set is not marked DONE until it is fully labeled).
HERE=$(cd "$(dirname "$0")" && pwd)
DATA="$HOME/Developer/vcv-data/rwx"
mkdir -p "$DATA"
if [ "$1" = "--check" ]; then
  shift
  echo "== check-sample $(date)"
  nice -n 10 python3 "$HERE/label.py" --check-sample --jobs 2 > "$DATA/sample-check-arm64.txt" 2>&1
  tail -1 "$DATA/sample-check-arm64.txt"
fi
for spec in "$@"; do
  name=${spec%%:*}
  target=""
  case "$spec" in *:*) target="--target ${spec##*:}" ;; esac
  case "$name" in
    dev300)    args="--seed 1001 --count 300 --mix dev" ;;
    hold300)   args="--seed 2002 --count 300 --mix dev" ;;
    odd200)    args="--seed 3003 --count 200 --mix odd" ;;
    traps150)  args="--seed 4004 --count 280 --mix traps" ;;
    *) echo "unknown set $name" >&2; continue ;;
  esac
  echo "== $spec $(date)"
  lockf "$DATA/build.lock" nice -n 10 python3 "$HERE/build_set.py" "$DATA/$name" $args $target --jobs 2
done
