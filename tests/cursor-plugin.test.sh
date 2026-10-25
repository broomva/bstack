#!/usr/bin/env bash
# tests/cursor-plugin.test.sh — the Cursor plugin is installable and does not
# take over the Claude Code plugin.
#
# .cursor-plugin/plugin.json must validate, name the skills it ships, and point
# hooks at an empty Cursor hooks file. hooks/hooks.json stays the Claude map.
# Run from anywhere:
#   bash tests/cursor-plugin.test.sh

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FAIL=0
ok()  { echo "  [ok] $1"; PASS=$((PASS + 1)); }
bad() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); }

PLUGIN="$BSTACK_REPO/.cursor-plugin/plugin.json"
HOOKS="$BSTACK_REPO/cursor-plugin/hooks.json"
RULE="$BSTACK_REPO/cursor-plugin/rules/bstack-hard-gates.mdc"
SKILLS="$BSTACK_REPO/cursor-plugin/skills"
CLAUDE_HOOKS="$BSTACK_REPO/hooks/hooks.json"
VALIDATOR="$BSTACK_REPO/scripts/validate-skill-frontmatter.py"

EXPECTED_SKILLS="autonomous bookkeeping broomva-workspace cross-review dogfood kg setup-bstack"

echo "== manifest is a Cursor plugin named bstack, version-locked =="
if [ ! -f "$PLUGIN" ]; then
  bad "missing $PLUGIN"
else
  python3 -I - "$PLUGIN" "$BSTACK_REPO/VERSION" <<'PY'
import json, sys
plugin_path, version_path = sys.argv[1], sys.argv[2]
errors = []
try:
    doc = json.load(open(plugin_path))
except json.JSONDecodeError as exc:
    print(f"invalid json: {exc}")
    sys.exit(1)
version = open(version_path).read().strip()
allowed = {
    "name", "displayName", "description", "version", "author", "publisher",
    "homepage", "repository", "license", "logo", "keywords", "commands",
    "agents", "skills", "rules", "hooks", "mcpServers",
}
extra = set(doc) - allowed
if extra:
    errors.append(f"unknown keys {sorted(extra)}")
if doc.get("name") != "bstack":
    errors.append(f"name is {doc.get('name')!r}")
if doc.get("version") != version:
    errors.append(f"version {doc.get('version')!r} != VERSION {version!r}")
author = doc.get("author")
if not isinstance(author, dict) or not author.get("name"):
    errors.append("author.name missing")
elif set(author) - {"name", "email"}:
    errors.append(f"author has unsupported keys {sorted(set(author) - {'name', 'email'})}")
for key in ("skills", "rules", "hooks"):
    value = doc.get(key)
    if not isinstance(value, str) or not value.startswith("./") or value.startswith("../") or "/../" in value:
        errors.append(f"{key} must be a relative ./ path, got {value!r}")
if doc.get("skills") != "./cursor-plugin/skills/":
    errors.append(f"skills path {doc.get('skills')!r}")
if doc.get("rules") != "./cursor-plugin/rules/":
    errors.append(f"rules path {doc.get('rules')!r}")
if doc.get("hooks") != "./cursor-plugin/hooks.json":
    errors.append(f"hooks path {doc.get('hooks')!r} would have to replace hooks/hooks.json discovery")
if doc.get("hooks") == "./hooks/hooks.json" or doc.get("hooks") == "hooks/hooks.json":
    errors.append("Cursor manifest points at the Claude hooks file")
if errors:
    print("\n".join(errors))
    sys.exit(1)
PY
  if [ $? -eq 0 ]; then
    ok "plugin.json shape, name, version, and component paths"
  else
    bad "plugin.json failed validation (see above)"
  fi
fi

echo "== Cursor hooks file is empty and is not the Claude map =="
if python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d=={"hooks": {}}' "$HOOKS" 2>/dev/null; then
  ok "cursor-plugin/hooks.json is {hooks: {}}"
else
  bad "cursor-plugin/hooks.json is not an empty Cursor hooks document"
fi
if grep -q 'CLAUDE_PLUGIN_ROOT' "$HOOKS"; then
  bad "Cursor hooks file references CLAUDE_PLUGIN_ROOT"
else
  ok "Cursor hooks file does not reference CLAUDE_PLUGIN_ROOT"
fi
if grep -q 'SessionStart' "$CLAUDE_HOOKS" && grep -q 'CLAUDE_PLUGIN_ROOT' "$CLAUDE_HOOKS"; then
  ok "hooks/hooks.json still has Claude SessionStart and CLAUDE_PLUGIN_ROOT"
