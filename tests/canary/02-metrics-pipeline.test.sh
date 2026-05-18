#!/usr/bin/env bash
# canary/02 — metrics pipeline (Phase 1, v0.4.0) end-to-end.
#
# Asserts that on a fresh workspace, `bstack metrics collect` produces a
# valid latest.json with the expected setpoint coverage, and that
# `bstack metrics observe <S-id>` returns matching JSON for at least one
# substrate-measurable setpoint.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

PASS=0
FAIL=0
FAILED=()

pass() { echo "  [ok] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); FAILED+=("$1"); }

MD=$(mktemp -d)
TW=$(mktemp -d)
trap 'rm -rf "$MD" "$TW"' EXIT

# Stage a minimal workspace so metrics can run (measure-S11.sh checks for
# governance files; S12 checks .claude/settings.json; etc.).
mkdir -p "$TW/.claude" "$TW/.control" "$TW/docs/conversations"
: > "$TW/CLAUDE.md"
: > "$TW/AGENTS.md"
: > "$TW/METALAYER.md"
: > "$TW/.control/policy.yaml"
cat > "$TW/.claude/settings.json" <<'EOF'
{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"x"}]}],"PreToolUse":[{"hooks":[{"type":"command","command":"y"}]}]}}
EOF
# Add a faux bridge stamp so S13 returns a number rather than null.
mkdir -p "$HOME/.cache" 2>/dev/null || true
faux_stamp=$(mktemp)
touch "$faux_stamp"

echo "── canary/02 — metrics pipeline ───────────────────────"
echo ""
echo "Step 1: bstack metrics collect --no-cache"
out=$(BSTACK_METRICS_DIR="$MD" BROOMVA_WORKSPACE="$TW" "$REPO/bin/bstack-metrics" collect --json --no-cache 2>/dev/null || true)
if [ -z "$out" ]; then
    fail "collect produced no output"
elif ! echo "$out" | jq -e '.setpoints' >/dev/null 2>&1; then
    fail "collect output is not valid JSON with .setpoints"
else
    count=$(echo "$out" | jq -r '.setpoints | keys | length')
    pass "collect returned $count setpoints"
    if [ "$count" -lt 4 ]; then
        fail "expected at least 4 setpoints, got $count"
    fi
fi

# Step 2: latest.json file present + valid.
echo ""
echo "Step 2: latest.json on disk"
if [ -f "$MD/latest.json" ] && jq -e '.generated_at' "$MD/latest.json" >/dev/null 2>&1; then
    pass "$MD/latest.json present + has generated_at timestamp"
else
    fail "latest.json absent or invalid"
fi

# Step 3: observe single setpoint.
echo ""
echo "Step 3: bstack metrics observe S11"
out=$(BSTACK_METRICS_DIR="$MD" BROOMVA_WORKSPACE="$TW" "$REPO/bin/bstack-metrics" observe S11 2>/dev/null || true)
if echo "$out" | jq -e '.id == "S11"' >/dev/null 2>&1; then
    val=$(echo "$out" | jq -r '.value')
    pass "observe S11 → id matches + value=$val"
else
    fail "observe S11 did not return id-matched JSON"
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
echo "  canary/02 passed."
