#!/usr/bin/env bash
# measure-S10.sh — bstack_skills_installed
#
# Counts globally-installed skills under ~/.agents/skills/ (canonical) and
# ~/.claude/skills/ (Claude Code symlink set). Returns the union count.
set -euo pipefail

count() {
    local d="$1"
    [ -d "$d" ] || { echo 0; return; }
    find "$d" -maxdepth 1 -mindepth 1 \( -type d -o -type l \) 2>/dev/null | wc -l | tr -d '[:space:]'
}

# Union: prefer ~/.agents/skills (canonical); fall back to ~/.claude/skills.
A="$(count "$HOME/.agents/skills")"
C="$(count "$HOME/.claude/skills")"

value="$A"
[ "$C" -gt "$A" ] && value="$C"

cat <<EOF
{"id":"S10","name":"bstack_skills_installed","value":${value},"target":27,"alert_below":27,"severity":"blocking","unit":"count","sources":{"agents_skills":${A},"claude_skills":${C}}}
EOF
