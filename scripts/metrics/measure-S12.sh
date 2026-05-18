#!/usr/bin/env bash
# measure-S12.sh — hooks_wired
#
# Confirms the three required hook surfaces are wired:
#   1. pre-commit (git hook)
#   2. Stop hook (Claude Code .claude/settings.json)
#   3. PreToolUse hook (Claude Code .claude/settings.json)
set -euo pipefail

WS="${BROOMVA_WORKSPACE:-$PWD}"
SETTINGS="$WS/.claude/settings.json"

count=0
present=()
missing=()

# 1. pre-commit hook (either checked-in .githooks/ or local .git/hooks/)
if [ -x "$WS/.githooks/pre-commit" ] || [ -x "$WS/.git/hooks/pre-commit" ]; then
    count=$((count + 1))
    present+=("pre-commit")
else
    missing+=("pre-commit")
fi

# 2. Stop hook
if [ -f "$SETTINGS" ] && jq -e '.hooks.Stop[0].hooks[0].command' "$SETTINGS" >/dev/null 2>&1; then
    count=$((count + 1))
    present+=("Stop")
else
    missing+=("Stop")
fi

# 3. PreToolUse hook
if [ -f "$SETTINGS" ] && jq -e '.hooks.PreToolUse[0].hooks[0].command' "$SETTINGS" >/dev/null 2>&1; then
    count=$((count + 1))
    present+=("PreToolUse")
else
    missing+=("PreToolUse")
fi

present_json="[]"
missing_json="[]"
[ "${#present[@]}" -gt 0 ] && present_json=$(printf '%s\n' "${present[@]}" | jq -R . | jq -s -c .)
[ "${#missing[@]}" -gt 0 ] && missing_json=$(printf '%s\n' "${missing[@]}" | jq -R . | jq -s -c .)

cat <<EOF
{"id":"S12","name":"hooks_wired","value":${count},"target":3,"alert_below":3,"severity":"blocking","unit":"count","present":${present_json},"missing":${missing_json}}
EOF
