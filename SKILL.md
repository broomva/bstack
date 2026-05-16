---
name: bstack
description: |
  The Broomva Stack — twenty irreducible primitives (P1–P20) that turn any
  agent-driven workspace into a self-operating system, plus 30 curated skills
  that ship with the stack. The primitives are not optional features; they are
  the substrate. P1 captures every session as episodic memory. P2 gates
  destructive operations. P3 tracks every work unit in Linear. P4 forces every
  change through CI. P5 isolates parallel agents in worktrees. P6 keeps the
  knowledge graph quality-controlled. P7 nudges when installed skills go
  stale. P8 cleans up squash-merged branches and dead worktrees. P9 is the
  productive-wait optimizer (`broomva/p9` skill — name matches primitive
  number) — drains a context-scoped queue while a blocking operation (PR
  CI, deploy, build, long index) runs; classifier + evaluator self-heal
  red CI is the reference implementation. P10 binds every agent to
  clean-tree discipline through the PR lifecycle. P11 is the cohesion glue —
  bind every agent to validate by interacting with what they build, not just
  by reasoning + lint + CI exit codes. P12 is the long-horizon discipline —
  cross-context restart loop. P13 is the dream-cycle discipline — replay
  against frozen substrate before consolidating. P14 binds every agent to
  enumerate the dep-chain explicitly before write. P15 binds every plan to a
  fresh state snapshot. P16 is the meta-primitive — the rule-of-three
  crystallization loop that produces every other primitive. P17 routes every
  substantive user input through a typed lens (`role/x` intake) — domain
  context loads from `roles/<name>.md` registry, P5 fan-out becomes a typed
  graph. P18 binds the format of every produced documentation artifact to
  its audience — markdown for LLM-loaded surfaces, HTML for human deliverables
  (specs, plans, reports, design exploration). P19 names the autonomous-
  continuation family (the 2×2 of /goal | P9 watcher | /loop | P12 persist)
  and the selection discipline — pick the right mechanism for the work
  shape; compose dynamically; never return control mid-arc when a mechanism
  would keep it closed. P20 is the cross-model adversarial review gate —
  the writer cannot be the final judge; before substantive PRs merge, fire
  a different-evaluator gate (Codex CLI cross-vendor / fresh subagent /
  composed adversarial-review skills); anti-slop ≥7/10, max 3 fix rounds,
  verdict logged in PR (skill: broomva/cross-review). The canonical mode
  of operation on top of the substrate is broomva/autonomous (Layer-1
  operating mode that fires every reflex without further prompting). Use
  bstack when:
  (1) bootstrapping a new agent-driven workspace, (2) verifying primitive
  compliance via `bstack doctor`, (3) repairing missing governance/hooks/
  policy via `bstack repair`, (4) listing installed-vs-missing skills via
  `bstack status`, (5) validating skill frontmatter health via
  `bstack validate`, (6) full reconfiguration via `bstack revamp`. Triggers
  on "bstack", "broomva stack", "bootstrap project", "setup broomva
  workflow", "install all skills", "skills status", "primitive contract",
  "P1" through "P20", "agent harness", "self-operating workspace".
---

# bstack — The Broomva Stack

**Twenty irreducible primitives. Thirty curated skills. One canonical operating mode. One self-operating workspace.**

bstack is a *portable harness metalayer* — it composes existing skills into a binding primitive contract that the agent enforces by reasoning, the doctor enforces by checking, the bootstrap enforces by scaffolding, and the **canonical operating mode** (`broomva/autonomous`) enforces in execution.

## Substrate vs Mode

bstack ships two complementary layers:

- **Substrate** (this skill, `/bstack`): the 20 primitives + 30 skills + governance + hooks + `.control/policy.yaml`. This is what `/bstack bootstrap` installs. The substrate is the *capability* — what's available in the workspace.
- **Mode** (`broomva/autonomous`): the canonical *behavior* that runs on top of the substrate. When the user says "go" / "proceed" / "be autonomous", `/autonomous` fires the 20-reflex pipeline that uses every primitive in sequence.

