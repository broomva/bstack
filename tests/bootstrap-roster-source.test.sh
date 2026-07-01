#!/usr/bin/env bash
# bootstrap-roster-source.test.sh — drift guard for BRO-1632.
#
# `bstack bootstrap` must install skills ONLY through the roster-driven installer
# (bin/bstack-skills, reading references/companion-skills.yaml). It must NOT carry
# a hardcoded skill→repo map: that map drifted to deleted standalone repos
# (broomva/agentic-control-kernel, broomva/p9, broomva/finance-substrate, …) after
# the BRO-1602 consolidation and 404'd every fresh-host bootstrap.
#
# This test keeps bootstrap.sh from regrowing standalone-repo install commands.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOTSTRAP="$HERE/scripts/bootstrap.sh"

PASS=0; FAIL=0; FAILED=()
ok()   { PASS=$((PASS + 1)); echo "  [pass] $1"; }
fail() { FAIL=$((FAIL + 1)); FAILED+=("$1"); echo "  [FAIL] $1"; }

[ -f "$BOOTSTRAP" ] || { echo "no bootstrap.sh at $BOOTSTRAP"; exit 2; }

# 1 — no `npx skills add broomva/<name>` where <name> != skills (standalone-repo install)
BAD="$(grep -oE 'skills add +broomva/[a-z0-9-]+' "$BOOTSTRAP" 2>/dev/null \
        | awk '{print $NF}' | grep -vx 'broomva/skills' | sort -u | tr '\n' ' ')"
if [ -z "$BAD" ]; then
  ok "bootstrap.sh has no standalone-repo install commands (broomva/<name> ≠ broomva/skills)"
else
  fail "bootstrap.sh still installs from standalone repos: $BAD"
fi

# 2 — no hardcoded SKILL_REPOS map (the drift vector)
if grep -qE 'declare -A SKILL_REPOS' "$BOOTSTRAP"; then
  fail "bootstrap.sh still declares a hardcoded SKILL_REPOS map"
else
  ok "no hardcoded SKILL_REPOS map"
fi

# 3 — bootstrap delegates to the roster-driven installer
if grep -qE 'bstack-skills["'"'"' ]+install|bstack-skills" install' "$BOOTSTRAP"; then
  ok "bootstrap delegates skill install to bin/bstack-skills (roster-driven)"
else
  fail "bootstrap.sh no longer delegates to bstack-skills install"
fi

echo ""
echo "bootstrap-roster-source: $PASS passed, $FAIL failed"
if [ "$FAIL" -ne 0 ]; then
  printf '  - %s\n' "${FAILED[@]}"
  exit 1
fi
