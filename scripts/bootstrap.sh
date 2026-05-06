#!/usr/bin/env bash
# bstack bootstrap — install all 28 Broomva Stack skills + scaffold governance + wire hooks
#
# Three phases:
#   1. Skill install: npx skills add for each ROSTER entry
#   2. Workspace scaffold: install missing CLAUDE.md / AGENTS.md / .control/policy.yaml
#      from assets/templates/ (idempotent — never overwrites existing files)
#   3. Hooks wire-up: merge bstack hooks into .claude/settings.json (additive only)
#
# After completion, runs `bstack doctor --quiet` to verify primitive contract.
set -e

AGENTS_DIR="${HOME}/.agents/skills"
CLAUDE_DIR="${HOME}/.claude/skills"
WORKSPACE_DIR="${BROOMVA_WORKSPACE:-$PWD}"

mkdir -p "$AGENTS_DIR" "$CLAUDE_DIR"

# skill-name:repo-name mapping (some skills share repos)
declare -A SKILL_REPOS=(
  [agentic-control-kernel]="broomva/agentic-control-kernel"
  [control-metalayer-loop]="broomva/control-metalayer"
  [harness-engineering-playbook]="broomva/harness-engineering-skill"
  [p9]="broomva/p9"
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
  [wealth-management]="broomva/wealth-management"
  [investment-management]="broomva/investment-management"
  [seo-llmeo]="aaron-he-zhu/seo-geo-claude-skills@technical-seo-checker"
  [brand-icons]="broomva/bstack"
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
  agentic-control-kernel control-metalayer-loop harness-engineering-playbook p9
  agent-consciousness knowledge-graph-memory prompt-library
  symphony symphony-forge autoany
  deep-dive-research-orchestrator skills skills-showcase
  arcan-glass next-forge
  alkosto-wait-optimizer content-creation finance-substrate wealth-management investment-management seo-llmeo brand-icons
  pre-mortem braindump morning-briefing drift-check
  strategy-critique stakeholder-update decision-log weekly-review
)

installed=0
skipped=0
failed=0

echo "=== bstack bootstrap ==="
echo "Installing 30 Broomva Stack skills..."
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
echo "=== bstack skills install complete ==="
echo "  Installed: $installed | Skipped: $skipped | Failed: $failed"
echo "  Total: $((installed + skipped))/30"
[ "$failed" -gt 0 ] && echo "  Run 'bstack validate' to diagnose issues."

# ─── Phase 2: scaffold missing governance files ────────────────────────────
# Idempotent: never overwrites existing files. Only installs when absent.
echo ""
echo "=== bstack governance scaffold ==="
BOOTSTRAP_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_ROOT="$(cd "$BOOTSTRAP_SCRIPT_DIR/.." && pwd)"
TEMPLATES_DIR="$SKILL_ROOT/assets/templates"
WORKSPACE_NAME="$(basename "$WORKSPACE_DIR")"

scaffolded=0
preserved=0

scaffold_governance_file() {
    local target="$1"
    local template="$2"
    if [ -f "$WORKSPACE_DIR/$target" ]; then
        echo "  [keep] $target (existing — preserved)"
        preserved=$((preserved + 1))
        return
    fi
    if [ ! -f "$TEMPLATES_DIR/$template" ]; then
        echo "  [skip] $target (template missing in skill: $template)"
        return
    fi
    mkdir -p "$WORKSPACE_DIR/$(dirname "$target")"
    sed "s/{{WORKSPACE_NAME}}/$WORKSPACE_NAME/g" \
        "$TEMPLATES_DIR/$template" > "$WORKSPACE_DIR/$target"
    echo "  [scaffold] $target ← assets/templates/$template"
    scaffolded=$((scaffolded + 1))
}

scaffold_governance_file "CLAUDE.md" "CLAUDE.md.template"
scaffold_governance_file "AGENTS.md" "AGENTS.md.template"
scaffold_governance_file ".control/policy.yaml" "policy.yaml.template"

echo "  scaffolded: $scaffolded | preserved: $preserved"

