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

# python3 -I, never bare python3 (BRO-2652): no script dir, cwd, PYTHONPATH or user
# site on sys.path, so a json.py beside the sensors or in the environment cannot
# replace a stdlib module and silently rewrite the brief. The sensors re-add the
# user site at the END of sys.path for PyYAML only. Tested by
# tests/hook-python-isolation.test.sh.
if [ -f "$SENSOR" ] && command -v python3 >/dev/null 2>&1; then
    python3 -I "$SENSOR" --workspace "$WORKSPACE" --brief --cached --no-store 2>/dev/null || true
fi

# BRO-1707: refresh the exogenous ship-signal (shadow) in the BACKGROUND — never blocks
# session start; throttled to once/24h; fails silent (gh may be offline/unauth). The main
# sensor merges the resulting .control/leverage-ship-state.json as a NON-actuating shadow
# metric (m6s) for calibration until BRO-1709 (enforced CICD gates) promotes it.
SHIP_SENSOR="$SELF_DIR/leverage-ship-sensor.py"
if [ -f "$SHIP_SENSOR" ] && command -v python3 >/dev/null 2>&1 && command -v gh >/dev/null 2>&1; then
    ( python3 -I "$SHIP_SENSOR" --workspace "$WORKSPACE" --throttle 86400 >/dev/null 2>&1 & ) || true
fi
exit 0
