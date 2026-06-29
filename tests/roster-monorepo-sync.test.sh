#!/usr/bin/env bash
# roster-monorepo-sync.test.sh — drift-guard for the companion-skills roster.
#
# After the BRO-1570/1575 consolidation, every bstack-native skill lives in the
# broomva/skills monorepo (bstack is roster + installer, not a skill-source
# bundler). This test keeps the roster from drifting back:
#   1. every entry points at broomva/skills        (single-source invariant)
#   2. roster names are unique
#   3. (opt-in, BSTACK_ROSTER_CHECK_REMOTE=1) every name actually resolves as a
#      skill in broomva/skills — catches dangling entries (e.g. a skill that was
#      relocated out, like microgrid-agent, or renamed, like skills→skills-catalog).
#
# Offline by default (1+2 are deterministic, no network). Run from anywhere.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROSTER="${BSTACK_SKILLS_YAML:-$HERE/references/companion-skills.yaml}"

PASS=0; FAIL=0; FAILED=()
ok()   { PASS=$((PASS + 1)); echo "  [pass] $1"; }
fail() { FAIL=$((FAIL + 1)); FAILED+=("$1"); echo "  [FAIL] $1"; }

[ -f "$ROSTER" ] || { echo "no roster at $ROSTER"; exit 2; }

# Preflight — the roster must PARSE and be non-empty. Without this, a corrupt or
# empty roster makes the $(python …) captures below come back empty (the traceback
# goes to stderr) and checks 1–2 would pass *open* — a drift-guard green-lighting a
# broken roster. Fail hard here instead.
python3 -c "
import yaml, sys
try:
    d = yaml.safe_load(open('$ROSTER'))
except Exception as e:
    sys.exit('roster does not parse: ' + str(e))
sys.exit(0 if isinstance(d, dict) and d.get('skills') else 'roster is empty / has no skills')
" || { echo "  [FAIL] roster preflight (must parse + be non-empty)"; echo ""; echo "roster-monorepo-sync: 0 passed, 1 failed"; exit 1; }

# 1 — every entry repo == broomva/skills
BAD="$(python3 -c "
import yaml
d = yaml.safe_load(open('$ROSTER'))
print(' '.join(s['name'] for s in d['skills'] if s.get('repo') != 'broomva/skills'))
")"
if [ -z "$BAD" ]; then
  ok "all $(python3 -c "import yaml;print(len(yaml.safe_load(open('$ROSTER'))['skills']))") entries point at broomva/skills"
else
  fail "entries not pointing at broomva/skills: $BAD"
fi

# 2 — names unique
DUP="$(python3 -c "
import yaml, collections
c = collections.Counter(s['name'] for s in yaml.safe_load(open('$ROSTER'))['skills'])
print(' '.join(n for n, k in c.items() if k > 1))
")"
if [ -z "$DUP" ]; then ok "roster names unique"; else fail "duplicate roster names: $DUP"; fi

# 3 — every name resolves in broomva/skills (catches a dangling entry — a name with
# no monorepo skill behind it, e.g. a relocated/renamed skill). Default: run when gh
# is available + authed (covers local dev AND CI with a token); skip gracefully if
# not. BSTACK_ROSTER_CHECK_REMOTE=1 *requires* it (fail if gh can't run); =0 disables.
REMOTE="${BSTACK_ROSTER_CHECK_REMOTE:-auto}"
if [ "$REMOTE" != "0" ] && command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  TREE="$(gh api 'repos/broomva/skills/git/trees/main?recursive=1' --jq '.tree[].path' 2>/dev/null \
          | grep -oE 'skills/[^/]+/[^/]+/SKILL.md' | sed -E 's#skills/[^/]+/([^/]+)/SKILL.md#\1#' | sort -u)"
  if [ -n "$TREE" ]; then
    MISSING=""
    for n in $(python3 -c "import yaml;print(' '.join(s['name'] for s in yaml.safe_load(open('$ROSTER'))['skills']))"); do
      grep -qxF "$n" <<<"$TREE" || MISSING="$MISSING $n"
    done
    if [ -z "$MISSING" ]; then ok "every roster name resolves in broomva/skills"; else fail "roster names not found in broomva/skills:$MISSING"; fi
  elif [ "$REMOTE" = "1" ]; then
    fail "remote check required but could not list the broomva/skills tree"
  else
    echo "  [skip] remote check: could not list broomva/skills tree"
  fi
elif [ "$REMOTE" = "1" ]; then
  fail "remote check required (BSTACK_ROSTER_CHECK_REMOTE=1) but gh is unavailable/unauthed"
else
  echo "  [skip] remote resolution check (gh unavailable/unauthed; set BSTACK_ROSTER_CHECK_REMOTE=1 to require)"
fi

echo ""
echo "roster-monorepo-sync: $PASS passed, $FAIL failed"
if [ "$FAIL" -ne 0 ]; then
  printf '  - %s\n' "${FAILED[@]}"
  exit 1
fi
