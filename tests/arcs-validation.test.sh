#!/usr/bin/env bash
# tests/arcs-validation.test.sh — Closure-contract arcs validation smoke
# (v0.19.0).
#
# Validates:
#   1. The bundled arcs.yaml.template loads via compute-arc-status.sh without
#      error
#   2. Both example arcs in the template have all 5 required components
#      (id, plant_surfaces, sensor, actuator, termination, tau_a)
#   3. Schema validation rejects schema_version: 99 (out-of-range)
#   4. Schema rejects an arc missing required fields
#   5. The schemas/arcs.v1.json parses as draft-07 JSON-schema
#   6. Workspace .control/arcs.yaml overrides the bundled template

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPUTE_ARC_STATUS="$BSTACK_REPO/scripts/compute-arc-status.sh"
SCHEMA="$BSTACK_REPO/schemas/arcs.v1.json"
TEMPLATE="$BSTACK_REPO/assets/templates/arcs.yaml.template"

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

fresh_ws() {
    local ws
    ws=$(mktemp -d)
    mkdir -p "$ws/.control/audit"
    echo "$ws"
}

# ── Test 1: bundled template loads via compute-arc-status ───────────────
echo ""
echo "Test 1: bundled arcs.yaml.template loads + reports both arcs"
WS=$(fresh_ws)
out=$(BROOMVA_WORKSPACE="$WS" bash "$COMPUTE_ARC_STATUS" --json 2>/dev/null || true)
if echo "$out" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d.get('arc_count') == 2, f'expected 2 arcs, got {d.get(\"arc_count\")}'
ids = [a['id'] for a in d.get('arcs', [])]
assert 'code-pr-greenflow' in ids and 'bookkeeping-promotion-quality' in ids, f'missing example ids; got {ids}'
" 2>/dev/null; then
    assert_pass "template loads + both example arcs surfaced"
else
    assert_fail "template did not yield 2 arcs" "$(echo "$out" | head -20)"
fi
rm -rf "$WS"

# ── Test 2: both example arcs have all 5 components ────────────────────
echo ""
echo "Test 2: both example arcs declare all 5 required components"
WS=$(fresh_ws)
out=$(BROOMVA_WORKSPACE="$WS" bash "$COMPUTE_ARC_STATUS" --json 2>/dev/null || true)
if echo "$out" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for a in d.get('arcs', []):
    for k in ('id', 'sensor_kind', 'actuator_kind', 'termination_kind', 'tau_a_seconds'):
        assert a.get(k), f'arc {a.get(\"id\")} missing {k}'
" 2>/dev/null; then
    assert_pass "every example arc has all 5 (id, sensor, actuator, termination, tau_a)"
else
    assert_fail "some example arc is missing a required component" "$(echo "$out" | head -20)"
fi
rm -rf "$WS"

# ── Test 3: schema_version 99 is rejected ──────────────────────────────
echo ""
echo "Test 3: schema_version: 99 yields exit 2"
WS=$(fresh_ws)
cat > "$WS/.control/arcs.yaml" <<'EOF'
schema_version: 99
arcs:
  - id: bogus
    plant_surfaces:
      - fs://nowhere
    sensor:
      kind: exit_code
      source: /bin/true
    actuator:
      kind: agent_reasoning
    termination:
      kind: exit_zero
      expr: ignored
    tau_a: 60
EOF
BROOMVA_WORKSPACE="$WS" bash "$COMPUTE_ARC_STATUS" --json >/dev/null 2>&1
rc=$?
if [ "$rc" = "2" ]; then
    assert_pass "schema_version: 99 produced exit 2"
else
    assert_fail "schema_version: 99 produced exit $rc (expected 2)"
fi
rm -rf "$WS"

# ── Test 4: arc missing required field still loads but is incomplete ──
# (The script is permissive — missing fields don't crash; the arc surfaces
# with verdict=unknown and the doctor.sh §20 completeness check catches
# the gap. This test confirms that permissive path.)
echo ""
echo "Test 4: arc missing 'sensor' is surfaced but does not crash"
WS=$(fresh_ws)
cat > "$WS/.control/arcs.yaml" <<'EOF'
schema_version: 1
arcs:
  - id: incomplete
    plant_surfaces:
      - fs://anywhere
    actuator:
      kind: agent_reasoning
    termination:
      kind: exit_zero
      expr: ignored
    tau_a: 60
EOF
out=$(BROOMVA_WORKSPACE="$WS" bash "$COMPUTE_ARC_STATUS" --json 2>/dev/null || true)
if echo "$out" | python3 -c "
import sys, json
d = json.load(sys.stdin)
arcs = d.get('arcs', [])
assert len(arcs) == 1, f'expected 1 arc, got {len(arcs)}'
assert arcs[0]['id'] == 'incomplete', f'wrong id'
assert arcs[0]['verdict'] in ('unknown', 'yellow', 'red'), f'expected non-green for incomplete arc'
" 2>/dev/null; then
    assert_pass "incomplete arc surfaces with non-green verdict (no crash)"
else
    assert_fail "incomplete arc behavior unexpected" "$(echo "$out" | head -20)"
fi
rm -rf "$WS"

# ── Test 5: schemas/arcs.v1.json is valid JSON ─────────────────────────
echo ""
echo "Test 5: schemas/arcs.v1.json parses as JSON"
if python3 -c "
import json, sys
d = json.load(open('$SCHEMA'))
assert d.get('\$schema', '').endswith('draft-07/schema#'), 'not draft-07'
assert d.get('required') == ['schema_version', 'arcs'], 'wrong required'
" 2>/dev/null; then
    assert_pass "schemas/arcs.v1.json is well-formed draft-07"
else
    assert_fail "schemas/arcs.v1.json malformed"
fi

# ── Test 6: workspace .control/arcs.yaml overrides bundled template ────
echo ""
echo "Test 6: workspace .control/arcs.yaml overrides bundled template"
WS=$(fresh_ws)
cat > "$WS/.control/arcs.yaml" <<'EOF'
schema_version: 1
arcs:
  - id: workspace-override-only
    plant_surfaces:
      - fs://test
    sensor:
      kind: exit_code
      source: /bin/true
    actuator:
      kind: agent_reasoning
    termination:
      kind: exit_zero
      expr: ignored
    tau_a: 60
EOF
out=$(BROOMVA_WORKSPACE="$WS" bash "$COMPUTE_ARC_STATUS" --json 2>/dev/null || true)
if echo "$out" | python3 -c "
import sys, json
d = json.load(sys.stdin)
ids = [a['id'] for a in d.get('arcs', [])]
assert ids == ['workspace-override-only'], f'workspace did not override; got ids={ids}'
" 2>/dev/null; then
    assert_pass "workspace .control/arcs.yaml took precedence over template"
else
    assert_fail "workspace override did not take effect" "$(echo "$out" | head -20)"
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
