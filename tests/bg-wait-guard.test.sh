#!/usr/bin/env bash
# bg-wait-guard.test.sh — BRO-2815. The opt-in Stop guard that refuses a turn ending
# on a background-wait promise while a background task is still in flight.
#
# Two arms, both against the REAL hook as hooks/hooks.json registers it:
#   cases   replays of the four reported Paseo stalls (blocked) and real normal
#           endings (not blocked), in both Stop-input modes, plus the cap, the gate
#           and fail-open behaviour.
#   mutate  each rule broken in turn (the matcher, the running-task check, the
#           ledger, the cap, the gate, every matcher exclusion, the registration);
#           every mutant must turn the cases red.
# The harness lives in tests/bg_wait_guard_cases.py; fixtures in tests/fixtures/bg-wait.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
command -v python3 >/dev/null 2>&1 || { echo "python3 required"; exit 1; }

rc=0
echo "== bg-wait guard: cases =="
python3 -I "$REPO/tests/bg_wait_guard_cases.py" "$REPO" cases || rc=1
echo "== bg-wait guard: mutation proof =="
python3 -I "$REPO/tests/bg_wait_guard_cases.py" "$REPO" mutate || rc=1
exit "$rc"
