#!/usr/bin/env bash
# tests/workspace.test.sh — Phase 8 (v0.18.0) federation registry smoke.
#
# Validates the contract documented in bin/bstack-workspace + scripts/
# workspace.py + schemas/workspaces.v1.json:
#
#   1. register a fresh entry → list --json includes it (count == 1)
#   2. register same path twice → action: refreshed (not duplicated)
#   3. register same name at a different path → exit 5 (name conflict)
#   4. info --path PATH reports registered: true after register
#   5. deregister --path PATH removes the entry (count == 0)
#   6. deregister with no matching entry → exit 4
#   7. corrupt registry to schema_version: 99 → list exits 3
#   8. register --tag X --tag Y → tags persisted and accumulated on refresh
#   9. register with invalid name → exit 2
#  10. --help renders the help block
#
# Hermetic: every test uses BSTACK_REGISTRY=$(mktemp -d)/registry.yaml so
# the host ~/.broomva/global/registry.yaml is never touched.

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_BIN="$BSTACK_REPO/bin/bstack-workspace"

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

# Each test gets a fresh registry path so they cannot contaminate each other.
fresh_reg() {
    local d
    d=$(mktemp -d)
    echo "$d/registry.yaml"
}

# ── Test 1: register a fresh entry → list shows it ─────────────────────
echo ""
echo "Test 1: register a fresh entry → list --json count == 1"
REG=$(fresh_reg)
TMP=$(mktemp -d)
BSTACK_REGISTRY="$REG" bash "$WORKSPACE_BIN" register --path "$TMP" --name t1 --json >/dev/null 2>&1
out=$(BSTACK_REGISTRY="$REG" bash "$WORKSPACE_BIN" list --json 2>/dev/null)
if echo "$out" | jq -e '.count == 1 and (.workspaces | length == 1) and (.workspaces[0].name == "t1")' >/dev/null 2>&1; then
    assert_pass "register-then-list returns one entry with correct name"
else
    assert_fail "register-then-list did not return expected shape" "$out"
fi
rm -rf "$REG" "$TMP" "$(dirname "$REG")"

# ── Test 2: register same path twice → action: refreshed ───────────────
echo ""
echo "Test 2: register same path twice → action: refreshed"
REG=$(fresh_reg)
TMP=$(mktemp -d)
BSTACK_REGISTRY="$REG" bash "$WORKSPACE_BIN" register --path "$TMP" --name t2 --json >/dev/null 2>&1
out=$(BSTACK_REGISTRY="$REG" bash "$WORKSPACE_BIN" register --path "$TMP" --name t2 --json 2>/dev/null)
count=$(BSTACK_REGISTRY="$REG" bash "$WORKSPACE_BIN" list --json 2>/dev/null | jq -r '.count')
if echo "$out" | jq -e '.action == "refreshed"' >/dev/null 2>&1 && [ "$count" = "1" ]; then
    assert_pass "second register on same path action=refreshed, count stays 1"
else
    assert_fail "duplicate register did not refresh in place" "action=$(echo "$out" | jq -r '.action // "?"'), count=$count"
fi
rm -rf "$REG" "$TMP" "$(dirname "$REG")"

# ── Test 3: register same name at different path → exit 5 ──────────────
echo ""
echo "Test 3: register conflicting name at different path → exit 5"
REG=$(fresh_reg)
TMP1=$(mktemp -d)
TMP2=$(mktemp -d)
BSTACK_REGISTRY="$REG" bash "$WORKSPACE_BIN" register --path "$TMP1" --name conflict --json >/dev/null 2>&1
BSTACK_REGISTRY="$REG" bash "$WORKSPACE_BIN" register --path "$TMP2" --name conflict --json >/dev/null 2>&1
ec=$?
if [ "$ec" = "5" ]; then
    assert_pass "duplicate name on different path returns exit 5"
else
    assert_fail "duplicate name did not return exit 5" "got exit $ec"
fi
rm -rf "$REG" "$TMP1" "$TMP2" "$(dirname "$REG")"

# ── Test 4: info reports registered: true ──────────────────────────────
echo ""
echo "Test 4: info --path PATH reports registered: true after register"
REG=$(fresh_reg)
TMP=$(mktemp -d)
BSTACK_REGISTRY="$REG" bash "$WORKSPACE_BIN" register --path "$TMP" --name t4 --json >/dev/null 2>&1
out=$(BSTACK_REGISTRY="$REG" bash "$WORKSPACE_BIN" info --path "$TMP" --json 2>/dev/null)
if echo "$out" | jq -e '.registered == true and (.entry.name == "t4")' >/dev/null 2>&1; then
    assert_pass "info returns registered: true with matching entry"
