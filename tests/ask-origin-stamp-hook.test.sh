#!/usr/bin/env bash
# tests/ask-origin-stamp-hook.test.sh — BRO-2918 PostToolUse auto-stamp.
#
# scripts/ask-origin-stamp-hook.sh runs `ask_ledger.py stamp` on every
# Write/Edit/MultiEdit of a `.control/asks/*.yaml` file. Because the plugin
# loads at personal scope (every session, every repo), the hook's bash-only
# prefilter must reject everything that is not that exact shape BEFORE
# spawning python3 — these are the cases it must reject, and the one it must
# accept, each checked by whether the file content changed.
#
# Every assertion runs against a scratch workspace; the operator's real
# environment is never touched.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$REPO/scripts/ask-origin-stamp-hook.sh"
PASS=0; FAIL=0; FAILED=()
pass() { echo "  [ok] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); FAILED+=("$1"); }

echo "── ask-origin-stamp-hook (BRO-2918) ────────────────────────────────"

[ -f "$HOOK" ] || { echo "  [FAIL] missing $HOOK"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "  [FAIL] python3 not available"; exit 1; }

TW=$(mktemp -d)
trap 'rm -rf "$TW"' EXIT
mkdir -p "$TW/.control/asks"
(cd "$TW" && git init -q)

ASK_LEDGER_YAML='arc: demo
opened: "2026-10-07T00:00Z"
tz: America/Bogota
lanes: [gate]
asks:
  - id: A1
    ask: "smoke test"
    class: authority
    gates: [gate]
    exhausted: ["read: x", "preauth: y"]
    blocking: false
    default: "leave it"
    preanswerable: true
    answered_at: null
'

reset_ledger() { printf '%s' "$ASK_LEDGER_YAML" > "$TW/.control/asks/demo.yaml"; }

tool_json() {   # tool_json <tool_name> <file_path>
    python3 -c "import json,sys; print(json.dumps({'tool_name': sys.argv[1], 'tool_input': {'file_path': sys.argv[2], 'content': 'x'}}))" "$1" "$2"
}

run_hook() {    # run_hook <json> [extra env assignments already exported by caller]
    printf '%s' "$1" | CLAUDE_PROJECT_DIR="$TW" CLAUDE_PLUGIN_ROOT="$REPO" bash "$HOOK"
    echo $?
}

# ── 1. no PASEO_AGENT_ID: no-op, exit 0, file untouched ─────────────────────
reset_ledger
before=$(md5 -q "$TW/.control/asks/demo.yaml" 2>/dev/null || md5sum "$TW/.control/asks/demo.yaml" | cut -d' ' -f1)
code=$(printf '%s' "$(tool_json Write "$TW/.control/asks/demo.yaml")" \
    | env -u PASEO_AGENT_ID CLAUDE_PROJECT_DIR="$TW" CLAUDE_PLUGIN_ROOT="$REPO" bash "$HOOK"; echo $?)
after=$(md5 -q "$TW/.control/asks/demo.yaml" 2>/dev/null || md5sum "$TW/.control/asks/demo.yaml" | cut -d' ' -f1)
if [ "$code" = "0" ] && [ "$before" = "$after" ]; then
    pass "no PASEO_AGENT_ID -> exit 0, file untouched"
else
    fail "no PASEO_AGENT_ID should no-op" "exit=$code before=$before after=$after"
fi

# ── 2. unrelated file: no-op ─────────────────────────────────────────────────
echo "hello" > "$TW/README.md"
before=$(md5 -q "$TW/README.md" 2>/dev/null || md5sum "$TW/README.md" | cut -d' ' -f1)
code=$(printf '%s' "$(tool_json Write "$TW/README.md")" \
    | PASEO_AGENT_ID=x CLAUDE_PROJECT_DIR="$TW" CLAUDE_PLUGIN_ROOT="$REPO" bash "$HOOK"; echo $?)
after=$(md5 -q "$TW/README.md" 2>/dev/null || md5sum "$TW/README.md" | cut -d' ' -f1)
if [ "$code" = "0" ] && [ "$before" = "$after" ]; then
    pass "unrelated file -> exit 0, untouched"
else
    fail "a file outside .control/asks/ must never be touched"
fi

# ── 3. wrong tool_name (Read): no-op ─────────────────────────────────────────
reset_ledger
before=$(md5 -q "$TW/.control/asks/demo.yaml" 2>/dev/null || md5sum "$TW/.control/asks/demo.yaml" | cut -d' ' -f1)
code=$(printf '%s' "$(tool_json Read "$TW/.control/asks/demo.yaml")" \
    | PASEO_AGENT_ID=x CLAUDE_PROJECT_DIR="$TW" CLAUDE_PLUGIN_ROOT="$REPO" bash "$HOOK"; echo $?)
after=$(md5 -q "$TW/.control/asks/demo.yaml" 2>/dev/null || md5sum "$TW/.control/asks/demo.yaml" | cut -d' ' -f1)
if [ "$code" = "0" ] && [ "$before" = "$after" ]; then
    pass "tool_name=Read is never a write -> ignored"
else
    fail "only Write/Edit/MultiEdit may trigger a stamp"
fi

# ── 4. nested subdirectory under asks/: no-op (shape must be exactly one segment) ─
mkdir -p "$TW/.control/asks/sub"
reset_ledger
cp "$TW/.control/asks/demo.yaml" "$TW/.control/asks/sub/nested.yaml"
before=$(md5 -q "$TW/.control/asks/sub/nested.yaml" 2>/dev/null || md5sum "$TW/.control/asks/sub/nested.yaml" | cut -d' ' -f1)
code=$(printf '%s' "$(tool_json Write "$TW/.control/asks/sub/nested.yaml")" \
    | PASEO_AGENT_ID=x CLAUDE_PROJECT_DIR="$TW" CLAUDE_PLUGIN_ROOT="$REPO" bash "$HOOK"; echo $?)
after=$(md5 -q "$TW/.control/asks/sub/nested.yaml" 2>/dev/null || md5sum "$TW/.control/asks/sub/nested.yaml" | cut -d' ' -f1)
if [ "$code" = "0" ] && [ "$before" = "$after" ]; then
    pass "a nested path under asks/ is not the shipped ledger shape -> ignored"
else
    fail "only a flat .control/asks/<arc>.yaml may be stamped"
fi

# ── 5. the one case that must fire: Write of .control/asks/<arc>.yaml with an agent id ──
reset_ledger
code=$(printf '%s' "$(tool_json Write "$TW/.control/asks/demo.yaml")" \
    | PASEO_AGENT_ID=hook-test-agent CLAUDE_PROJECT_DIR="$TW" CLAUDE_PLUGIN_ROOT="$REPO" bash "$HOOK"; echo $?)
if [ "$code" = "0" ] && grep -q "agent_id: hook-test-agent" "$TW/.control/asks/demo.yaml"; then
    pass "Write of .control/asks/<arc>.yaml with PASEO_AGENT_ID set -> stamped"
else
    fail "the hook must stamp the one shape it exists for" "exit=$code"
fi

# ── 6. idempotent: a second run (Edit, different agent) must not overwrite ──
code=$(printf '%s' "$(tool_json Edit "$TW/.control/asks/demo.yaml")" \
    | PASEO_AGENT_ID=second-agent CLAUDE_PROJECT_DIR="$TW" CLAUDE_PLUGIN_ROOT="$REPO" bash "$HOOK"; echo $?)
if [ "$code" = "0" ] && grep -q "agent_id: hook-test-agent" "$TW/.control/asks/demo.yaml" \
    && ! grep -q "agent_id: second-agent" "$TW/.control/asks/demo.yaml"; then
    pass "a second Edit never overwrites the first session's stamp"
else
    fail "the hook never passes --force; re-stamping must be a human's own call"
fi
# exactly one origin: key — never two, from either the hook or a race of edits.
count=$(grep -c "^    origin:" "$TW/.control/asks/demo.yaml")
if [ "$count" = "1" ]; then
    pass "exactly one origin: block after two hook firings"
else
    fail "origin: block count should be 1" "got $count"
fi

echo ""
echo "── ask-origin-stamp-hook: $PASS passed, $FAIL failed ──"
if [ "$FAIL" -gt 0 ]; then
    printf '  - %s\n' "${FAILED[@]}"
    exit 1
fi
exit 0
