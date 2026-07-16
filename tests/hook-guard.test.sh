#!/usr/bin/env bash
# tests/hook-guard.test.sh — bstack-hook-guard.sh scope-safety (BRO-1926, v0.36.0).
#
# The bstack plugin loads at personal scope, so its hooks fire in every session.
# bstack-hook-guard.sh gates each hook on the workspace being bstack-governed
# (has a .control/ dir), so the global plugin never pollutes non-bstack repos.
#
# Validates:
#   1. In a NON-governed dir (no .control/), a wrapped generic hook does NOT run
#   2. In a NON-governed dir, a wrapped PreToolUse (l3) hook emits approve + exit 0
#   3. In a GOVERNED dir (has .control/), the wrapped hook runs
#   4. The wrapped script's $0/dirname still resolves to the script's own dir
#      (sibling-exec resolution preserved through the guard)

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GUARD="$BSTACK_REPO/hooks/bstack-hook-guard.sh"

PASS=0
FAIL=0
FAILED_TESTS=()

assert_pass() { PASS=$((PASS + 1)); echo "  ✓ $1"; }
assert_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS+=("$1")
    echo "  ✗ $1"
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/nogov" "$TMP/gov/.control"

# 1. non-governed → generic hook must NOT run
out="$(cd "$TMP/nogov" && bash "$GUARD" bash -c 'echo RAN' 2>/dev/null)"
if ! echo "$out" | grep -q RAN; then
    assert_pass "non-governed workspace: wrapped hook is a no-op"
else
    assert_fail "non-governed workspace: wrapped hook is a no-op (it ran)"
fi

# 2. non-governed → PreToolUse (l3) must emit approve
out="$(cd "$TMP/nogov" && bash "$GUARD" bash "/x/scripts/l3-stability-pretool-hook.sh" 2>/dev/null)"
if echo "$out" | grep -q '"decision":"approve"'; then
    assert_pass "non-governed workspace: PreToolUse hook emits approve"
else
    assert_fail "non-governed workspace: PreToolUse hook emits approve (missing)"
fi

# 3. governed → hook must run
out="$(cd "$TMP/gov" && bash "$GUARD" bash -c 'echo RAN' 2>/dev/null)"
if echo "$out" | grep -q RAN; then
    assert_pass "governed workspace: wrapped hook runs"
else
    assert_fail "governed workspace: wrapped hook runs (it did not)"
fi

# 4. governed → wrapped script's $0/dirname resolves to its own dir
# shellcheck disable=SC2016  # the $(...) is written literally into probe.sh on purpose
printf '#!/usr/bin/env bash\necho "SELF=$(cd "$(dirname "$0")" && pwd)"\n' > "$TMP/gov/probe.sh"
out="$(cd "$TMP/gov" && bash "$GUARD" bash "$TMP/gov/probe.sh" 2>/dev/null)"
if echo "$out" | grep -q "SELF=$TMP/gov"; then
    assert_pass "governed workspace: sibling \$0/dirname resolution preserved"
else
    assert_fail "governed workspace: sibling \$0/dirname resolution preserved"
fi

echo ""
echo "hook-guard: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
    printf '  FAILED: %s\n' "${FAILED_TESTS[@]}"
    exit 1
fi
exit 0
