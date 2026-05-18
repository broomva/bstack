#!/usr/bin/env bash
# canary/05 — crystallize detector (Phase 7, v0.9.5).
#
# Asserts the rule-of-three detector surfaces a known recurring pattern
# from fixture conversations and does NOT false-positive on the
# negative fixtures or below-threshold session counts.
#
# Coverage:
#   - bin/bstack crystallize --help advertises both subcommands
#   - candidates --json produces valid JSON with at least one candidate
#   - the squash-merge-race fixture pattern surfaces (>=3 sessions)
#   - --min-sessions=99 filters everything (no false positives)
#   - promote <slug> produces a draft scaffold with the no-auto-merge
#     disclaimer
#   - unknown subcommands exit non-zero
#   - missing conversations directory exits 3

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BSTACK_BIN="$REPO/bin/bstack"
FIXTURES_DIR="$REPO/tests/fixtures/conversations"

PASS=0
FAIL=0
FAILED=()

pass() { echo "  [ok] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); FAILED+=("$1"); }

echo "── canary/05 — crystallize detector ───────────────────"
echo "  fixtures: $FIXTURES_DIR"
echo ""

# ── Step 1: fixtures present ────────────────────────────────
echo "Step 1: fixtures present"
if [ -d "$FIXTURES_DIR" ]; then
    pos_count=$(find "$FIXTURES_DIR" -maxdepth 1 -name 'positive-*.md' 2>/dev/null | wc -l | tr -d ' ')
    neg_count=$(find "$FIXTURES_DIR" -maxdepth 1 -name 'negative-*.md' 2>/dev/null | wc -l | tr -d ' ')
    if [ "$pos_count" -ge 3 ]; then
        pass ">=3 positive fixtures (count=$pos_count)"
    else
        fail "<3 positive fixtures (count=$pos_count)"
    fi
    if [ "$neg_count" -ge 1 ]; then
        pass ">=1 negative fixture (count=$neg_count)"
    else
        fail "<1 negative fixture (count=$neg_count)"
    fi
else
    fail "fixtures dir absent: $FIXTURES_DIR"
fi

# ── Step 2: bstack crystallize --help ───────────────────────
echo ""
echo "Step 2: bstack crystallize --help"
help_out=$("$BSTACK_BIN" crystallize --help 2>&1 || true)
if echo "$help_out" | grep -q 'candidates'; then
    pass "crystallize --help mentions 'candidates'"
else
    fail "crystallize --help missing 'candidates'"
fi
if echo "$help_out" | grep -q 'promote'; then
    pass "crystallize --help mentions 'promote'"
else
    fail "crystallize --help missing 'promote'"
fi

# ── Step 3: candidates --json against fixtures ──────────────
echo ""
echo "Step 3: bstack crystallize candidates --json"
cand_out=$("$BSTACK_BIN" crystallize candidates --conversations "$FIXTURES_DIR" --json 2>/dev/null || true)
if [ -z "$cand_out" ]; then
    fail "candidates --json produced no output"
elif ! echo "$cand_out" | jq -e '.candidates' >/dev/null 2>&1; then
    fail "candidates --json is not valid JSON with .candidates"
else
    count=$(echo "$cand_out" | jq -r '.candidates | length')
    pass "candidates --json returned $count candidate(s)"
    if [ "$count" -lt 1 ]; then
        fail "expected >=1 candidate, got $count"
    fi
fi

# ── Step 4: squash-related rule-of-three surfaces ──────────
echo ""
echo "Step 4: known squash pattern surfaces"
matching_slug=$(echo "$cand_out" | jq -r '.candidates[] | select(.slug | contains("squash")) | .slug' 2>/dev/null | head -1)
matching_count=$(echo "$cand_out" | jq -r '.candidates[] | select(.slug | contains("squash")) | .session_count' 2>/dev/null | head -1)
if [ -n "$matching_slug" ]; then
    pass "candidate slug contains 'squash' (slug=$matching_slug)"
else
    fail "no candidate slug contains 'squash'"
fi
if [ -n "$matching_count" ] && [ "$matching_count" -ge 3 ]; then
    pass "squash pattern spans >=3 sessions (count=$matching_count)"
else
    fail "squash pattern spans <3 sessions (count=${matching_count:-unknown})"
fi

# ── Step 5: --min-sessions=99 → no candidates ───────────────
echo ""
echo "Step 5: --min-sessions=99 returns 0 (no false positives)"
hi_out=$("$BSTACK_BIN" crystallize candidates --conversations "$FIXTURES_DIR" --min-sessions 99 --json 2>/dev/null || true)
hi_count=$(echo "$hi_out" | jq -r '.candidates | length' 2>/dev/null || echo 0)
if [ "$hi_count" = "0" ]; then
    pass "--min-sessions=99 returns 0 candidates"
else
    fail "--min-sessions=99 returned $hi_count candidates (expected 0)"
fi

# ── Step 6: promote draft scaffold ──────────────────────────
echo ""
echo "Step 6: bstack crystallize promote <slug>"
if [ -n "$matching_slug" ]; then
    promote_out=$("$BSTACK_BIN" crystallize promote "$matching_slug" --conversations "$FIXTURES_DIR" 2>/dev/null || true)
    if echo "$promote_out" | grep -q 'DRAFT'; then
        pass "promote scaffold contains DRAFT marker"
    else
        fail "promote scaffold missing DRAFT marker"
    fi
    if echo "$promote_out" | grep -qi 'auto-merge'; then
        pass "promote scaffold disclaims auto-merge"
    else
        fail "promote scaffold missing auto-merge disclaimer"
    fi
    if echo "$promote_out" | grep -q 'P16'; then
        pass "promote scaffold cites P16 manual gates"
    else
        fail "promote scaffold missing P16 reference"
    fi
else
    fail "no slug available for promote step"
fi

# ── Step 7: unknown subcommand exits non-zero ───────────────
echo ""
echo "Step 7: unknown subcommand exits non-zero"
if "$BSTACK_BIN" crystallize bogus-sub-xyzzy >/dev/null 2>&1; then
    fail "unknown subcommand exited 0 (expected non-zero)"
else
    rc=$?
    pass "unknown subcommand exited $rc"
fi

# ── Step 8: missing conversations dir exits 3 ───────────────
echo ""
echo "Step 8: missing conversations dir exits 3"
missing_dir="/tmp/bstack-crystallize-missing-$$"
rm -rf "$missing_dir" 2>/dev/null
if "$BSTACK_BIN" crystallize candidates --conversations "$missing_dir" --json >/dev/null 2>&1; then
    fail "missing-dir invocation exited 0 (expected 3)"
else
    rc=$?
    if [ "$rc" = 3 ]; then
        pass "missing-dir exited 3 as expected"
    else
        fail "missing-dir exited $rc (expected 3)"
    fi
fi

# ── Step 9: promote with unknown slug exits 4 ───────────────
echo ""
echo "Step 9: promote with unknown slug exits 4"
if "$BSTACK_BIN" crystallize promote nonexistent-slug-xyzzy --conversations "$FIXTURES_DIR" >/dev/null 2>&1; then
    fail "promote unknown-slug exited 0 (expected 4)"
else
    rc=$?
    if [ "$rc" = 4 ]; then
        pass "promote unknown-slug exited 4 as expected"
    else
        fail "promote unknown-slug exited $rc (expected 4)"
    fi
fi

echo ""
echo "─────────────────────────────────────"
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo "  Failed assertions:"
    for t in "${FAILED[@]}"; do echo "    - $t"; done
    exit 1
fi
echo "  canary/05 passed."
