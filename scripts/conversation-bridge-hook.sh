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
# P6 CATALOG REFRESH is part of this chain (0.37.2, BRO-2021). It used to be a
# second Stop hook, scripts/knowledge-catalog-refresh-hook.sh — retired because
# it resolved bookkeeping.py WORKSPACE-relative while bookkeeping installs
# GLOBALLY (npx skills add -g → ~/.claude/skills, ~/.agents/skills), making it a
# silent no-op by construction on every workspace that did not vendor bookkeeping
# in-tree. Two Stop hooks with two cooldowns for one dependency chain was also
# one too many: the catalog must be regenerated AFTER the session is captured, so
# it belongs in this background chain, sequenced, not racing it.
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

# P6 catalog generator. Workspace-vendored copy first, then the GLOBAL install
# dirs that `npx skills add -g` actually writes to — the resolution the retired
# catalog hook lacked. Empty when bookkeeping is not installed: the chain then
# simply skips the step (never an error).
BOOKKEEPING=""
for _bk in "$REPO_ROOT/skills/bookkeeping/scripts/bookkeeping.py" \
           "$HOME/.claude/skills/bookkeeping/scripts/bookkeeping.py" \
           "$HOME/.agents/skills/bookkeeping/scripts/bookkeeping.py"; do
  [ -f "$_bk" ] && { BOOKKEEPING="$_bk"; break; }
done

# Prefer a richer bridge if the workspace ships one. The catalog refresh runs
# AFTER it in the same background subshell — the index must see the session the
# bridge just wrote, so this is a sequence, not two racing hooks.
BRIDGE="$REPO_ROOT/scripts/conversation-history.py"
if [ -f "$BRIDGE" ] && command -v python3 >/dev/null 2>&1; then
  (
    cd "$REPO_ROOT" || exit 0
    python3 "$BRIDGE" >/dev/null 2>&1
    [ -n "$BOOKKEEPING" ] && python3 "$BOOKKEEPING" index >/dev/null 2>&1
    exit 0
  ) &
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

# Same catalog refresh on the minimal-bridge path — the capability must not
# depend on which bridge the workspace happens to ship.
if [ -n "$BOOKKEEPING" ] && command -v python3 >/dev/null 2>&1; then
  ( cd "$REPO_ROOT" && python3 "$BOOKKEEPING" index >/dev/null 2>&1 ) &
  disown 2>/dev/null || true
fi
exit 0
