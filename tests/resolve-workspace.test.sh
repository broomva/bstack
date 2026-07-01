#!/usr/bin/env bash
# resolve-workspace.test.sh — BRO-1632: the shared workspace resolver must consult
# the config `workspace:` key, not just $BROOMVA_WORKSPACE / $PWD. This is what
# stops `bstack status` from reporting false blocking violations when the env var
# is unset and the command runs outside the workspace.
#
# Resolution order under test: $BROOMVA_WORKSPACE → ~/.bstack/config.yaml → $PWD
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB="$HERE/scripts/lib/resolve-workspace.sh"

PASS=0; FAIL=0; FAILED=()
ok()   { PASS=$((PASS + 1)); echo "  [pass] $1"; }
fail() { FAIL=$((FAIL + 1)); FAILED+=("$1"); echo "  [FAIL] $1"; }

[ -f "$LIB" ] || { echo "no resolver at $LIB"; exit 2; }

# tier 1 — explicit env override wins
got="$(BROOMVA_WORKSPACE=/tmp bash -c ". '$LIB'; resolve_workspace")"
[ "$got" = "/tmp" ] && ok "tier 1: \$BROOMVA_WORKSPACE override" || fail "tier 1 got '$got' (expected /tmp)"

# tier 2 — config workspace key (env unset)
CFG="$(mktemp -d)"; WS="$(mktemp -d)"
printf 'workspace: %s\n' "$WS" > "$CFG/config.yaml"
got="$(BSTACK_STATE_DIR="$CFG" bash -c "unset BROOMVA_WORKSPACE; . '$LIB'; resolve_workspace")"
[ "$got" = "$WS" ] && ok "tier 2: config workspace: key" || fail "tier 2 got '$got' (expected $WS)"

# tier 3 — PWD last resort (env unset, no config)
TMP="$(mktemp -d)"
got="$(cd "$TMP" && BSTACK_STATE_DIR="$(mktemp -d)" bash -c "unset BROOMVA_WORKSPACE; . '$LIB'; resolve_workspace")"
[ "$got" = "$TMP" ] && ok "tier 3: \$PWD fallback" || fail "tier 3 got '$got' (expected $TMP)"

rm -rf "$CFG" "$WS" "$TMP"
echo ""
echo "resolve-workspace: $PASS passed, $FAIL failed"
if [ "$FAIL" -ne 0 ]; then
  printf '  - %s\n' "${FAILED[@]}"
  exit 1
fi