Installing the substrate without the mode = the workspace has primitives but no entry point to engage them. Invoking the mode without the substrate = wishful thinking. Compounded: `/bstack bootstrap` installs the substrate, then `/autonomous` is the standing operating mode for substantive work units.

## Quick start

Install:
```bash
npx skills add broomva/bstack
```

Then, in your agent session:
```
/bstack bootstrap     → install 30 skills + scaffold governance + wire hooks + run doctor
/bstack doctor        → verify primitive contract compliance (always exits 0)
/bstack repair        → fix specific gaps surfaced by doctor (asks before writing)
/bstack status        → show which skills are installed vs missing
/bstack validate      → check skill SKILL.md frontmatter health
/bstack revamp        → full reconfiguration (force-reinstall + rewire + re-doctor)
```

## What bstack enforces

The twenty primitives. Each closes one specific failure mode that drifts into entropy in unsupervised sessions:

| # | Primitive | Closes |
|---|---|---|
| **P1** | Conversation Bridge | session amnesia |
| **P2** | Control Gate | destructive ops the model didn't authorize |
| **P3** | Linear Tickets | invisible work |
| **P4** | PR Pipeline | merging unreviewed code |
| **P5** | Parallel Agents | sequential bottleneck |
| **P6** | Knowledge Bookkeeping | knowledge graph rot |
| **P7** | Skill Freshness Check | silent rot of `npx skills add` snapshots |
| **P8** | Branch + Worktree Janitor | squash-merge accumulation |
| **P9** | CI Watcher + Productive Wait (`broomva/p9` skill — name matches number) | sleep-on-wait dead time (CI, deploys, builds — PR CI is the reference impl) |
| **P10** | Worktree Hygiene Discipline | dirty-tree drift across the PR lifecycle |
| **P11** | Empirical Feedback Loop | shipping code that compiles but doesn't work |
| **P12** | Persistent Loop Discipline (`broomva/persist` skill) | long-horizon work decaying as the context window rots |
| **P13** | Dream Cycle Discipline | tier-crossing consolidation corrupting upper-tier rules without replay (the *shadow dream* failure mode) |
| **P14** | Dependency-Chain Reasoning Discipline | "think deeply through chain of dependencies" becoming a ritual phrase without concrete upstream/downstream enumeration |
| **P15** | State-Snapshot Before Action | plans built on stale state (uncommitted work, in-flight PRs, stale deploys) |
| **P16** | Crystallization Discipline (the Bstack Engine) | recurring valuable patterns living only in the user's head — never promoted to skill/primitive/policy infrastructure |
| **P17** | Lens-Routed Request Articulation (`broomva/role-x` skill, planned) | flat-dispatch fan-out failing to load domain context; agents performing tasks without the typed lens (legal review vs design vs research) that shapes the correct quality_bar |
| **P18** | Format-Follows-Audience Discipline | markdown-by-default for everything regardless of audience; long specs nobody reads; ASCII pseudo-diagrams + unicode-color-approximation when SVG-in-HTML is the correct primitive |
| **P19** | Orchestration-Mechanism Selection Discipline | implicit between-reflex handoffs ("continue please"); using wrong mechanism for work shape (/goal on >1h work, persist on 30-min task, /loop on event wait); the autonomous arc broken by missing-mechanism failure |
| **P20** | Cross-Model Adversarial Review Gate (`broomva/cross-review` skill) | same-model echo chamber; writer self-validates own work; AI slop (over-engineered abstractions, template-paste, unnecessary wrappers) merged because no different evaluator scored ≥7/10 |

Full reference: see [references/primitives.md](references/primitives.md).

### Naming convention for agent prose (binding on every agent)

