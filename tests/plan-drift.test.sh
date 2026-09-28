#!/usr/bin/env bash
# tests/plan-drift.test.sh — BRO-2542. Runs the plan_drift unittest suite under
# bstack CI, which discovers tests/*.test.sh and nothing else: a python suite
# with no wrapper is a dead gate.
#
# There is deliberately NO skip path (same philosophy as ask-ledger.test.sh). The
# suite is stdlib-only, so python3 is the only dependency; if it is missing the
# wrapper fails. Three guards beyond "unittest exited 0":
#   - the summary line must be a bare "OK". "OK (skipped=N)" or
#     "OK (expected failures=N)" FAILS: a suite that silently stops asserting
#     still reports green, which is worse than one that is absent.
#   - a floor on "Ran N tests". unittest exits 0 on an empty module, so the exit
#     code alone cannot tell "26 ran" from "0 collected".
#   - the CLI and the bin shim must run as subprocesses, not merely import.
#
# Run from anywhere:
#   bash tests/plan-drift.test.sh

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODULE="tests.test_plan_drift"
SUITE="$BSTACK_REPO/tests/test_plan_drift.py"
TOOL_PY="$BSTACK_REPO/scripts/plan_drift.py"
SHIM="$BSTACK_REPO/bin/bstack-plan-drift"
MIN_TESTS=27

PASS=0
FAIL=0
FAILED_TESTS=()
assert_pass() { PASS=$((PASS + 1)); echo "  [pass] $1"; }
assert_fail() { FAIL=$((FAIL + 1)); FAILED_TESTS+=("$1"); echo "  [FAIL] $1"; [ -n "${2:-}" ] && echo "         $2"; }

echo "── plan-drift suite (BRO-2542) ────────────────────────────────────"

for f in "$SUITE" "$TOOL_PY" "$SHIM"; do
    [ -f "$f" ] || { echo "  [FAIL] missing $f"; exit 1; }
done
command -v python3 >/dev/null 2>&1 || { echo "  [FAIL] python3 not found — not skipping"; exit 1; }

LOG="$(mktemp)"; trap 'rm -f "$LOG"' EXIT
(cd "$BSTACK_REPO" && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v "$MODULE") >"$LOG" 2>&1
RC=$?

if [ "$RC" -eq 0 ]; then
    assert_pass "unittest suite exits clean"
else
    assert_fail "unittest suite exits clean" "$(tail -15 "$LOG")"
fi

RESULT="$(grep -E '^(OK|FAILED)' "$LOG" | tail -1)"
if [ "$RESULT" = "OK" ]; then
    assert_pass "result line is a bare OK"
else
    assert_fail "result line is a bare OK" "got: ${RESULT:-<none>}"
fi

if grep -qE '\.\.\. (skipped|expected failure)|skipped=|expected failures=' "$LOG"; then
    assert_fail "no test silently stopped asserting" "$(grep -E 'skipped|expected failure' "$LOG" | head -5 | tr '\n' ' ')"
else
    assert_pass "no test silently stopped asserting"
fi

COUNT="$(grep -oE '^Ran [0-9]+ tests?' "$LOG" | tail -1 | grep -oE '[0-9]+')"
COUNT="${COUNT:-0}"
if [ "$COUNT" -ge "$MIN_TESTS" ]; then
    assert_pass "suite size >= $MIN_TESTS (ran $COUNT)"
else
    assert_fail "suite size >= $MIN_TESTS" "only $COUNT ran — the suite may have silently shrunk"
fi

# The suite imports plan_drift directly; every consumer invokes it as a process.
# A shebang or syntax fault that breaks the second is invisible to the first.
if python3 "$TOOL_PY" --help >/dev/null 2>&1; then
    assert_pass "plan_drift.py --help runs as a subprocess"
else
    assert_fail "plan_drift.py --help runs as a subprocess"
fi
if [ -x "$SHIM" ] && "$SHIM" --help >/dev/null 2>&1; then
    assert_pass "bin/bstack-plan-drift is executable and dispatches"
else
    assert_fail "bin/bstack-plan-drift is executable and dispatches"
fi

echo
tail -4 "$LOG" | sed 's/^/  | /'
echo
echo "── Summary ────────────────────────────────────────────────────────"
echo "  passed: $PASS"
echo "  failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
    echo "  failed tests:"
    for t in "${FAILED_TESTS[@]}"; do
        echo "    - $t"
    done
    exit 1
fi
exit 0
