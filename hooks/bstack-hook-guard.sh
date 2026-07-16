#!/usr/bin/env bash
# bstack plugin hook guard (BRO-1926 Phase 2).
#
# The bstack plugin loads at PERSONAL scope (~/.claude/skills/bstack), so its
# hooks fire in EVERY Claude Code session, including non-bstack repos. Several
# bstack hooks write workspace state (leverage-sensor.py + leverage-ship-sensor.py
# create <workspace>/.control/*.json; l3-stability-pretool logs to
# <workspace>/.control/audit/). Left unguarded, a global plugin would litter
# .control/ into unrelated repos (e.g. a client repo) on every session.
#
# This wrapper gates the ENTIRE hook: if the current workspace is not
# bstack-governed (no .control/ directory), the hook is a no-op. That covers
# every current and future writer in one place, rather than guarding each script.
#
# Usage (from hooks/hooks.json):
#   bash "${CLAUDE_PLUGIN_ROOT}/hooks/bstack-hook-guard.sh" <cmd> [args...]
# e.g.
#   bash ".../bstack-hook-guard.sh" bash    ".../scripts/arc-continuation-hook.sh"
#   bash ".../bstack-hook-guard.sh" python3 ".../scripts/leverage-sensor.py" --throttle 21600
#
# bstack-autoupdate-hook.sh is intentionally NOT wrapped — it keeps the bstack
# install itself fresh and is workspace-agnostic, so it should run globally.
set -euo pipefail

WORKSPACE="${BSTACK_WORKSPACE:-${BROOMVA_WORKSPACE:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}}"

if [ ! -d "$WORKSPACE/.control" ]; then
    # Not a bstack-governed workspace — no-op. PreToolUse hooks must still emit a
    # decision so the tool is not left in an undecided state; emit approve.
    case "$*" in
        *l3-stability-pretool-hook.sh*|*-pretool-hook.sh*)
            printf '{"decision":"approve"}\n' ;;
    esac
    exit 0
fi

exec "$@"
