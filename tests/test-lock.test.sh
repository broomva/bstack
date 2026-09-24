#!/usr/bin/env bash
# tests/test-lock.test.sh — BRO-2542. Runs the test-lock contract suite
# (tests/test_test_lock.py, stdlib unittest) under the `tests/*.test.sh` CI job,
# which knows nothing about python suites: a suite with no wrapper is a dead gate.
#
# There is deliberately NO skip path, for the reason tests/ask-ledger.test.sh
# gives: a wrapper that warns and exits 0 reports green for a suite that never
# ran. The suite needs only python3 and git, and a missing one FAILS the run.
#
# Three guards beyond "unittest exited 0":
#   - the result line must be exactly "OK". "OK (skipped=1)" is a suite that
#     silently stopped asserting, and it still reports green.
#   - no test may be skipped, expected-to-fail or unexpectedly-passing.
#   - a floor on "Ran N tests". unittest exits 0 on "Ran 0 tests" for a module
#     whose classes stopped being collected, so the exit code alone cannot tell
#     "63 ran" from "none ran".
#
# Run from anywhere:
#   bash tests/test-lock.test.sh

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BSTACK_REPO" || exit 1
MODULE="tests.test_test_lock"
MIN_TESTS=63

PASS=0
FAIL=0
FAILED_TESTS=()
assert_pass() { PASS=$((PASS + 1)); echo "  [pass] $1"; }
assert_fail() { FAIL=$((FAIL + 1)); FAILED_TESTS+=("$1"); echo "  [FAIL] $1"; [ -n "${2:-}" ] && echo "         $2"; }

echo "── test-lock contract suite (BRO-2542) ────────────────────────────"

for f in scripts/test_lock.py scripts/test-lock-hook.sh bin/bstack-test-lock tests/test_test_lock.py; do
    [ -f "$f" ] || { echo "  [FAIL] missing $f"; exit 1; }
done
for tool in python3 git; do
    command -v "$tool" >/dev/null 2>&1 || { echo "  [FAIL] $tool not on PATH — not skipping"; exit 1; }
done

LOG="$(mktemp)"; trap 'rm -f "$LOG"' EXIT
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v "$MODULE" >"$LOG" 2>&1
RC=$?
tail -n 8 "$LOG"
echo

if [ "$RC" -eq 0 ]; then
    assert_pass "unittest exits clean"
else
    assert_fail "unittest exits clean" "$(grep -E '^(FAIL|ERROR):' "$LOG" | head -10 | tr '\n' ' ')"
fi

RESULT="$(grep -E '^(OK|FAILED)( \(.*\))?$' "$LOG" | tail -1)"
if [ "$RESULT" = "OK" ]; then
    assert_pass "result line is exactly OK"
else
    assert_fail "result line is exactly OK" "got: ${RESULT:-<none>}"
fi

if grep -qE '\.\.\. (skipped|expected failure|unexpected success)' "$LOG"; then
    assert_fail "no test silently stopped asserting" \
        "$(grep -E '\.\.\. (skipped|expected failure|unexpected success)' "$LOG" | head -5 | tr '\n' ' ')"
else
    assert_pass "no test silently stopped asserting"
fi

COUNT="$(grep -oE '^Ran [0-9]+ tests?' "$LOG" | tail -1 | grep -oE '[0-9]+')"
COUNT="${COUNT:-0}"
if [ "$COUNT" -ge "$MIN_TESTS" ]; then
    assert_pass "suite size >= $MIN_TESTS (got $COUNT)"
else
    assert_fail "suite size >= $MIN_TESTS" "only $COUNT ran — collection may have silently shrunk"
fi

# The suite imports test_lock directly for a few pure-function tests; every
# consumer invokes it as a subprocess. A shebang or exec-bit fault in the
# shipped entry points is invisible to the first and fatal to the second.
if python3 scripts/test_lock.py --help >/dev/null 2>&1; then
    assert_pass "test_lock.py --help runs as a subprocess"
else
    assert_fail "test_lock.py --help runs as a subprocess"
fi
if [ -x bin/bstack-test-lock ] && [ -x scripts/test-lock-hook.sh ]; then
    assert_pass "bin/bstack-test-lock and scripts/test-lock-hook.sh are executable"
else
    assert_fail "bin/bstack-test-lock and scripts/test-lock-hook.sh are executable"
fi

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
