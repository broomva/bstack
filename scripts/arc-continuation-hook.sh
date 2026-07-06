#!/usr/bin/env bash
# arc-continuation-hook.sh — Stop hook. THE machine-checkable core of BRO-1700
# (loop-stall rejection, disturbances #3 "No response requested." and #4
# parent-never-resumes). When an autonomous arc is active and the agent's final
# turn is an UNAMBIGUOUS no-op terminal, returns a Stop-hook block decision so the
# harness continues the arc instead of parking on a dead turn.
#
# DESIGN: bias-to-safety. The two failure modes are asymmetric — a false positive
# (force-continue a legitimate stop) fights the user and burns tokens; a false
# negative (miss a stall) just costs one manual nudge (the status quo). So this
# blocks ONLY on the two unambiguous no-ops — an empty final turn, or the literal
# CC sentinel "No response requested." as the whole message — and accepts every
# false negative. Bounded by BOTH a consecutive cap (reconcile_count<2, reset on a
# productive turn) AND a lifetime cap (total_blocks<5, never reset) so an
# interleaved-trivial-tool loop still terminates. Honors CC's stop_hook_active.
#
# Transcript race (BRO-1616): CC flushes the final assistant entry ~125ms AFTER
# Stop fires, so a single read judges the PREVIOUS turn. We drain by IDENTITY —
# poll until an assistant entry with a *different* signature than the one present
# at hook start appears (not a timestamp window, which misfires on <2s-apart turns).
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
ARC_HELPER="$SELF_DIR/autonomous-arc.sh"
INPUT="$(cat 2>/dev/null || echo '{}')"
CONSEC_MAX=2
LIFE_MAX=5

command -v python3 >/dev/null 2>&1 || exit 0
[ -x "$ARC_HELPER" ] || exit 0

# session_id (l1) + transcript_path (l2) + stop_hook_active (l3)
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
"$ARC_HELPER" active "$SID" >/dev/null 2>&1 || exit 0   # arc active + not stale

VERDICT="$(python3 - "$TRANSCRIPT" <<'PY'
import sys, json, re, time, os, hashlib

path = sys.argv[1]
BUDGET = float(os.environ.get("ARC_DRAIN_MS", "1200")) / 1000.0
INTERVAL = 0.05

SENTINEL_RE = re.compile(r"^\s*no response requested[.\s]*$", re.I)   # whole-message only
# completion-DOMINANT (leading-anchored) — broad enough to catch natural phrasings
# but never a mid-arc mention ("the first task is complete, moving on" won't match).
COMPLETE_RE = re.compile(
    r"^\s*(the\s+)?(arc (is )?complete|arc[- ]done|"
    r"all milestones?\b.{0,60}?\b(shipped|done|complete)|milestones? complete|"
    r"task complete|all done|everything(?:'s| is)?\s+(?:shipped|done|merged|complete))\b", re.I)

def last_assistant(p):
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

def sig(entry):
    if entry is None:
        return None
    for k in ("uuid", "id", "messageId", "requestId"):
        v = entry.get(k)
        if v:
            return "id:" + str(v)
    msg = entry.get("message") if isinstance(entry.get("message"), dict) else entry
    body = json.dumps(msg.get("content"), sort_keys=True, default=str)[:800]
    return "h:" + hashlib.md5(body.encode()).hexdigest()

# DRAIN by identity: wait for an entry whose signature differs from the one present
# at hook start (the freshly-flushed final turn). If none appears (already flushed),
# fall through at budget and classify whatever is last.
sig0 = sig(last_assistant(path))
entry = last_assistant(path)
waited = 0.0
while waited < BUDGET:
    entry = last_assistant(path)
    if sig(entry) != sig0:
        break
    time.sleep(INTERVAL); waited += INTERVAL

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
    print("PRODUCTIVE")                      # tool call = progress
elif COMPLETE_RE.match(text):
    print("COMPLETE")                        # finished (completion-dominant) → release
elif (not text) or SENTINEL_RE.match(text):
    print("BLOCK")                           # unambiguous no-op → continue the arc
else:
    print("PRODUCTIVE")                      # substantive text = a healthy yield
PY
)"

case "$VERDICT" in
    PRODUCTIVE)
        # reset the CONSECUTIVE counter only outside a hook-driven continuation chain,
        # so an interleaved trivial tool_use cannot keep a forced loop alive.
        [ "${STOP_ACTIVE:-0}" = "1" ] || "$ARC_HELPER" reset "$SID" reconcile_count >/dev/null 2>&1 || true
        exit 0 ;;
    COMPLETE)
        "$ARC_HELPER" complete "$SID" >/dev/null 2>&1 || true   # auto-release
        exit 0 ;;
    BLOCK)
        [ "$("$ARC_HELPER" try-block "$SID" "$CONSEC_MAX" "$LIFE_MAX" 2>/dev/null)" = "BLOCK" ] || exit 0
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
