#!/usr/bin/env bash
# tests/template_lockstep.test.sh — Assert primitive-count and structural
# consistency across the four governance surfaces that must agree:
#
#   1. SKILL.md                         — frontmatter description (canonical count)
#   2. scripts/doctor.sh                — EXPECTED_COUNT (the validator)
#   3. assets/templates/CLAUDE.md.template  — scaffolded into new workspaces
#   4. assets/templates/AGENTS.md.template  — scaffolded into new workspaces
#
# Run from the bstack repo root:
#   bash tests/template_lockstep.test.sh
#
# Exits non-zero on first mismatch. No external test framework.
#
# Why this exists: prior drift had SKILL.md saying "nineteen primitives"
# while templates still said "thirteen", scaffolding new workspaces into
# permanent disagreement with the catalog. This test makes that drift
# CI-visible.

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SKILL_MD="$BSTACK_REPO/SKILL.md"
DOCTOR_SH="$BSTACK_REPO/scripts/doctor.sh"
CLAUDE_TPL="$BSTACK_REPO/assets/templates/CLAUDE.md.template"
AGENTS_TPL="$BSTACK_REPO/assets/templates/AGENTS.md.template"

PASS=0
FAIL=0
FAILED_TESTS=()

# Number-word mapping for verifying spelled-out counts.
declare -a NUM_WORDS=(
    [1]="one" [2]="two" [3]="three" [4]="four" [5]="five"
    [6]="six" [7]="seven" [8]="eight" [9]="nine" [10]="ten"
    [11]="eleven" [12]="twelve" [13]="thirteen" [14]="fourteen"
    [15]="fifteen" [16]="sixteen" [17]="seventeen" [18]="eighteen"
    [19]="nineteen" [20]="twenty" [21]="twenty-one"
)

# ── Helpers ──────────────────────────────────────────────────────────────
assert_eq() {
    local name="$1" actual="$2" expected="$3"
    if [ "$actual" = "$expected" ]; then
        echo "  [ok] $name: $actual"
        PASS=$((PASS + 1))
    else
        echo "  [FAIL] $name: got '$actual', expected '$expected'"
        FAIL=$((FAIL + 1))
        FAILED_TESTS+=("$name")
    fi
}

assert_contains() {
    local name="$1" haystack="$2" needle="$3"
    if echo "$haystack" | grep -qF "$needle"; then
        echo "  [ok] $name: contains '$needle'"
        PASS=$((PASS + 1))
    else
        echo "  [FAIL] $name: missing '$needle'"
        FAIL=$((FAIL + 1))
        FAILED_TESTS+=("$name")
    fi
}

# ── 1. Discover the canonical primitive count from doctor.sh ─────────────
echo ""
echo "=== Discovering canonical primitive count ==="

DOCTOR_COUNT="$(grep -E '^EXPECTED_COUNT=' "$DOCTOR_SH" | head -1 | sed 's/.*=//' | tr -d '"' | tr -d "'")"
if [ -z "$DOCTOR_COUNT" ] || ! [[ "$DOCTOR_COUNT" =~ ^[0-9]+$ ]]; then
    # Fallback: count P_NAMES array entries.
    DOCTOR_COUNT="$(grep -cE '^\s+"P[0-9]+:' "$DOCTOR_SH" || echo 0)"
fi

if ! [[ "$DOCTOR_COUNT" =~ ^[0-9]+$ ]] || [ "$DOCTOR_COUNT" -lt 1 ]; then
    echo "  [FAIL] could not derive primitive count from doctor.sh"
    exit 1
fi

CANONICAL_COUNT="$DOCTOR_COUNT"
CANONICAL_WORD="${NUM_WORDS[$CANONICAL_COUNT]:-}"
echo "  Canonical count (from doctor.sh): $CANONICAL_COUNT ($CANONICAL_WORD)"

if [ -z "$CANONICAL_WORD" ]; then
    echo "  [FAIL] no spelled-out word for count $CANONICAL_COUNT (extend NUM_WORDS in this test)"
    exit 1
fi

# ── 2. SKILL.md says the same count ─────────────────────────────────────
echo ""
echo "=== SKILL.md frontmatter ==="

# Description references the count as e.g. "nineteen irreducible primitives".
SKILL_DESC="$(sed -n '/^description:/,/^[a-z_]\+:/p' "$SKILL_MD" | head -50)"
assert_contains "SKILL.md description has '$CANONICAL_WORD irreducible primitives'" \
    "$SKILL_DESC" "$CANONICAL_WORD irreducible primitives"

# Also references the highest P-number explicitly.
assert_contains "SKILL.md description references P$CANONICAL_COUNT" \
    "$SKILL_DESC" "P$CANONICAL_COUNT"

