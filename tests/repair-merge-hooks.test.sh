#!/usr/bin/env bash
# tests/repair-merge-hooks.test.sh — fixture-based tests for the hook-merge
# path in scripts/repair.sh.
#
# Verifies:
#   1. Missing template hook is added to user settings.json.
#   2. Existing user hooks are preserved (not overwritten or duplicated).
#   3. Re-running on an already-synced settings.json is a no-op (idempotent).
#   4. Scaffolds settings.json from snippet when target is absent.
#   5. --dry-run prints what would be added but writes nothing.
#
# Run from repo root: bash tests/repair-merge-hooks.test.sh
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPAIR_SH="$BSTACK_REPO/scripts/repair.sh"

PASS=0
FAIL=0
FAILED_TESTS=()

fresh_workspace() {
    # Returns a tmp workspace with minimal CLAUDE.md / AGENTS.md / policy.yaml
    # stubs (so doctor doesn't bail before the hook merge runs).
    local ws
    ws=$(mktemp -d)
    mkdir -p "$ws/.claude" "$ws/.control"
    # Bare-bones governance fixtures — repair only cares about hook merge here.
    : > "$ws/CLAUDE.md"
    : > "$ws/AGENTS.md"
    : > "$ws/.control/policy.yaml"
    echo "$ws"
}

assert_pass() {
    PASS=$((PASS + 1))
    echo "  ✓ $1"
}

assert_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS+=("$1")
    echo "  ✗ $1"
    [ -n "${2:-}" ] && echo "    ${2}"
}

# Probe: does the user's settings.json contain a hook command ending with $1?
has_hook() {
    local target="$1"
    local script_name="$2"
    python3 - "$target" "$script_name" <<'PY'
import json
import sys
from pathlib import Path

settings, script_name = sys.argv[1], sys.argv[2]
data = json.loads(Path(settings).read_text())
for event, blocks in data.get("hooks", {}).items():
    for block in blocks:
        for h in block.get("hooks", []):
            if Path(h.get("command", "")).name == script_name:
                sys.exit(0)
sys.exit(1)
PY
}

# Count: how many hook entries in the settings file?
count_hooks() {
    local target="$1"
    python3 - "$target" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text())
total = 0
for event, blocks in data.get("hooks", {}).items():
    for block in blocks:
        total += len(block.get("hooks", []))
print(total)
PY
}

# ── Test 1: missing template hook is added ────────────────────────────────
echo ""
echo "Test 1: missing template hook is added to user settings.json"
WS=$(fresh_workspace)
# Pre-seed settings.json with ONLY a UserPromptSubmit hook (missing the
# SessionStart entries the snippet declares).
cat > "$WS/.claude/settings.json" <<'JSON'
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          { "type": "command", "command": "/some/user/custom.sh" }
        ]
      }
    ]
  }
}
JSON
BEFORE=$(count_hooks "$WS/.claude/settings.json")
BROOMVA_WORKSPACE="$WS" bash "$REPAIR_SH" --apply-all >/dev/null 2>&1 || true
AFTER=$(count_hooks "$WS/.claude/settings.json")

if [ "$AFTER" -gt "$BEFORE" ] && has_hook "$WS/.claude/settings.json" "skill-freshness-hook.sh"; then
    assert_pass "P7 skill-freshness hook added (before=$BEFORE, after=$AFTER)"
else
    assert_fail "P7 hook NOT added" "before=$BEFORE, after=$AFTER"
fi
rm -rf "$WS"

# ── Test 2: existing user hook preserved ─────────────────────────────────
echo ""
echo "Test 2: pre-existing user customization is preserved"
WS=$(fresh_workspace)
cat > "$WS/.claude/settings.json" <<'JSON'
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          { "type": "command", "command": "/Users/preserve/me.sh" }
        ]
      }
    ]
  }
}
JSON
BROOMVA_WORKSPACE="$WS" bash "$REPAIR_SH" --apply-all >/dev/null 2>&1 || true
if has_hook "$WS/.claude/settings.json" "me.sh"; then
    assert_pass "user's /Users/preserve/me.sh entry retained"
else
    assert_fail "user hook was lost during merge"
fi
rm -rf "$WS"

# ── Test 3: re-running is idempotent ─────────────────────────────────────
echo ""
echo "Test 3: re-running on a synced settings.json is a no-op"
WS=$(fresh_workspace)
cat > "$WS/.claude/settings.json" <<'JSON'
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command", "command": "/x.sh" }] }
    ]
  }
}
JSON
BROOMVA_WORKSPACE="$WS" bash "$REPAIR_SH" --apply-all >/dev/null 2>&1 || true
SYNCED_COUNT=$(count_hooks "$WS/.claude/settings.json")
SYNCED_HASH=$(shasum "$WS/.claude/settings.json" | awk '{print $1}')
BROOMVA_WORKSPACE="$WS" bash "$REPAIR_SH" --apply-all >/dev/null 2>&1 || true
REPEAT_COUNT=$(count_hooks "$WS/.claude/settings.json")
REPEAT_HASH=$(shasum "$WS/.claude/settings.json" | awk '{print $1}')

if [ "$SYNCED_COUNT" = "$REPEAT_COUNT" ] && [ "$SYNCED_HASH" = "$REPEAT_HASH" ]; then
    assert_pass "second run produced no diff ($SYNCED_COUNT hooks, hash matches)"
else
    assert_fail "second run mutated file" "count $SYNCED_COUNT → $REPEAT_COUNT, hash $SYNCED_HASH → $REPEAT_HASH"
fi
rm -rf "$WS"

# ── Test 4: missing settings.json is scaffolded from snippet ────────────
echo ""
echo "Test 4: missing settings.json is scaffolded from snippet"
WS=$(fresh_workspace)
rm -f "$WS/.claude/settings.json"
BROOMVA_WORKSPACE="$WS" bash "$REPAIR_SH" --apply-all >/dev/null 2>&1 || true
if [ -f "$WS/.claude/settings.json" ] && has_hook "$WS/.claude/settings.json" "skill-freshness-hook.sh"; then
    assert_pass ".claude/settings.json scaffolded with P7 wired"
else
    assert_fail "settings.json scaffolding failed"
fi
rm -rf "$WS"

# ── Test 5: --dry-run reports without writing ────────────────────────────
echo ""
echo "Test 5: --dry-run does not write"
WS=$(fresh_workspace)
cat > "$WS/.claude/settings.json" <<'JSON'
{ "hooks": {} }
JSON
BEFORE_HASH=$(shasum "$WS/.claude/settings.json" | awk '{print $1}')
OUTPUT=$(BROOMVA_WORKSPACE="$WS" bash "$REPAIR_SH" --dry-run 2>&1 || true)
AFTER_HASH=$(shasum "$WS/.claude/settings.json" | awk '{print $1}')

if [ "$BEFORE_HASH" = "$AFTER_HASH" ] && echo "$OUTPUT" | grep -q '\[dry-run\] would add'; then
    assert_pass "dry-run reported intended additions and left file untouched"
else
    assert_fail "dry-run wrote to file OR did not announce additions" \
        "before=$BEFORE_HASH after=$AFTER_HASH"
fi
rm -rf "$WS"

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
