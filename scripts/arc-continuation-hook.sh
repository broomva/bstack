#!/usr/bin/env bash
# arc-continuation-hook.sh — Stop hook. THE machine-checkable core of BRO-1700
# (loop-stall rejection, disturbances #3 "No response requested." and #4
# parent-never-resumes). When an autonomous arc is active and the agent's final
# turn is a no-op terminal (empty, or an explicit "No response requested." /
# acknowledgement) with NO tool calls, this returns a Stop-hook block decision so
# the harness continues the arc instead of parking on a dead turn.
#
# Predicate (all must hold; zero reasoning trusted):
#   arc active  ∧  final assistant turn has no tool_use  ∧  text is empty OR matches
#   the no-op-terminal regex  ∧  no explicit completion phrase  ∧  reconcile_count < 2
#
# False-positive guards: the complete-sentinel (an arc genuinely done is marked
# inactive via `autonomous-arc.sh complete`, which fails the arc-active clause) and
# the reconcile_count<2 runaway cap (after 2 blocks it gives up and lets the agent
# stop, so it can never loop forever). Never blocks on a productive (tool-using)
# turn. See CLAUDE.md §Ritual-vs-Substance and leverage-sensor.py m1.
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
ARC_HELPER="$SELF_DIR/autonomous-arc.sh"
INPUT="$(cat 2>/dev/null || echo '{}')"
MAX_RECONCILE=2

command -v python3 >/dev/null 2>&1 || exit 0
[ -x "$ARC_HELPER" ] || exit 0

# session_id (line 1) + transcript_path (line 2)
{ read -r SID; read -r TRANSCRIPT; } < <(python3 - "$INPUT" <<'PY'
import sys, json
try:
    d = json.loads(sys.argv[1])
except Exception:
    d = {}
print(d.get("session_id") or d.get("sessionId") or "")
print(d.get("transcript_path") or d.get("transcriptPath") or "")
PY
)

[ -n "${SID:-}" ] || exit 0
[ -n "${TRANSCRIPT:-}" ] && [ -f "$TRANSCRIPT" ] || exit 0

# arc must be active
"$ARC_HELPER" active "$SID" >/dev/null 2>&1 || exit 0

# runaway guard — give up after MAX_RECONCILE blocks so we can never loop forever
RC="$("$ARC_HELPER" get "$SID" reconcile_count 2>/dev/null)"
case "$RC" in ''|*[!0-9]*) RC=0 ;; esac
[ "$RC" -lt "$MAX_RECONCILE" ] || exit 0

# classify the final assistant turn from the transcript (pure predicate)
VERDICT="$(python3 - "$TRANSCRIPT" <<'PY'
import sys, json, re

NO_OP_RE = re.compile(
    r"^\s*(no response requested|acknowledged|noted|understood|ok(ay)?|"
    r"done|got it|sounds good|will do)\.?\s*$", re.I)
COMPLETE_RE = re.compile(
    r"\b(arc complete|all milestones? (are )?(shipped|done|complete)|"
    r"milestones? complete|arc[- ]done)\b", re.I)

def rows(path):
    try:
        with open(path, errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    yield json.loads(ln)
                except Exception:
                    continue
    except OSError:
        return

last = None
for o in rows(sys.argv[1]):
    role = o.get("type") or (o.get("message") or {}).get("role") or o.get("role")
    if role == "assistant":
        last = o

if last is None:
    print("SKIP"); sys.exit(0)

msg = last.get("message") if isinstance(last.get("message"), dict) else last
content = msg.get("content")
has_tool_use = False
parts = []
if isinstance(content, list):
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "tool_use":
            has_tool_use = True
        elif b.get("type") == "text":
            parts.append(b.get("text", ""))
elif isinstance(content, str):
    parts.append(content)
text = " ".join(p for p in parts if p).strip()

if has_tool_use:            # productive turn — never block
    print("SKIP"); sys.exit(0)
if COMPLETE_RE.search(text): # explicit completion — never block
    print("SKIP"); sys.exit(0)
# no-op terminal = the agent produced nothing, or only a bare acknowledgement
print("BLOCK" if (not text or NO_OP_RE.match(text)) else "SKIP")
PY
)"

[ "$VERDICT" = "BLOCK" ] || exit 0

# bump the guard counter and emit the Stop-hook block decision
"$ARC_HELPER" bump "$SID" reconcile_count >/dev/null 2>&1 || true
NEXT="$("$ARC_HELPER" next "$SID" 2>/dev/null)"
SLUG="$("$ARC_HELPER" status "$SID" 2>/dev/null | awk '{print $2}')"

REASON="Autonomous arc${SLUG:+ $SLUG} is active and this turn ended without continuing it. 'No response requested' / an empty acknowledgement is never a valid mid-arc terminal. Reconcile git/PR/watcher state, then continue"
[ -n "${NEXT:-}" ] && REASON="$REASON the next slice: $NEXT"
REASON="$REASON. If the arc is genuinely finished, run \`autonomous-arc.sh complete $SID\` so this stops firing."

python3 - "$REASON" <<'PY'
import sys, json
print(json.dumps({"decision": "block", "reason": sys.argv[1]}))
PY
exit 0