Each primitive carries a **short name** for use in agent prose. When referencing a primitive in responses, PR bodies, commit messages, code comments, knowledge-graph entries, or any human-readable surface, use the **`Name (Pn)`** form — *"applying Snapshot (P15)"*, *"via Dep-Chain (P14)"*, *"running Bookkeeping (P6)"* — not bare `P15` / `P14` / `P6`. The number is the canonical identifier (stable across renames); the name is the human-readable handle. First mention in a response uses the full form; subsequent mentions in the same response may drop to bare `Name` ("Snapshot showed clean state") but never to bare `Pn`. Anchors, section IDs (`#p15-state-snapshot-before-action`), and primitive-count headers ("Twenty irreducible primitives") stay numeric — URL stability and arithmetic respectively. Failure mode: bare `Pn` makes responses read as numeric soup; cross-session readers can't decode the reference without a lookup. The Short-name index below is the recall key.

**Short-name index** (canonical numbering): Bridge (P1) · Gate (P2) · Tickets (P3) · Pipeline (P4) · Fanout (P5) · Bookkeeping (P6) · Freshness (P7) · Janitor (P8) · Wait (P9) · Hygiene (P10) · Empirical (P11) · Persist (P12) · Dream (P13) · Dep-Chain (P14) · Snapshot (P15) · Crystallize (P16) · Lens (P17) · Audience (P18) · Orchestrate (P19) · Cross-Review (P20).

**Canonical statement** lives in workspace `CLAUDE.md` §Bstack Core Automation Primitives and workspace `AGENTS.md` near line 93. This SKILL.md restates the rule so it's visible at the entry point where `/bstack` loads. **Note**: Wait sits at P9 to match the `broomva/p9` skill repo name — the primitive number and the skill name are intentionally aligned. Skill repos that don't carry a number (e.g., `broomva/bookkeeping` = P6, `broomva/persist` = P12) take their name from the function, not the number; carrying a numeric skill name is a commitment to keep that number stable.

**Canonical operating mode**: `broomva/autonomous` — when the user says "go" / "proceed" / "be autonomous" / "automerge" / any bare execution directive, `/autonomous` fires the 20-reflex pipeline that exercises every primitive above in the right sequence. Substrate without mode is dormant; mode without substrate is wishful. Compounded, they produce a self-operating workspace.

## Preamble (run first, every session)

Detect skill installation state, update overdue skills, and check for first-time setup.

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

# ─── First-time setup check ──────────────────────────────────
# If the init marker is missing, bstack hasn't been onboarded on this
# machine. Recommend the wizard. In Claude Code, the agent should follow
# the `## Onboarding` section below to ask the user the 4 wizard
# questions interactively via the AskUserQuestion tool.
_BSTACK_MARKER="${BROOMVA_STATE_DIR:-$HOME/.config/broomva/bstack}/initialized"
if [ ! -f "$_BSTACK_MARKER" ]; then
  echo "ONBOARDING: bstack not yet initialized on this machine."
  echo "  → In Claude Code: agent will guide you (see ## Onboarding section)."
  echo "  → In a shell: run \`bash $_BSTACK_ROOT/scripts/onboard.sh\`"
fi
```

If output shows `UPGRADE_AVAILABLE <old> <new>`: read `bstack-upgrade/SKILL.md` and follow the inline upgrade flow. If `JUST_UPGRADED <from> <to>`: tell the user "Running bstack v{to}" and continue.

If the preamble printed `ONBOARDING: bstack not yet initialized`: jump to the **`## Onboarding`** section below before running any other bstack command. New users get a guided 4-question wizard; returning users skip this automatically once the marker exists.

```bash
# ─── Skill roster check ──────────────────────────────────────
AGENTS_DIR="${HOME}/.agents/skills"
CLAUDE_DIR="${HOME}/.claude/skills"
ROSTER=(autonomous cross-review agentic-control-kernel control-metalayer-loop harness-engineering-playbook p9 agent-consciousness knowledge-graph-memory prompt-library symphony symphony-forge autoany deep-dive-research-orchestrator skills skills-showcase arcan-glass next-forge alkosto-wait-optimizer content-creation finance-substrate seo-llmeo brand-icons pre-mortem braindump morning-briefing drift-check strategy-critique stakeholder-update decision-log weekly-review role-x)
INSTALLED=0; MISSING=()
for s in "${ROSTER[@]}"; do
  if [ -d "$AGENTS_DIR/$s" ] || [ -d "$CLAUDE_DIR/$s" ]; then
    INSTALLED=$((INSTALLED + 1))
  else
    MISSING+=("$s")
  fi
done
echo "bstack: $INSTALLED/${#ROSTER[@]} skills installed (30 total)"
[ ${#MISSING[@]} -gt 0 ] && echo "Missing: ${MISSING[*]}"
```

