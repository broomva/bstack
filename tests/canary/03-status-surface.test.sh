#!/usr/bin/env bash
# canary/03 — status surface (Phase 2, v0.5.0) renders.
#
# Asserts `bstack status` produces the documented 8 sections (or close
# to it — some sections are conditional on RCS parameters present),
# and `--json` mode emits valid structured JSON conforming to the
# documented shape.

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

mkdir -p "$TW/.claude" "$TW/.control"
: > "$TW/CLAUDE.md"
: > "$TW/AGENTS.md"
: > "$TW/.control/policy.yaml"
echo '{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"x"}]}],"PreToolUse":[{"hooks":[{"type":"command","command":"y"}]}]}}' > "$TW/.claude/settings.json"

echo "── canary/03 — status surface ─────────────────────────"
echo ""

# Step 1: text mode prints at least core sections.
echo "Step 1: bstack status (text)"
out=$(BSTACK_METRICS_DIR="$MD" BROOMVA_WORKSPACE="$TW" "$REPO/bin/bstack-status" --no-color 2>/dev/null || true)
sections=(Plant Setpoints Gates Primitives "Companion skills" Bridge "Last upgrade")
missing=0
for s in "${sections[@]}"; do
    if ! echo "$out" | grep -qF "$s"; then
        fail "section '$s' missing from text output"
        missing=$((missing + 1))
    fi
done
if [ "$missing" -eq 0 ]; then
    pass "all 7 core sections rendered in text output"
fi

# Step 2: --json shape.
echo ""
echo "Step 2: bstack status --json"
out=$(BSTACK_METRICS_DIR="$MD" BROOMVA_WORKSPACE="$TW" "$REPO/bin/bstack-status" --json 2>/dev/null || true)
if echo "$out" | jq -e '.bstack_version and .workspace and .profile and .generated_at and .setpoints and .summary' >/dev/null 2>&1; then
    pass "JSON has bstack_version + workspace + profile + generated_at + setpoints + summary"
else
    fail "JSON missing one of the documented top-level keys"
fi

# Step 3: --setpoint S11 deep view.
echo ""
echo "Step 3: bstack status --setpoint S11 --json"
out=$(BSTACK_METRICS_DIR="$MD" BROOMVA_WORKSPACE="$TW" "$REPO/bin/bstack-status" --setpoint S11 --json 2>/dev/null || true)
if echo "$out" | jq -e '.id == "S11"' >/dev/null 2>&1; then
    pass "setpoint deep-view returns id-matched JSON"
else
    fail "setpoint deep-view did not return id-matched JSON"
fi

# Step 4: --aggregate ships in v0.18.0 (this PR). Assert success against an
# isolated empty registry — exit 0 with a "no registered workspaces" message
# (or equivalent), never the old Phase-8 placeholder error.
echo ""
echo "Step 4: bstack status --aggregate works against empty registry (Phase 8, v0.18.0)"
EMPTY_REG=$(mktemp -d)/registry.yaml
set +e
out=$(BSTACK_METRICS_DIR="$MD" BROOMVA_WORKSPACE="$TW" BSTACK_REGISTRY="$EMPTY_REG" "$REPO/bin/bstack-status" --aggregate 2>&1)
rc=$?
set -e
if [ "$rc" = "0" ] && ! echo "$out" | grep -qF "Phase 8 placeholder"; then
    pass "--aggregate succeeds against empty registry"
else
    fail "--aggregate did not succeed against empty registry (rc=$rc out=$out)"
fi
rm -rf "$(dirname "$EMPTY_REG")"

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
echo "  canary/03 passed."
