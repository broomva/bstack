#!/usr/bin/env bash
# bstack/scripts/conversation-bridge-hook.sh — P1 Conversation Bridge (Stop hook).
#
# Captures the session to the workspace knowledge graph. SHIPPED by bstack and
# DEPLOYED into each workspace by `bstack bootstrap`. Self-contained + graceful:
#   - if a richer bridge (scripts/conversation-history.py) is present, run it;
#   - otherwise write a minimal session stamp to docs/conversations/ so a fresh
#     workspace still captures something.
# Non-blocking, cooldown-throttled, always exit 0.
#
# Claude Code Stop protocol: stdin { "transcript_path", "session_id", ... }.

set -uo pipefail

REPO_ROOT="${CLAUDE_PROJECT_DIR:-${BROOMVA_WORKSPACE:-$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")}}"
STAMP="${HOME}/.cache/bstack-bridge-stamp"
COOLDOWN="${BSTACK_BRIDGE_COOLDOWN:-120}"

# cooldown
now=$(date +%s)
if [ -f "$STAMP" ]; then
  if [ "$(uname)" = "Darwin" ]; then last=$(stat -f %m "$STAMP" 2>/dev/null || echo 0); else last=$(stat -c %Y "$STAMP" 2>/dev/null || echo 0); fi
  [ $((now - last)) -lt "$COOLDOWN" ] && exit 0
fi
mkdir -p "$(dirname "$STAMP")"; touch "$STAMP"

# Prefer a richer bridge if the workspace ships one.
BRIDGE="$REPO_ROOT/scripts/conversation-history.py"
if [ -f "$BRIDGE" ] && command -v python3 >/dev/null 2>&1; then
  ( cd "$REPO_ROOT" && python3 "$BRIDGE" >/dev/null 2>&1 ) &
  disown 2>/dev/null || true
  exit 0
fi

# Minimal fallback: append a session stamp to docs/conversations/Conversations.md
INPUT="$(cat 2>/dev/null || echo '{}')"
CONV_DIR="$REPO_ROOT/docs/conversations"
mkdir -p "$CONV_DIR" 2>/dev/null || exit 0
if command -v python3 >/dev/null 2>&1; then
  python3 - "$INPUT" "$CONV_DIR/Conversations.md" <<'PYEOF' 2>/dev/null || true
import sys, json, time
raw = sys.argv[1] if len(sys.argv) > 1 else "{}"
out = sys.argv[2]
try:
    data = json.loads(raw)
except Exception:
    data = {}
sid = data.get("session_id", "unknown")
ts = time.strftime("%Y-%m-%d %H:%M:%S")
with open(out, "a") as f:
    f.write(f"- {ts} — session {sid} (bstack minimal bridge; install knowledge-graph-memory for full capture)\n")
PYEOF
fi
exit 0
