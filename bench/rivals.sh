#!/bin/sh
# Build + score rivals (list.txt order, nap-time first) on every finished set, again and again as sets
# appear. Stop with: touch ~/Developer/vcv-data/rwx/STOP
#   nohup caffeinate -is sh bench/rivals.sh --first 30 > ~/Developer/vcv-data/rwx/rivals.log 2>&1 &
HERE=$(cd "$(dirname "$0")" && pwd)
DATA="$HOME/Developer/vcv-data/rwx"
rm -f "$DATA/STOP"
while [ ! -e "$DATA/STOP" ]; do
  python3 "$HERE/rivals.py" "$@"
  [ -n "$ONCE" ] && break
  sleep 600
done
