#!/usr/bin/env bash
# skill-selfwarn-banner.test.sh — BRO-1633.
#
# `npx skills add broomva/bstack` lands ONLY the root SKILL.md and drops bin/scripts
# (BRO-1561). Since SKILL.md is the one file that survives that partial install, it
# must carry a loud self-warning so the broken install is self-diagnosing rather than
# a silent deceptive success. This test keeps the banner from being removed or
# hollowed out.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL="$HERE/SKILL.md"

PASS=0; FAIL=0; FAILED=()
ok()   { PASS=$((PASS + 1)); echo "  [pass] $1"; }
fail() { FAIL=$((FAIL + 1)); FAILED+=("$1"); echo "  [FAIL] $1"; }

[ -f "$SKILL" ] || { echo "no SKILL.md at $SKILL"; exit 2; }

# The banner must appear near the TOP (first ~15 lines) so it's the first thing an
# agent reads when the lone SKILL.md loads.
HEAD="$(head -20 "$SKILL")"

if printf '%s' "$HEAD" | grep -q 'SELF-WARNING BANNER'; then
  ok "self-warning banner marker present near top of SKILL.md"
else
  fail "self-warning banner marker missing from the top of SKILL.md"
fi

if printf '%s' "$HEAD" | grep -qiE 'BROKEN partial install|partial install'; then
  ok "banner states the install is broken/partial"
else
  fail "banner does not warn about a broken/partial install"
fi

# The fix command must be present (clone + bootstrap — the only working install).
if printf '%s' "$HEAD" | grep -qE 'git clone https://github.com/broomva/bstack'; then
  ok "banner gives the clone-install fix command"
else
  fail "banner missing the 'git clone …/bstack' fix command"
fi

echo ""
echo "skill-selfwarn-banner: $PASS passed, $FAIL failed"
if [ "$FAIL" -ne 0 ]; then
  printf '  - %s\n' "${FAILED[@]}"
  exit 1
fi
