#!/usr/bin/env bash
# bstack bootstrap — install all 25 Broomva Stack skills
set -e

AGENTS_DIR="${HOME}/.agents/skills"
CLAUDE_DIR="${HOME}/.claude/skills"

mkdir -p "$AGENTS_DIR" "$CLAUDE_DIR"

# skill-name:repo-name mapping (some skills share repos)
declare -A SKILL_REPOS=(
  [agentic-control-kernel]="broomva/agentic-control-kernel"
  [control-metalayer-loop]="broomva/control-metalayer"
  [harness-engineering-playbook]="broomva/harness-engineering-skill"
  [agent-consciousness]="broomva/control-metalayer"
  [knowledge-graph-memory]="broomva/control-metalayer"
  [prompt-library]="broomva/prompt-library"
  [symphony]="broomva/symphony"
  [symphony-forge]="broomva/symphony-forge"
  [autoany]="broomva/autoany"
  [deep-dive-research-orchestrator]="broomva/deep-dive-research-skill"
  [skills]="broomva/skills"
  [skills-showcase]="broomva/skills"
  [arcan-glass]="broomva/arcan-glass"
  [next-forge]="broomva/symphony-forge"
  [alkosto-wait-optimizer]="broomva/alkosto-wait-optimizer-skill"
  [content-creation]="broomva/bstack"
  [finance-substrate]="broomva/finance-substrate"
  [pre-mortem]="broomva/strategy-skills"
  [braindump]="broomva/strategy-skills"
  [morning-briefing]="broomva/strategy-skills"
  [drift-check]="broomva/strategy-skills"
  [strategy-critique]="broomva/strategy-skills"
  [stakeholder-update]="broomva/strategy-skills"
  [decision-log]="broomva/strategy-skills"
  [weekly-review]="broomva/strategy-skills"
)

ORDERED_SKILLS=(
  agentic-control-kernel control-metalayer-loop harness-engineering-playbook
  agent-consciousness knowledge-graph-memory prompt-library
  symphony symphony-forge autoany
  deep-dive-research-orchestrator skills skills-showcase
  arcan-glass next-forge
  alkosto-wait-optimizer content-creation finance-substrate
  pre-mortem braindump morning-briefing drift-check
  strategy-critique stakeholder-update decision-log weekly-review
)

installed=0
skipped=0
failed=0

echo "=== bstack bootstrap ==="
echo "Installing 25 Broomva Stack skills..."
echo ""

for skill in "${ORDERED_SKILLS[@]}"; do
  repo="${SKILL_REPOS[$skill]}"

  if [ -d "$AGENTS_DIR/$skill" ] && [ -f "$AGENTS_DIR/$skill/SKILL.md" ]; then
    echo "  [skip] $skill"
    skipped=$((skipped + 1))
  else
    echo "  [install] $skill ($repo)..."
    if npx skills add "$repo" 2>/dev/null; then
      installed=$((installed + 1))
    else
      echo "  [FAIL] $skill"
      failed=$((failed + 1))
    fi
  fi

  # Ensure claude symlink
  if [ -d "$AGENTS_DIR/$skill" ] && [ ! -e "$CLAUDE_DIR/$skill" ]; then
    ln -snf "$AGENTS_DIR/$skill" "$CLAUDE_DIR/$skill" 2>/dev/null || true
  fi
done

echo ""
echo "=== bstack bootstrap complete ==="
echo "  Installed: $installed | Skipped: $skipped | Failed: $failed"
echo "  Total: $((installed + skipped))/25"
[ "$failed" -gt 0 ] && echo "  Run 'bstack validate' to diagnose issues."