Report the count. If all 30 present, say "bstack fully installed." If any missing, list them and offer the `bootstrap` command.

After skill check, run the harness validation:

```bash
cd ~/broomva && make bstack-check 2>&1
```

Report results. If any checks fail, fix them before proceeding.

## Onboarding (first-time setup wizard)

When the preamble reports `ONBOARDING: bstack not yet initialized`, run the guided wizard. There are **two paths** — same underlying script (`scripts/onboard.sh`), different drivers.

### Path A: In Claude Code (agent-driven via AskUserQuestion)

When in a Claude Code session, the agent collects the 4 wizard inputs through `AskUserQuestion` calls (so the user answers in chat), then invokes `scripts/onboard.sh` with the collected flags. **No `read -r` prompts in the user's terminal** — the agent IS the input mechanism.

The 4 questions (with defaults shown to the user):

1. **Workspace path** — `$HOME/broomva` (default). Where bstack scaffolds governance files.
2. **Profile** — `personal` / `enterprise` / `autonomous-strict`. Determines gate strictness:
   - `personal` — relaxed; solo dev / experimentation
   - `enterprise` — strict, audit-friendly
   - `autonomous-strict` — gates-are-trust principle; L3 auto-merge **enabled** (requires G-L3-* gates in CI)
3. **Life Agent OS integration** — `install` / `skip`. Whether to also install `life-os` + `arcan` binaries.
4. **Auto-merge policy for governance paths** — `human-required` / `trust-gates`:
   - `human-required` — safe default until G-L3-1/G-L3-2 are wired into CI
   - `trust-gates` — L3 paths auto-merge when L3 trust gates pass

After collecting the four answers, the agent invokes:

```bash
bash "$_BSTACK_ROOT/scripts/onboard.sh" \
  --workspace="$A1" \
  --profile="$A2" \
  --life="$A3" \
  --auto-merge="$A4" \
  --skip-prompts
```

The script persists choices to `~/.bstack/config.yaml` via `bin/bstack-config`, runs `scripts/bootstrap.sh` against the chosen workspace, and writes the init marker at `~/.config/broomva/bstack/initialized`. The agent then reports the onboarding receipt (workspace + profile + life + auto-merge + bootstrap status) to the user and recommends `/autonomous` as the next move.

**If `AskUserQuestion` is unavailable** (running outside Claude Code): fall through to Path B.

### Path B: In a shell (interactive `read -r` prompts)

```bash
bash $_BSTACK_ROOT/scripts/onboard.sh
```

The script prompts for the same 4 questions via `read -r`. Same downstream effect: config persisted, bootstrap run, marker written.

### Idempotency + re-running

- Once `~/.config/broomva/bstack/initialized` exists, subsequent `onboard.sh` invocations exit 0 immediately (no prompts, no bootstrap).
- Re-run with `--force` to redo the wizard.
- Run `--dry-run` to preview choices without persisting.

### Receipt schema (the marker file)

The init marker is a YAML-style flat file at `~/.config/broomva/bstack/initialized`:

```yaml
# bstack initialization marker
onboarded_at: 2026-05-12T22:49:44Z
workspace: /Users/foo/broomva
profile: personal
life: skip
auto_merge: human-required
bstack_repo: /Users/foo/.agents/skills/bstack
bootstrap_status: ok           # ok | failed | skipped
```

Future sessions inspect this for state. `bootstrap_status: failed` is captured transparently — the user knows bootstrap needs follow-up without losing the wizard answers.

## Commands

