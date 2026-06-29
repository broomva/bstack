#!/usr/bin/env bash
# tests/policy-template-schema.test.sh — BRO-1600 policy.yaml.template v1.1 schema smoke.
#
# Asserts the reconciled v1.1 template (assets/templates/policy.yaml.template) carries
# the full v1.1 schema contract the bootstrap hook + bstack doctor depend on:
#   1. parses as YAML                         (PyYAML; skip-with-pass if absent)
#   2. version == "1.1"
#   3. all declared v1.1 top-level blocks present
#        (permissions, trust_tiers, write_gate, gates, ci_watch, ci_heal,
#         auto_merge, setpoints, profile)
#   4. permissions sub-keys: defaults{read,write,execute},
#        never_auto_granted (non-empty list), approval{mode}
#   5. trust_tiers present and non-empty
#   6. write_gate retained (regression guard — shipped earlier, must not be
#      dropped by this PR)
#
# Dependency-light: python3 one-liners for YAML introspection, no jq/jsonschema.
# Run from anywhere — template path resolves relative to the repo root via BASH_SOURCE.
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$BSTACK_REPO/assets/templates/policy.yaml.template"

PASS=0; FAIL=0; FAILED=()
ok()   { PASS=$((PASS + 1)); echo "  [pass] $1"; }
fail() { FAIL=$((FAIL + 1)); FAILED+=("$1"); echo "  [FAIL] $1"; }

summary() {
  echo ""
  echo "policy-template-schema: $PASS passed, $FAIL failed"
  if [ "$FAIL" -ne 0 ]; then
    printf '  - %s\n' "${FAILED[@]}"
    exit 1
  fi
  exit 0
}

# ── Preflight: template must exist ──────────────────────────────────────────
if [ ! -f "$TEMPLATE" ]; then
  fail "template not found at $TEMPLATE"
  summary
fi

# ── Preflight: PyYAML present? (skip-with-pass per repo convention) ─────────
if ! python3 -c "import yaml" 2>/dev/null; then
  echo "policy-template-schema.test.sh: PyYAML unavailable; skipping (skip-with-pass)."
  ok "PyYAML absent — schema introspection skipped"
  summary
fi

# ── Test 1: template parses as YAML ─────────────────────────────────────────
echo ""
echo "Test 1: template parses as YAML"
if python3 -c "import sys, yaml; yaml.safe_load(open(sys.argv[1]))" "$TEMPLATE" 2>/dev/null; then
  ok "template parses as valid YAML"
else
  fail "template is not valid YAML"
  summary  # nothing else is meaningful if it doesn't parse
fi

# ── Test 2: version == "1.1" ────────────────────────────────────────────────
echo ""
echo "Test 2: version == \"1.1\""
v=$(python3 -c "import sys, yaml; print(yaml.safe_load(open(sys.argv[1])).get('version'))" "$TEMPLATE" 2>/dev/null)
if [ "$v" = "1.1" ]; then
  ok "version is \"1.1\""
else
  fail "version != \"1.1\" (got '$v')"
fi

# ── Test 3: all declared v1.1 top-level blocks present ────────────────────────
echo ""
echo "Test 3: all v1.1 top-level blocks present"
missing=$(python3 -c '
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
req = ["permissions", "trust_tiers", "write_gate", "gates",
       "ci_watch", "ci_heal", "auto_merge", "setpoints", "profile"]
print(" ".join(k for k in req if k not in (d or {})))
' "$TEMPLATE" 2>/dev/null)
if [ -z "$missing" ]; then
  ok "all 9 v1.1 top-level blocks present"
else
  fail "missing top-level blocks: $missing"
fi

# ── Test 4: permissions sub-keys ────────────────────────────────────────────
echo ""
echo "Test 4: permissions has defaults{read,write,execute} + never_auto_granted + approval.mode"
perm_err=$(python3 -c '
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
p = d.get("permissions") or {}
errs = []
defaults = p.get("defaults") or {}
for k in ("read", "write", "execute"):
    if k not in defaults:
        errs.append("defaults." + k + " missing")
nag = p.get("never_auto_granted")
if not isinstance(nag, list) or not nag:
    errs.append("never_auto_granted not a non-empty list")
appr = p.get("approval") or {}
if "mode" not in appr:
    errs.append("approval.mode missing")
print(" | ".join(errs))
' "$TEMPLATE" 2>/dev/null)
if [ -z "$perm_err" ]; then
  ok "permissions sub-keys all present"
else
  fail "permissions schema issue: $perm_err"
fi

# ── Test 5: trust_tiers present and non-empty ───────────────────────────────
echo ""
echo "Test 5: trust_tiers present and non-empty"
tt=$(python3 -c "import sys, yaml; print('ok' if (yaml.safe_load(open(sys.argv[1])) or {}).get('trust_tiers') else 'empty')" "$TEMPLATE" 2>/dev/null)
if [ "$tt" = "ok" ]; then
  ok "trust_tiers present and non-empty"
else
  fail "trust_tiers missing or empty"
fi

# ── Test 6: write_gate retained (regression guard) ──────────────────────────
echo ""
echo "Test 6: write_gate retained (regression guard)"
wg=$(python3 -c "import sys, yaml; wg = (yaml.safe_load(open(sys.argv[1])) or {}).get('write_gate'); print('ok' if isinstance(wg, dict) and wg else 'missing')" "$TEMPLATE" 2>/dev/null)
if [ "$wg" = "ok" ]; then
  ok "write_gate retained (not dropped by this PR)"
else
  fail "write_gate dropped — regression!"
fi

summary