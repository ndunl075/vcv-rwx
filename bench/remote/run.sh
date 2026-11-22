#!/usr/bin/env bash
# The unattended benchmark loop. Safe to run again: it refuses to start twice and resumes where it stopped.
#   bash bench/remote/run.sh            start in the background (log: $VCV_DATA/run.log)
#   bash bench/remote/run.sh --stop     ask it to stop (finishes the current step first)
# Stages, each followed by a push of the results to the `data` branch:
#   label dev300 (first 100) -> score ours + 11 most active rivals on sample/dev300 -> traps150 -> hold300 ->
#   odd200 -> rest of dev300 -> the remaining rivals, 10 at a time -> report -> repeat.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BENCH=$(cd "$HERE/.." && pwd)
REPO=$(cd "$BENCH/.." && pwd)
export VCV_DATA=${VCV_DATA:-$HOME/vcv-data/rwx}
export VCV_RIVALS=${VCV_RIVALS:-$HOME/vcv-rivals/rwx}
DATA=$VCV_DATA
mkdir -p "$DATA"
LOG=$DATA/run.log
PIDF=$DATA/run.pid

if [ "${1:-}" = "--stop" ]; then
  touch "$DATA/STOP"
  echo "Asked the loop to stop. It will stop after the current step (can take several minutes)."
  exit 0
fi

if [ -z "${VCV_RUN_CHILD:-}" ]; then
  if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
    echo "Already running (process $(cat "$PIDF")). Check with: bash bench/remote/status.sh"
    exit 0
  fi
  rm -f "$DATA/STOP"
  # keep the machine awake while it runs, where the tool exists (Linux/WSL: nothing to do, Windows power settings rule)
  PRE=""; command -v caffeinate >/dev/null 2>&1 && PRE="caffeinate -is"
  VCV_RUN_CHILD=1 nohup $PRE bash "$HERE/run.sh" "$@" >> "$LOG" 2>&1 &
  echo $! > "$PIDF"
  echo "Started in the background (process $!). Log: $LOG"
  echo "Check on it with: bash bench/remote/status.sh"
  exit 0
fi

# ---------------------------------------------------------------- child: the loop
echo $$ > "$PIDF"
FIRST=11            # nap-time + the ten most active rivals (rivals.txt order)
CHUNK=10
LABEL_TARGET=100
if [ -n "${VCV_SMOKE:-}" ]; then FIRST=1; LABEL_TARGET=2; CHUNK=1; fi
SETS_ALL="sample,dev300,hold300,odd200,traps150"
SKIP=",${VCV_SKIP_SETS:-},"   # e.g. VCV_SKIP_SETS=odd200,hold300

log()   { echo "[$(date '+%F %T')] $*"; }
err()   { echo "$(date '+%F %T') $*" >> "$DATA/last_error"; log "PROBLEM: $*"; }
stage() { echo "$*" > "$DATA/stage.txt"; log "== $*"; }
stopped() { [ -e "$DATA/STOP" ]; }
step()  { "$@" || err "step failed ($?): $*"; }
push()  { step python3 "$BENCH/sync_data.py" --quiet; }
label() { stopped && return; case "$SKIP" in *",$1,"*) return;; esac; stage "labeling $1 ${2:+(first $2 patches)}"; step nice -n 5 python3 "$BENCH/build_set.py" "$1" ${2:+--target "$2"}; push; }
score_top() { stopped && return; stage "scoring our entry and the $FIRST most active rivals on: $1"
  step python3 "$BENCH/ours.py" --sets "$1"
  step python3 "$BENCH/rivals.py" --first "$FIRST" --sets "$1"
  step python3 "$BENCH/report.py"; push; }
score_rest() { stopped && return
  local ptr; ptr=$(cat "$DATA/rival_ptr" 2>/dev/null || echo "$FIRST")
  stage "scoring more rivals (#$((ptr+1)) to #$((ptr+CHUNK))) on all finished sets"
  step python3 "$BENCH/rivals.py" --skip "$ptr" --first $((ptr+CHUNK)) --sets "$SETS_ALL"
  ptr=$((ptr+CHUNK)); [ "$ptr" -ge "$(grep -c . "$BENCH/remote/rivals.txt")" ] && ptr=$FIRST
  echo "$ptr" > "$DATA/rival_ptr"
  step python3 "$BENCH/report.py"; push; }

# push every 15 minutes in the background, whatever stage we are in
( while true; do sleep 900; python3 "$BENCH/sync_data.py" --quiet || true; done ) &
SYNCPID=$!
trap 'kill $SYNCPID 2>/dev/null; rm -f "$PIDF"; echo "stopped" > "$DATA/stage.txt"' EXIT

SELF_HASH=$(cksum < "$HERE/run.sh")
while ! stopped; do
  # pick up new scripts / new commits of our own entry
  git -C "$REPO" pull -q --ff-only >/dev/null 2>&1 || true
  if [ "$(cksum < "$HERE/run.sh")" != "$SELF_HASH" ]; then
    log "run.sh changed on disk, restarting"; kill $SYNCPID 2>/dev/null
    exec env VCV_RUN_CHILD=1 bash "$HERE/run.sh"
  fi
  label dev300 "$LABEL_TARGET"
  score_top "sample,dev300"
  label traps150;  score_top "$SETS_ALL"
  label hold300;   score_top "$SETS_ALL"
  label odd200;    score_top "$SETS_ALL"
  label dev300;    score_top "$SETS_ALL"
  score_rest
  [ -n "${VCV_SMOKE:-}" ] && break
  stage "idle (a pass finished); waiting 10 minutes"
  for _ in $(seq 1 60); do stopped && break; sleep 10; done
done
step python3 "$BENCH/report.py"; push
stage "finished"
log "loop ended"
