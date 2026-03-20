#!/usr/bin/env bash
# bstack validate — check health of all 25 skills
set -e

AGENTS_DIR="${HOME}/.agents/skills"
CLAUDE_DIR="${HOME}/.claude/skills"

SKILLS=(agentic-control-kernel control-metalayer-loop harness-engineering-playbook agent-consciousness knowledge-graph-memory prompt-library symphony symphony-forge autoany deep-dive-research-orchestrator skills skills-showcase arcan-glass next-forge alkosto-wait-optimizer content-creation finance-substrate pre-mortem braindump morning-briefing drift-check strategy-critique stakeholder-update decision-log weekly-review)
LAYERS=("Foundation" "Foundation" "Foundation" "Memory" "Memory" "Memory" "Orchestration" "Orchestration" "Orchestration" "Research" "Research" "Research" "Design" "Design" "Platform" "Platform" "Platform" "Strategy" "Strategy" "Strategy" "Strategy" "Strategy" "Strategy" "Strategy" "Strategy")

healthy=0
missing=0
broken=0

printf "\n%-35s %-15s %-10s %s\n" "SKILL" "LAYER" "STATUS" "NOTES"
printf "%-35s %-15s %-10s %s\n" "---" "---" "---" "---"

for i in "${!SKILLS[@]}"; do
  skill="${SKILLS[$i]}"
  layer="${LAYERS[$i]}"
  dir=""
  status="MISSING"
  notes=""

  if [ -d "$AGENTS_DIR/$skill" ]; then
    dir="$AGENTS_DIR/$skill"
  elif [ -d "$CLAUDE_DIR/$skill" ]; then
    dir="$CLAUDE_DIR/$skill"
  fi

  if [ -n "$dir" ]; then
    if [ -f "$dir/SKILL.md" ]; then
      if head -20 "$dir/SKILL.md" | grep -q "^name:"; then
        status="OK"
        healthy=$((healthy + 1))
      else
        status="WARN"
        notes="Missing frontmatter"
        broken=$((broken + 1))
      fi
    else
      status="BROKEN"
      notes="No SKILL.md"
      broken=$((broken + 1))
    fi
  else
    missing=$((missing + 1))
  fi

  printf "%-35s %-15s %-10s %s\n" "$skill" "$layer" "$status" "$notes"
done

echo ""
echo "Health: $healthy/25 OK | $missing missing | $broken broken"
[ "$missing" -gt 0 ] && echo "Run: bash scripts/bootstrap.sh"

# ── PII Redaction Check ──────────────────────────────────────────────────────
echo ""
echo "=== PII Redaction ==="
BRIDGE="$(git rev-parse --show-toplevel 2>/dev/null)/scripts/conversation-history.py"
if [ -f "$BRIDGE" ]; then
  if grep -q "_redact_pii" "$BRIDGE"; then
    echo "  [ok] PII redaction active in conversation bridge"
  else
    echo "  [FAIL] _redact_pii() not found in conversation-history.py — S15 violated"
  fi
else
  echo "  [warn] conversation-history.py not found"
fi
