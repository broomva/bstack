#!/usr/bin/env bash
# tests/hook-guard.test.sh — bstack plugin scope-safety (BRO-1926, v0.36.0).
#
# The bstack plugin loads at personal scope, so its hooks fire in every session.
# Two mechanisms keep it from polluting / misbehaving in non-bstack repos:
#   - bstack-hook-guard.sh wraps the two workspace-.control WRITERS (knowledge-wakeup,
#     leverage-sensor) and no-ops them when the workspace has no .control/ dir.
#   - l3-stability-pretool-hook.sh is source-guarded: it approves without writing
#     .control/audit when the workspace is not bstack-governed.
#
# Validates:
#   Guard:  1. non-governed → wrapped hook is a silent no-op (no output)
#           2. governed → wrapped hook runs
#           3. governed → wrapped script's $0/dirname resolves to its own dir
#           4. governed → non-zero exit of the wrapped hook propagates (exec transparency)
#           5. governed → stdout of the wrapped hook passes through
#   l3:     6. non-governed + L3-file edit → approve, and NO .control/ is created
#           7. governed + L3-file edit → an audit line is written to .control/audit/

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GUARD="$BSTACK_REPO/hooks/bstack-hook-guard.sh"
L3_HOOK="$BSTACK_REPO/scripts/l3-stability-pretool-hook.sh"

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

# 1. non-governed → silent no-op (no stdout)
out="$(cd "$TMP/nogov" && bash "$GUARD" bash -c 'echo RAN' 2>/dev/null)"
if [ -z "$out" ]; then
    assert_pass "guard: non-governed workspace is a silent no-op"
else
    assert_fail "guard: non-governed workspace is a silent no-op (got: $out)"
fi

# 2. governed → hook runs
out="$(cd "$TMP/gov" && bash "$GUARD" bash -c 'echo RAN' 2>/dev/null)"
if echo "$out" | grep -q RAN; then
    assert_pass "guard: governed workspace runs the wrapped hook"
else
    assert_fail "guard: governed workspace runs the wrapped hook (it did not)"
fi

# 3. governed → wrapped script's $0/dirname resolves to its own dir
# shellcheck disable=SC2016  # the $(...) is written literally into probe.sh on purpose
printf '#!/usr/bin/env bash\necho "SELF=$(cd "$(dirname "$0")" && pwd)"\n' > "$TMP/gov/probe.sh"
out="$(cd "$TMP/gov" && bash "$GUARD" bash "$TMP/gov/probe.sh" 2>/dev/null)"
if echo "$out" | grep -q "SELF=$TMP/gov"; then
    assert_pass "guard: sibling \$0/dirname resolution preserved through exec"
else
    assert_fail "guard: sibling \$0/dirname resolution preserved through exec"
fi

# 4. governed → non-zero exit of the wrapped hook propagates
(cd "$TMP/gov" && bash "$GUARD" bash -c 'exit 7') 2>/dev/null
rc=$?
if [ "$rc" -eq 7 ]; then
    assert_pass "guard: wrapped hook's non-zero exit propagates (got $rc)"
else
    assert_fail "guard: wrapped hook's non-zero exit propagates (got $rc, want 7)"
fi

# 5. governed → stdout of the wrapped hook (e.g. a JSON decision) passes through
out="$(cd "$TMP/gov" && bash "$GUARD" bash -c 'echo "{\"decision\":\"block\"}"' 2>/dev/null)"
if echo "$out" | grep -q '"decision":"block"'; then
    assert_pass "guard: wrapped hook stdout (JSON decision) passes through"
else
    assert_fail "guard: wrapped hook stdout passes through (got: $out)"
fi

# 6. l3 source-guard: non-governed + L3-file edit → approve, no .control/ created
l3in='{"tool_name":"Edit","tool_input":{"file_path":"CLAUDE.md"}}'
out="$(cd "$TMP/nogov" && echo "$l3in" | bash "$L3_HOOK" 2>/dev/null)"
if echo "$out" | grep -q '"decision":"approve"' && [ ! -d "$TMP/nogov/.control" ]; then
    assert_pass "l3: non-governed L3 edit approves and creates no .control/"
else
    assert_fail "l3: non-governed L3 edit approves and creates no .control/ (out=$out, control=$([ -d "$TMP/nogov/.control" ] && echo yes || echo no))"
fi

# 7. l3 source-guard: governed + L3-file edit → audit line written
out="$(cd "$TMP/gov" && echo "$l3in" | bash "$L3_HOOK" 2>/dev/null)"
if [ -s "$TMP/gov/.control/audit/l3-edits.jsonl" ]; then
    assert_pass "l3: governed L3 edit writes an audit line"
else
    assert_fail "l3: governed L3 edit writes an audit line (missing/empty)"
fi

echo ""
echo "hook-guard: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
    printf '  FAILED: %s\n' "${FAILED_TESTS[@]}"
    exit 1
fi
exit 0
