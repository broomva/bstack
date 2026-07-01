---
name: bstack
description: "bstack — the Broomva Stack. Twenty irreducible primitives (P1-P20) plus 30 curated skills that turn an agent-driven workspace into a self-operating system. Each primitive closes one specific failure mode (session amnesia, destructive ops, invisible work, dirty-tree drift, context rot, ritual phrases without substance, single-model echo chambers). The canonical operating mode is broomva/autonomous (fires every reflex without prompting). Use when: (1) bootstrapping a new agent-driven workspace, (2) verifying primitive compliance via 'bstack doctor', (3) repairing missing governance/hooks/policy via 'bstack repair', (4) listing installed-vs-missing skills via 'bstack status', (5) validating skill frontmatter health via 'bstack validate', (6) full reconfiguration via 'bstack revamp'. Triggers on 'bstack', 'broomva stack', 'bootstrap project', 'install all skills', 'P1' through 'P20', 'agent harness', 'self-operating workspace'."
---

<!-- SELF-WARNING BANNER (BRO-1633) — do not remove; tests/skill-selfwarn-banner.test.sh asserts it. -->
> [!CAUTION]
> **Is this `SKILL.md` the only bstack file you have? Then you have a BROKEN partial install.**
> If you ran `npx skills add broomva/bstack`, skills.sh copied only this file and **dropped `bin/`, `scripts/`, `schemas/`, and the templates** — the repo-root-`SKILL.md` case of [vercel-labs/skills#1523](https://github.com/vercel-labs/skills/issues/1523). There is **no `bstack` CLI, no `bstack bootstrap`, no `bstack doctor`.** bstack is a CLI + governance substrate, *not* a skill.
>
> **Fix — install the real thing:**
> ```bash
> git clone https://github.com/broomva/bstack.git && cd bstack && ./bin/bstack bootstrap
> ```
> Just want the primitives primer for an agent (no CLI)? `npx skills add broomva/skills --skill bstack`.

# bstack — The Broomva Stack

**Twenty irreducible primitives. Thirty curated skills. One canonical operating mode. One self-operating workspace.**

bstack is a *portable harness metalayer* — it composes existing skills into a binding primitive contract that the agent enforces by reasoning, the doctor enforces by checking, the bootstrap enforces by scaffolding, and the **canonical operating mode** (`broomva/autonomous`) enforces in execution.

## Substrate vs Mode

bstack ships two complementary layers:

- **Substrate** (this skill, `/bstack`): the 20 primitives + 30 skills + governance + hooks + `.control/policy.yaml`. This is what `/bstack bootstrap` installs. The substrate is the *capability* — what's available in the workspace.
- **Mode** (`broomva/autonomous`): the canonical *behavior* that runs on top of the substrate. When the user says "go" / "proceed" / "be autonomous", `/autonomous` fires the 20-reflex pipeline that uses every primitive in sequence.

Installing the substrate without the mode = the workspace has primitives but no entry point to engage them. Invoking the mode without the substrate = wishful thinking. Compounded: `/bstack bootstrap` installs the substrate, then `/autonomous` is the standing operating mode for substantive work units.

Bootstrap itself is **two-flow** — a deterministic structured scaffold (the floor) plus an agent-authored generative tailoring pass (the bespoke layer). See [Two-flow workspace setup](#two-flow-workspace-setup-structured--generative).

## Quick start

Install (bstack is a CLI/substrate — clone + bootstrap, NOT `npx skills add`, which
would drop `bin/`/`scripts/` per vercel-labs/skills#1523):
```bash
git clone https://github.com/broomva/bstack.git && cd bstack && ./bin/bstack bootstrap
```

Then, in your agent session:
```
/bstack bootstrap     → install 30 skills + scaffold governance + wire hooks + run doctor
/bstack doctor        → verify primitive contract compliance (always exits 0)
/bstack repair        → fix specific gaps surfaced by doctor (asks before writing)
/bstack status        → show which skills are installed vs missing
/bstack validate      → check skill SKILL.md frontmatter health
/bstack revamp        → full reconfiguration (force-reinstall + rewire + re-doctor)
/bstack wave dispatch <plan...>   → atomic parallel-agent dispatch from N plan files
/bstack wave status <wave-id>     → forensic per-wave state table
/bstack wave list                 → all waves with summary state
/bstack bench run                 → two-phase skill-evolution benchmark (P11)
/bstack bench compare             → Phase 1 vs Phase 2 REPORT.md
/bstack bench tasks list          → registered task sets
/bstack bench run --runner live --provider databricks --model ...
                                  → real LLM via OpenAI-compatible provider (≥ 0.11.0)
/bstack workspace register        → add this workspace to ~/.broomva/global/registry.yaml (≥ 0.18.0)
/bstack workspace list            → list all registered workspaces (Federation, Phase 8)
/bstack workspace info            → is the current workspace registered?
/bstack workspace deregister      → remove a workspace by --name or --path
/bstack status --aggregate        → federation rollup: name × bstack_version × composite_ω × verdict
/bstack cross-review <pr> --repo <owner/name>
                                  → P20 cross-model adversarial review via remote
                                    git fetch (≥ 0.20.0, BRO-1227 Fix B)
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

**Primitive operationalization references** (loaded on demand by the agent applying the primitive):

- **Empirical (P11) — dogfood patterns cookbook**: [references/dogfood-patterns.md](references/dogfood-patterns.md). Per-tech-stack interaction surfaces matrix (Tauri+sidecar / Next.js / Expo RN / Rust CLI / REST API / MCP server). Names the skill toolkit (Interceptor mandatory for visual deploy verification; gstack / cliclick / screencapture / curl+jq compose per stack), the canonical arc per stack, the gotchas, the Dogfood Plan contract, and the receipt template. `bstack doctor` §13 enforces stack-keyed plan presence (informational until rule-of-three).

**Roadmap to v1.0.0** — the architectural contracts, gap catalog, and 9-phase closure plan from v0.3.1 onwards live in [`specs/2026-05-18-substrate-completion.md`](specs/2026-05-18-substrate-completion.md) (canonical) and [`references/substrate-completion-overview.md`](references/substrate-completion-overview.md) (agent-readable summary). Every future bstack release through v1.0.0 references this spec for "what does done look like".

### Naming convention for agent prose (binding on every agent)

Each primitive carries a **short name** for use in agent prose. When referencing a primitive in responses, PR bodies, commit messages, code comments, knowledge-graph entries, or any human-readable surface, use the **`Name (Pn)`** form — *"applying Snapshot (P15)"*, *"via Dep-Chain (P14)"*, *"running Bookkeeping (P6)"* — not bare `P15` / `P14` / `P6`. The number is the canonical identifier (stable across renames); the name is the human-readable handle. First mention in a response uses the full form; subsequent mentions in the same response may drop to bare `Name` ("Snapshot showed clean state") but never to bare `Pn`. Anchors, section IDs (`#p15-state-snapshot-before-action`), and primitive-count headers ("Twenty irreducible primitives") stay numeric — URL stability and arithmetic respectively. Failure mode: bare `Pn` makes responses read as numeric soup; cross-session readers can't decode the reference without a lookup. The Short-name index below is the recall key.

**Short-name index** (canonical numbering): Bridge (P1) · Gate (P2) · Tickets (P3) · Pipeline (P4) · Fanout (P5) · Bookkeeping (P6) · Freshness (P7) · Janitor (P8) · Wait (P9) · Hygiene (P10) · Empirical (P11) · Persist (P12) · Dream (P13) · Dep-Chain (P14) · Snapshot (P15) · Crystallize (P16) · Lens (P17) · Audience (P18) · Orchestrate (P19) · Cross-Review (P20).

**Canonical statement** lives in workspace `CLAUDE.md` §Bstack Core Automation Primitives and workspace `AGENTS.md` near line 93. This SKILL.md restates the rule so it's visible at the entry point where `/bstack` loads.

**Skill-name ↔ primitive-number alignment**: when a skill carries a numeric name (e.g., `p9` for Wait at P9), the primitive numbering commits to keeping that name stable — renaming it would break every `npx skills add broomva/skills --skill p9` install. Skills with functional names (`bookkeeping` = P6, `persist` = P12) take their name from the function, not the number. (All are vendored in the broomva/skills monorepo; the `--skill <name>` handle is the stable identifier.)

> **`bstack wave` (new in this version).** Fills the N>1 × across-session × external-trigger cell of Orchestrate (P19)'s mechanism cube. Use it when you have N independent plan files that can run in parallel without shared mutable file writes (Fanout (P5)). Each plan's frontmatter declares its `worktree` and `branch`; the wrapper validates atomically, creates worktrees, and launches one `claude --bg` per plan. State lives in `~/.cache/bstack/wave/<wave-id>/` (Persist (P12) filesystem-as-state). See the design at `docs/superpowers/specs/2026-05-13-bstack-wave-design.md` (workspace repo) for the full primitive coalescence map.

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

1. Installs the companion-skills roster by delegating to `bin/bstack-skills install` (reads `references/companion-skills.yaml`, installs each as `npx skills add broomva/skills --skill <name>`) — `autonomous` is the canonical operating mode
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

**Two-flow**: bootstrap is not a single deterministic pass. The structured scaffold above is the *floor*; the agent then runs a generative tailoring pass on top of it. See [Two-flow workspace setup](#two-flow-workspace-setup-structured--generative) below for the full model and canonical sequence.

### Two-flow workspace setup (structured + generative)

`bstack bootstrap` is a *two-flow* operation, not a single deterministic pass. This mirrors the Audience (P18) split bstack already applies to documents — a deterministic Category-B *projection floor* plus a context-aware Category-C *bespoke authoring* layer — now applied to workspace setup itself.

```
bstack bootstrap  =  STRUCTURED flow      →   GENERATIVE flow            →   VERIFY
                     (deterministic, no LLM)    (agent-authored, contextual)   (deterministic)
                     scripts + templates        tailoring of THIS workspace    bstack doctor
                     + shipped+deployed hooks
                     + gates + .control
```

**1. Structured flow (the floor — reproducible, no LLM).** `bstack bootstrap` runs the idempotent scaffold: installs skills; scaffolds governance from `assets/templates/*` (CLAUDE.md, AGENTS.md, METALAYER.md, `.control/policy.yaml`, `.control/arcs.yaml`, `.control/rcs-parameters.toml`, `schemas/`); **deploys** the hook scripts into the workspace (control-gate / skill-freshness / conversation-bridge / knowledge-catalog-refresh + the L0/L1 audit hooks); wires `.claude/settings.json`; installs the L3 rate gate + CI gate. This flow must be COMPLETE and CORRECT — every wired hook must have a backing script deployed (no dangling references). It is the lossless baseline: same inputs → same workspace, every time.

**2. Generative flow (the bespoke layer — agent-authored, to-the-ceiling).** After the structured scaffold, the agent does a context-aware pass that templates cannot produce. Concretely the agent:

   a. **Detects the stack + project intent** — signals: language/build files, existing code, README, the user's stated goal.
   b. **Tailors the scaffolded governance prose to THIS project** — rewrites the generic CLAUDE.md / AGENTS.md placeholders into project-specific invariants, conventions, and architecture notes (not generic template text).
   c. **Authors a project-specific CI workflow** — the structured flow ships the L3-stability gate; the agent generates the test/lint/build job that matches the detected stack.
   d. **Fills the Dogfood Plan (Empirical, P11)** with the real entry surfaces + evidence anchors for this project's stack (per [references/dogfood-patterns.md](references/dogfood-patterns.md)).
   e. **For RCS/RSI or control-systems repos** — optionally lays down a runnable L0–L3 substrate + a HIERARCHY/instantiation map, so the workspace doesn't merely DESCRIBE a control system, it RUNS one. For ordinary repos, this step is skipped.
   f. **Files the initial knowledge-graph entities / decision log (Bookkeeping, P6)** for the new workspace — proactively, never asking permission.

**3. Verify (deterministic).** `bstack doctor` gates BOTH flows: the structured contract (governance files, hooks wired+deployed, gates, schemas) AND the generative output (the doctor surfaces gaps if the agent's tailoring left a hole). Generative output is always checked by the structured contract — never trusted blind.

**Key principles:**

- This mirrors the established Audience (P18) discipline: the STRUCTURED flow is the Category-B *projection floor* (deterministic, lossless, reproducible); the GENERATIVE flow is Category-C *bespoke authoring* (context-aware, to-the-ceiling). The same structured-vs-generative split bstack already applies to documents, now applied to workspace setup.
- The structured flow must never wire a hook whose script isn't deployed — the *dangling-hook* failure mode this work fixes. "Wired but dangling" is forbidden: every hook reference resolves to a real, executable, deployed script.
- The agent runs **structured FIRST** (idempotent floor), **THEN generative** (bespoke), **THEN doctor** (verify). Never generative-without-structured (no floor) or structured-without-generative (generic, untailored workspace).

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

### `workspace` — multi-workspace federation registry (≥ 0.18.0, Phase 8)

`bin/bstack-workspace` + `scripts/workspace.py`. Maintains the host-level
roster at `~/.broomva/global/registry.yaml` of bstack-governed workspaces
on this machine. Federation is **opt-in** and **read-only** — each
workspace remains the source of truth for its own state. The registry is
the index `bstack status --aggregate` walks to rollup composite-ω health.

```bash
bstack workspace register             # registers $PWD (name = basename)
bstack workspace register --path ~/projects/foo --tag client-x
bstack workspace list --json          # machine-readable for scripts
bstack workspace info                 # is this workspace registered?
bstack workspace deregister --name foo
```

Exit codes: `0` ok, `2` invalid args, `3` schema/parse error, `4` target
not found, `5` name conflict at different path. SLO: register/list p50 <
100ms.

Federation is **not a new primitive** — no P21. It composes existing
primitives: Snapshot (P15) emits the per-workspace audit signal; the
multi-layer composite-ω from v0.16.0 §19 feeds the per-workspace verdict.
Doctor §20 surfaces registry health (informational unless the registry
file's `schema_version != 1`).

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
