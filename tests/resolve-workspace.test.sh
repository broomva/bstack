#!/usr/bin/env bash
# resolve-workspace.test.sh — BRO-1632: the shared workspace resolver must consult
# the config `workspace:` key (and prefer a cwd that is itself a workspace), not just
# $BROOMVA_WORKSPACE / $PWD. This is what stops `bstack status` from reporting false
# blocking violations when the env var is unset and the command runs elsewhere.
#
# Resolution order under test:
#   1. $BROOMVA_WORKSPACE
#   2. $PWD if it is a workspace ($PWD/.control/policy.yaml exists)
#   3. ~/.bstack/config.yaml `workspace:` key
#   4. $PWD (last resort)
#
# Each tier runs from an ISOLATED temp cwd so tier 2 (PWD-is-workspace) never fires
# by accident.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB="$HERE/scripts/lib/resolve-workspace.sh"

PASS=0; FAIL=0; FAILED=()
ok()   { PASS=$((PASS + 1)); echo "  [pass] $1"; }
fail() { FAIL=$((FAIL + 1)); FAILED+=("$1"); echo "  [FAIL] $1"; }

[ -f "$LIB" ] || { echo "no resolver at $LIB"; exit 2; }

# tier 1 — explicit env override wins (from a plain cwd)
PLAIN="$(mktemp -d)"
got="$(cd "$PLAIN" && BROOMVA_WORKSPACE=/tmp bash -c ". '$LIB'; resolve_workspace")"
[ "$got" = "/tmp" ] && ok "tier 1: \$BROOMVA_WORKSPACE override" || fail "tier 1 got '$got' (expected /tmp)"

# tier 2 — PWD is a workspace (has .control/policy.yaml), env unset → PWD wins over config
WS_PWD="$(mktemp -d)"; mkdir -p "$WS_PWD/.control"; : > "$WS_PWD/.control/policy.yaml"
CFG_OTHER="$(mktemp -d)"; OTHER="$(mktemp -d)"; printf 'workspace: %s\n' "$OTHER" > "$CFG_OTHER/config.yaml"
got="$(cd "$WS_PWD" && BSTACK_STATE_DIR="$CFG_OTHER" bash -c "unset BROOMVA_WORKSPACE; . '$LIB'; resolve_workspace")"
[ "$got" = "$WS_PWD" ] && ok "tier 2: cwd-is-workspace beats config" || fail "tier 2 got '$got' (expected $WS_PWD)"

# tier 3 — cwd NOT a workspace, env unset, config set → config workspace key
CFG="$(mktemp -d)"; WS="$(mktemp -d)"; printf 'workspace: %s\n' "$WS" > "$CFG/config.yaml"
got="$(cd "$PLAIN" && BSTACK_STATE_DIR="$CFG" bash -c "unset BROOMVA_WORKSPACE; . '$LIB'; resolve_workspace")"
[ "$got" = "$WS" ] && ok "tier 3: config workspace: key" || fail "tier 3 got '$got' (expected $WS)"

# tier 4 — cwd NOT a workspace, env unset, no config → PWD fallback
got="$(cd "$PLAIN" && BSTACK_STATE_DIR="$(mktemp -d)" bash -c "unset BROOMVA_WORKSPACE; . '$LIB'; resolve_workspace")"
[ "$got" = "$PLAIN" ] && ok "tier 4: \$PWD fallback" || fail "tier 4 got '$got' (expected $PLAIN)"

rm -rf "$PLAIN" "$WS_PWD" "$CFG_OTHER" "$OTHER" "$CFG" "$WS"
echo ""
echo "resolve-workspace: $PASS passed, $FAIL failed"
if [ "$FAIL" -ne 0 ]; then
  printf '  - %s\n' "${FAILED[@]}"
  exit 1
fi
