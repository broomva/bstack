#!/usr/bin/env bash
# bootstrap-roster-source.test.sh — install-mechanism drift guard (BRO-1632).
#
# Every skill install in bstack must go through the roster-driven installer
# (bin/bstack-skills, reading references/companion-skills.yaml → `npx skills add
# broomva/skills --skill <name>`). No script may carry a hardcoded skill→repo map or
# a raw `npx skills add <standalone-repo>` loop: those maps drifted to repos deleted
# in BRO-1602 and 404'd `bstack bootstrap` AND `bstack revamp` on every fresh host.
#
# The original version of this guard only scanned bootstrap.sh and only matched a
# LITERAL repo after `skills add` — it missed the variable-indirected `npx skills add
# "$repo"` loop (the actual bug) and the second map in revamp.sh. This version scans
# every installer script for both patterns.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PASS=0; FAIL=0; FAILED=()
ok()   { PASS=$((PASS + 1)); echo "  [pass] $1"; }
fail() { FAIL=$((FAIL + 1)); FAILED+=("$1"); echo "  [FAIL] $1"; }

# Scripts that perform installs (scan bin/ + scripts/, skip the lib + this test).
mapfile -t SCRIPTS < <(find "$HERE/bin" "$HERE/scripts" -type f \( -name '*.sh' -o ! -name '*.*' \) 2>/dev/null | sort)

# 1 — no LITERAL standalone-repo install: `npx skills add broomva/<name>` where
#     <name> != skills. The correct monorepo form is `broomva/skills --skill <name>`
#     (repo == broomva/skills), so only non-skills repos are violations.
BAD_LITERAL=""
for f in "${SCRIPTS[@]}"; do
  # strip full-comment lines first — a comment mentioning a repo isn't an install
  hits="$(grep -vE '^[[:space:]]*#' "$f" 2>/dev/null | grep -oE 'skills add +broomva/[a-z0-9._-]+' | awk '{print $NF}' | grep -vx 'broomva/skills' || true)"
  [ -n "$hits" ] && BAD_LITERAL="$BAD_LITERAL $(basename "$f"):$(echo "$hits" | tr '\n' ',')"
done
if [ -z "$BAD_LITERAL" ]; then
  ok "no literal standalone-repo install command in any installer script"
else
  fail "literal standalone-repo installs:$BAD_LITERAL"
fi

# 2 — no VARIABLE-INDIRECTED install loop: `npx skills add "$var"` / `npx skills add $var`.
#     This is the pattern the hardcoded maps fed (bootstrap's SKILL_REPOS + revamp's
#     REPOS) — indirection hides the repo from a literal grep. All installs must route
#     through bin/bstack-skills, which resolves the repo from the roster.
BAD_INDIRECT=""
for f in "${SCRIPTS[@]}"; do
  case "$f" in */bstack-skills) continue ;; esac   # the installer itself legitimately calls npx with a var
  # A variable-indirected install is fine when it carries `--skill` (the monorepo
  # single-skill form, e.g. skill-graduate's `npx skills add $MONOREPO --skill $T`).
  # Flag only bare `npx skills add "$repo"` — the whole-repo map-loop pattern.
  hits="$(grep -vE '^[[:space:]]*#' "$f" 2>/dev/null | grep -E 'npx +skills +add +"?\$' | grep -v -- '--skill' || true)"
  [ -n "$hits" ] && BAD_INDIRECT="$BAD_INDIRECT $(basename "$f")"
done
if [ -z "$BAD_INDIRECT" ]; then
  ok "no variable-indirected 'npx skills add \$repo' loop outside bin/bstack-skills"
else
  fail "variable-indirected npx installs (should delegate to bstack-skills):$BAD_INDIRECT"
fi

# 3 — no hardcoded skill→repo map: any `declare -A` whose body holds a broomva/<name>
#     literal (catches SKILL_REPOS, REPOS, and any future rename).
BAD_MAP=""
for f in "${SCRIPTS[@]}"; do
  # extract each `declare -A NAME=( ... )` block and check for a broomva/<name> literal
  if awk '/declare -A/{inblk=1} inblk{print} /\)/{if(inblk)inblk=0}' "$f" 2>/dev/null \
       | grep -qE 'broomva/[a-z0-9._-]+'; then
    BAD_MAP="$BAD_MAP $(basename "$f")"
  fi
done
if [ -z "$BAD_MAP" ]; then
  ok "no hardcoded skill→repo declare -A map in any installer script"
else
  fail "hardcoded skill→repo map(s):$BAD_MAP"
fi

# 4 — the two known installers delegate to the roster-driven installer
for inst in bootstrap.sh revamp.sh; do
  if grep -qE 'bstack-skills" +install|bstack-skills install' "$HERE/scripts/$inst" 2>/dev/null; then
    ok "$inst delegates skill install to bin/bstack-skills"
  else
    fail "$inst no longer delegates to bstack-skills install"
  fi
done

# 5 — awk-fallback ↔ PyYAML parity (BRO-1632). The dependency-free awk parser is what
#     makes a fresh (PyYAML-absent) host install skills instead of silently installing
#     nothing. If the roster format ever drifts (e.g. a multi-line YAML description)
#     the awk path would diverge from PyYAML and quietly break fresh installs. When
#     PyYAML is present, assert name/repo/required match between the two parsers.
ROSTER="$HERE/references/companion-skills.yaml"
if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' >/dev/null 2>&1 && [ -f "$ROSTER" ]; then
  py_out="$(python3 - "$ROSTER" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
for s in d.get("skills", []):
    print(f"{s['name']}\t{s['repo']}\t{'true' if s.get('required') else 'false'}")
PY
)"
  awk_out="$(ROSTER_YAML="$ROSTER" bash -c 'source <(sed -n "/^_parse_roster_awk()/,/^}/p" "'"$HERE"'/bin/bstack-skills"); _parse_roster_awk "'"$ROSTER"'"' | cut -f1,2,4)"
  if [ "$(printf '%s' "$py_out" | sort)" = "$(printf '%s' "$awk_out" | sort)" ]; then
    ok "awk fallback parses the roster identically to PyYAML (name/repo/required)"
  else
    fail "awk fallback diverges from PyYAML — roster format drifted; fresh-host install would break"
  fi
else
  echo "  [skip] awk↔PyYAML parity (PyYAML unavailable)"
fi

echo ""
echo "bootstrap-roster-source: $PASS passed, $FAIL failed"
if [ "$FAIL" -ne 0 ]; then
  printf '  - %s\n' "${FAILED[@]}"
  exit 1
fi
