#!/usr/bin/env bash
# bstack plugin hook guard (BRO-1926 Phase 2).
#
# The bstack plugin loads at PERSONAL scope (~/.claude/skills/bstack), so its
# hooks fire in EVERY Claude Code session, including non-bstack repos. Two hooks
# WRITE workspace state and would otherwise litter <workspace>/.control/ into an
# unrelated repo (e.g. a client repo) every session:
#   - knowledge-wakeup-hook.sh  → runs leverage-ship-sensor.py (writes
#                                 <ws>/.control/leverage-ship-state.json)
#   - leverage-sensor.py (Stop) → makedirs+writes <ws>/.control/leverage-*.json
#
# This wrapper gates those hooks on the workspace being bstack-governed (has a
# .control/ dir); if not, the hook is a no-op.
#
# Usage (from hooks/hooks.json):
#   bash "${CLAUDE_PLUGIN_ROOT}/hooks/bstack-hook-guard.sh" <cmd> [args...]
#
# ONLY workspace-.control WRITERS run through this guard. Deliberately NOT wrapped:
#   - bstack-autoupdate-hook.sh   — keeps the install fresh; workspace-agnostic (global).
#   - arc-continuation-hook.sh    — session-scoped (arc state in ~/.config/broomva/
#     autonomous-posture-hook.sh    autonomous/), self-guarding (no-ops without an
#                                   active arc), writes nothing to <ws>/.control. Wrapping
#                                   them on a cwd-.control gate would FALSE-NEGATIVE the
#                                   loop-stall/posture logic when cwd is a nested
#                                   non-governed git repo (BRO-1926 P20 review).
#   - l3-stability-pretool-hook.sh — source-guarded internally (only writes .control/audit
#                                   for L3-file edits, and only in a governed workspace),
#                                   so wrapping it would add a git spawn to EVERY edit.
set -euo pipefail

WORKSPACE="${BSTACK_WORKSPACE:-${BROOMVA_WORKSPACE:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}}"

# Not a bstack-governed workspace — no-op (exit 0, no output). The wrapped hooks are
# SessionStart/Stop only, so "no opinion" is the correct neutral result.
[ -d "$WORKSPACE/.control" ] || exit 0

exec "$@"
