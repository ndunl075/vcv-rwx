#!/bin/sh
# Runs tools/build_maps.py in the dev image against the repo checkout (writes .work/cold_raw.json.gz).
set -e
cd "$(dirname "$0")/.."
docker run --rm --platform linux/amd64 --cpus "${RWX_JOBS:-4}" -v "$PWD":/work -e RWX_JOBS="${RWX_JOBS:-4}" ${RWX_ENV} \
  rwx-core-dev python /work/tools/build_maps.py
