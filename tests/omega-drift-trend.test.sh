#!/usr/bin/env bash
# tests/omega-drift-trend.test.sh — composite-ω drift trend smoke (v0.19.0).
#
# Validates:
#   1. --trend writes exactly one history line per call
#   2. Reading 7+ synthetic history lines produces a slope
#   3. drift_down verdict fires when synthetic data trends negative
#   4. drift_up verdict fires when synthetic data trends positive
#   5. stable_flat verdict fires when synthetic data is roughly constant
#   6. Without --trend, no history line is written (point-in-time only)

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUDGET="$BSTACK_REPO/scripts/compute-budget-status.sh"

PASS=0
FAIL=0
FAILED_TESTS=()

assert_pass() { PASS=$((PASS + 1)); echo "  ✓ $1"; }
assert_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS+=("$1")
    echo "  ✗ $1"
    [ -n "${2:-}" ] && echo "    ${2}"
}

fresh_ws() {
    local ws
    ws=$(mktemp -d)
    mkdir -p "$ws/.control/audit"
    echo "$ws"
}

# Seed N synthetic history points with the given slope direction.
# Args: <workspace> <count> <first_omega> <last_omega>
# Distributes points evenly over 7 days, omega linearly interpolated
# between first_omega and last_omega.
seed_history() {
    local ws="$1"
    local n="$2"
    local first="$3"
    local last="$4"
    local hist="$ws/.control/audit/composite-omega-history.jsonl"
    mkdir -p "$ws/.control/audit"
    python3 - "$hist" "$n" "$first" "$last" <<'PYEOF'
import sys, json, time
hist_path = sys.argv[1]
n = int(sys.argv[2])
first = float(sys.argv[3])
last = float(sys.argv[4])
now_ms = int(time.time() * 1000)
window_ms = 6 * 24 * 60 * 60 * 1000  # 6 days back to first point (within 7d)
with open(hist_path, "w") as f:
    for i in range(n):
        if n > 1:
            frac = i / (n - 1)
        else:
            frac = 0.0
        ts = now_ms - window_ms + int(frac * window_ms)
        omega = first + frac * (last - first)
        f.write(json.dumps({"ts": ts, "omega": omega, "per_layer": {"L3": omega}}) + "\n")
PYEOF
}

# ── Test 1: --trend writes exactly one history line per call ──────────
echo ""
echo "Test 1: --trend writes one history line per call"
WS=$(fresh_ws)
HIST="$WS/.control/audit/composite-omega-history.jsonl"
BROOMVA_WORKSPACE="$WS" bash "$BUDGET" --trend --json >/dev/null 2>&1
n1=$(wc -l < "$HIST" 2>/dev/null | tr -d ' ')
BROOMVA_WORKSPACE="$WS" bash "$BUDGET" --trend --json >/dev/null 2>&1
n2=$(wc -l < "$HIST" 2>/dev/null | tr -d ' ')
if [ "$n1" = "1" ] && [ "$n2" = "2" ]; then
    assert_pass "history grew 0→1→2 across two --trend calls"
else
    assert_fail "history did not grow by exactly one per --trend call" "n1=$n1 n2=$n2"
fi
rm -rf "$WS"

# ── Test 2: 7+ history lines produce a slope ───────────────────────────
echo ""
echo "Test 2: 7+ history lines yield a numeric slope"
WS=$(fresh_ws)
seed_history "$WS" 8 0.006 0.006
out=$(BROOMVA_WORKSPACE="$WS" bash "$BUDGET" --trend --json 2>/dev/null)
if echo "$out" | python3 -c "
import sys, json
d = json.load(sys.stdin)
t = d.get('trend') or {}
assert 'slope_per_second' in t, f'no slope in trend; got {t}'
assert isinstance(t['slope_per_second'], (int, float)), f'slope not numeric'
assert t.get('points', 0) >= 8, f'expected ≥ 8 points, got {t.get(\"points\")}'
" 2>/dev/null; then
    assert_pass "trend block contains numeric slope_per_second + ≥ 8 points"
else
    assert_fail "no slope produced for 8 synthetic points" "$(echo "$out" | head -20)"
fi
rm -rf "$WS"

# ── Test 3: drift_down verdict fires on negative slope ────────────────
echo ""
echo "Test 3: drift_down fires when synthetic omega trends down (0.01 → 0.005)"
WS=$(fresh_ws)
seed_history "$WS" 10 0.01 0.005
out=$(BROOMVA_WORKSPACE="$WS" bash "$BUDGET" --trend --json 2>/dev/null)
verdict=$(echo "$out" | python3 -c "import sys,json;print((json.load(sys.stdin).get('trend') or {}).get('verdict','?'))" 2>/dev/null)
if [ "$verdict" = "drift_down" ]; then
    assert_pass "drift_down verdict produced for downward synthetic trend"
else
    assert_fail "expected drift_down, got '$verdict'" "$(echo "$out" | head -30)"
fi
rm -rf "$WS"

# ── Test 4: drift_up verdict fires on positive slope ──────────────────
echo ""
echo "Test 4: drift_up fires when synthetic omega trends up (0.005 → 0.01)"
WS=$(fresh_ws)
seed_history "$WS" 10 0.005 0.01
out=$(BROOMVA_WORKSPACE="$WS" bash "$BUDGET" --trend --json 2>/dev/null)
verdict=$(echo "$out" | python3 -c "import sys,json;print((json.load(sys.stdin).get('trend') or {}).get('verdict','?'))" 2>/dev/null)
if [ "$verdict" = "drift_up" ]; then
    assert_pass "drift_up verdict produced for upward synthetic trend"
else
    assert_fail "expected drift_up, got '$verdict'" "$(echo "$out" | head -30)"
fi
rm -rf "$WS"

# ── Test 5: stable_flat fires on flat data ────────────────────────────
echo ""
echo "Test 5: stable_flat fires when omega is constant"
WS=$(fresh_ws)
seed_history "$WS" 10 0.006398 0.006398
out=$(BROOMVA_WORKSPACE="$WS" bash "$BUDGET" --trend --json 2>/dev/null)
verdict=$(echo "$out" | python3 -c "import sys,json;print((json.load(sys.stdin).get('trend') or {}).get('verdict','?'))" 2>/dev/null)
if [ "$verdict" = "stable_flat" ]; then
    assert_pass "stable_flat verdict produced for constant data"
else
    assert_fail "expected stable_flat, got '$verdict'" "$(echo "$out" | head -30)"
fi
rm -rf "$WS"

# ── Test 6: without --trend, no history line is written ───────────────
echo ""
echo "Test 6: --json (no --trend) does NOT write a history line"
WS=$(fresh_ws)
HIST="$WS/.control/audit/composite-omega-history.jsonl"
BROOMVA_WORKSPACE="$WS" bash "$BUDGET" --json >/dev/null 2>&1
if [ ! -f "$HIST" ]; then
    assert_pass "history file absent after --json without --trend"
else
    n=$(wc -l < "$HIST" 2>/dev/null | tr -d ' ')
    assert_fail "history file unexpectedly present (n=$n lines)"
fi
rm -rf "$WS"

# ── Summary ──────────────────────────────────────────────────────────────
echo ""
echo "─────────────────────────────────────"
echo "Passed: $PASS"
echo "Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo "Failed tests:"
    for t in "${FAILED_TESTS[@]}"; do
        echo "  - $t"
    done
    exit 1
fi
echo "All tests passed."
exit 0
