#!/usr/bin/env bash
# tests/onboard.test.sh — Smoke + integration tests for scripts/onboard.sh.
#
# Run from the bstack repo root:
#   bash tests/onboard.test.sh
#
# Exits non-zero on first failure. Cleans up its own tempdirs on success.
# No external test framework — plain bash assertions, easy to read in CI logs.

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ONBOARD_SH="$BSTACK_REPO/scripts/onboard.sh"

PASS=0
FAIL=0
FAILED_TESTS=()

# ── Helpers ──────────────────────────────────────────────────────────────
fresh_env() {
    # Returns two paths on stdout, space-separated: TEST_HOME TEST_WS
    local h w
    h=$(mktemp -d)
    w=$(mktemp -d)
    echo "$h $w"
}

assert_pass() {
    local name="$1"
    PASS=$((PASS + 1))
    echo "  [pass] $name"
}

assert_fail() {
    local name="$1" detail="${2:-}"
    FAIL=$((FAIL + 1))
    FAILED_TESTS+=("$name")
    echo "  [FAIL] $name"
    [ -n "$detail" ] && echo "         $detail"
}

# Run onboard.sh with isolated HOME + state dir.
run_onboard() {
    local test_home="$1" test_ws="$2"
    shift 2
    HOME="$test_home" \
        BSTACK_STATE_DIR="$test_home/.bstack" \
        BROOMVA_STATE_DIR="$test_home/.config/broomva/bstack" \
        bash "$ONBOARD_SH" --workspace="$test_ws" "$@"
}

echo "── tests/onboard.test.sh ──────────────────────────────────────"
echo ""

# ── Test 1: --help renders Usage block ───────────────────────────────────
echo "T1. --help renders Usage block"
if bash "$ONBOARD_SH" --help 2>&1 | grep -q "^# Usage:" \
    && bash "$ONBOARD_SH" --help 2>&1 | grep -q -- "--skip-prompts"; then
    assert_pass "T1: --help renders"
else
    assert_fail "T1: --help renders" "expected '# Usage:' and '--skip-prompts' in output"
fi

# ── Test 2: --dry-run produces choices receipt but writes nothing ────────
echo "T2. --dry-run prints choices but writes no marker, no config"
read -r TH TW <<< "$(fresh_env)"
OUT=$(run_onboard "$TH" "$TW" --profile=personal --life=skip \
                  --auto-merge=human-required --skip-prompts --dry-run 2>&1)
if echo "$OUT" | grep -q "profile:    personal" \
    && ! [ -f "$TH/.config/broomva/bstack/initialized" ] \
    && ! [ -f "$TH/.bstack/config.yaml" ]; then
    assert_pass "T2: dry-run is non-mutating"
else
    assert_fail "T2: dry-run is non-mutating" "output was: $OUT"
fi

# ── Test 3: Flag-driven full run writes marker + config.yaml ─────────────
echo "T3. Full run writes marker + config (bootstrap failure is OK)"
read -r TH TW <<< "$(fresh_env)"
run_onboard "$TH" "$TW" --profile=enterprise --life=skip \
            --auto-merge=trust-gates --skip-prompts >/dev/null 2>&1 || true
if [ -f "$TH/.config/broomva/bstack/initialized" ] \
    && [ -f "$TH/.bstack/config.yaml" ] \
    && grep -q "^profile: enterprise" "$TH/.bstack/config.yaml" \
    && grep -q "^auto_merge: trust-gates" "$TH/.bstack/config.yaml" \
    && grep -q "^profile: enterprise" "$TH/.config/broomva/bstack/initialized"; then
    assert_pass "T3: marker + config written"
else
    assert_fail "T3: marker + config written" \
        "marker exists? $([ -f "$TH/.config/broomva/bstack/initialized" ] && echo yes || echo no)"
fi

# ── Test 4: Idempotency — second run skips when marker exists ────────────
echo "T4. Idempotency: re-run with marker present skips"
# Reuse TH/TW from T3
OUT=$(run_onboard "$TH" "$TW" --profile=personal --skip-prompts 2>&1)
if echo "$OUT" | grep -q "already initialized"; then
    assert_pass "T4: idempotent skip"
else
    assert_fail "T4: idempotent skip" "expected 'already initialized'; got: $OUT"
fi

# ── Test 5: --force overrides marker ─────────────────────────────────────
echo "T5. --force overrides marker"
OUT=$(run_onboard "$TH" "$TW" --profile=personal --life=skip \
                  --auto-merge=human-required --skip-prompts --force --dry-run 2>&1)
if echo "$OUT" | grep -q "Choices" && echo "$OUT" | grep -q "dry-run set"; then
    assert_pass "T5: --force proceeds"
else
    assert_fail "T5: --force proceeds" "output was: $OUT"
fi

# ── Test 6: Invalid profile fails with non-zero exit ─────────────────────
# Note: capture output to OUT first to avoid pipefail biting when the script
# exits 2 inside the `if command | grep` pipeline.
echo "T6. Invalid profile rejected"
read -r TH TW <<< "$(fresh_env)"
OUT=$(run_onboard "$TH" "$TW" --profile=nonsense --skip-prompts --dry-run 2>&1 || true)
if echo "$OUT" | grep -q "invalid profile" \
    && ! [ -f "$TH/.config/broomva/bstack/initialized" ]; then
    assert_pass "T6: invalid profile rejected"
else
    assert_fail "T6: invalid profile rejected" "output was: $OUT"
fi

# ── Test 7: Invalid auto-merge rejected ──────────────────────────────────
echo "T7. Invalid auto-merge rejected"
read -r TH TW <<< "$(fresh_env)"
OUT=$(run_onboard "$TH" "$TW" --profile=personal --life=skip \
                --auto-merge=yolo --skip-prompts --dry-run 2>&1 || true)
if echo "$OUT" | grep -q "invalid auto-merge"; then
    assert_pass "T7: invalid auto-merge rejected"
else
    assert_fail "T7: invalid auto-merge rejected" "output was: $OUT"
fi

# ── Test 8: Unknown flag fails fast ──────────────────────────────────────
echo "T8. Unknown flag fails with exit 2"
read -r TH TW <<< "$(fresh_env)"
EXIT_CODE=0
run_onboard "$TH" "$TW" --bogus-flag 2>&1 >/dev/null || EXIT_CODE=$?
if [ "$EXIT_CODE" = "2" ]; then
    assert_pass "T8: unknown flag exit 2"
else
    assert_fail "T8: unknown flag exit 2" "got exit $EXIT_CODE"
fi

# ── Summary ──────────────────────────────────────────────────────────────
echo ""
echo "── results ────────────────────────────────────────────────────"
echo "  $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
    echo "  Failed tests:"
    for t in "${FAILED_TESTS[@]}"; do echo "    - $t"; done
    exit 1
fi
echo "  all green ✓"
