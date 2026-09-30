#!/usr/bin/env bash
# tests/context-ledger.test.sh — the context ledger (scripts/context_ledger.py, the
# `context_ledger` block of leverage-sensor.py, doctor §29) under bstack CI, which
# discovers tests/*.test.sh and knows nothing about python suites.
#
# Two parts:
#   1. tests/test_context_ledger.py — fixture transcripts for every injected source,
#      the stdout/content dedup, follow-through true positives and true negatives, the
#      no-prose invariance, liveness, and doctor §29 on every ledger state.
#   2. tests/context_ledger_mutation.py — each rule removed in turn from a copy of the
#      scripts; the named test must fail by assertion. The first mutant bills a
#      hook_success record's stdout on top of its content, and the byte test must go red.
#
# No skip path, as in tests/bands.test.sh: PyYAML missing FAILS (the shadow-setpoint
# case loads the shipped template through it), a skipped test FAILS, and a shrunken
# suite FAILS on the "Ran N tests" floor, because unittest exits 0 on all three.
#
#   bash tests/context-ledger.test.sh
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
MIN_TESTS=112

PASS=0; FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  [pass] $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  [FAIL] $1"; [ -n "${2:-}" ] && echo "         $2"; }

echo "── context ledger (shadow) ─────────────────────────────────────────"
for f in scripts/context_ledger.py scripts/leverage-sensor.py tests/test_context_ledger.py \
         tests/context_ledger_mutation.py; do
    [ -f "$f" ] || { echo "  [FAIL] missing $f"; exit 1; }
done
if ! python3 -c 'import yaml' >/dev/null 2>&1; then
    echo "  [FAIL] python3 cannot import yaml (PyYAML). Not skipping: install it."
    exit 1
fi

LOG="$(PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_context_ledger 2>&1)"
RC=$?
[ "$RC" -eq 0 ] && ok "unittest suite exits clean" || bad "unittest suite exits clean" "exit $RC"

VERDICT="$(grep -E '^(OK|FAILED)' <<< "$LOG" | tail -1)"
[ "$VERDICT" = "OK" ] && ok "verdict is a bare OK" || bad "verdict is a bare OK" "got: ${VERDICT:-<none>}"

if grep -qE '\.\.\. (skipped|expected failure|unexpected success)' <<< "$LOG"; then
    bad "no test silently stopped asserting" \
        "$(grep -E '\.\.\. (skipped|expected failure|unexpected success)' <<< "$LOG" | head -3)"
else
    ok "no test silently stopped asserting"
fi

COUNT="$(grep -oE '^Ran [0-9]+ test' <<< "$LOG" | tail -1 | awk '{print $2}')"
COUNT="${COUNT:-0}"
if [ "$COUNT" -ge "$MIN_TESTS" ]; then
    ok "suite size >= $MIN_TESTS (ran $COUNT)"
else
    bad "suite size >= $MIN_TESTS" "only $COUNT ran; collection may have silently shrunk"
fi

echo
echo "── mutation proof ──────────────────────────────────────────────────"
MUT="$(PYTHONDONTWRITEBYTECODE=1 python3 tests/context_ledger_mutation.py 2>&1)"
MRC=$?
echo "$MUT"
[ "$MRC" -eq 0 ] && ok "every mutant is killed by its named test" \
    || bad "every mutant is killed by its named test" "exit $MRC"

echo
echo "── Summary ─────────────────────────────────────────────────────────"
echo "  passed: $PASS"
echo "  failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
    echo
    echo "── unittest log ────────────────────────────────────────────────────"
    echo "$LOG"
    exit 1
fi
exit 0