# Trigger list should span P1..P<canonical>.
SKILL_TRIGGERS="$(grep -E 'P1.*through.*P[0-9]+' "$SKILL_MD" | head -1)"
if [ -n "$SKILL_TRIGGERS" ]; then
    assert_contains "SKILL.md trigger list spans through P$CANONICAL_COUNT" \
        "$SKILL_TRIGGERS" "P$CANONICAL_COUNT"
fi

# ── 3. CLAUDE.md.template ───────────────────────────────────────────────
echo ""
echo "=== CLAUDE.md.template ==="

CLAUDE_TPL_TEXT="$(cat "$CLAUDE_TPL")"
assert_contains "CLAUDE.md.template intro says '$CANONICAL_WORD irreducible primitives (P1–P$CANONICAL_COUNT)'" \
    "$CLAUDE_TPL_TEXT" "$CANONICAL_WORD irreducible primitives (P1"
assert_contains "CLAUDE.md.template references P$CANONICAL_COUNT" \
    "$CLAUDE_TPL_TEXT" "P$CANONICAL_COUNT"

# Primitive table has a row for each P1..P<canonical>.
CLAUDE_ROW_COUNT="$(grep -cE '^\| P[0-9]+ \|' "$CLAUDE_TPL" || echo 0)"
assert_eq "CLAUDE.md.template primitive table row count" "$CLAUDE_ROW_COUNT" "$CANONICAL_COUNT"

# ── 4. AGENTS.md.template ───────────────────────────────────────────────
echo ""
echo "=== AGENTS.md.template ==="

AGENTS_TPL_TEXT="$(cat "$AGENTS_TPL")"
assert_contains "AGENTS.md.template intro says '$CANONICAL_WORD irreducible building blocks'" \
    "$AGENTS_TPL_TEXT" "$CANONICAL_WORD irreducible building blocks"
assert_contains "AGENTS.md.template composition-loop intro says '$CANONICAL_WORD primitives'" \
    "$AGENTS_TPL_TEXT" "$CANONICAL_WORD primitives compose"

# One ### Pn — section per primitive.
AGENTS_SECTION_COUNT="$(grep -cE '^### P[0-9]+ — ' "$AGENTS_TPL" || echo 0)"
assert_eq "AGENTS.md.template ### Pn section count" "$AGENTS_SECTION_COUNT" "$CANONICAL_COUNT"

# ── 5. Short-name index symmetry between CLAUDE.md.template and AGENTS.md.template ──
echo ""
echo "=== Short-name index symmetry ==="

CLAUDE_IDX="$(grep -E '^\*\*Short-name index\*\*' "$CLAUDE_TPL" | head -1)"
AGENTS_IDX="$(grep -E '^\*\*Short-name index\*\*' "$AGENTS_TPL" | head -1)"

if [ -n "$CLAUDE_IDX" ] && [ -n "$AGENTS_IDX" ]; then
    CLAUDE_IDX_PAYLOAD="$(echo "$CLAUDE_IDX" | sed 's/^\*\*Short-name index\*\*[^:]*:[[:space:]]*//')"
    AGENTS_IDX_PAYLOAD="$(echo "$AGENTS_IDX" | sed 's/^\*\*Short-name index\*\*[^:]*:[[:space:]]*//')"
    assert_eq "Short-name index payload matches between templates" \
        "$CLAUDE_IDX_PAYLOAD" "$AGENTS_IDX_PAYLOAD"
else
    echo "  [FAIL] Short-name index missing in one of the templates"
    FAIL=$((FAIL + 1))
    FAILED_TESTS+=("Short-name index presence")
fi

# Index lists exactly $CANONICAL_COUNT entries (count `(P` occurrences).
if [ -n "$CLAUDE_IDX" ]; then
    INDEX_ENTRIES="$(echo "$CLAUDE_IDX" | grep -oE '\(P[0-9]+\)' | wc -l | tr -d ' ')"
    assert_eq "CLAUDE.md.template short-name index entry count" "$INDEX_ENTRIES" "$CANONICAL_COUNT"
fi

# ── 6. Plugin Skill Precedence section present in both templates ─────────
echo ""
echo "=== Plugin Skill Precedence presence ==="
assert_contains "CLAUDE.md.template has Plugin Skill Precedence section" \
    "$CLAUDE_TPL_TEXT" "## Plugin Skill Precedence"
assert_contains "AGENTS.md.template has Plugin Skill Precedence section" \
    "$AGENTS_TPL_TEXT" "## Plugin Skill Precedence"

# ── Summary ──────────────────────────────────────────────────────────────
echo ""
echo "=== Lockstep test summary ==="
echo "  Passed: $PASS"
echo "  Failed: $FAIL"

if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo "Failed assertions:"
    for t in "${FAILED_TESTS[@]}"; do
        echo "  - $t"
    done
    exit 1
fi

echo ""
echo "  All lockstep checks passed at canonical count = $CANONICAL_COUNT ($CANONICAL_WORD)"
exit 0