else
  bad "hooks/hooks.json lost its Claude hook map"
fi
if [ -f "$BSTACK_REPO/.claude-plugin/plugin.json" ] && grep -q '"defaultEnabled": false' "$BSTACK_REPO/.claude-plugin/plugin.json"; then
  ok "Claude plugin.json still defaultEnabled false"
else
  bad "Claude plugin.json missing or defaultEnabled flipped"
fi

echo "== rule is on-demand, not always applied =="
if grep -q '^alwaysApply: false$' "$RULE"; then
  ok "bstack-hard-gates.mdc alwaysApply false"
else
  bad "hard-gate rule must ship alwaysApply: false"
fi
for needle in 'force-push' 'git reset --hard' 'human-only' '.control/policy.yaml'; do
  if grep -q "$needle" "$RULE"; then
    ok "rule mentions $needle"
  else
    bad "rule missing $needle"
  fi
done

echo "== skills are portable Agent Skills =="
FOUND="$(
  for d in "$SKILLS"/*/; do
    [ -f "${d}SKILL.md" ] || continue
    basename "$d"
  done | sort | tr '\n' ' ' | sed 's/ *$//'
)"
if [ "$FOUND" = "$EXPECTED_SKILLS" ]; then
  ok "skill set is $EXPECTED_SKILLS"
else
  bad "skill set is '$FOUND', expected '$EXPECTED_SKILLS'"
fi

FM_OUT="$(python3 "$VALIDATOR" "$SKILLS" 2>&1)"
FM_RC=$?
if [ "$FM_RC" -eq 0 ] && ! printf '%s\n' "$FM_OUT" | grep -q '\[warn\]' && printf '%s\n' "$FM_OUT" | grep -q '0 error(s) · 0 warning(s)'; then
  ok "frontmatter validator clean"
else
  bad "frontmatter validator failed"
  printf '%s\n' "$FM_OUT"
fi

# Host-specific wording that the ports were rewritten to drop.
if grep -R -n -E 'sand-workflow:|Grok|ListMachines|computerUse|CallDynamicTool|cursor-github|Auto-review' "$SKILLS" >/dev/null; then
  bad "a skill still has host-specific wording"
  grep -R -n -E 'sand-workflow:|Grok|ListMachines|computerUse|CallDynamicTool|cursor-github|Auto-review' "$SKILLS" || true
else
  ok "no host-specific link or product wording in skills"
fi

for skill in kg bookkeeping dogfood broomva-workspace; do
  if grep -q 'BROOMVA_ROOT=/Volumes/Broomva/home/broomva' "$SKILLS/$skill/SKILL.md"; then
    ok "$skill keeps the Mac BROOMVA_ROOT note"
  else
    bad "$skill dropped the Mac BROOMVA_ROOT note"
  fi
done

if grep -q 'no-optional-locks' "$SKILLS/broomva-workspace/SKILL.md" \
  && grep -q 'no-optional-locks' "$SKILLS/cross-review/SKILL.md"; then
  ok "Mac git --no-optional-locks notes kept"
else
  bad "Mac git --no-optional-locks notes missing"
fi

if grep -q '~/.cursor/rules/bstack.mdc' "$SKILLS/setup-bstack/SKILL.md" \
  && grep -q 'alwaysApply: true' "$SKILLS/setup-bstack/SKILL.md" \
  && grep -q 'force-push' "$SKILLS/setup-bstack/SKILL.md" \
  && grep -q 'git reset --hard' "$SKILLS/setup-bstack/SKILL.md"; then
  ok "setup-bstack writes the user hard-gate rule"
else
  bad "setup-bstack does not write ~/.cursor/rules/bstack.mdc"
fi

# Relative skill links, not a host link scheme.
python3 -I - "$SKILLS" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
missing = []
for skill in root.glob("*/SKILL.md"):
    text = skill.read_text()
    # markdown links only; ignore fenced blocks by a light scan
    for raw in text.split("](")[1:]:
        target = raw.split(")", 1)[0]
        if not target.endswith(".md"):
            continue
        if target.startswith(("http://", "https://")):
            continue
        resolved = (skill.parent / target).resolve()
        if not resolved.is_file():
            missing.append(f"{skill.parent.name}: {target}")
if missing:
    print("\n".join(missing))
    sys.exit(1)
PY
if [ $? -eq 0 ]; then
  ok "relative markdown links resolve"
else
  bad "a skill link does not resolve (see above)"
fi

echo
echo "cursor-plugin: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
