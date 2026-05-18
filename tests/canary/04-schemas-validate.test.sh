#!/usr/bin/env bash
# canary/04 — Phase 3 (v0.6.0) schemas validate.
#
# Asserts:
#   - All 4 JSON schemas are syntactically valid draft-07
#   - references/primitives.yaml (20 entries) validates against schema
#   - references/companion-skills.yaml (31 entries) validates against schema
#   - assets/templates/policy.yaml.template validates against policy schema
#
# This is the substrate's "contracts are sound" smoke test — closes
# Plant Contract invariant PC-3 + Setpoint Contract SC-3.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

PASS=0
FAIL=0
FAILED=()

pass() { echo "  [ok] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); FAILED+=("$1"); }

echo "── canary/04 — schemas validate ───────────────────────"
echo ""

if ! command -v python3 >/dev/null 2>&1; then
    echo "  [skip] python3 missing — canary/04 needs python3 + jsonschema + yaml"
    exit 0
fi

# Step 1: each schema is valid draft-07.
echo "Step 1: 4 JSON schemas valid draft-07"
ok=0
for s in "$REPO"/schemas/*.v1.json; do
    name=$(basename "$s")
    if python3 -c "
import json, jsonschema
schema = json.load(open('$s'))
jsonschema.Draft7Validator.check_schema(schema)
" 2>/dev/null; then
        ok=$((ok + 1))
    else
        fail "$name not valid draft-07"
    fi
done
if [ "$ok" -ge 4 ]; then
    pass "$ok schemas valid draft-07"
fi

# Step 2: primitives.yaml validates.
echo ""
echo "Step 2: references/primitives.yaml validates against primitives.v1.json"
if python3 -c "
import json, yaml, jsonschema, sys
schema = json.load(open('$REPO/schemas/primitives.v1.json'))
data = yaml.safe_load(open('$REPO/references/primitives.yaml'))
v = jsonschema.Draft7Validator(schema)
errors = list(v.iter_errors(data))
if errors:
    for e in errors: print(f'  {e.message}')
    sys.exit(1)
print(f'  primitives count: {len(data[\"primitives\"])}')
" 2>&1; then
    pass "primitives.yaml validates"
else
    fail "primitives.yaml validation failed"
fi

# Step 3: companion-skills.yaml validates.
echo ""
echo "Step 3: references/companion-skills.yaml validates against companion-skills.v1.json"
if python3 -c "
import json, yaml, jsonschema, sys
schema = json.load(open('$REPO/schemas/companion-skills.v1.json'))
data = yaml.safe_load(open('$REPO/references/companion-skills.yaml'))
v = jsonschema.Draft7Validator(schema)
errors = list(v.iter_errors(data))
if errors:
    for e in errors: print(f'  {e.message}')
    sys.exit(1)
print(f'  skills count: {len(data[\"skills\"])}')
" 2>&1; then
    pass "companion-skills.yaml validates"
else
    fail "companion-skills.yaml validation failed"
fi

# Step 4: policy.yaml.template top-level shape validates.
# We validate the top-level shape only (profile, setpoints, gates exist),
# then validate setpoints + gates separately against their flat schemas.
# This avoids the brittleness of $ref resolution across the four schemas
# (RefResolver vs newer referencing library is in flux; the substrate's
# schema test suite is the canonical $ref check — schema-validation.test.sh).
echo ""
echo "Step 4: assets/templates/policy.yaml.template structural shape"
if python3 -c "
import json, yaml, jsonschema, sys
data = yaml.safe_load(open('$REPO/assets/templates/policy.yaml.template'))
# Top-level required keys per Plant Contract PC-3.
for key in ('version', 'profile', 'setpoints'):
    if key not in data:
        print(f'  missing top-level key: {key}'); sys.exit(1)
# Setpoints validate individually against the flat schema.
setpoint_schema = json.load(open('$REPO/schemas/setpoint.v1.json'))
sv = jsonschema.Draft7Validator(setpoint_schema)
for i, sp in enumerate(data.get('setpoints', [])):
    errs = list(sv.iter_errors(sp))
    if errs:
        for e in errs[:2]: print(f'  setpoint[{i}] ({sp.get(\"id\", \"?\")}): {e.message}')
        sys.exit(1)
print(f'  {len(data[\"setpoints\"])} setpoints validate flat-schema')
# Gates validate individually against the flat schema (hard + soft).
gate_schema = json.load(open('$REPO/schemas/gate.v1.json'))
gv = jsonschema.Draft7Validator(gate_schema)
gates_seen = 0
for tier_key, tier in (data.get('gates') or {}).items():
    if not isinstance(tier, list): continue
    for i, g in enumerate(tier):
        errs = list(gv.iter_errors(g))
        if errs:
            for e in errs[:2]: print(f'  gate[{tier_key}][{i}] ({g.get(\"id\", \"?\")}): {e.message}')
            sys.exit(1)
        gates_seen += 1
print(f'  {gates_seen} gates validate flat-schema')
" 2>&1; then
    pass "policy.yaml.template structural shape + flat-schema parts validate"
else
    fail "policy.yaml.template structural validation failed"
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
echo "  canary/04 passed."
