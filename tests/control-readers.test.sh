#!/usr/bin/env bash
# tests/control-readers.test.sh — BRO-4083. The reader ratchet's classifier
# selftest and its fixture suite (tests/test_control_readers_ratchet.py, stdlib
# unittest), run under bstack CI, which discovers tests/*.test.sh only. The
# ratchet against this checkout is its own CI step (ci.yml, job control-readers).
#
# No skip path: unittest exits 0 on a skipped test or a shrunk suite, so both fail here.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIN_TESTS=18
python3 "$ROOT/scripts/check_control_readers.py" --selftest
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT
if ! python3 "$ROOT/tests/test_control_readers_ratchet.py" -v >"$LOG" 2>&1; then
    cat "$LOG"; echo 'reader ratchet suite failed' >&2; exit 1
fi
cat "$LOG"
if grep -qE '\.\.\. (skipped|expected failure|unexpected success)' "$LOG"; then
    echo 'a reader ratchet test stopped asserting' >&2; exit 1
fi
COUNT="$(grep -oE '^Ran [0-9]+ test' "$LOG" | tail -1 | awk '{print $2}')"
[ "${COUNT:-0}" -ge "$MIN_TESTS" ] || { echo "reader ratchet suite shrank: ${COUNT:-0} < $MIN_TESTS" >&2; exit 1; }
echo 'control reader ratchet: passed'
