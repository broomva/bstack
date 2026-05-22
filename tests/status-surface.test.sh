#!/usr/bin/env bash
# tests/status-surface.test.sh — Phase 2 (v0.5.0) status surface smoke.
#
# Validates:
#   1. Text mode renders the expected sections
#   2. --json output is valid JSON with documented top-level shape
#   3. --setpoint S<n> returns single-setpoint view (text)
#   4. --setpoint S<n> --json returns just that setpoint
#   5. --setpoint S999 (unknown) errors cleanly
#   6. --aggregate works on an empty registry (Phase 8 shipped in v0.18.0)
#   7. --no-color strips ANSI sequences
#   8. Status auto-collects metrics when latest.json is stale/missing
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BSTACK_STATUS="$BSTACK_REPO/bin/bstack-status"

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

fresh_env() {
    local md ws sd
    md=$(mktemp -d)
    ws=$(mktemp -d)
    sd=$(mktemp -d)
    # Minimal workspace fixtures so metrics + status have something to read.
    mkdir -p "$ws/.claude" "$ws/.control" "$ws/docs/conversations"
    : > "$ws/CLAUDE.md"
    : > "$ws/AGENTS.md"
    : > "$ws/.control/policy.yaml"
    echo "{\"hooks\":{\"Stop\":[{\"hooks\":[{\"type\":\"command\",\"command\":\"x\"}]}],\"PreToolUse\":[{\"hooks\":[{\"type\":\"command\",\"command\":\"y\"}]}]}}" > "$ws/.claude/settings.json"
    echo "$md $ws $sd"
}

# ── Test 1: text mode renders expected sections ─────────────────────────
echo ""
echo "Test 1: text mode renders all sections"
read -r MD WS SD < <(fresh_env)
out=$(BSTACK_DIR="$BSTACK_REPO" BSTACK_METRICS_DIR="$MD" BSTACK_STATE_DIR="$SD" \
      BROOMVA_WORKSPACE="$WS" "$BSTACK_STATUS" --no-color 2>&1)
missing=()
for section in "Plant" "Setpoints" "Gates" "Primitives" "Companion skills" "Bridge" "RCS stability" "Last upgrade"; do
    echo "$out" | grep -qF "$section" || missing+=("$section")
done
if [ "${#missing[@]}" -eq 0 ]; then
    assert_pass "all 8 sections rendered"
else
    assert_fail "missing sections: ${missing[*]}" "$out"
fi
rm -rf "$MD" "$WS" "$SD"

# ── Test 2: --json output has documented top-level shape ────────────────
echo ""
echo "Test 2: --json output has documented top-level shape"
read -r MD WS SD < <(fresh_env)
out=$(BSTACK_DIR="$BSTACK_REPO" BSTACK_METRICS_DIR="$MD" BSTACK_STATE_DIR="$SD" \
      BROOMVA_WORKSPACE="$WS" "$BSTACK_STATUS" --json 2>&1)
if echo "$out" | jq -e '
    .bstack_version and .workspace and .profile and .generated_at
    and (.setpoints | type == "object")
    and (.summary.setpoints_in_target | type == "string")
    and (.summary.primitives | type == "number")
    and (.summary.gates_total | type == "number")
' >/dev/null 2>&1; then
    assert_pass "JSON has bstack_version/workspace/profile/generated_at/setpoints/summary"
else
    assert_fail "JSON output shape invalid" "$out"
fi
rm -rf "$MD" "$WS" "$SD"

# ── Test 3: --setpoint S13 returns text view ────────────────────────────
echo ""
echo "Test 3: --setpoint S13 returns single-setpoint text view"
read -r MD WS SD < <(fresh_env)
out=$(BSTACK_DIR="$BSTACK_REPO" BSTACK_METRICS_DIR="$MD" BSTACK_STATE_DIR="$SD" \
      BROOMVA_WORKSPACE="$WS" "$BSTACK_STATUS" --no-color --setpoint S13 2>&1)
if echo "$out" | grep -q '^. S13$' && echo "$out" | grep -q 'bridge_freshness_seconds'; then
    assert_pass "S13 detail view rendered with marker + field listing"
else
    assert_fail "S13 detail view missing expected lines" "$out"
fi
rm -rf "$MD" "$WS" "$SD"

# ── Test 4: --setpoint S<n> --json returns single setpoint ──────────────
echo ""
echo "Test 4: --setpoint S13 --json returns single-setpoint JSON"
read -r MD WS SD < <(fresh_env)
out=$(BSTACK_DIR="$BSTACK_REPO" BSTACK_METRICS_DIR="$MD" BSTACK_STATE_DIR="$SD" \
      BROOMVA_WORKSPACE="$WS" "$BSTACK_STATUS" --setpoint S13 --json 2>&1)