### `bootstrap` — full install + system wire-up

`scripts/bootstrap.sh` is the install/wire path. It:

1. Installs all 30 skills via `npx skills add broomva/<skill>` — `broomva/autonomous` is the first in the roster (canonical operating mode)
2. **Scaffolds missing governance files** from `assets/templates/`:
   - `CLAUDE.md` (workspace invariants + RCS hierarchy + primitive table P1–P20 + §Ritual vs Substance)
   - `AGENTS.md` (operational rules + per-primitive sections + reflexive triggers for all reasoning-enforced primitives)
   - `.control/policy.yaml` (ci_watch / ci_heal / auto_merge / gates G1–G11)
   - `.claude/settings.json` (P1, P2, P7 hook wiring)
3. Adds `make` targets to existing Makefile (or creates one): `bstack-check`, `control-audit`, `janitor`, `bstack-primitive-lint` (G-L3-1), `bstack-rule-of-three` (G-L3-2), `bstack-l3-trust` (combined L3 gates)
4. Installs pre-commit hook (`.githooks/pre-commit`) via `git config core.hooksPath .githooks`
5. Runs `bstack doctor` to verify primitive contract compliance + `make bstack-l3-trust` to verify L3 gates pass
6. Reports a *bootstrap receipt* — what was installed, what was scaffolded, what was already present
7. **Recommends invoking `/autonomous` for the user's next substantive work unit** — the substrate is installed; the canonical mode is ready to engage

**Idempotent**: never overwrites existing user customizations. If a file already exists, the bootstrap appends only the missing primitive sections / blocks / hooks, never the whole file.

**Self-application**: when `/bstack bootstrap` is invoked in an existing workspace, the bootstrap itself runs under `/autonomous` discipline — state snapshot, dep-chain trace, validation plan, PR pipeline. The bootstrap that installs the discipline embodies the contract it ships.

### `doctor` — verify primitive contract

`scripts/doctor.sh`. Eight check sections:

1. Governance files exist (CLAUDE.md, AGENTS.md, .control/policy.yaml)
2. CLAUDE.md primitives table has all P1–P20 rows + correct count header ("Twenty irreducible…")
3. AGENTS.md has each primitive section (`### P1:` or `### P1 — Short: Long` format through `### P20`)
4. Reflexive Trigger Rules present for P6, P9, P10, P11, P12, P13, P14, P15, P16, P17, P18, P19, P20 (the reasoning-enforced primitives)
5. `.control/policy.yaml` has required blocks (`ci_watch:`, `ci_heal:`, `auto_merge:`)
6. `.claude/settings.json` wires the expected hook scripts (P1, P2, P7)
7. Each primitive's mechanism is reachable on disk
8. **L3 trust gates pass** — runs `make bstack-l3-trust` if the target exists; reports G-L3-1 + G-L3-2 results; surfaces any structural/ rule-of-three violations as gaps

Modes: default (full report), `--quiet` (only gaps), `--strict` (exit 1 on gap, for CI lanes). **Always exits 0 by default.** Each gap includes an actionable `→ fix:` hint.

`bootstrap` invokes `doctor --quiet` automatically as its final step. The L3 trust gate (check 8) is the *substrate-level* equivalent of the new mode's anti-rationalization layer — both close the failure mode where governance evolves without machine-checkable evidence behind it.

### `repair` — apply targeted fixes

`scripts/repair.sh`. Reads the doctor's gap list, asks the user before each fix, then applies the specific repair (add missing primitive section from template, add missing policy block, wire missing hook). Idempotent. Never destructive.

### `status` — installed vs missing + harness health

Re-run the preamble. For each skill show: name, layer, installed/missing. Then run `make bstack-check` and report harness health.

### `validate` — skill frontmatter health

`scripts/validate.sh`. Verifies each skill has a valid SKILL.md with proper frontmatter. Then runs the full bstack-check harness validation.

### `revamp` — full agent reconfiguration

`scripts/revamp.sh`. Triggers complete workspace reconfiguration:

