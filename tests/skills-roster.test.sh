#!/usr/bin/env bash
# tests/skills-roster.test.sh — Phase 4 (v0.7.0) companion-skills smoke.
#
# Validates:
#   1. references/companion-skills.yaml validates against schemas/companion-skills.v1.json
#   2. bstack skills list returns expected count
#   3. bstack skills list --required-only filters correctly
#   4. bstack skills list --json produces valid JSON with expected shape
#   5. bstack skills status produces structured output with text mode
#   6. bstack skills status --json shape includes installed/missing counts
#   7. bstack skills install --dry-run does not invoke the npx mock
#   8. bstack skills install with mock npx records each install attempt
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BSTACK_SKILLS="$BSTACK_REPO/bin/bstack-skills"

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

# Build a tiny fixture roster YAML for tests that need control over the entry count.
fixture_roster() {
    local f
    f=$(mktemp -t bstack-skills-roster.XXXXXX.yaml)
    cat > "$f" <<'EOF'
schema_version: 1
skills:
  - name: foo-skill
    repo: testorg/foo-skill
    category: meta
    required: true
    introduced_in: 0.1.0
  - name: bar-skill
    repo: testorg/bar-skill
    category: lifecycle
    required: false
    introduced_in: 0.1.0
  - name: baz-skill
    repo: testorg/baz-skill
    category: design
    required: true
    introduced_in: 0.1.0
EOF
    echo "$f"
}

# Mock npx that records each invocation to a log instead of installing.
mock_npx_log_file() {
    mktemp -t bstack-skills-npx.XXXXXX.log
}
make_mock_npx() {
    local log_file="$1"
    local mock
    mock=$(mktemp -t bstack-skills-mock.XXXXXX.sh)
    cat > "$mock" <<EOF
#!/bin/sh
echo "\$@" >> "$log_file"
exit 0
EOF
    chmod +x "$mock"
    echo "$mock"
}

# ── Test 1: YAML validates against schema ──────────────────────────────
echo ""
echo "Test 1: companion-skills.yaml validates against companion-skills.v1.json"
result=$(python3 -c "
import json, yaml, jsonschema
schema = json.load(open('$BSTACK_REPO/schemas/companion-skills.v1.json'))
data = yaml.safe_load(open('$BSTACK_REPO/references/companion-skills.yaml'))
v = jsonschema.Draft7Validator(schema)
errors = list(v.iter_errors(data))
if errors:
    print('FAIL ' + ' | '.join(e.message for e in errors[:3]))
else:
    print(f'OK {len(data[\"skills\"])}')" 2>&1)
if echo "$result" | grep -q "^OK "; then
    n=$(echo "$result" | awk '{print $2}')
    assert_pass "validates ($n skills)"
else
    assert_fail "schema validation failed" "$result"
fi

# ── Test 2: list returns expected count ────────────────────────────────
echo ""
echo "Test 2: bstack skills list returns the same count as the YAML"
ROSTER=$(fixture_roster)
count_actual=$(BSTACK_SKILLS_YAML="$ROSTER" "$BSTACK_SKILLS" list 2>&1 | grep -E "skill\(s\)|skills declared" | head -1 | awk '{print $1}')
if [ "$count_actual" = "3" ]; then
    assert_pass "list returned 3 skills (matches fixture)"
else
    assert_fail "expected 3, got '$count_actual'"
fi
rm -f "$ROSTER"

# ── Test 3: list --required-only filters ────────────────────────────────
echo ""
echo "Test 3: --required-only returns only required:true entries"
ROSTER=$(fixture_roster)
count_required=$(BSTACK_SKILLS_YAML="$ROSTER" "$BSTACK_SKILLS" list --required-only 2>&1 | grep -E "skills declared" | awk '{print $1}')
if [ "$count_required" = "2" ]; then
    assert_pass "--required-only returned 2 of 3 (foo + baz, not bar)"
else
    assert_fail "expected 2, got '$count_required'"
fi
rm -f "$ROSTER"

# ── Test 4: list --json shape ──────────────────────────────────────────
echo ""
echo "Test 4: list --json produces valid structured JSON"
ROSTER=$(fixture_roster)
out=$(BSTACK_SKILLS_YAML="$ROSTER" "$BSTACK_SKILLS" list --json 2>&1)
if echo "$out" | jq -e '.count == 3 and (.skills | length == 3) and (.skills[0].name | type == "string")' >/dev/null 2>&1; then
    assert_pass "list --json shape valid"
else
    assert_fail "list --json shape invalid" "$out"
fi
rm -f "$ROSTER"

# ── Test 5: status text output ─────────────────────────────────────────
echo ""
echo "Test 5: status text output includes headers + counts"
ROSTER=$(fixture_roster)
out=$(BSTACK_SKILLS_YAML="$ROSTER" "$BSTACK_SKILLS" status 2>&1)
if echo "$out" | grep -qE "Skill" && echo "$out" | grep -qE "installed$|missing"; then
    assert_pass "status produced headers + per-skill state"
else
    assert_fail "status missing expected lines" "$out"
fi
rm -f "$ROSTER"

# ── Test 6: status --json shape ────────────────────────────────────────
echo ""
echo "Test 6: status --json shape includes installed/missing/total"
ROSTER=$(fixture_roster)
out=$(BSTACK_SKILLS_YAML="$ROSTER" "$BSTACK_SKILLS" status --json 2>&1)
if echo "$out" | jq -e '.total == 3 and .installed >= 0 and .missing >= 0 and (.skills | length == 3)' >/dev/null 2>&1; then
    assert_pass "status --json shape valid"
else
    assert_fail "status --json shape invalid" "$out"
fi
rm -f "$ROSTER"

# ── Test 7: install --dry-run does not invoke npx mock ─────────────────
echo ""
echo "Test 7: install --dry-run does not call npx"
ROSTER=$(fixture_roster)
LOG=$(mock_npx_log_file)
MOCK=$(make_mock_npx "$LOG")
BSTACK_SKILLS_YAML="$ROSTER" BSTACK_NPX_CMD="$MOCK" "$BSTACK_SKILLS" install --dry-run >/dev/null 2>&1
if [ ! -s "$LOG" ]; then
    assert_pass "dry-run did not invoke npx (log empty)"
else
    assert_fail "dry-run invoked npx" "$(wc -l < "$LOG") calls"
fi
rm -f "$ROSTER" "$LOG" "$MOCK"

# ── Test 8: install with mock npx records each attempt ─────────────────
echo ""
echo "Test 8: install actually invokes npx for missing skills"
ROSTER=$(fixture_roster)
LOG=$(mock_npx_log_file)
MOCK=$(make_mock_npx "$LOG")
# Point search paths at a tmpdir so all skills are "missing".
TMP_HOME=$(mktemp -d)
HOME="$TMP_HOME" BSTACK_SKILLS_YAML="$ROSTER" BSTACK_NPX_CMD="$MOCK" "$BSTACK_SKILLS" install >/dev/null 2>&1
calls=$(wc -l < "$LOG" | tr -d ' ')
if [ "$calls" = "3" ]; then
    assert_pass "install invoked npx 3 times (one per fixture skill)"
else
    assert_fail "expected 3 npx calls, got $calls" "$(cat "$LOG")"
fi
rm -rf "$ROSTER" "$LOG" "$MOCK" "$TMP_HOME"

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
