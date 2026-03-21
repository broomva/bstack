#!/usr/bin/env bash
# bstack validate — check health of all 27 skills
set -e

AGENTS_DIR="${HOME}/.agents/skills"
CLAUDE_DIR="${HOME}/.claude/skills"

SKILLS=(agentic-control-kernel control-metalayer-loop harness-engineering-playbook agent-consciousness knowledge-graph-memory prompt-library symphony symphony-forge autoany deep-dive-research-orchestrator skills skills-showcase arcan-glass next-forge alkosto-wait-optimizer content-creation finance-substrate seo-llmeo brand-icons pre-mortem braindump morning-briefing drift-check strategy-critique stakeholder-update decision-log weekly-review)
LAYERS=("Foundation" "Foundation" "Foundation" "Memory" "Memory" "Memory" "Orchestration" "Orchestration" "Orchestration" "Research" "Research" "Research" "Design" "Design" "Platform" "Platform" "Platform" "Platform" "Platform" "Strategy" "Strategy" "Strategy" "Strategy" "Strategy" "Strategy" "Strategy" "Strategy")

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
echo "Health: $healthy/27 OK | $missing missing | $broken broken"
[ "$missing" -gt 0 ] && echo "Run: bash scripts/bootstrap.sh"

# ── PII Redaction Check ──────────────────────────────────────────────────────
echo ""
echo "=== PII Redaction ==="
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
BRIDGE="$REPO_ROOT/scripts/conversation-history.py"
if [ -f "$BRIDGE" ]; then
  if grep -q "_redact_pii" "$BRIDGE"; then
    echo "  [ok] PII redaction active in conversation bridge"
  else
    echo "  [FAIL] _redact_pii() not found in conversation-history.py — S15 violated"
  fi
else
  echo "  [warn] conversation-history.py not found"
fi

# ── Regression Gate Check ────────────────────────────────────────────────────
echo ""
echo "=== Regression Testing Gate (G11) ==="
if [ -x "$REPO_ROOT/scripts/regression-gate-hook.sh" ]; then
  echo "  [ok] regression-gate-hook.sh (executable)"
else
  echo "  [FAIL] regression-gate-hook.sh missing or not executable"
fi

if [ -f "$REPO_ROOT/scripts/regression-test-map.json" ]; then
  FEAT_COUNT=$(python3 -c "import json; print(len(json.load(open('$REPO_ROOT/scripts/regression-test-map.json')).get('features',{})))" 2>/dev/null || echo "0")
  if [ "$FEAT_COUNT" -gt 0 ]; then
    echo "  [ok] regression-test-map.json ($FEAT_COUNT features mapped)"
  else
    echo "  [warn] regression-test-map.json has 0 features — populate with file-pattern → scenario mappings"
  fi
else
  echo "  [FAIL] regression-test-map.json missing"
fi

CLAUDE_SETTINGS="$REPO_ROOT/.claude/settings.json"
if [ -f "$CLAUDE_SETTINGS" ] && grep -q "regression-gate-hook" "$CLAUDE_SETTINGS" 2>/dev/null; then
  echo "  [ok] regression hook wired in .claude/settings.json"
else
  echo "  [FAIL] regression hook not wired in .claude/settings.json"
fi

if grep -q "G11" "$REPO_ROOT/.control/policy.yaml" 2>/dev/null; then
  echo "  [ok] G11 gate defined in policy.yaml"
else
  echo "  [warn] G11 gate not in policy.yaml — add regression testing gate"
fi
