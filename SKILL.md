---
name: bstack
description: |
  The Broomva Stack — thirteen irreducible primitives (P1–P13) that turn any
  agent-driven workspace into a self-operating system, plus 28 curated skills
  that ship with the stack. The primitives are not optional features; they are
  the substrate. P1 captures every session as episodic memory. P2 gates
  destructive operations. P3 tracks every work unit in Linear. P4 forces every
  change through CI. P5 isolates parallel agents in worktrees. P6 keeps the
  knowledge graph quality-controlled. P7 replaces sleep-on-CI with productive
  wait + classifier-evaluator self-heal. P8 nudges when installed skills go
  stale. P9 cleans up squash-merged branches and dead worktrees. P10 binds
  every agent to clean-tree discipline through the PR lifecycle. P11 is the
  cohesion glue — bind every agent to validate by interacting with what they
  build, not just by reasoning + lint + CI exit codes. Use bstack when:
  (1) bootstrapping a new agent-driven workspace, (2) verifying primitive
  compliance via `bstack doctor`, (3) repairing missing governance/hooks/policy
  via `bstack repair`, (4) listing installed-vs-missing skills via
  `bstack status`, (5) validating skill frontmatter health via `bstack
  validate`, (6) full reconfiguration via `bstack revamp`. Triggers on
  "bstack", "broomva stack", "bootstrap project", "setup broomva workflow",
  "install all skills", "skills status", "primitive contract", "P1" through
  "P11", "agent harness", "self-operating workspace".
---

# bstack — The Broomva Stack

**Thirteen irreducible primitives. Twenty-nine curated skills. One self-operating workspace.**

bstack is a *portable harness metalayer* — it composes existing skills into a binding primitive contract that the agent enforces by reasoning, the doctor enforces by checking, and the bootstrap enforces by scaffolding.

## Quick start

Install:
```bash
npx skills add broomva/bstack
```

Then, in your agent session:
```
/bstack bootstrap     → install 28 skills + scaffold governance + wire hooks + run doctor
/bstack doctor        → verify primitive contract compliance (always exits 0)
/bstack repair        → fix specific gaps surfaced by doctor (asks before writing)
/bstack status        → show which skills are installed vs missing
/bstack validate      → check skill SKILL.md frontmatter health
/bstack revamp        → full reconfiguration (force-reinstall + rewire + re-doctor)
```

## What bstack enforces

The twelve primitives. Each closes one specific failure mode that drifts into entropy in unsupervised sessions:

| # | Primitive | Closes |
|---|---|---|
| **P1** | Conversation Bridge | session amnesia |
| **P2** | Control Gate | destructive ops the model didn't authorize |
| **P3** | Linear Tickets | invisible work |
| **P4** | PR Pipeline | merging unreviewed code |
| **P5** | Parallel Agents | sequential bottleneck |
| **P6** | Knowledge Bookkeeping | knowledge graph rot |
| **P7** | CI Watcher + Productive Wait | sleep-on-CI |
| **P8** | Skill Freshness Check | silent rot of `npx skills add` snapshots |
| **P9** | Branch + Worktree Janitor | squash-merge accumulation |
| **P10** | Worktree Hygiene Discipline | dirty-tree drift across the PR lifecycle |
| **P11** | Empirical Feedback Loop | shipping code that compiles but doesn't work |
| **P12** | Persistent Loop Discipline (`broomva/persist` skill) | long-horizon work decaying as the context window rots |
| **P13** | Dream Cycle Discipline | tier-crossing consolidation corrupting upper-tier rules without replay (the *shadow dream* failure mode) |

Full reference: see [references/primitives.md](references/primitives.md).

## Preamble (run first, every session)

Detect skill installation state and update overdue skills.

