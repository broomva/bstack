#!/usr/bin/env bash
# bstack/scripts/knowledge-catalog-refresh-hook.sh — P6 catalog refresh (Stop hook).
#
# Regenerates the dense, LLM-loadable knowledge index (the catalog that routes
# `/kg load`) after a session. SHIPPED by bstack and DEPLOYED into each workspace
# by `bstack bootstrap`. Self-contained + graceful: runs the bookkeeping index
# only if bookkeeping is installed; otherwise no-ops. Non-blocking, cooldown-
# throttled, always exit 0.

set -uo pipefail

REPO_ROOT="${CLAUDE_PROJECT_DIR:-${BROOMVA_WORKSPACE:-$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")}}"
STAMP="${HOME}/.cache/bstack-catalog-stamp"
COOLDOWN="${BSTACK_CATALOG_COOLDOWN:-300}"

now=$(date +%s)
if [ -f "$STAMP" ]; then
  if [ "$(uname)" = "Darwin" ]; then last=$(stat -f %m "$STAMP" 2>/dev/null || echo 0); else last=$(stat -c %Y "$STAMP" 2>/dev/null || echo 0); fi
  [ $((now - last)) -lt "$COOLDOWN" ] && exit 0
fi

BOOKKEEPING="$REPO_ROOT/skills/bookkeeping/scripts/bookkeeping.py"
if [ -f "$BOOKKEEPING" ] && command -v python3 >/dev/null 2>&1; then
  mkdir -p "$(dirname "$STAMP")"; touch "$STAMP"
  ( cd "$REPO_ROOT" && python3 "$BOOKKEEPING" index >/dev/null 2>&1 ) &
  disown 2>/dev/null || true
fi
exit 0
