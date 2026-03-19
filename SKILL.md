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

**Installing bstack is not just a skill install — it activates the full control harness.**

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

Report the count. If all 24 present, say "bstack fully installed."
If any missing, list them and offer the `bootstrap` command.

After skill check, run the harness validation:

```bash
cd ~/broomva && make bstack-check 2>&1
```

Report results. If any checks fail, fix them before proceeding.

## Commands

### `bootstrap` — Install all 24 skills + activate control harness

Run `scripts/bootstrap.sh` to install every skill in the roster.
Skips already-installed skills. Creates symlinks from `~/.claude/skills/` to `~/.agents/skills/`.

**After skill install, bootstrap ALSO:**
1. Verifies governance files exist (CLAUDE.md, AGENTS.md, METALAYER.md, `.control/policy.yaml`)
2. Installs pre-commit hooks via `git config core.hooksPath .githooks`
3. Validates Claude Code hooks in `.claude/settings.json` (Stop + Notification for conversation bridge)
4. Runs conversation bridge to ensure knowledge graph indexing is active
5. Runs `make control-audit` to verify full compliance
6. Reports bstack-check results

### `status` — Show installed vs missing + harness health

Re-run the preamble. For each skill show: name, layer, installed/missing.
Then run `make bstack-check` and report the harness health status.

### `validate` — Full health check

Run `scripts/validate.sh`. Verifies each skill has a valid SKILL.md with proper frontmatter.
Then run the full bstack-check harness validation.

### `revamp` — Full agent reconfiguration

Triggers a complete workspace reconfiguration:
1. Reinstall all 24 skills (force mode)
2. Regenerate governance files from templates
3. Rewire hooks (git pre-commit + Claude Code Stop/Notification)
4. Force-run conversation bridge across all 9 projects
5. Run full control audit
6. Update AGENTS.md with current state

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

## Metalayer Integration

bstack is not just skills — it is the **measurement substrate** for the agentic-control-kernel.

### What bstack Measures

| Metric | Target | How |
|--------|--------|-----|
| Skills installed | 24/24 | Preamble roster check |
| Governance files | 5/5 | CLAUDE.md, AGENTS.md, METALAYER.md, .control/policy.yaml, schemas/ |
| Hooks wired | 3/3 | Stop hook, Notification hook, pre-commit hook |
| Bridge operational | fresh < 120s | `~/.cache/broomva-bridge-stamp` mtime check |
| Control audit | 5/5 sections | `make control-audit` exit code |
| Conversations indexed | ≥1 session | `docs/conversations/Conversations.md` exists with entries |

### Self-Improvement Loop

```
bstack install
  → skills registered (24/24)
  → hooks wired (conversation capture active)
  → control audit passing
  → every session captured to knowledge graph
  → agent reads prior sessions on next start
  → agent discovers better patterns
  → agent proposes governance updates
  → bstack validates the update (control audit)
  → improvement promoted (AGENTS.md / policy.yaml updated)
  → next agent inherits the improvement
```

This is the EGRI loop at the workspace level:
- **Mutable artifact**: Agent behavior, AGENTS.md rules, policy gates
- **Immutable evaluator**: `make bstack-check` (24 skills + 5 governance + 3 hooks + bridge + audit)
- **Promotion policy**: Changes that pass all checks get committed

## Browse

Full roster with install commands: https://broomva.tech/skills
For architecture diagram, read `references/stack-architecture.md`.
For first-time setup, read `references/quickstart.md`.
