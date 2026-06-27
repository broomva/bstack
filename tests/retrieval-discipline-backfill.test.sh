#!/usr/bin/env bash
# tests/retrieval-discipline-backfill.test.sh — fixture-based tests for the §P6
# "/kg for discovery, never substrate grep" retrieval-discipline backfill in
# scripts/repair.sh and the advisory (§4c) in scripts/doctor.sh.
#
# Verifies:
#   1. A pre-BRO-1426 AGENTS.md (§P6 present, retrieval paragraph absent) gets
#      the paragraph backfilled, positioned INSIDE §P6 (after ### P6, before ### P7).
#   2. Re-running is idempotent (exactly one occurrence; no duplicate).
#   3. --dry-run reports the backfill but writes nothing.
#   4. Missing §P6→heading anchor → skip with a warning, file unchanged.
#   5. doctor §4c surfaces the advisory when absent, ok when present — and the
#      advisory is NOT a GAP / does NOT change the GAP total or --strict exit.
#   6. CRLF / trailing-space tolerant; section content extracted verbatim.
#
# Run from repo root: bash tests/retrieval-discipline-backfill.test.sh
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPAIR_SH="$BSTACK_REPO/scripts/repair.sh"
DOCTOR_SH="$BSTACK_REPO/scripts/doctor.sh"
TEMPLATE="$BSTACK_REPO/assets/templates/AGENTS.md.template"

PASS=0
FAIL=0
FAILED_TESTS=()

assert_pass() { PASS=$((PASS + 1)); echo "  ✓ $1"; }
assert_fail() {
    FAIL=$((FAIL + 1)); FAILED_TESTS+=("$1")
    echo "  ✗ $1"; [ -n "${2:-}" ] && echo "    ${2}"
}

# Count occurrences of the shared marker phrase (matches repair + doctor).
refl_count() { grep -cF "substrate grep" "$1" 2>/dev/null || true; }

# A workspace whose AGENTS.md has the Primitives anchor + §P6 and §P7 sections
# but NO retrieval-discipline paragraph (simulates a pre-BRO-1426 bootstrap).
# CLAUDE.md carries the anchor so the sibling philosophy backfill doesn't error.
fresh_pre1426_workspace() {
    local ws; ws="$(mktemp -d)"
    cat > "$ws/AGENTS.md" <<'EOF'
# demo — Agent Guidelines

## Self-Meta Definition

This file IS the control harness.

## Bstack Core Automation Primitives

### P6 — Bookkeeping: Knowledge Bookkeeping

**What**: pipeline.

**Reflexive Trigger Rule**: Bookkeeping is a reflex.

**Never a question.** Capture is the default.

### P7 — Freshness: Skill Freshness Check

**What**: stale-install detector.
EOF
    cat > "$ws/CLAUDE.md" <<'EOF'
# demo — bstack-governed workspace

## Identity

Governed by bstack.

## Bstack Core Automation Primitives

(table)
EOF
    echo "$ws"
}

echo "=== retrieval-discipline-backfill.test.sh ==="

# Precondition: the template carries the paragraph (else the whole feature is moot).
if [ "$(refl_count "$TEMPLATE")" -ge 1 ]; then
    assert_pass "template AGENTS.md.template carries the retrieval-discipline paragraph"
else
    assert_fail "template is missing the retrieval-discipline paragraph (feature source gone)"
fi

# ── Test 1 + 2: backfill + idempotency ──────────────────────────────────────
echo ""
echo "Test 1+2: backfill into pre-BRO-1426 workspace, then idempotency"
WS="$(fresh_pre1426_workspace)"

[ "$(refl_count "$WS/AGENTS.md")" -eq 0 ] && assert_pass "precondition: AGENTS.md has no retrieval paragraph" \
    || assert_fail "precondition: AGENTS.md should start without the paragraph"

BROOMVA_WORKSPACE="$WS" bash "$REPAIR_SH" --apply-all >/dev/null 2>&1

if [ "$(refl_count "$WS/AGENTS.md")" -eq 1 ]; then
    assert_pass "AGENTS.md retrieval paragraph backfilled (exactly one)"
else
    assert_fail "retrieval paragraph not backfilled (count=$(refl_count "$WS/AGENTS.md"))"
fi

# Position: paragraph must land INSIDE §P6 — after ### P6, before ### P7.
refl_line="$(grep -nF "substrate grep" "$WS/AGENTS.md" | head -1 | cut -d: -f1)"
p6_line="$(grep -nE "^### P6" "$WS/AGENTS.md" | head -1 | cut -d: -f1)"
p7_line="$(grep -nE "^### P7" "$WS/AGENTS.md" | head -1 | cut -d: -f1)"
if [ -n "$refl_line" ] && [ "$refl_line" -gt "$p6_line" ] && [ "$refl_line" -lt "$p7_line" ]; then
    assert_pass "paragraph positioned inside §P6 (P6=$p6_line < para=$refl_line < P7=$p7_line)"