else
    assert_fail "info did not report registered after register" "$out"
fi
rm -rf "$REG" "$TMP" "$(dirname "$REG")"

# ── Test 5: deregister --path removes the entry ────────────────────────
echo ""
echo "Test 5: deregister --path PATH removes the entry"
REG=$(fresh_reg)
TMP=$(mktemp -d)
BSTACK_REGISTRY="$REG" bash "$WORKSPACE_BIN" register --path "$TMP" --name t5 --json >/dev/null 2>&1
BSTACK_REGISTRY="$REG" bash "$WORKSPACE_BIN" deregister --path "$TMP" --json >/dev/null 2>&1
count=$(BSTACK_REGISTRY="$REG" bash "$WORKSPACE_BIN" list --json 2>/dev/null | jq -r '.count')
if [ "$count" = "0" ]; then
    assert_pass "deregister by path removes entry (count == 0)"
else
    assert_fail "deregister by path did not remove entry" "count=$count"
fi
rm -rf "$REG" "$TMP" "$(dirname "$REG")"

# ── Test 6: deregister non-existent entry → exit 4 ─────────────────────
echo ""
echo "Test 6: deregister with no matching entry → exit 4"
REG=$(fresh_reg)
BSTACK_REGISTRY="$REG" bash "$WORKSPACE_BIN" deregister --name nosuch --json >/dev/null 2>&1
ec=$?
if [ "$ec" = "4" ]; then
    assert_pass "deregister with no match returns exit 4"
else
    assert_fail "deregister of missing entry did not return exit 4" "got exit $ec"
fi
rm -rf "$REG" "$(dirname "$REG")"

# ── Test 7: schema_version != 1 → list exits 3 ─────────────────────────
echo ""
echo "Test 7: corrupt registry to schema_version: 99 → list exits 3"
REG=$(fresh_reg)
mkdir -p "$(dirname "$REG")"
cat > "$REG" <<EOF
schema_version: 99
workspaces: []
EOF
BSTACK_REGISTRY="$REG" bash "$WORKSPACE_BIN" list --json >/dev/null 2>&1
ec=$?
if [ "$ec" = "3" ]; then
    assert_pass "schema_version != 1 returns exit 3 with parse error"
else
    assert_fail "corrupt registry did not return exit 3" "got exit $ec"
fi
rm -rf "$REG" "$(dirname "$REG")"

# ── Test 8: --tag values accumulate on refresh ─────────────────────────
echo ""
echo "Test 8: register --tag x --tag y persists + accumulates on refresh"
REG=$(fresh_reg)
TMP=$(mktemp -d)
BSTACK_REGISTRY="$REG" bash "$WORKSPACE_BIN" register --path "$TMP" --name t8 --tag alpha --tag beta --json >/dev/null 2>&1
BSTACK_REGISTRY="$REG" bash "$WORKSPACE_BIN" register --path "$TMP" --name t8 --tag gamma --json >/dev/null 2>&1
tags=$(BSTACK_REGISTRY="$REG" bash "$WORKSPACE_BIN" list --json 2>/dev/null | jq -r '.workspaces[0].tags | sort | join(",")')
if [ "$tags" = "alpha,beta,gamma" ]; then
    assert_pass "tags accumulate on refresh (sorted: alpha,beta,gamma)"
else
    assert_fail "tag accumulation broken" "got tags=$tags"
fi
rm -rf "$REG" "$TMP" "$(dirname "$REG")"

# ── Test 9: invalid name → exit 2 ──────────────────────────────────────
echo ""
echo "Test 9: register with invalid name → exit 2"
REG=$(fresh_reg)
TMP=$(mktemp -d)
BSTACK_REGISTRY="$REG" bash "$WORKSPACE_BIN" register --path "$TMP" --name '!bad name' --json >/dev/null 2>&1
ec=$?
if [ "$ec" = "2" ]; then
    assert_pass "invalid name returns exit 2"
else
    assert_fail "invalid name did not return exit 2" "got exit $ec"
fi
rm -rf "$REG" "$TMP" "$(dirname "$REG")"

# ── Test 10: --help renders help text ──────────────────────────────────
echo ""
echo "Test 10: --help renders help block"
out=$(bash "$WORKSPACE_BIN" --help 2>&1)
if echo "$out" | grep -q "Subcommands:" && echo "$out" | grep -q "register"; then
    assert_pass "--help renders subcommands block"
else
    assert_fail "--help did not render subcommands block" "$out"
fi

# ── Summary ─────────────────────────────────────────────────────────────
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
