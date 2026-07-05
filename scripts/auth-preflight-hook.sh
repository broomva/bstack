#!/usr/bin/env bash
# auth-preflight-hook.sh — SessionStart pre-flight. Warns (NEVER blocks) if auth a
# bstack workflow depends on is missing, so the agent learns it at session start
# instead of hitting the wall mid-task. The failure mode this closes: an autonomous
# arc does all the work, then the push/PR step (P4 Pipeline) dies on an unauthenticated
# gh — wasting the whole arc. Silent when everything is authed; a single line otherwise.
#
# Deliberately minimal (gh only) per rule-of-three — widen to other CLIs when a
# concrete mid-task auth failure recurs for them, not speculatively.
set -uo pipefail

warn=""
if command -v gh >/dev/null 2>&1; then
    if ! gh auth status >/dev/null 2>&1; then
        warn="gh (GitHub CLI) not authenticated — run \`gh auth login\` before push/PR steps."
    fi
fi

[ -n "$warn" ] && echo "[auth pre-flight] $warn"
exit 0
