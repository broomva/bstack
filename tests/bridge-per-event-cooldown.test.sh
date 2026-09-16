#!/usr/bin/env bash
# bridge-per-event-cooldown.test.sh
#
# The bridge hook throttles on a stamp file. That stamp used to be ONE path
# shared by every event the hook is wired to, which made the throttle
# first-come ACROSS DIFFERENT EVENTS: Stop, SubagentStop and PreCompact all
# fire within seconds of each other, so whichever arrived first silently
# suppressed the rest.
#
# Measured on a workspace that wired SubagentStop: a subagent finishing
# displaced the session's own Stop receipt -- P1's primary artifact, and the
# record `doctor` reads to decide the loop is live. The event that mattered
# least won on timing alone. Because the hook exits 0 at the throttle it left
# no trace, which reads exactly like "the hook never fired".
#
# Case A is the fix and is RED before it. Case B is the reason the throttle
# exists and must stay green -- a fix that simply removed the cooldown would
# pass A and let a busy session write a record per event per second. C and D
# cover the payload the throttle now has to parse to key itself: a missing
# event name must not break capture, and an attacker-shaped one must not
# escape into a path.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="${HOOK:-$REPO/scripts/conversation-bridge-hook.sh}"

PASS=0; FAIL=0
ok()  { echo "  ok   $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL $1"; FAIL=$((FAIL+1)); }

WS="$(mktemp -d)"
trap 'rm -rf "$WS"' EXIT
CONV="$WS/docs/conversations/Conversations.md"

clear_stamps() { rm -f "$HOME"/.cache/bstack-bridge-stamp*; }
records() { [ -f "$CONV" ] && grep -c . "$CONV" 2>/dev/null || echo 0; }

fire() { # fire <event> [session]
  printf '{"session_id":"%s","transcript_path":"/dev/null","hook_event_name":"%s"}' \
    "${2:-s-$RANDOM}" "$1" | CLAUDE_PROJECT_DIR="$WS" bash "$HOOK" >/dev/null 2>&1
}

# --- A: different events inside the window must BOTH record -------------------
clear_stamps; rm -rf "$WS/docs"
fire SubagentStop; a1="$(records)"
fire Stop;         a2="$(records)"
if [ "$a1" -gt 0 ] && [ "$a2" -gt "$a1" ]; then
  ok "different events inside the window both record ($a1 -> $a2)"
else
  bad "a second event was swallowed by the first event's stamp ($a1 -> $a2)"
fi

# --- B: the SAME event twice inside the window must still be throttled --------
clear_stamps; rm -rf "$WS/docs"
fire Stop; b1="$(records)"
fire Stop; b2="$(records)"
if [ "$b1" -gt 0 ] && [ "$b2" -eq "$b1" ]; then
  ok "same event twice inside the window is still throttled ($b1 -> $b2)"
else
  bad "throttle no longer holds for a repeated event ($b1 -> $b2)"
fi

# --- C: a payload with no event name must not break capture ------------------
clear_stamps; rm -rf "$WS/docs"
printf '{"session_id":"no-event"}' | CLAUDE_PROJECT_DIR="$WS" bash "$HOOK" >/dev/null 2>&1
if [ "$(records)" -gt 0 ]; then
  ok "payload without hook_event_name still records"
else
  bad "payload without hook_event_name recorded nothing"
fi

# --- D: an event name must never escape into the stamp path ------------------
clear_stamps; rm -rf "$WS/docs"
fire '../../../../tmp/bstack-escape' esc
if [ -e "/tmp/bstack-escape" ]; then
  bad "a crafted hook_event_name created a stamp outside the cache dir"
else
  ok "crafted hook_event_name cannot escape the stamp path"
fi

clear_stamps
echo
echo "  $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
