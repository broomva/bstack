#!/usr/bin/env bash
# tests/metrics-pipeline.test.sh — Phase 1 (v0.4.0) setpoint measurement smoke.
#
# Validates:
#   1. `bstack metrics collect` produces valid JSON with the documented shape
#   2. `bstack metrics observe S<n>` returns single-setpoint JSON
#   3. Cache TTL is honored (mtime preservation on repeat call)
#   4. --no-cache forces re-measurement
#   5. Each per-setpoint script outputs valid JSON with correct id
#   6. Missing measure script produces structured error, exit 1
#   7. Aggregate JSON has generated_at + setpoints map
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BSTACK_METRICS="$BSTACK_REPO/bin/bstack-metrics"

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

# Each test uses an isolated METRICS_DIR + workspace to keep state clean.
fresh_env() {
    local md ws
    md=$(mktemp -d)
    ws=$(mktemp -d)
    # Minimal workspace fixtures so S11/S12/S14 have something to look at.
    mkdir -p "$ws/.claude" "$ws/.control" "$ws/docs/conversations"
    : > "$ws/CLAUDE.md"
    : > "$ws/AGENTS.md"
    : > "$ws/.control/policy.yaml"
    echo "{\"hooks\":{\"Stop\":[{\"hooks\":[{\"type\":\"command\",\"command\":\"x\"}]}],\"PreToolUse\":[{\"hooks\":[{\"type\":\"command\",\"command\":\"y\"}]}]}}" > "$ws/.claude/settings.json"
    echo "$md $ws"
}

# ── Test 1: collect produces valid JSON with expected shape ─────────────
echo ""
echo "Test 1: bstack metrics collect produces valid latest.json"
read -r MD WS < <(fresh_env)
BSTACK_DIR="$BSTACK_REPO" BSTACK_METRICS_DIR="$MD" BROOMVA_WORKSPACE="$WS" \
    "$BSTACK_METRICS" collect >/dev/null 2>&1
if [ -f "$MD/latest.json" ] && jq -e '.generated_at and .setpoints' "$MD/latest.json" >/dev/null 2>&1; then
    setpoint_count=$(jq -r '.setpoints | keys | length' "$MD/latest.json")
    assert_pass "latest.json present, valid shape, $setpoint_count setpoints recorded"
else
    assert_fail "collect did not write a valid latest.json" "$(cat "$MD/latest.json" 2>/dev/null || echo missing)"
fi
rm -rf "$MD" "$WS"

# ── Test 2: observe returns single setpoint JSON ────────────────────────
echo ""
echo "Test 2: bstack metrics observe S13 returns single setpoint JSON"
read -r MD WS < <(fresh_env)
out=$(BSTACK_DIR="$BSTACK_REPO" BSTACK_METRICS_DIR="$MD" BROOMVA_WORKSPACE="$WS" \
    "$BSTACK_METRICS" observe S13 2>/dev/null || true)
if echo "$out" | jq -e '.id == "S13" and (.name | type == "string")' >/dev/null 2>&1; then
    assert_pass "observe S13 returns valid JSON with id=S13"
else
    assert_fail "observe S13 did not return expected JSON" "$out"
fi
rm -rf "$MD" "$WS"

# ── Test 3: cache TTL — re-running within TTL preserves mtime ─────────
echo ""
echo "Test 3: collect respects TTL (re-run within TTL is no-op)"
read -r MD WS < <(fresh_env)
BSTACK_DIR="$BSTACK_REPO" BSTACK_METRICS_DIR="$MD" BROOMVA_WORKSPACE="$WS" \
    BSTACK_METRICS_TTL=3600 "$BSTACK_METRICS" collect >/dev/null 2>&1
if stat -f %m "$MD/latest.json" >/dev/null 2>&1; then
    m1=$(stat -f %m "$MD/latest.json")
else
    m1=$(stat -c %Y "$MD/latest.json")
fi
sleep 1
BSTACK_DIR="$BSTACK_REPO" BSTACK_METRICS_DIR="$MD" BROOMVA_WORKSPACE="$WS" \
    BSTACK_METRICS_TTL=3600 "$BSTACK_METRICS" collect >/dev/null 2>&1
if stat -f %m "$MD/latest.json" >/dev/null 2>&1; then
    m2=$(stat -f %m "$MD/latest.json")