# ─── Phase 3: wire missing hooks into .claude/settings.json ────────────────
# Idempotent: never overwrites existing hook entries. Only adds missing ones.
echo ""
echo "=== bstack hooks wire-up ==="
SETTINGS_FILE="$WORKSPACE_DIR/.claude/settings.json"
if [ ! -f "$SETTINGS_FILE" ]; then
    echo "  [scaffold] .claude/settings.json ← assets/templates/settings.json.snippet"
    mkdir -p "$WORKSPACE_DIR/.claude"
    sed "s|\${BROOMVA_WORKSPACE}|$WORKSPACE_DIR|g" \
        "$TEMPLATES_DIR/settings.json.snippet" > "$SETTINGS_FILE"
elif command -v python3 >/dev/null 2>&1; then
    # Use python3 to merge missing hooks without overwriting existing ones
    python3 - <<PYEOF
import json
import sys
from pathlib import Path

settings_path = Path("$SETTINGS_FILE")
template_path = Path("$TEMPLATES_DIR/settings.json.snippet")
workspace = "$WORKSPACE_DIR"

current = json.loads(settings_path.read_text())
template = json.loads(template_path.read_text().replace("\${BROOMVA_WORKSPACE}", workspace))

# Drop the _comment if present
template.pop("_comment", None)

current.setdefault("hooks", {})
added = 0
for event, blocks in template.get("hooks", {}).items():
    current_blocks = current["hooks"].setdefault(event, [])
    for block in blocks:
        for hook in block.get("hooks", []):
            cmd = hook.get("command")
            # Check if any existing hook for this event references the same script
            already = any(
                any(h.get("command", "").endswith(Path(cmd).name)
                    for h in cb.get("hooks", []))
                for cb in current_blocks
            )
            if already:
                print(f"  [keep] {event}: {Path(cmd).name} (already wired)")
            else:
                # Append a new block for this hook
                new_block = {"hooks": [hook]}
                if "matcher" in block:
                    new_block["matcher"] = block["matcher"]
                current_blocks.append(new_block)
                print(f"  [wire] {event}: {Path(cmd).name} (P{hook.get('_bstack_primitive', '?')})")
                added += 1

settings_path.write_text(json.dumps(current, indent=2) + "\n")
print(f"  added: {added} new hook(s)")
PYEOF
else
    echo "  [skip] python3 not available; cannot merge into existing settings.json"
    echo "  manual: see assets/templates/settings.json.snippet"
fi

# ─── Phase 4: bstack doctor verification ───────────────────────────────────
# Always-active step; never blocks. Surfaces gaps in AGENTS.md / CLAUDE.md /
# .control/policy.yaml compliance with the bstack primitive contract.
DOCTOR_SCRIPT="${BOOTSTRAP_SCRIPT_DIR}/doctor.sh"
if [ -f "$DOCTOR_SCRIPT" ]; then
  echo ""
  echo "=== bstack doctor (primitive contract) ==="
  BROOMVA_WORKSPACE="$WORKSPACE_DIR" bash "$DOCTOR_SCRIPT" --quiet || true
fi

# --- Arcan skill sync ---
# If .arcan/ exists (Arcan agent is initialized), sync skills into .arcan/skills/
ARCAN_DIR="${PWD}/.arcan"
if [ -d "$ARCAN_DIR" ]; then
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  if [ -f "${SCRIPT_DIR}/arcan-skills-sync.sh" ]; then
    echo ""
    bash "${SCRIPT_DIR}/arcan-skills-sync.sh" "$ARCAN_DIR"
  fi
elif [ -d "${HOME}/.agents/skills/bstack/scripts" ]; then
  SYNC_SCRIPT="${HOME}/.agents/skills/bstack/scripts/arcan-skills-sync.sh"
  if [ -f "$SYNC_SCRIPT" ] && [ -d "$ARCAN_DIR" ]; then
    echo ""
    bash "$SYNC_SCRIPT" "$ARCAN_DIR"
  fi
fi