```bash
# ─── Update check ────────────────────────────────────────────
_BSTACK_ROOT=""
[ -d "$HOME/.claude/skills/bstack" ] && _BSTACK_ROOT="$HOME/.claude/skills/bstack"
[ -z "$_BSTACK_ROOT" ] && [ -d "$HOME/.agents/skills/bstack" ] && _BSTACK_ROOT="$HOME/.agents/skills/bstack"
_UPD=""
if [ -n "$_BSTACK_ROOT" ] && [ -x "$_BSTACK_ROOT/bin/bstack-update-check" ]; then
  _UPD=$("$_BSTACK_ROOT/bin/bstack-update-check" 2>/dev/null || true)
fi
[ -n "$_UPD" ] && echo "$_UPD" || true
_BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
echo "BRANCH: $_BRANCH"
```

If output shows `UPGRADE_AVAILABLE <old> <new>`: read `bstack-upgrade/SKILL.md` and follow the inline upgrade flow. If `JUST_UPGRADED <from> <to>`: tell the user "Running bstack v{to}" and continue.

```bash
# ─── Skill roster check ──────────────────────────────────────
AGENTS_DIR="${HOME}/.agents/skills"
CLAUDE_DIR="${HOME}/.claude/skills"
ROSTER=(agentic-control-kernel control-metalayer-loop harness-engineering-playbook p9 agent-consciousness knowledge-graph-memory prompt-library symphony symphony-forge autoany deep-dive-research-orchestrator skills skills-showcase arcan-glass next-forge alkosto-wait-optimizer content-creation finance-substrate seo-llmeo brand-icons pre-mortem braindump morning-briefing drift-check strategy-critique stakeholder-update decision-log weekly-review)
INSTALLED=0; MISSING=()
for s in "${ROSTER[@]}"; do
  if [ -d "$AGENTS_DIR/$s" ] || [ -d "$CLAUDE_DIR/$s" ]; then
    INSTALLED=$((INSTALLED + 1))
  else
    MISSING+=("$s")
  fi
done
echo "bstack: $INSTALLED/${#ROSTER[@]} skills installed (28 total)"
[ ${#MISSING[@]} -gt 0 ] && echo "Missing: ${MISSING[*]}"
```

Report the count. If all 28 present, say "bstack fully installed." If any missing, list them and offer the `bootstrap` command.

After skill check, run the harness validation:

```bash
cd ~/broomva && make bstack-check 2>&1
```

Report results. If any checks fail, fix them before proceeding.

## Commands

### `bootstrap` — full install + system wire-up

`scripts/bootstrap.sh` is the install/wire path. It:

1. Installs all 28 skills via `npx skills add broomva/<skill>`
2. **Scaffolds missing governance files** from `assets/templates/`:
   - `CLAUDE.md` (workspace invariants + RCS hierarchy + primitive table)
   - `AGENTS.md` (operational rules + per-primitive sections + reflexive triggers)
   - `.control/policy.yaml` (ci_watch / ci_heal / auto_merge / gates G1–G11)
   - `.claude/settings.json` (P1, P2, P8 hook wiring)
3. Adds `make` targets to existing Makefile (or creates one): `bstack-check`, `control-audit`, `janitor`
4. Installs pre-commit hook (`.githooks/pre-commit`) via `git config core.hooksPath .githooks`
5. Runs `bstack doctor` to verify primitive contract compliance
6. Reports a *bootstrap receipt* — what was installed, what was scaffolded, what was already present

**Idempotent**: never overwrites existing user customizations. If a file already exists, the bootstrap appends only the missing primitive sections / blocks / hooks, never the whole file.

### `doctor` — verify primitive contract

`scripts/doctor.sh`. Seven check sections:

1. Governance files exist (CLAUDE.md, AGENTS.md, .control/policy.yaml)
2. CLAUDE.md primitives table has all P1–P13 rows + correct count header
3. AGENTS.md has each primitive section (`### P1:` through `### P11:`)
4. Reflexive Trigger Rules present for P6, P7, P10, P11, P12, P13 (the reasoning-enforced primitives)
5. `.control/policy.yaml` has required blocks (`ci_watch:`, `ci_heal:`, `auto_merge:`)
6. `.claude/settings.json` wires the expected hook scripts (P1, P2, P8)
7. Each primitive's mechanism is reachable on disk

