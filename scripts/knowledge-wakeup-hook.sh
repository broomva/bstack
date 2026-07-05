#!/bin/bash
# knowledge-wakeup-hook.sh — SessionStart ACTUATION WIRE of the bstack
# self-improvement loop. Injects the loop's current error (worst leverage
# setpoint gap + its named corrective actuator, plus any not-closed / unsigned-
# reference warning) into the new session, so the next context starts by knowing
# its own top failure mode.
#
# Fast path: renders the cached snapshot the Stop hook wrote (leverage-state.json);
# recomputes only on a stale/missing cache. Never blocks (always exit 0).
#
# Replaces the historical `bookkeeping wakeup` call — a subcommand that never
# existed, so the SessionStart wire emitted 0 bytes every session. See
# leverage-sensor.py (the real sensor h) + doctor.sh §23 (the closure verdict).
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
SENSOR="$SELF_DIR/leverage-sensor.py"
WORKSPACE="${BSTACK_WORKSPACE:-${BROOMVA_WORKSPACE:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}}"

if [ -f "$SENSOR" ] && command -v python3 >/dev/null 2>&1; then
    python3 "$SENSOR" --workspace "$WORKSPACE" --brief --cached --no-store 2>/dev/null || true
fi
exit 0