else
    m2=$(stat -c %Y "$MD/latest.json")
fi
if [ "$m1" = "$m2" ]; then
    assert_pass "second call within TTL preserved mtime (no re-measurement)"
else
    assert_fail "second call within TTL re-measured" "m1=$m1 m2=$m2"
fi
rm -rf "$MD" "$WS"

# ── Test 4: --no-cache forces re-measurement ────────────────────────────
echo ""
echo "Test 4: --no-cache bypasses TTL"
read -r MD WS < <(fresh_env)
BSTACK_DIR="$BSTACK_REPO" BSTACK_METRICS_DIR="$MD" BROOMVA_WORKSPACE="$WS" \
    BSTACK_METRICS_TTL=3600 "$BSTACK_METRICS" collect >/dev/null 2>&1
if stat -f %m "$MD/latest.json" >/dev/null 2>&1; then
    m1=$(stat -f %m "$MD/latest.json")
else
    m1=$(stat -c %Y "$MD/latest.json")
fi
sleep 1
BSTACK_DIR="$BSTACK_REPO" BSTACK_METRICS_DIR="$MD" BROOMVA_WORKSPACE="$WS" \
    BSTACK_METRICS_TTL=3600 "$BSTACK_METRICS" collect --no-cache >/dev/null 2>&1
if stat -f %m "$MD/latest.json" >/dev/null 2>&1; then
    m2=$(stat -f %m "$MD/latest.json")
else
    m2=$(stat -c %Y "$MD/latest.json")
fi
if [ "$m2" -gt "$m1" ]; then
    assert_pass "--no-cache re-measured (mtime advanced)"
else
    assert_fail "--no-cache did not re-measure" "m1=$m1 m2=$m2"
fi
rm -rf "$MD" "$WS"

# ── Test 5: each per-setpoint script outputs valid JSON with correct id ─
echo ""
echo "Test 5: each measure-S<n>.sh outputs valid JSON with matching id"
script_count=0
script_ok=0
read -r _ WS < <(fresh_env)
for s in "$BSTACK_REPO"/scripts/metrics/measure-S*.sh; do
    script_count=$((script_count + 1))
    sid=$(basename "$s" .sh | sed 's/^measure-//')
    out=$(BROOMVA_WORKSPACE="$WS" timeout 2 bash "$s" 2>/dev/null || true)
    if echo "$out" | jq -e --arg sid "$sid" '.id == $sid' >/dev/null 2>&1; then
        script_ok=$((script_ok + 1))
    else
        echo "    [debug] $s returned: $out"
    fi
done
if [ "$script_count" -gt 0 ] && [ "$script_ok" = "$script_count" ]; then
    assert_pass "all $script_count measure-S<n>.sh scripts produce valid id-matched JSON"
else
    assert_fail "only $script_ok/$script_count scripts produced valid JSON"
fi
rm -rf "$WS"

# ── Test 6: missing measure script produces structured error, exit 1 ───
echo ""
echo "Test 6: observe on unknown setpoint returns structured error"
read -r MD WS < <(fresh_env)
out=$(BSTACK_DIR="$BSTACK_REPO" BSTACK_METRICS_DIR="$MD" BROOMVA_WORKSPACE="$WS" \
    "$BSTACK_METRICS" observe S999 2>/dev/null || true)
if echo "$out" | jq -e '.id == "S999" and .error == "no-measurement-script"' >/dev/null 2>&1; then
    assert_pass "missing setpoint produces structured no-measurement-script error"
else
    assert_fail "missing setpoint did not produce expected error JSON" "$out"
fi
rm -rf "$MD" "$WS"

# ── Test 7: aggregate JSON has generated_at + setpoints map ────────────
echo ""
echo "Test 7: aggregate output has correct top-level shape"
read -r MD WS < <(fresh_env)
out=$(BSTACK_DIR="$BSTACK_REPO" BSTACK_METRICS_DIR="$MD" BROOMVA_WORKSPACE="$WS" \
    "$BSTACK_METRICS" collect --json --no-cache 2>/dev/null)
if echo "$out" | jq -e '.generated_at and (.setpoints | type == "object")' >/dev/null 2>&1; then
    assert_pass "--json output has {generated_at, setpoints} shape"
else
    assert_fail "--json output shape invalid" "$out"
fi
rm -rf "$MD" "$WS"

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
