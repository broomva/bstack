#!/usr/bin/env bash
# tests/philosophy-backfill.test.sh — fixture-based tests for the Development
# Philosophy backfill in scripts/repair.sh and the advisory in scripts/doctor.sh.
#
# Verifies:
#   1. A pre-0.24.0 AGENTS.md/CLAUDE.md (no section) gets the section backfilled,
#      positioned BEFORE the `## Bstack Core Automation Primitives` anchor.
#   2. Re-running is idempotent (exactly one section; no duplicate).
#   3. --dry-run reports the backfill but writes nothing.
#   4. Missing anchor → skip with a warning, file unchanged (never guesses).
#   5. doctor surfaces the advisory when the section is absent, and reports ok
#      when present — and the advisory is NOT a GAP / does NOT fail --strict.
#
# Run from repo root: bash tests/philosophy-backfill.test.sh
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPAIR_SH="$BSTACK_REPO/scripts/repair.sh"
DOCTOR_SH="$BSTACK_REPO/scripts/doctor.sh"

PASS=0
FAIL=0
FAILED_TESTS=()

assert_pass() { PASS=$((PASS + 1)); echo "  ✓ $1"; }
assert_fail() {
    FAIL=$((FAIL + 1)); FAILED_TESTS+=("$1")
    echo "  ✗ $1"; [ -n "${2:-}" ] && echo "    ${2}"
}

# Count `## Development Philosophy` headings in a file.
# grep -c prints "0" and exits 1 on zero matches; `|| true` swallows the exit
# without appending a second "0" (the naive `|| echo 0` double-prints).
phil_count() { grep -cE "^## Development Philosophy" "$1" 2>/dev/null || true; }

# A workspace with AGENTS.md + CLAUDE.md that have the Primitives anchor but
# NO Development Philosophy section (simulates a pre-0.24.0 bootstrap). No
# .git / .control → doctor early-exits, so repair doesn't trigger the heavier
# RCS-install fix paths; the backfill (which runs before the early-exit) still
# executes. That keeps this test fast and focused.
fresh_pre024_workspace() {
    local ws; ws="$(mktemp -d)"
    cat > "$ws/AGENTS.md" <<'EOF'
# demo — Agent Guidelines

## Self-Meta Definition

This file IS the control harness.

## Bstack Core Automation Primitives

(primitive table here)
EOF
    cat > "$ws/CLAUDE.md" <<'EOF'
# demo — bstack-governed workspace

## Identity

Governed by bstack.

## Bstack Core Automation Primitives

(primitive table here)
EOF
    echo "$ws"
}

echo "=== philosophy-backfill.test.sh ==="

# ── Test 1 + 2: backfill + idempotency ──────────────────────────────────────
echo ""
echo "Test 1+2: backfill into pre-0.24.0 workspace, then idempotency"
WS="$(fresh_pre024_workspace)"

[ "$(phil_count "$WS/AGENTS.md")" -eq 0 ] && assert_pass "precondition: AGENTS.md has no section" \
    || assert_fail "precondition: AGENTS.md should start with no section"

BROOMVA_WORKSPACE="$WS" bash "$REPAIR_SH" --apply-all >/dev/null 2>&1

if [ "$(phil_count "$WS/AGENTS.md")" -eq 1 ]; then
    assert_pass "AGENTS.md section backfilled (exactly one)"
else
    assert_fail "AGENTS.md section not backfilled (count=$(phil_count "$WS/AGENTS.md"))"
fi
if [ "$(phil_count "$WS/CLAUDE.md")" -eq 1 ]; then
    assert_pass "CLAUDE.md section backfilled (exactly one)"
else
    assert_fail "CLAUDE.md section not backfilled (count=$(phil_count "$WS/CLAUDE.md"))"
fi

# Position: section must come BEFORE the Primitives anchor in AGENTS.md.
phil_line="$(grep -nE "^## Development Philosophy" "$WS/AGENTS.md" | head -1 | cut -d: -f1)"
anchor_line="$(grep -nF "## Bstack Core Automation Primitives" "$WS/AGENTS.md" | head -1 | cut -d: -f1)"
if [ -n "$phil_line" ] && [ -n "$anchor_line" ] && [ "$phil_line" -lt "$anchor_line" ]; then
    assert_pass "section positioned before Primitives anchor (line $phil_line < $anchor_line)"
else
    assert_fail "section not positioned before anchor (phil=$phil_line anchor=$anchor_line)"
fi

# Key content row survived extraction intact (backticks/quotes/pipes).
if grep -qF 'No verifiable "done"' "$WS/AGENTS.md"; then
    assert_pass "section content extracted verbatim (quotes intact)"
