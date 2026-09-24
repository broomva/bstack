#!/usr/bin/env bash
# tests/bands.test.sh — BRO-2542. Runs the control-band detector suite
# (tests/test_bands.py, stdlib unittest) under bstack CI, which discovers
# tests/*.test.sh and knows nothing about python suites.
#
# There is deliberately NO skip path. A wrapper that warns and exits 0 when a
# dependency is missing reports green for a suite that never ran: the check
# exists, the check passes, the check is inert. So:
#   - PyYAML missing FAILS the run (CI installs it; the config parser needs it).
#   - a skipped test, an expected failure or an unexpected success FAILS the run.
#     unittest exits 0 on "OK (skipped=3)", so the exit code alone cannot see it.
#   - a floor on "Ran N tests". unittest exits 0 on "Ran 0 tests" too (5 on
#     3.12+, but not on 3.10/3.11), so a silently shrunk suite must fail here.
#
# Run from anywhere:
#   bash tests/bands.test.sh

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BSTACK_REPO" || exit 1
MODULE="tests.test_bands"
MIN_TESTS=76

PASS=0
FAIL=0
FAILED_TESTS=()
assert_pass() { PASS=$((PASS + 1)); echo "  [pass] $1"; }
assert_fail() { FAIL=$((FAIL + 1)); FAILED_TESTS+=("$1"); echo "  [FAIL] $1"; [ -n "${2:-}" ] && echo "         $2"; }

echo "── control-band detector suite (BRO-2542) ─────────────────────────"

[ -f tests/test_bands.py ] || { echo "  [FAIL] missing tests/test_bands.py"; exit 1; }
[ -f scripts/bands.py ]    || { echo "  [FAIL] missing scripts/bands.py"; exit 1; }

if ! python3 -c 'import yaml' >/dev/null 2>&1; then
    echo "  [FAIL] python3 cannot import yaml (PyYAML)."
    echo "         Not skipping: a green wrapper around a suite that never ran is"
    echo "         worse than no wrapper. Install it: python3 -m pip install pyyaml"
    exit 1
fi

LOG="$(mktemp)"; trap 'rm -f "$LOG"' EXIT
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v "$MODULE" >"$LOG" 2>&1
RC=$?

if [ "$RC" -eq 0 ]; then
    assert_pass "unittest suite exits clean"
else
    assert_fail "unittest suite exits clean" "exit $RC"
fi

# The verdict line must be exactly "OK": "OK (skipped=1)" and
# "OK (expected failures=1)" are a suite that stopped asserting.
VERDICT="$(grep -E '^(OK|FAILED)' "$LOG" | tail -1)"
if [ "$VERDICT" = "OK" ]; then
    assert_pass "verdict is a bare OK"
else
    assert_fail "verdict is a bare OK" "got: ${VERDICT:-<none>}"
fi

if grep -qE '\.\.\. (skipped|expected failure|unexpected success)' "$LOG"; then
    assert_fail "no test silently stopped asserting" \
        "$(grep -E '\.\.\. (skipped|expected failure|unexpected success)' "$LOG" | head -3)"
else
    assert_pass "no test silently stopped asserting"
fi

COUNT="$(grep -oE '^Ran [0-9]+ test' "$LOG" | tail -1 | awk '{print $2}')"
COUNT="${COUNT:-0}"
if [ "$COUNT" -ge "$MIN_TESTS" ]; then
    assert_pass "suite size >= $MIN_TESTS (ran $COUNT)"
else
    assert_fail "suite size >= $MIN_TESTS" "only $COUNT ran; collection may have silently shrunk"
fi

# The suite imports bands directly; every consumer runs it as a subprocess
# through the shim. A shebang, mode or path fault breaks only the second.
if bin/bstack-bands --help >/dev/null 2>&1; then
    assert_pass "bin/bstack-bands --help runs"
else
    assert_fail "bin/bstack-bands --help runs"
fi

# The shipped template must satisfy its own validator, end to end.
if bin/bstack-bands check references/templates/bands.example.yaml \
        --series tests/fixtures/bands/series-2sigma.json --json >/dev/null 2>&1; then
    assert_pass "shipped bands.example.yaml validates and evaluates"
else
    assert_fail "shipped bands.example.yaml validates and evaluates"
fi

echo
echo "── unittest (tail) ────────────────────────────────────────────────"
tail -n 6 "$LOG"

echo
echo "── Summary ────────────────────────────────────────────────────────"
echo "  passed: $PASS"
echo "  failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
    echo "  failed tests:"
    for t in "${FAILED_TESTS[@]}"; do
        echo "    - $t"
    done
    echo
    echo "── unittest log ───────────────────────────────────────────────────"
    cat "$LOG"
    exit 1
fi
exit 0
