#!/usr/bin/env bash
# tests/schema-validation.test.sh — Phase 3 (v0.6.0) schema validation smoke.
#
# Validates:
#   1. All schemas/*.json are valid JSON Schema draft-07
#   2. references/primitives.yaml validates against primitives.v1.json
#   3. assets/templates/policy.yaml.template validates against policy.v1.json
#      (with setpoint + gate $refs resolved)
#   4. Invalid setpoint shape is rejected
#   5. Invalid gate shape is rejected
#   6. Invalid primitive shape is rejected
#   7. scripts/migrate.sh detects v1 policy + reports no-op
#   8. scripts/migrate.sh --dry-run produces a "would do" message without changes
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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

# ── Test 1: all schemas are valid draft-07 ──────────────────────────────
echo ""
echo "Test 1: all schemas valid draft-07"
all_ok=1
for s in "$BSTACK_REPO"/schemas/*.json; do
    if ! python3 -c "
import json, jsonschema
jsonschema.Draft7Validator.check_schema(json.load(open('$s')))
" 2>/dev/null; then
        assert_fail "schema $(basename "$s") invalid"
        all_ok=0
    fi
done
[ "$all_ok" = "1" ] && assert_pass "all $(ls "$BSTACK_REPO"/schemas/*.json | wc -l | tr -d ' ') schemas valid draft-07"

# ── Test 2: primitives.yaml validates against primitives.v1.json ────────
echo ""
echo "Test 2: references/primitives.yaml validates against schema"
result=$(python3 -c "
import json, yaml, jsonschema
schema = json.load(open('$BSTACK_REPO/schemas/primitives.v1.json'))
data = yaml.safe_load(open('$BSTACK_REPO/references/primitives.yaml'))
v = jsonschema.Draft7Validator(schema)
errors = list(v.iter_errors(data))
if errors:
    print('FAIL ' + ' | '.join(e.message for e in errors[:3]))
else:
    n = len(data['primitives'])
    print(f'OK {n}')
" 2>&1)
if echo "$result" | grep -q "^OK "; then
    n=$(echo "$result" | awk '{print $2}')
    assert_pass "primitives.yaml validates ($n primitives)"
else
    assert_fail "primitives.yaml validation failed" "$result"
fi

# ── Test 3: policy.yaml.template validates against policy.v1.json ───────
echo ""
echo "Test 3: assets/templates/policy.yaml.template validates against policy.v1.json"
result=$(python3 -c "
import json, yaml, jsonschema
from pathlib import Path
sd = Path('$BSTACK_REPO/schemas')

# Build a resolver so \$refs to sibling schemas work.
store = {}
for f in sd.glob('*.json'):
    s = json.load(open(f))
    sid = s.get('\$id') or f.name
    store[sid] = s
    store[f.name] = s

policy_schema = json.load(open(sd / 'policy.v1.json'))
resolver = jsonschema.RefResolver(base_uri='', referrer=policy_schema, store=store)
v = jsonschema.Draft7Validator(policy_schema, resolver=resolver)

# Load the template — strip Jinja-style placeholders if present.
text = open('$BSTACK_REPO/assets/templates/policy.yaml.template').read()
data = yaml.safe_load(text)
errors = list(v.iter_errors(data))
if errors:
    print('FAIL ' + ' | '.join(f'{e.message} @ {list(e.absolute_path)}' for e in errors[:3]))
else:
    print('OK')
" 2>&1)
if echo "$result" | grep -q "^OK"; then
    assert_pass "policy.yaml.template validates against policy.v1.json"
else
    assert_fail "policy.yaml.template validation failed" "$result"
fi

# ── Test 4: invalid setpoint shape is rejected ──────────────────────────
echo ""
echo "Test 4: invalid setpoint shape is rejected"
result=$(python3 -c "
import json, jsonschema
schema = json.load(open('$BSTACK_REPO/schemas/setpoint.v1.json'))
bad = {'id': 'BAD', 'name': 'broken', 'severity': 'blocking'}  # id pattern violation
v = jsonschema.Draft7Validator(schema)
errors = list(v.iter_errors(bad))
print('REJECTED' if errors else 'ACCEPTED')
" 2>&1)
if [ "$result" = "REJECTED" ]; then
    assert_pass "setpoint with bad id pattern rejected"
else
    assert_fail "invalid setpoint not rejected" "$result"
fi

# ── Test 5: invalid gate shape is rejected ──────────────────────────────
echo ""
echo "Test 5: invalid gate shape is rejected"
result=$(python3 -c "
import json, jsonschema
schema = json.load(open('$BSTACK_REPO/schemas/gate.v1.json'))
bad = {'rule': 'no id provided'}  # missing required: id
v = jsonschema.Draft7Validator(schema)
errors = list(v.iter_errors(bad))
print('REJECTED' if errors else 'ACCEPTED')
" 2>&1)
if [ "$result" = "REJECTED" ]; then
    assert_pass "gate missing required id rejected"
else
    assert_fail "invalid gate not rejected" "$result"
fi

# ── Test 6: invalid primitive shape is rejected ─────────────────────────
echo ""
echo "Test 6: invalid primitive shape is rejected"
result=$(python3 -c "
import json, jsonschema
schema = json.load(open('$BSTACK_REPO/schemas/primitives.v1.json'))
bad = {'schema_version': 1, 'primitives': [{'id': 'P99', 'short_name': 'lower-case-wrong'}]}  # missing invariant + failure_mode, bad short_name
v = jsonschema.Draft7Validator(schema)
errors = list(v.iter_errors(bad))
print('REJECTED' if errors else 'ACCEPTED')
" 2>&1)
if [ "$result" = "REJECTED" ]; then
    assert_pass "primitive missing required fields rejected"
else
    assert_fail "invalid primitive not rejected" "$result"
fi

# ── Test 7: migrate.sh detects v1 → reports no-op ───────────────────────
echo ""
echo "Test 7: migrate.sh v1 → v1 is identity"
WS=$(mktemp -d)
mkdir -p "$WS/.control"
cat > "$WS/.control/policy.yaml" <<EOF
version: "1.0"
profile: governed
EOF
out=$(BROOMVA_WORKSPACE="$WS" bash "$BSTACK_REPO/scripts/migrate.sh" --apply-all 2>&1)
rc=$?
if [ "$rc" = "0" ] && echo "$out" | grep -qF "v1 → v1 identity"; then
    assert_pass "migrate detected v1, applied identity, exit 0"
else
    assert_fail "migrate did not produce expected v1→v1 output" "rc=$rc out=$out"
fi
rm -rf "$WS"

# ── Test 8: migrate.sh --dry-run does not modify policy.yaml ────────────
echo ""
echo "Test 8: migrate --dry-run is non-mutating"
WS=$(mktemp -d)
mkdir -p "$WS/.control"
cat > "$WS/.control/policy.yaml" <<EOF
version: "1.0"
profile: governed
EOF
hash_before=$(shasum "$WS/.control/policy.yaml" | awk '{print $1}')
out=$(BROOMVA_WORKSPACE="$WS" bash "$BSTACK_REPO/scripts/migrate.sh" --dry-run 2>&1)
hash_after=$(shasum "$WS/.control/policy.yaml" | awk '{print $1}')
if [ "$hash_before" = "$hash_after" ] && echo "$out" | grep -qF "dry-run"; then
    assert_pass "migrate --dry-run left file untouched, reported dry-run"
else
    assert_fail "migrate --dry-run modified file or missed report" "before=$hash_before after=$hash_after"
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
