#!/usr/bin/env bash
# limit-stall-resume-hook.sh — Stop hook, LOG-ONLY calibration instrument for
# BRO-1700 disturbance #1 (a usage/rate-limit terminal error kills the arc and the
# printed reset time is never used; documented 4h13m + 5h29m of dead wall-clock).
#
# The LIVE auto-resume path is deferred on two open empirical questions: (a) the
# exact live rate-limit string is not reliably captured in the stored corpus, and
# (b) it is unconfirmed whether Claude Code even fires the Stop hook on a hard
# limit-kill. This instrument resolves (a): while an arc is active it scans the
# transcript tail for limit-ish tokens and, on a match, appends the excerpt to
# resume-dryrun.jsonl so the real string is captured for calibration. It NEVER
# schedules, sleeps, or acts — it is a data-collection probe only. Promote to the
# live scheduler once a genuine sample is logged. See BRO-1700, BRO-1698.
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
ARC_HELPER="$SELF_DIR/autonomous-arc.sh"
HOME_DIR="${BROOMVA_AUTONOMOUS_HOME:-$HOME/.config/broomva/autonomous}"
DRYRUN_LOG="$HOME_DIR/resume-dryrun.jsonl"
INPUT="$(cat 2>/dev/null || echo '{}')"

command -v python3 >/dev/null 2>&1 || exit 0
[ -x "$ARC_HELPER" ] || exit 0

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

# only observe during an active arc (the disturbance only matters mid-arc)
"$ARC_HELPER" active "$SID" >/dev/null 2>&1 || exit 0

TAIL="$(tail -n 40 "$TRANSCRIPT" 2>/dev/null || true)"
[ -n "$TAIL" ] || exit 0

# broad candidate match (calibration net — deliberately wide, since we do not yet
# know the exact live string). LOG-ONLY on a hit.
if printf '%s' "$TAIL" | grep -qiE '(usage|rate|weekly|hourly|5-hour)[ -]?limit|limit reached|temporarily disabled|resets? (at|in)|quota (exceeded|reached)'; then
    SLUG="$("$ARC_HELPER" status "$SID" 2>/dev/null | awk '{print $2}')"
    mkdir -p "$HOME_DIR" 2>/dev/null || true
    TAIL="$TAIL" SID="$SID" SLUG="${SLUG:-}" python3 - >>"$DRYRUN_LOG" 2>/dev/null <<'PY' || true
import os, json, datetime
excerpt = os.environ.get("TAIL", "")[:4000]
rec = {
    "ts": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
    "session_id": os.environ.get("SID", ""),
    "slug": os.environ.get("SLUG", ""),
    "action": "dry-run-only (never scheduled)",
    "candidate_excerpt": excerpt,
}
print(json.dumps(rec))
PY
fi
exit 0
