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
echo "Phase 1: Reinstalling all 24 skills..."
echo ""

AGENTS_DIR="${HOME}/.agents/skills"
CLAUDE_DIR="${HOME}/.claude/skills"

declare -A REPOS=(
  [broomva/agentic-control-kernel]=1
  [broomva/control-metalayer]=1
  [broomva/harness-engineering-skill]=1
  [broomva/prompt-library]=1
  [broomva/symphony]=1
  [broomva/symphony-forge]=1
  [broomva/autoany]=1
  [broomva/deep-dive-research-skill]=1
  [broomva/skills]=1
  [broomva/arcan-glass]=1
  [broomva/alkosto-wait-optimizer-skill]=1
  [broomva/bstack]=1
  [broomva/finance-substrate]=1
  [broomva/strategy-skills]=1
)

installed=0
failed=0

for repo in "${!REPOS[@]}"; do
  echo "  [reinstall] $repo"
  if npx skills add "$repo" -y -g >/dev/null 2>&1; then
    installed=$((installed + 1))
  else
    echo "    [FAIL] $repo"
    failed=$((failed + 1))
  fi
done

echo ""
echo "  Repos processed: $installed OK, $failed failed"
echo ""

# Ensure all Claude symlinks
for skill_dir in "$AGENTS_DIR"/*/; do
  skill=$(basename "$skill_dir")
  if [ ! -e "$CLAUDE_DIR/$skill" ]; then
    ln -snf "$skill_dir" "$CLAUDE_DIR/$skill" 2>/dev/null || true
  fi
done

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
