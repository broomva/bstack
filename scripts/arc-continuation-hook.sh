#!/usr/bin/env bash
# arc-continuation-hook.sh — Stop hook. THE machine-checkable core of BRO-1700
# (loop-stall rejection, disturbances #3 "No response requested." and #4
# parent-never-resumes). When an autonomous arc is active and the agent's final
# turn is a genuine no-op terminal (empty, or the literal CC sentinel
# "No response requested"), returns a Stop-hook block decision so the harness
# continues the arc instead of parking on a dead turn.
#
# Correctness properties (each earned a P20 finding):
#   - DRAIN-RETRY for the transcript race: Claude Code writes the final assistant
#     entry ~125ms AFTER the Stop hook fires (BRO-1616). A single read would judge
#     the PREVIOUS turn, so we poll (bounded) until an assistant entry written
#     at/after hook start is present before classifying.
#   - TIGHT no-op predicate: only empty text or a search for the literal
#     "No response requested" sentinel counts. Conversational acknowledgements
#     (ok / got it / done / …) are NOT no-ops — they are how an agent yields to a
#     user's mid-arc pause, one of the legitimate stops this must respect.
#   - AUTO-RELEASE: a completion phrase releases the arc (calls `complete`) rather
#     than force-continuing a finished agent.
#   - CONSECUTIVE runaway cap: reconcile_count<2 bounds *consecutive* no-op blocks;
#     a productive/substantive turn resets it, so a healthy long arc is still
#     nudged whenever it genuinely stalls, but two dead-ends in a row give up.
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
ARC_HELPER="$SELF_DIR/autonomous-arc.sh"
INPUT="$(cat 2>/dev/null || echo '{}')"
MAX_RECONCILE=2

command -v python3 >/dev/null 2>&1 || exit 0
[ -x "$ARC_HELPER" ] || exit 0

# session_id (l1) + transcript_path (l2) + stop_hook_active (l3, CC's loop signal)
{ read -r SID; read -r TRANSCRIPT; read -r STOP_ACTIVE; } < <(python3 - "$INPUT" <<'PY'
import sys, json
try:
    d = json.loads(sys.argv[1])
except Exception:
    d = {}
print(d.get("session_id") or d.get("sessionId") or "")
print(d.get("transcript_path") or d.get("transcriptPath") or "")
print("1" if d.get("stop_hook_active") or d.get("stopHookActive") else "0")
PY
)

[ -n "${SID:-}" ] || exit 0
[ -n "${TRANSCRIPT:-}" ] && [ -f "$TRANSCRIPT" ] || exit 0
"$ARC_HELPER" active "$SID" >/dev/null 2>&1 || exit 0   # arc must be active + not stale

# classify the final assistant turn (with a bounded drain-retry for the flush race)
VERDICT="$(python3 - "$TRANSCRIPT" <<'PY'
import sys, json, re, time, datetime

path = sys.argv[1]
HOOK_START = time.time()
SKEW = 2.0                                    # accept turns written within 2s before start
BUDGET = float(__import__("os").environ.get("ARC_DRAIN_MS", "1500")) / 1000.0
INTERVAL = 0.05

SENTINEL_RE = re.compile(r"no response requested", re.I)   # the CC no-op sentinel (search)
NO_OP_RE    = re.compile(r"^\s*acknowledged\.?\s*$", re.I)  # bare acknowledgement, anchored
# ANCHORED (^…$): only a completion-DOMINANT final message releases the arc. A long
# substantive turn that merely mentions "the first task is complete" must NOT auto-
# release (premature release = silent under-protection, worse than a missed nudge).
COMPLETE_RE = re.compile(
    r"^\s*(arc complete|arc[- ]done|all milestones?\b.{0,40}?\b(shipped|done|complete)|"
    r"milestones? complete|task complete|nothing (?:left|more) to do)\.?\s*$", re.I)

def last_assistant(p):
    # bounded tail read (final assistant turn is always near EOF)
    try:
        with open(p, "rb") as f:
            f.seek(0, 2); size = f.tell()
            f.seek(max(0, size - 262144))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    last = None
    for ln in data.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        role = o.get("type") or (o.get("message") or {}).get("role") or o.get("role")
        if role == "assistant":
            last = o
    return last

def epoch(entry):
    ts = entry.get("timestamp") if isinstance(entry, dict) else None
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None

# DRAIN: wait until an assistant entry written at/after hook start is present
entry = last_assistant(path)
waited = 0.0
while waited < BUDGET:
    ep = epoch(entry)
    if ep is not None and ep >= HOOK_START - SKEW:
        break                       # the fresh (post-Stop) turn has landed
    if ep is None and waited >= 0.3:
        break                       # no-timestamp transcripts: short settle then classify
    time.sleep(INTERVAL); waited += INTERVAL
    entry = last_assistant(path)

if entry is None:
    print("SKIP"); sys.exit(0)

msg = entry.get("message") if isinstance(entry.get("message"), dict) else entry
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

if has_tool_use:
    print("PRODUCTIVE")                      # tool call = progress → reset the cap
elif COMPLETE_RE.match(text):
    print("COMPLETE")                        # finished (completion-dominant) → release the arc
elif (not text) or SENTINEL_RE.search(text) or NO_OP_RE.match(text):
    print("BLOCK")                           # genuine no-op terminal → continue the arc
else:
    print("PRODUCTIVE")                      # substantive text = a healthy yield → reset
PY
)"

case "$VERDICT" in
    PRODUCTIVE)
        "$ARC_HELPER" reset "$SID" reconcile_count >/dev/null 2>&1 || true
        exit 0 ;;
    COMPLETE)
        "$ARC_HELPER" complete "$SID" >/dev/null 2>&1 || true   # auto-release
        exit 0 ;;
    BLOCK)
        RC="$("$ARC_HELPER" get "$SID" reconcile_count 2>/dev/null)"
        case "$RC" in ''|*[!0-9]*) RC=0 ;; esac
        [ "$RC" -lt "$MAX_RECONCILE" ] || exit 0              # consecutive-stall cap reached
        "$ARC_HELPER" bump "$SID" reconcile_count >/dev/null 2>&1 || true
        NEXT="$("$ARC_HELPER" next "$SID" 2>/dev/null)"
        SLUG="$("$ARC_HELPER" status "$SID" 2>/dev/null | awk '{print $2}')"
        REASON="Autonomous arc${SLUG:+ $SLUG} is active and this turn ended without continuing it. 'No response requested' / an empty terminal is never a valid mid-arc stop. Reconcile git/PR/watcher state, then continue"
        [ -n "${NEXT:-}" ] && REASON="$REASON the next slice: $NEXT"
        REASON="$REASON. If the arc is genuinely finished, run \`autonomous-arc.sh complete $SID\` so this stops firing."
        python3 - "$REASON" <<'PY'
import sys, json
print(json.dumps({"decision": "block", "reason": sys.argv[1]}))
PY
        exit 0 ;;
    *)
        exit 0 ;;
esac
