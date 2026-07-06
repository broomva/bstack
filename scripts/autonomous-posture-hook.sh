#!/usr/bin/env bash
# autonomous-posture-hook.sh — UserPromptSubmit. Two jobs, both keyed on the
# session_id from stdin (BRO-1700 loop-stall rejection, disturbance #5:
# posture-decay — "/autonomous" was manually re-stamped 143x across 79 sessions
# because nothing re-injected it per-turn):
#   1. Self-bootstrap: if the user just invoked /autonomous, create/refresh the
#      session arc file. No skill change is required for the loop to work — the
#      skill PR only *enriches* the arc (milestones, explicit complete).
#   2. Sticky posture: if an arc is active, re-stamp one posture line into context
#      so the agent does not silently drop the autonomous stance mid-arc.
#
# Never blocks; stdout becomes additional context; always exit 0.
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
ARC_HELPER="$SELF_DIR/autonomous-arc.sh"
INPUT="$(cat 2>/dev/null || echo '{}')"

command -v python3 >/dev/null 2>&1 || exit 0
[ -x "$ARC_HELPER" ] || exit 0

# extract session_id (line 1) + single-line prompt (line 2) from the hook payload
{ read -r SID; read -r PROMPT; } < <(python3 - "$INPUT" <<'PY'
import sys, json
try:
    d = json.loads(sys.argv[1])
except Exception:
    d = {}
sid = d.get("session_id") or d.get("sessionId") or ""
prompt = d.get("prompt") or d.get("user_prompt") or ""
print(sid)
print(" ".join(str(prompt).split())[:200])
PY
)

[ -n "${SID:-}" ] || exit 0

# 1. self-bootstrap on a /autonomous invocation (slash command at line start or after
#    space) — but ONLY if no arc is already active, so re-typing /autonomous mid-arc
#    (the posture-decay remedy) does not reset the consecutive-stall counter and
#    re-arm the runaway cap (a P20 finding).
if printf '%s' "${PROMPT:-}" | grep -qiE '(^|[[:space:]])/autonomous([[:space:]]|$)'; then
    if ! "$ARC_HELPER" active "$SID" >/dev/null 2>&1; then
        "$ARC_HELPER" set "$SID" "autonomous" >/dev/null 2>&1 || true
    fi
fi

# 2. re-stamp sticky posture while the arc is active
if "$ARC_HELPER" active "$SID" >/dev/null 2>&1; then
    SLUG="$("$ARC_HELPER" status "$SID" 2>/dev/null | awk '{print $2}')"
    NEXT="$("$ARC_HELPER" next "$SID" 2>/dev/null)"
    MSG="[autonomous arc${SLUG:+ $SLUG} active — sticky posture: do not return control mid-arc; reconcile state and continue"
    [ -n "${NEXT:-}" ] && MSG="$MSG (next slice: $NEXT)"
    MSG="$MSG. Only a cross-repo / destructive / public-API-break decision justifies a mid-arc pause; run \`autonomous-arc.sh complete $SID\` when the arc is genuinely done.]"
    printf '%s\n' "$MSG"
fi
exit 0