Modes: default (full report), `--quiet` (only gaps), `--strict` (exit 1 on gap, for CI lanes). **Always exits 0 by default.** Each gap includes an actionable `→ fix:` hint.

`bootstrap` invokes `doctor --quiet` automatically as its final step.

### `repair` — apply targeted fixes

`scripts/repair.sh`. Reads the doctor's gap list, asks the user before each fix, then applies the specific repair (add missing primitive section from template, add missing policy block, wire missing hook). Idempotent. Never destructive.

### `status` — installed vs missing + harness health

Re-run the preamble. For each skill show: name, layer, installed/missing. Then run `make bstack-check` and report harness health.

### `validate` — skill frontmatter health

`scripts/validate.sh`. Verifies each skill has a valid SKILL.md with proper frontmatter. Then runs the full bstack-check harness validation.

### `revamp` — full agent reconfiguration

`scripts/revamp.sh`. Triggers complete workspace reconfiguration:

1. Reinstall all 28 skills (force mode)
2. Regenerate governance files from templates (asks before overwriting)
3. Rewire hooks (git pre-commit + Claude Code Stop/Notification/PreToolUse/SessionStart)
4. Force-run conversation bridge across all projects
5. Run full control audit
6. Update AGENTS.md with current state

## Stack layers (28 skills)

For the full skill roster + descriptions, see [references/skills-roster.md](references/skills-roster.md). For the layered architecture, see [references/stack-architecture.md](references/stack-architecture.md). For the full primitive contract with reflexive triggers, see [references/primitives.md](references/primitives.md).

## Metalayer integration

bstack is the *measurement substrate* for the agentic-control-kernel. The harness records:

| Metric | Target | How |
|--------|--------|-----|
| Skills installed | 28/28 | preamble roster |
| Governance files | 4/4 | CLAUDE.md, AGENTS.md, METALAYER.md, .control/policy.yaml |
| Hooks wired | 4/4 | Stop, PreToolUse safety, PreToolUse regression, pre-commit |
| Status line | active | `~/.claude/statusline-command.sh` |
| Bridge operational | fresh < 24h | `~/.cache/broomva-bridge-stamp` mtime |
| Control audit | 5/5 sections | `make control-audit` exit code |
| Conversations indexed | ≥1 session | `docs/conversations/Conversations.md` exists |
| **Primitive contract** | **11/11** | **`bstack doctor` exit code** |

## When to use bstack

- **Setting up a new project with Broomva conventions** → `bootstrap`
- **Validating an existing project meets the primitive contract** → `doctor`
- **Fixing a specific gap doctor reports** → `repair`
- **Checking skill freshness or roster completeness** → `status` or `validate`
- **Major workspace cleanup** → `revamp`

## Self-evolution

When the agent improves a primitive, the workflow is:

1. Pattern observed across multiple sessions (L3 stability budget; rapid changes destabilize the system)
2. Captured in conversation log (Stop hook — automatic via P1)
3. Crystallized in AGENTS.md (one PR, deliberate)
4. Enforced in `.control/policy.yaml` if mechanically gateable (one PR, deliberate)
5. Doctor extended to check the new rule (one PR, deliberate)
6. Future agents inherit the improvement

This is the f₃ dynamics function at L3 of the RCS hierarchy. See [references/primitives.md](references/primitives.md) for the formal stability constraint.

## See also

- [references/primitives.md](references/primitives.md) — full P1–P13 reference with reflexive triggers
- [references/skills-roster.md](references/skills-roster.md) — all 28 skills with install commands
- [references/stack-architecture.md](references/stack-architecture.md) — layer dependency diagram
- [references/quickstart.md](references/quickstart.md) — 5-minute install walkthrough
- [bstack-upgrade/SKILL.md](bstack-upgrade/SKILL.md) — version-upgrade flow
