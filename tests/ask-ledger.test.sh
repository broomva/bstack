#!/usr/bin/env bash
# tests/ask-ledger.test.sh — BRO-2179. Runs the ask-ledger contract suite under
# bstack CI, which discovers tests/*.test.sh and knows nothing about pytest.
#
# There is deliberately NO skip path. A wrapper that warns and exits 0 when pytest
# is missing reports green for a suite that never ran, which is the failure mode
# this repo has hit before: the check exists, the check passes, the check is inert.
# So the wrapper resolves an interpreter (system pytest, else a venv it builds) and
# fails loudly if it cannot.
#
# Two guards beyond "pytest exited 0", both ported from the workspace CI step this
# replaces:
#   - a skipped or xfailed test FAILS the run. A suite that silently stops asserting
#     is worse than one that is absent, because it still reports green.
#   - a floor on the passing count. `pytest -q` exits 0 when collection finds nothing
#     at all, so the exit code alone cannot tell "110 passed" from "0 collected".
#
# Run from the bstack repo root:
#   bash tests/ask-ledger.test.sh

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUITE="$BSTACK_REPO/tests/test_ask_ledger.py"
LEDGER_PY="$BSTACK_REPO/scripts/ask_ledger.py"
VENV="${BSTACK_AL_VENV:-/tmp/bstack-al-venv}"
MIN_TESTS=100

PASS=0
FAIL=0
FAILED_TESTS=()
assert_pass() { PASS=$((PASS + 1)); echo "  [pass] $1"; }
assert_fail() { FAIL=$((FAIL + 1)); FAILED_TESTS+=("$1"); echo "  [FAIL] $1"; [ -n "${2:-}" ] && echo "         $2"; }

echo "── ask-ledger contract suite (BRO-2179) ───────────────────────────"

[ -f "$SUITE" ]     || { echo "  [FAIL] missing $SUITE"; exit 1; }
[ -f "$LEDGER_PY" ] || { echo "  [FAIL] missing $LEDGER_PY"; exit 1; }

# -- resolve an interpreter that has pytest + yaml ----------------------------
PY=""
if python3 -c 'import pytest, yaml' >/dev/null 2>&1; then
    PY="python3"
else
    python3 -m venv "$VENV" >/dev/null 2>&1 || true
    if [ -x "$VENV/bin/python" ]; then
        "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check pytest pyyaml >/dev/null 2>&1 || true
        "$VENV/bin/python" -c 'import pytest, yaml' >/dev/null 2>&1 && PY="$VENV/bin/python"
    fi
fi
if [ -z "$PY" ]; then
    echo "  [FAIL] no interpreter with pytest+pyyaml, and a venv could not be built."
    echo "         Not skipping: a green wrapper around a suite that never ran is"
    echo "         worse than no wrapper. Install pytest+pyyaml, or set BSTACK_AL_VENV."
    exit 1
fi
echo "  [info] interpreter: $PY"

LOG="$(mktemp)"; trap 'rm -f "$LOG"' EXIT
"$PY" -m pytest "$SUITE" -q >"$LOG" 2>&1
RC=$?

if [ "$RC" -eq 0 ]; then
    assert_pass "pytest suite exits clean"
else
    assert_fail "pytest suite exits clean" "$(tail -5 "$LOG")"
fi

if grep -qE '[0-9]+ (skipped|xfailed)' "$LOG"; then
    assert_fail "no test silently stopped asserting" "$(grep -oE '[0-9]+ (skipped|xfailed)' "$LOG" | tr '\n' ' ')"
else
    assert_pass "no test silently stopped asserting"
fi

COUNT="$(grep -oE '[0-9]+ passed' "$LOG" | tail -1 | cut -d' ' -f1)"
COUNT="${COUNT:-0}"
if [ "$COUNT" -ge "$MIN_TESTS" ]; then
    assert_pass "suite size >= $MIN_TESTS (got $COUNT)"
else
    assert_fail "suite size >= $MIN_TESTS" "only $COUNT passed — collection may have silently shrunk"
fi

# -- the CLI must be invocable as a script, not merely importable -------------
# The suite imports ask_ledger directly; every consumer invokes it as a subprocess.
# A shebang or syntax fault that breaks the second is invisible to the first.
if "$PY" "$LEDGER_PY" --help >/dev/null 2>&1; then
    assert_pass "ask_ledger.py --help runs as a subprocess"
else
    assert_fail "ask_ledger.py --help runs as a subprocess"
fi

# -- the shipped preauth template must satisfy its own validator --------------
if "$PY" -c "
import sys, yaml
d = yaml.safe_load(open('$BSTACK_REPO/references/preauth.example.yaml'))
g = d.get('grants') or []
sys.exit(0 if g and all(x.get('match') for x in g) else 1)
" >/dev/null 2>&1; then
    assert_pass "shipped preauth.example.yaml parses and scopes every grant"
else
    assert_fail "shipped preauth.example.yaml parses and scopes every grant"
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
