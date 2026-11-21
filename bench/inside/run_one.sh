#!/bin/sh
# Usage (inside container): run_one.sh <patch-or-"-"> <result.json>
# Applies the patch to a fresh copy of the pinned sqlglot and runs the whole suite.
set -u
PATCH="$1"; OUT="$2"
ONLY_ARGS=""; [ -f /in/only.txt ] && ONLY_ARGS="@/in/only.txt"
mkdir -p /w && (cd /clean && tar cf - --exclude=.git .) | (cd /w && tar xf -)
cd /w
git init -q . >/dev/null 2>&1
if [ "$PATCH" != "-" ]; then
  if ! git apply --whitespace=nowarn --recount "$PATCH" 2>/tmp/apply.err; then
    echo '{"apply_error": true}' > "$OUT"; cat /tmp/apply.err >&2; exit 3
  fi
fi
export PYTHONPATH=/bench:/w SKIP_INTEGRATION=0 RESULT_PATH="$OUT.tmp" PYTHONDONTWRITEBYTECODE=1
timeout -k 10 "${SUITE_TIMEOUT:-1500}" python -m pytest ${ONLY_ARGS:-tests} -p suite_plugin -p no:cacheprovider -q --timeout="${TEST_TIMEOUT:-300}" --continue-on-collection-errors >/tmp/pytest.out 2>&1
rc=$?
tail -5 /tmp/pytest.out >&2
if [ -f "$OUT.tmp" ]; then mv "$OUT.tmp" "$OUT"; else echo "{\"no_result\": true, \"rc\": $rc}" > "$OUT"; fi