else
    assert_fail "section content lost in extraction"
fi

# Idempotency: second run must not duplicate.
BROOMVA_WORKSPACE="$WS" bash "$REPAIR_SH" --apply-all >/dev/null 2>&1
if [ "$(phil_count "$WS/AGENTS.md")" -eq 1 ] && [ "$(phil_count "$WS/CLAUDE.md")" -eq 1 ]; then
    assert_pass "idempotent: re-run did not duplicate the section"
else
    assert_fail "idempotency broken (AGENTS=$(phil_count "$WS/AGENTS.md") CLAUDE=$(phil_count "$WS/CLAUDE.md"))"
fi
rm -rf "$WS"

# ── Test 3: --dry-run writes nothing ────────────────────────────────────────
echo ""
echo "Test 3: --dry-run reports but does not write"
WS="$(fresh_pre024_workspace)"
DRY_OUT="$(BROOMVA_WORKSPACE="$WS" bash "$REPAIR_SH" --dry-run 2>&1)"
if echo "$DRY_OUT" | grep -q "would backfill Development Philosophy into AGENTS.md"; then
    assert_pass "--dry-run reports the backfill"
else
    assert_fail "--dry-run did not report the backfill"
fi
if [ "$(phil_count "$WS/AGENTS.md")" -eq 0 ]; then
    assert_pass "--dry-run wrote nothing"
else
    assert_fail "--dry-run modified the file (count=$(phil_count "$WS/AGENTS.md"))"
fi
rm -rf "$WS"

# ── Test 4: missing anchor → skip, file unchanged ───────────────────────────
echo ""
echo "Test 4: missing Primitives anchor → skip with warning"
WS="$(mktemp -d)"
printf '# demo\n\nNo anchor here.\n' > "$WS/AGENTS.md"
printf '# demo\n\nNo anchor here.\n' > "$WS/CLAUDE.md"
SKIP_OUT="$(BROOMVA_WORKSPACE="$WS" bash "$REPAIR_SH" --apply-all 2>&1)"
if echo "$SKIP_OUT" | grep -q "anchor '## Bstack Core Automation Primitives' not found"; then
    assert_pass "missing anchor reported as skip"
else
    assert_fail "missing-anchor skip not reported"
fi
if [ "$(phil_count "$WS/AGENTS.md")" -eq 0 ]; then
    assert_pass "missing-anchor file left unchanged"
else
    assert_fail "section inserted despite missing anchor"
fi
rm -rf "$WS"

# ── Test 5: doctor advisory present/absent + --strict safety ─────────────────
echo ""
echo "Test 5: doctor advisory + --strict does not fail on missing section"
WS="$(mktemp -d)"; mkdir -p "$WS/.control"
: > "$WS/.control/policy.yaml"
cp "$BSTACK_REPO/assets/templates/CLAUDE.md.template" "$WS/CLAUDE.md"
# AGENTS.md WITHOUT the section (strip it from the template between the heading
# and the Primitives anchor).
awk '/^## Development Philosophy$/{skip=1} /^## Bstack Core Automation Primitives$/{skip=0} !skip' \
    "$BSTACK_REPO/assets/templates/AGENTS.md.template" > "$WS/AGENTS.md"

DOC_OUT="$(BROOMVA_WORKSPACE="$WS" bash "$DOCTOR_SH" 2>&1)"
if echo "$DOC_OUT" | grep -q "no Development Philosophy section"; then
    assert_pass "doctor surfaces the advisory when section absent"
else
    assert_fail "doctor did not surface the advisory"
fi

# --strict must still exit 0 for *this* (advisory is not a GAP). Other gaps may
# exist in this minimal workspace, so we assert specifically that the advisory
# itself is phrased as informational (not a [gap] line).
if echo "$DOC_OUT" | grep -q "\[gap\].*Development Philosophy"; then
    assert_fail "advisory was emitted as a GAP (should be informational)"
else
    assert_pass "advisory is informational, not a GAP"
fi

# With the section present, doctor reports ok.
cp "$BSTACK_REPO/assets/templates/AGENTS.md.template" "$WS/AGENTS.md"
DOC_OUT2="$(BROOMVA_WORKSPACE="$WS" bash "$DOCTOR_SH" 2>&1)"
if echo "$DOC_OUT2" | grep -q "AGENTS.md has Development Philosophy section"; then
    assert_pass "doctor reports ok when section present"
else
    assert_fail "doctor did not report ok with section present"
fi
rm -rf "$WS"

# ── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo "=== results: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
    printf '  - %s\n' "${FAILED_TESTS[@]}"
    exit 1
fi
exit 0
