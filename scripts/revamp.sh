#!/usr/bin/env bash
# bstack revamp — full workspace reconfiguration.
#
# Reinstalls all skills (force mode), regenerates governance artifacts,
# rewires hooks, force-runs conversation bridge, and validates everything.
#
# Usage: bash scripts/revamp.sh [TARGET_DIR]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:-$(git rev-parse --show-toplevel 2>/dev/null || echo "$HOME/broomva")}"

echo "========================================="
echo "  bstack revamp — full reconfiguration"
echo "  Target: $TARGET"
echo "========================================="
echo ""

# ── Phase 1: Reinstall all skills ────────────────────────────────────────────
# Force-reinstall the companion-skills roster via bin/bstack-skills (--all). Single
# source of truth = references/companion-skills.yaml; there is NO hardcoded repo map
# here anymore — the old one pointed at repos deleted in BRO-1602 and 404'd every
# `bstack revamp` (same defect as bootstrap.sh; BRO-1632). Each entry installs as
# `npx skills add broomva/skills --skill <name> -g`.
echo "Phase 1: Reinstalling the companion-skills roster (force)..."
echo ""

if bash "$SCRIPT_DIR/../bin/bstack-skills" install --all; then
  echo ""
  echo "=== skill reinstall complete ==="
else
  echo ""
  echo "=== skill reinstall reported failures — run 'bstack skills status' ==="
fi
echo ""

# ── Phase 2: Wire control harness ────────────────────────────────────────────
echo "Phase 2: Wiring control harness..."
echo ""

bash "$SCRIPT_DIR/postinstall.sh" "$TARGET"
echo ""

# ── Phase 3: Force-run conversation bridge ───────────────────────────────────
echo "Phase 3: Running conversation bridge..."
echo ""

if [ -f "$TARGET/scripts/conversation-history.py" ] && command -v python3 >/dev/null 2>&1; then
  (cd "$TARGET" && python3 scripts/conversation-history.py --force 2>&1) || true
  echo "  [ok] Conversation bridge completed"
else
  echo "  [skip] No conversation bridge script found"
fi

# Also run for sub-projects
for project in \
  "$TARGET/core/life" \
  "$TARGET/core/symphony" \
  "$TARGET/core/autoany" \
  "$TARGET/core/agentic-control-kernel" \
  "$TARGET/apps/chatOS" \
  "$TARGET/apps/symphony-cloud" \
  "$TARGET/apps/mission-control" \
  "$TARGET/apps/healthOS"; do
  if [ -f "$project/scripts/conversation-history.py" ]; then
    echo "  [bridge] $(basename "$(dirname "$project")")/$(basename "$project")"
    (cd "$project" && python3 scripts/conversation-history.py 2>&1) || true
  fi
done
echo ""

# ── Phase 4: Validate ────────────────────────────────────────────────────────
echo "Phase 4: Running full validation..."
echo ""

if [ -f "$TARGET/Makefile" ] && grep -q "bstack-check" "$TARGET/Makefile" 2>/dev/null; then
  (cd "$TARGET" && make bstack-check 2>&1)
else
  echo "  [warn] No bstack-check target in Makefile — running skill validation only"
  bash "$SCRIPT_DIR/validate.sh"
fi

echo ""
echo "========================================="
echo "  bstack revamp complete"
echo "========================================="