1. Reinstall all 30 skills (force mode)
2. Regenerate governance files from templates (asks before overwriting)
3. Rewire hooks (git pre-commit + Claude Code Stop/Notification/PreToolUse/SessionStart)
4. Force-run conversation bridge across all projects
5. Run full control audit
6. Update AGENTS.md with current state

## Stack layers (30 skills)

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
| **Primitive contract** | **20/20** | **`bstack doctor` exit code** |

## When to use bstack

- **Setting up a new project with Broomva conventions** → `bootstrap`
- **Validating an existing project meets the primitive contract** → `doctor`
- **Fixing a specific gap doctor reports** → `repair`
- **Checking skill freshness or roster completeness** → `status` or `validate`
- **Major workspace cleanup** → `revamp`

## Prompts as a shared knowledge surface

bstack treats the broomva.tech prompts library as a *shared knowledge surface across agents*, not a private repository. Two things every bstack workspace gets from it:

1. **Reusable directives** — versioned, parameterized prompts (`code-review-agent`, `deep-research-agent`, `ai-native-platform-architect`, `bstack-control-harness-bootstrap`, etc.) that any agent can pull and apply.
2. **An evaluation engine** — every pull and every completion writes a typed row to `prompt_invocation`. Source attribution (`web|cli|skill|api`), latency, tokens, cost, and explicit user feedback all flow into the eval surface at `broomva.tech/api/metrics/*`.

### Reflexive rule (mandatory)

When the user asks for a known pattern (code review, deep research, platform redesign, harness bootstrap, etc.) **AND** the pattern exists in the library, reach for it instead of writing from scratch. The five-step mandate is non-negotiable:

```bash
# 1. Tag the session
export BROOMVA_SOURCE=skill

# 2. Pull — captures invocation_id on stderr
broomva prompts pull <slug> --json 2>&1 | tee /tmp/broomva-last.json

# 3. Use the prompt body as instructions for the work

# 4. MANDATORY after completing the work
broomva prompts complete <invocation_id> \
  --status completed --model <name> \
  --latency-ms <ms> --tokens-in <n> --tokens-out <m>

# 5. Optional — capture explicit user feedback
broomva prompts feedback <invocation_id> --slug <slug> --signal up --text "..."
```

Skipping step 4 means the row stays `pulled` and the eval engine can't learn from your run. The 24h sweeper eventually flips it to `abandoned` — a quality signal, not a useful run.

### Discovery

```bash
broomva prompts list --metrics --sort skill_invokes   # most-invoked first
broomva prompts list --category agent-instructions    # filtered
broomva prompts get <slug> --raw                      # body only
```

### Composition with the primitive contract

- **P1** captures the invocation id in the conversation log — backpointer from the session to the eval engine.
- **P4** ties PR-review prompt invocations to merge outcomes — measurable pass rates per prompt version.
- **P6** promotes synthesis-worthy prompts to entity pages in `research/entities/` (the library is the runtime registry; the knowledge graph is the crystallized form).
- **P11** completing the invocation with real `tokens_in/out`, `latency_ms`, `error_message` is the same discipline as P11: validate with measurable outcomes.
- **P13** the eval engine's evolving rankings are a dream-tier substrate; per-run telemetry is the dense lower-tier signal.

Full integration guide with discovery patterns, traps, and per-primitive composition: [references/prompts-integration.md](references/prompts-integration.md).

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

- [references/primitives.md](references/primitives.md) — full P1–P20 reference with reflexive triggers
- [references/prompts-integration.md](references/prompts-integration.md) — when/how to leverage the broomva.tech prompts library (5-step auto-tracing mandate, discovery, common traps)
- [references/skills-roster.md](references/skills-roster.md) — all 30 skills with install commands
- [references/stack-architecture.md](references/stack-architecture.md) — layer dependency diagram
- [references/quickstart.md](references/quickstart.md) — 5-minute install walkthrough
- [bstack-upgrade/SKILL.md](bstack-upgrade/SKILL.md) — version-upgrade flow
