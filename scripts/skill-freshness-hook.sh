#!/usr/bin/env bash
# bstack/scripts/skill-freshness-hook.sh — P7 Skill Freshness (Claude Code SessionStart).
#
# Nudges the user when installed skills are stale (>= 7d since the last update
# check). Never blocks; stdout from a SessionStart hook is injected as context.
# SHIPPED by bstack and DEPLOYED into each workspace by `bstack bootstrap`.
#
# Marker file: $BROOMVA_P7_HOME or ~/.config/broomva/p7/last-skill-update-check

set -uo pipefail

P7_HOME="${BROOMVA_P7_HOME:-$HOME/.config/broomva/p7}"
MARKER="$P7_HOME/last-skill-update-check"
THRESHOLD_DAYS="${BROOMVA_P7_THRESHOLD_DAYS:-7}"
THRESHOLD_SECS=$((THRESHOLD_DAYS * 86400))

now=$(date +%s)

stale=1
if [ -f "$MARKER" ]; then
  if [ "$(uname)" = "Darwin" ]; then
    last=$(stat -f %m "$MARKER" 2>/dev/null || echo 0)
  else
    last=$(stat -c %Y "$MARKER" 2>/dev/null || echo 0)
  fi
  [ $((now - last)) -lt "$THRESHOLD_SECS" ] && stale=0
fi

if [ "$stale" = "1" ]; then
  age="never"
  if [ -f "$MARKER" ]; then age="$(( (now - last) / 86400 ))d ago"; fi
  echo "[bstack P7] Skill freshness check overdue (last: $age)."
  echo "[bstack P7]   Refresh: npx skills update -g   then:  mkdir -p \"$P7_HOME\" && touch \"$MARKER\""
fi
exit 0
