#!/usr/bin/env bash
# One-time (and safe to repeat) setup of a benchmark machine: Linux / WSL2 Ubuntu / macOS with Docker.
#   bash bench/remote/setup.sh
# Prints PASS at the end only if the labeling check reproduced the organizers' answers for all 30 sample patches.
# SETUP_CHECK_N=2 checks only 2 patches (testing the plumbing) and then prints PARTIAL instead of PASS.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BENCH=$(cd "$HERE/.." && pwd)
REPO=$(cd "$BENCH/.." && pwd)
export VCV_DATA=${VCV_DATA:-$HOME/vcv-data/rwx}
export VCV_RIVALS=${VCV_RIVALS:-$HOME/vcv-rivals/rwx}
export GIT_TERMINAL_PROMPT=0
say()  { echo "$*"; }
bad()  { echo; echo "STOP: $*"; exit 1; }

say "1/6  Checking the tools..."
command -v git >/dev/null     || bad "git is missing. Install it:  sudo apt-get update && sudo apt-get install -y git"
command -v python3 >/dev/null || bad "python3 is missing. Install it:  sudo apt-get install -y python3"
python3 - <<'PY' || bad "python3 is too old (need 3.9 or newer)."
import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)
PY
command -v docker >/dev/null  || bad "docker is not available in this window. In Docker Desktop: Settings > Resources > WSL integration, switch on Ubuntu, then close and reopen this window."
docker info >/dev/null 2>&1   || bad "docker does not answer. Is Docker Desktop running (whale icon in the taskbar)? Start it, wait one minute, try again."
git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || bad "this is not a git checkout of the repository."
MEM=$(docker info --format '{{.MemTotal}}' 2>/dev/null)
if [ -n "$MEM" ] && [ "$MEM" -lt 9000000000 ]; then
  say "WARNING: Docker has only $((MEM/1073741824)) GB of memory. Labeling will run fewer jobs at once. To give it more, see docs/WINDOWS-SETUP.md (section: memory)."
fi
FREE=$(df -Pk "$HOME" | awk 'NR==2{print int($4/1048576)}')
[ "${FREE:-0}" -lt 25 ] && say "WARNING: only ${FREE} GB of disk free in your home folder. This needs about 25 GB free."
mkdir -p "$VCV_DATA" "$VCV_RIVALS"
say "     ok (data in $VCV_DATA, rival copies in $VCV_RIVALS)"

say "2/6  Building the labeling image (a few minutes the first time)..."
cp "$REPO/kit/requirements.lock" "$BENCH/labelimg/requirements.lock"
docker build -q -t rwx-label:native "$BENCH/labelimg" >/dev/null || bad "building the labeling image failed. Run 'docker build -t rwx-label:native bench/labelimg' to see why (usually no internet)."
say "     ok"

say "3/6  Getting sqlglot at the pinned commit..."
if [ ! -d "$VCV_DATA/sqlglot-pinned/.git" ]; then
  git clone -q https://github.com/tobymao/sqlglot "$VCV_DATA/sqlglot-pinned" || bad "could not download sqlglot (internet?)."
fi
git -C "$VCV_DATA/sqlglot-pinned" checkout -q eb11aec4be8cb2122afe713a4816662c3f0fb2fa 2>/dev/null || bad "could not check out the pinned sqlglot commit."
say "     ok"

say "4/6  Copying the rival repositories (small, shallow, bare copies; never opened on this machine)..."
RIVLIST=${VCV_RIVALS_LIST:-$BENCH/remote/rivals.txt}
n=0; fail=0
while read -r r; do
  [ -z "$r" ] && continue
  case "$r" in \#*) continue;; esac
  d="$VCV_RIVALS/${r//\//__}.git"
  [ -d "$d" ] && { n=$((n+1)); continue; }
  if git clone -q --bare --depth 1 "https://github.com/$r.git" "$d" 2>/dev/null; then n=$((n+1)); else fail=$((fail+1)); rm -rf "$d"; fi
done < "$RIVLIST"
say "     $n rival repos present, $fail could not be copied (deleted or private: fine)"

say "5/6  Checking that this machine labels exactly like the organizers (the slow step: 30 patches, about 20-60 minutes)..."
N=${SETUP_CHECK_N:-30}
if python3 "$BENCH/label.py" --check-sample --limit "$N" 2>"$VCV_DATA/setup-check.err" | tee "$VCV_DATA/setup-check.txt" | tail -3; then CHECK=ok; else CHECK=bad; fi

say "6/6  Result"
if [ "$CHECK" = ok ] && [ "$N" = 30 ]; then
  say "PASS: all 30 sample patches matched. Now start the loop:  bash bench/remote/run.sh"
elif [ "$CHECK" = ok ]; then
  say "PARTIAL: the $N patches checked matched, but the full 30-patch check was not run."
else
  say "FAIL: some patches did not match. Do not start the loop. Send the lines above and $VCV_DATA/setup-check.txt to Nico."
  exit 1
fi
