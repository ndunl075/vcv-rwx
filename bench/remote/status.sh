#!/usr/bin/env bash
# Prints what the benchmark machine is doing, in plain words.
HERE=$(cd "$(dirname "$0")" && pwd)
exec python3 "$HERE/../status.py" "$@"
