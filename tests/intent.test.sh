#!/usr/bin/env bash
# tests/intent.test.sh — BRO-2542. Runs the intent.md contract suite under bstack CI,
# which discovers tests/*.test.sh.
#
# Stdlib unittest only, so there is nothing to install and deliberately NO skip path:
# a wrapper that exits 0 around a suite that never ran reports green for nothing.
#
# Guards beyond "unittest exited 0":
#   - the final status line must be exactly "OK" — `OK (skipped=N)` fails the run.
#   - a floor on "Ran N tests", so a suite that silently shrank cannot pass.
#   - the CLI runs as a subprocess, and the shipped template is still one lint can
#     read: every required section present, failing only on its placeholders.
#
# Run from the bstack repo root:
#   bash tests/intent.test.sh

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODULE="tests.test_intent"
SCRIPT="$BSTACK_REPO/scripts/intent.py"
TEMPLATE="$BSTACK_REPO/references/templates/intent.md"
MIN_TESTS=33

PASS=0
FAIL=0
FAILED_TESTS=()
assert_pass() { PASS=$((PASS + 1)); echo "  [pass] $1"; }
assert_fail() { FAIL=$((FAIL + 1)); FAILED_TESTS+=("$1"); echo "  [FAIL] $1"; [ -n "${2:-}" ] && echo "         $2"; }

echo "── intent.md contract suite (BRO-2542) ────────────────────────────"

[ -f "$BSTACK_REPO/tests/test_intent.py" ] || { echo "  [FAIL] missing tests/test_intent.py"; exit 1; }
[ -f "$SCRIPT" ]   || { echo "  [FAIL] missing $SCRIPT"; exit 1; }
[ -f "$TEMPLATE" ] || { echo "  [FAIL] missing $TEMPLATE"; exit 1; }

LOG="$(mktemp)"; SCRATCH="$(mktemp -d)"
trap 'rm -rf "$LOG" "$SCRATCH"' EXIT

(cd "$BSTACK_REPO" && python3 -m unittest -v "$MODULE") >"$LOG" 2>&1
RC=$?

if [ "$RC" -eq 0 ]; then
    assert_pass "unittest suite exits clean"
else
    assert_fail "unittest suite exits clean" "$(tail -15 "$LOG")"
fi

STATUS_LINE="$(grep -E '^(OK|FAILED)' "$LOG" | tail -1)"
if [ "$STATUS_LINE" = "OK" ]; then
    assert_pass "final status is exactly OK (no skips, no expected failures)"
else
    assert_fail "final status is exactly OK (no skips, no expected failures)" "got: ${STATUS_LINE:-<none>}"
fi

if grep -qE '(\.\.\. skipped|expected failure|unexpected success)' "$LOG"; then
    assert_fail "no test silently stopped asserting" "$(grep -E '(\.\.\. skipped|expected failure|unexpected success)' "$LOG" | head -3)"
else
    assert_pass "no test silently stopped asserting"
fi

COUNT="$(grep -oE '^Ran [0-9]+ tests?' "$LOG" | tail -1 | grep -oE '[0-9]+')"
COUNT="${COUNT:-0}"
if [ "$COUNT" -ge "$MIN_TESTS" ]; then
    assert_pass "suite size >= $MIN_TESTS (ran $COUNT)"
else
    assert_fail "suite size >= $MIN_TESTS" "only $COUNT ran — collection may have silently shrunk"
fi

if python3 "$SCRIPT" --help >/dev/null 2>&1; then
    assert_pass "intent.py --help runs as a subprocess"
else
    assert_fail "intent.py --help runs as a subprocess"
fi

# End to end through the CLI: a fresh intent fails lint (placeholders), a filled one
# passes, and set-status moves it to accepted.
F="$SCRATCH/intent/2026-01-02-e2e.md"
python3 "$SCRIPT" new e2e --author "CI" --title "E2E" --dir "$SCRATCH/intent" --date 2026-01-02 >/dev/null 2>&1
if python3 "$SCRIPT" lint "$F" >/dev/null 2>&1; then
    assert_fail "a fresh intent fails lint until filled"
else
    assert_pass "a fresh intent fails lint until filled"
fi
python3 - "$F" <<'PY'
import re, sys
p = sys.argv[1]
t = open(p).read()
t = re.sub(r"<[^<>\n]+>", "Filled in by the CI end-to-end check.", t)
open(p, "w").write(t)
PY
if python3 "$SCRIPT" lint "$F" >/dev/null 2>&1 \
   && python3 "$SCRIPT" set-status "$F" accepted >/dev/null 2>&1 \
   && [ "$(python3 "$SCRIPT" status "$F")" = "accepted" ]; then
    assert_pass "filled intent lints clean and set-status → accepted"
else
    assert_fail "filled intent lints clean and set-status → accepted" "$(python3 "$SCRIPT" lint "$F" 2>&1 | tail -3)"
fi

echo
echo "── unittest tail ──────────────────────────────────────────────────"
tail -4 "$LOG"
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