else
    assert_fail "paragraph not inside §P6 (P6=$p6_line para=$refl_line P7=$p7_line)"
fi

# Content extracted verbatim (backticks / pipes / quotes intact).
if grep -qF 'never as the step that decides' "$WS/AGENTS.md"; then
    assert_pass "paragraph content extracted verbatim (delimiters intact)"
else
    assert_fail "paragraph content lost in extraction"
fi

# Idempotency: second run must not duplicate.
BROOMVA_WORKSPACE="$WS" bash "$REPAIR_SH" --apply-all >/dev/null 2>&1
if [ "$(refl_count "$WS/AGENTS.md")" -eq 1 ]; then
    assert_pass "idempotent: re-run did not duplicate the paragraph"
else
    assert_fail "idempotency broken (count=$(refl_count "$WS/AGENTS.md"))"
fi
rm -rf "$WS"

# ── Test 3: --dry-run writes nothing ────────────────────────────────────────
echo ""
echo "Test 3: --dry-run reports but does not write"
WS="$(fresh_pre1426_workspace)"
DRY_OUT="$(BROOMVA_WORKSPACE="$WS" bash "$REPAIR_SH" --dry-run 2>&1)"
if echo "$DRY_OUT" | grep -q "would backfill P6 retrieval-discipline into AGENTS.md"; then
    assert_pass "--dry-run reports the backfill"
else
    assert_fail "--dry-run did not report the backfill"
fi
if [ "$(refl_count "$WS/AGENTS.md")" -eq 0 ]; then
    assert_pass "--dry-run wrote nothing"
else
    assert_fail "--dry-run modified the file (count=$(refl_count "$WS/AGENTS.md"))"
fi
rm -rf "$WS"

# ── Test 4: missing §P6 anchor → skip, file unchanged ───────────────────────
echo ""
echo "Test 4: missing §P6 section → skip with warning"
WS="$(mktemp -d)"
printf '# demo\n\n## Bstack Core Automation Primitives\n\n(no P6 here)\n' > "$WS/AGENTS.md"
printf '# demo\n\n## Bstack Core Automation Primitives\n\n(table)\n' > "$WS/CLAUDE.md"
SKIP_OUT="$(BROOMVA_WORKSPACE="$WS" bash "$REPAIR_SH" --apply-all 2>&1)"
if echo "$SKIP_OUT" | grep -q "retrieval-discipline — no §P6→heading anchor"; then
    assert_pass "missing §P6 anchor reported as skip"
else
    assert_fail "missing-anchor skip not reported"
fi
if [ "$(refl_count "$WS/AGENTS.md")" -eq 0 ]; then
    assert_pass "missing-anchor file left unchanged"
else
    assert_fail "paragraph inserted despite missing anchor"
fi
rm -rf "$WS"

# ── Test 4b: reflex present under DIFFERENT wording → no duplication ─────────
echo ""
echo "Test 4b: a reworded reflex (drops the phrase, keeps the **Retrieval discipline lead) is not duplicated"
WS="$(fresh_pre1426_workspace)"
# Inject a reworded reflex into §P6 that OMITS the phrase "substrate grep" but
# carries the structural **Retrieval discipline lead.
awk '
    { print }
    /^### P6 / && !done {
        print ""
        print "**Retrieval discipline.** Use `/kg` for discovery; never grep `research/entities/` directly."
        done = 1
    }
' "$WS/AGENTS.md" > "$WS/AGENTS.md.tmp" && mv "$WS/AGENTS.md.tmp" "$WS/AGENTS.md"
lead_before="$(grep -cE '^\*\*Retrieval discipline' "$WS/AGENTS.md")"
BROOMVA_WORKSPACE="$WS" bash "$REPAIR_SH" --apply-all >/dev/null 2>&1
lead_after="$(grep -cE '^\*\*Retrieval discipline' "$WS/AGENTS.md")"
if [ "$lead_before" -eq 1 ] && [ "$lead_after" -eq 1 ]; then
    assert_pass "reworded reflex not duplicated (lead count stayed 1)"
else
    assert_fail "reworded reflex duplicated (before=$lead_before after=$lead_after)"
fi
# doctor §4c must ALSO report ok on the reworded variant (drops the phrase,
# keeps the **Retrieval discipline lead) — proving doctor's structural signal
# agrees with repair's. Needs .control so doctor runs its sections. Capture the
# output to a var first: the bare fixture has real gaps so doctor exits non-zero,
# and `doctor | grep -q` under `set -o pipefail` would return doctor's exit, not
# grep's — a false failure. Grepping the captured string avoids that.
mkdir -p "$WS/.control"
cp "$BSTACK_REPO/assets/templates/policy.yaml.template" "$WS/.control/policy.yaml" 2>/dev/null || true
DOC_REWORDED="$(BROOMVA_WORKSPACE="$WS" bash "$DOCTOR_SH" 2>&1)"
if echo "$DOC_REWORDED" | grep -q "has P6 retrieval-discipline reflex"; then
    assert_pass "doctor §4c treats the reworded (phrase-less) reflex as present"