if echo "$out" | jq -e '.id == "S13" and .name == "bridge_freshness_seconds"' >/dev/null 2>&1; then
    assert_pass "--setpoint S13 --json returns matching id+name"
else
    assert_fail "--setpoint S13 --json shape invalid" "$out"
fi
rm -rf "$MD" "$WS" "$SD"

# ── Test 5: --setpoint S999 errors cleanly (exit 4) ─────────────────────
echo ""
echo "Test 5: --setpoint S999 (unknown) errors with exit 4"
read -r MD WS SD < <(fresh_env)
# Capture exit without enabling -e: command sub already swallows non-zero
# under `set -uo pipefail` (no -e). Explicit `|| true` makes it bullet-proof.
out=$(BSTACK_DIR="$BSTACK_REPO" BSTACK_METRICS_DIR="$MD" BSTACK_STATE_DIR="$SD" \
      BROOMVA_WORKSPACE="$WS" "$BSTACK_STATUS" --setpoint S999 --json 2>&1 || true)
rc=$?  # this is the exit of `true`, always 0 — re-check via run-and-grab
BSTACK_DIR="$BSTACK_REPO" BSTACK_METRICS_DIR="$MD" BSTACK_STATE_DIR="$SD" \
    BROOMVA_WORKSPACE="$WS" "$BSTACK_STATUS" --setpoint S999 --json >/dev/null 2>&1
rc=$?
if [ "$rc" = "4" ] || [ "$rc" = "1" ]; then
    assert_pass "unknown setpoint returns non-zero exit ($rc)"
else
    assert_fail "unknown setpoint returned exit $rc (expected 4 or 1)" "$out"
fi
rm -rf "$MD" "$WS" "$SD"

# ── Test 6: --aggregate works on empty registry (Phase 8, v0.18.0) ─────
# Pre-v0.18.0 this exit'd 3 (not-implemented placeholder); v0.18.0 ships
# the implementation. On a fresh isolated registry path with no entries,
# --aggregate should exit 0 with a "no registered workspaces" message
# (or equivalent) — never the old Phase-8 placeholder error.
echo ""
echo "Test 6: --aggregate succeeds against an empty registry"
EMPTY_REG=$(mktemp -d)/registry.yaml
out=$(BSTACK_REGISTRY="$EMPTY_REG" "$BSTACK_STATUS" --aggregate 2>&1)
rc=$?
if [ "$rc" = "0" ] && ! echo "$out" | grep -qF "Phase 8 placeholder"; then
    assert_pass "--aggregate succeeds against empty registry (no placeholder error)"
else
    assert_fail "--aggregate did not succeed against empty registry" "rc=$rc out=$out"
fi
rm -rf "$(dirname "$EMPTY_REG")"

# ── Test 7: --no-color strips ANSI sequences ───────────────────────────
echo ""
echo "Test 7: --no-color produces ANSI-free output"
read -r MD WS SD < <(fresh_env)
out=$(BSTACK_DIR="$BSTACK_REPO" BSTACK_METRICS_DIR="$MD" BSTACK_STATE_DIR="$SD" \
      BROOMVA_WORKSPACE="$WS" "$BSTACK_STATUS" --no-color 2>&1)
if echo "$out" | grep -q $'\033'; then
    assert_fail "ANSI escape sequences present despite --no-color"
else
    assert_pass "no ANSI escape sequences in --no-color output"
fi
rm -rf "$MD" "$WS" "$SD"

# ── Test 8: auto-collect on stale/missing latest.json ───────────────────
echo ""
echo "Test 8: status auto-collects when latest.json absent"
read -r MD WS SD < <(fresh_env)
# Don't pre-populate latest.json — status should run collect itself.
BSTACK_DIR="$BSTACK_REPO" BSTACK_METRICS_DIR="$MD" BSTACK_STATE_DIR="$SD" \
    BROOMVA_WORKSPACE="$WS" "$BSTACK_STATUS" --json >/dev/null 2>&1
if [ -f "$MD/latest.json" ] && jq -e '.setpoints' "$MD/latest.json" >/dev/null 2>&1; then
    assert_pass "status auto-populated latest.json"
else
    assert_fail "status did not auto-collect" "$(ls "$MD" 2>&1)"
fi
rm -rf "$MD" "$WS" "$SD"

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
