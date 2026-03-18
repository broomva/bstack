---
name: bstack
description: >
  The Broomva Stack — 24 curated agent skills forming a complete AI-native development
  workflow across 7 layers: Foundation (control, governance, harness), Memory (consciousness,
  knowledge graph, prompts), Orchestration (symphony, scaffold, EGRI), Research (deep dive,
  inventory, showcase), Design (Arcan Glass, Next.js templates), Platform (decision tools,
  content pipeline), Strategy (pre-mortem, braindump, morning-briefing, drift-check,
  strategy-critique, stakeholder-update, decision-log, weekly-review). Bootstrap any project
  with Broomva conventions, install all skills with one command, check status, validate health.
  Use when: (1) setting up a new project with Broomva conventions, (2) installing the full
  agent skills stack, (3) checking which skills are installed vs missing, (4) validating skill
  health, (5) user says "bstack", "broomva stack", "bootstrap project", "setup broomva
  workflow", "install all skills", "skills status".
---

# bstack — The Broomva Stack

24 agent skills across 7 layers for complete AI-native development.

## Preamble

Run this first to detect current state:

```bash
AGENTS_DIR="${HOME}/.agents/skills"
CLAUDE_DIR="${HOME}/.claude/skills"
ROSTER=(agentic-control-kernel control-metalayer-loop harness-engineering-playbook agent-consciousness knowledge-graph-memory prompt-library symphony symphony-forge autoany deep-dive-research-orchestrator skills skills-showcase arcan-glass next-forge alkosto-wait-optimizer content-creation pre-mortem braindump morning-briefing drift-check strategy-critique stakeholder-update decision-log weekly-review)
INSTALLED=0; MISSING=()
for s in "${ROSTER[@]}"; do
  if [ -d "$AGENTS_DIR/$s" ] || [ -d "$CLAUDE_DIR/$s" ]; then
    INSTALLED=$((INSTALLED + 1))
  else
    MISSING+=("$s")
  fi
done
echo "bstack: $INSTALLED/${#ROSTER[@]} skills installed (24 total)"
[ ${#MISSING[@]} -gt 0 ] && echo "Missing: ${MISSING[*]}"
```

Report the count. If all 16 present, say "bstack fully installed."
If any missing, list them and offer the `bootstrap` command.

## Commands

### `bootstrap` — Install all 24 skills

Run `scripts/bootstrap.sh` to install every skill in the roster.
Skips already-installed skills. Creates symlinks from `~/.claude/skills/` to `~/.agents/skills/`.

### `status` — Show installed vs missing

Re-run the preamble. For each skill show: name, layer, installed/missing.

### `validate` — Check health

Run `scripts/validate.sh`. Verifies each skill has a valid SKILL.md with proper frontmatter.

## Stack Layers

| Layer | Skills | Purpose |
|-------|--------|---------|
| Foundation | agentic-control-kernel, control-metalayer-loop, harness-engineering-playbook | Safety, governance, workflow |
| Memory | agent-consciousness, knowledge-graph-memory, prompt-library | Persistence across sessions |
| Orchestration | symphony, symphony-forge, autoany | Agent dispatch, scaffolding, self-improvement |
| Research | deep-dive-research-orchestrator, skills, skills-showcase | Multi-dim research, inventory |
| Design | arcan-glass, next-forge | Design system, production templates |
| Platform | alkosto-wait-optimizer, content-creation | Decision tools, content pipeline |
| Strategy | pre-mortem, braindump, morning-briefing, drift-check, strategy-critique, stakeholder-update, decision-log, weekly-review | Strategic thinking, decision intelligence, personal productivity |

For full descriptions, read `references/skills-roster.md`.
For architecture diagram, read `references/stack-architecture.md`.
For first-time setup, read `references/quickstart.md`.

## Browse

Full roster with install commands: https://broomva.tech/skills