else
    assert_fail "doctor §4c did not recognize the reworded reflex (structural signal broken)"
fi
rm -rf "$WS"

# ── Test 5: doctor §4c advisory present/absent + 0 gaps / strict-neutral ─────
echo ""
echo "Test 5: doctor §4c advisory is informational (does not change GAP total / --strict)"
gap_lines() { grep -c "^  \[gap\]" 2>/dev/null || true; }
WS="$(mktemp -d)"; mkdir -p "$WS/.control"
cp "$BSTACK_REPO/assets/templates/policy.yaml.template" "$WS/.control/policy.yaml" 2>/dev/null || true
cp "$BSTACK_REPO/assets/templates/CLAUDE.md.template" "$WS/CLAUDE.md" 2>/dev/null || true

# (a) WITHOUT the paragraph: strip it from the real template (para line → next ### , exclusive).
awk '/^\*\*Retrieval discipline/{skip=1} skip && /^### /{skip=0} !skip' \
    "$TEMPLATE" > "$WS/AGENTS.md"
DOC_WITHOUT="$(BROOMVA_WORKSPACE="$WS" bash "$DOCTOR_SH" 2>&1)"
BROOMVA_WORKSPACE="$WS" bash "$DOCTOR_SH" --strict >/dev/null 2>&1; STRICT_WITHOUT=$?
GAPS_WITHOUT="$(echo "$DOC_WITHOUT" | gap_lines)"

if echo "$DOC_WITHOUT" | grep -q "no P6 retrieval-discipline reflex"; then
    assert_pass "doctor surfaces §4c advisory when paragraph absent"
else
    assert_fail "doctor did not surface the §4c advisory"
fi
if echo "$DOC_WITHOUT" | grep -qE "\[gap\].*(retrieval|substrate grep)"; then
    assert_fail "advisory emitted as a GAP (should be informational)"
else
    assert_pass "advisory is informational, not a [gap] line"
fi

# (b) WITH the paragraph: identical workspace, real template (paragraph present).
cp "$TEMPLATE" "$WS/AGENTS.md"
DOC_WITH="$(BROOMVA_WORKSPACE="$WS" bash "$DOCTOR_SH" 2>&1)"
BROOMVA_WORKSPACE="$WS" bash "$DOCTOR_SH" --strict >/dev/null 2>&1; STRICT_WITH=$?
GAPS_WITH="$(echo "$DOC_WITH" | gap_lines)"

if echo "$DOC_WITH" | grep -q "has P6 retrieval-discipline reflex"; then
    assert_pass "doctor §4c reports ok when paragraph present"
else
    assert_fail "doctor §4c did not report ok with paragraph present"
fi
if [ "$GAPS_WITHOUT" = "$GAPS_WITH" ]; then
    assert_pass "GAP total identical with/without paragraph ($GAPS_WITH) — advisory adds 0 gaps"
else
    assert_fail "advisory changed the GAP total (without=$GAPS_WITHOUT with=$GAPS_WITH)"
fi
if [ "$STRICT_WITHOUT" = "$STRICT_WITH" ]; then
    assert_pass "--strict exit identical with/without paragraph (=$STRICT_WITH)"
else
    assert_fail "--strict exit changed (without=$STRICT_WITHOUT with=$STRICT_WITH)"
fi
rm -rf "$WS"

# ── Test 6: CRLF / trailing-space §P6 heading is matched ─────────────────────
echo ""
echo "Test 6: CRLF + trailing-space §P6/§P7 headings are matched"
WS="$(mktemp -d)"
printf '# demo\r\n\r\n## Bstack Core Automation Primitives\r\n\r\n### P6 — Bookkeeping   \r\n\r\n**What**: x.\r\n\r\n### P7 — Freshness\r\n\r\n**What**: y.\r\n' > "$WS/AGENTS.md"
printf '# demo\n\n## Bstack Core Automation Primitives\n\n(table)\n' > "$WS/CLAUDE.md"
BROOMVA_WORKSPACE="$WS" bash "$REPAIR_SH" --apply-all >/dev/null 2>&1
if [ "$(refl_count "$WS/AGENTS.md")" -eq 1 ]; then
    assert_pass "CRLF/trailing-space §P6 heading matched — paragraph inserted"
else
    assert_fail "CRLF/trailing-space §P6 NOT matched (count=$(refl_count "$WS/AGENTS.md"))"
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
