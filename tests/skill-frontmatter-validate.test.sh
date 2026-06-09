#!/usr/bin/env bash
# tests/skill-frontmatter-validate.test.sh — validate-skill-frontmatter.py (BRO-1449)
#
# Validates the SKILL.md frontmatter checker against the Agent Skills open
# standard. Severity policy under test:
#   ERROR (exit 1): no frontmatter · description missing/empty
#   WARNING (exit 0): bad name casing/length · description over ceiling · name!=dir
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VALIDATOR="$BSTACK_REPO/scripts/validate-skill-frontmatter.py"

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

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ── Fixture builders ──────────────────────────────────────────────────────────
mk_skill() {
    # mk_skill <dir-name> <file-content>
    local dir="$TMP/$1"
    mkdir -p "$dir"
    printf '%s' "$2" > "$dir/SKILL.md"
    echo "$dir/SKILL.md"
}

echo ""
echo "Test 0: validator exists and runs"
if [ -f "$VALIDATOR" ] && python3 "$VALIDATOR" --help >/dev/null 2>&1; then
    assert_pass "validate-skill-frontmatter.py present and --help works"
else
    assert_fail "validator missing or --help failed"
    echo "Summary: $PASS passed, $FAIL failed"; exit 1
fi

# ── Test 1: valid skill → exit 0, no error ────────────────────────────────────
echo ""
echo "Test 1: valid SKILL.md passes cleanly"
VALID="$(mk_skill good-skill '---
name: good-skill
description: Does a clear thing. Use when the user mentions the clear thing or wants it done.
---
body
')"
if python3 "$VALIDATOR" "$VALID" >/dev/null 2>&1; then
    assert_pass "valid skill exits 0"
else
    assert_fail "valid skill should exit 0"
fi

# ── Test 2: missing description → ERROR, exit 1 ───────────────────────────────
echo ""
echo "Test 2: missing description is an ERROR"
NODESC="$(mk_skill nodesc-skill '---
name: nodesc-skill
---
body
')"
out="$(python3 "$VALIDATOR" "$NODESC" 2>&1)"; rc=$?
if [ "$rc" -eq 1 ] && echo "$out" | grep -q "ERROR"; then
    assert_pass "missing description → exit 1 + ERROR"
else
    assert_fail "missing description should error (rc=$rc)" "$out"
fi

# ── Test 3: no frontmatter → ERROR, exit 1 ────────────────────────────────────
echo ""
echo "Test 3: no frontmatter block is an ERROR"
NOFM="$(mk_skill nofm-skill 'just a body, no frontmatter
')"
out="$(python3 "$VALIDATOR" "$NOFM" 2>&1)"; rc=$?
if [ "$rc" -eq 1 ] && echo "$out" | grep -qi "no yaml frontmatter"; then
    assert_pass "no frontmatter → exit 1 + ERROR"
else
    assert_fail "no frontmatter should error (rc=$rc)" "$out"
fi

# ── Test 4: bad name casing → WARNING, exit 0 ─────────────────────────────────
echo ""
echo "Test 4: non-lowercase-hyphen name is a WARNING (not fatal)"
BADNAME="$(mk_skill BadName '---
name: BadName
description: A skill with a non-conformant PascalCase name. Use when testing name validation.
---
body
')"
out="$(python3 "$VALIDATOR" "$BADNAME" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && echo "$out" | grep -qi "lowercase-hyphen"; then
    assert_pass "bad name → exit 0 + warning"
else
    assert_fail "bad name should warn but not fail (rc=$rc)" "$out"
fi

# ── Test 5: description over ceiling → WARNING ────────────────────────────────
echo ""
echo "Test 5: over-ceiling description warns (custom --ceiling 50)"
LONGDESC="$(mk_skill long-skill '---
name: long-skill
description: This description is deliberately quite a bit longer than fifty characters to trip the ceiling.
---
body
')"
out="$(python3 "$VALIDATOR" --ceiling 50 "$LONGDESC" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && echo "$out" | grep -qi "portable ceiling"; then
    assert_pass "over-ceiling description → exit 0 + warning"
else
    assert_fail "over-ceiling description should warn (rc=$rc)" "$out"
fi

# ── Test 6: name != parent dir → WARNING ──────────────────────────────────────
echo ""
echo "Test 6: name not matching parent dir warns"
MISMATCH="$(mk_skill dir-name-x '---
name: other-name
description: Name does not match its parent directory. Use when validating the dir-match rule.
---
body
')"
out="$(python3 "$VALIDATOR" "$MISMATCH" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && echo "$out" | grep -qi "!= parent directory"; then
    assert_pass "name != dir → warning"
else
    assert_fail "name != dir should warn (rc=$rc)" "$out"
fi

# ── Test 7: folded (>) block description length is measured ───────────────────
echo ""
echo "Test 7: folded block-scalar description is parsed and measured"
FOLDED="$(mk_skill folded-skill '---
name: folded-skill
description: >
  This is a folded block scalar description that spans
  multiple lines and should be joined into one string.
---
body
')"
out="$(python3 "$VALIDATOR" --ceiling 20 "$FOLDED" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && echo "$out" | grep -qi "portable ceiling"; then
    assert_pass "folded description parsed + measured (warns over ceiling 20)"
else
    assert_fail "folded description should be measured (rc=$rc)" "$out"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════"
echo "Summary: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
    printf '  FAILED: %s\n' "${FAILED_TESTS[@]}"
    exit 1
fi
exit 0
