# bstack — Primitive Contract Reference

The eleven primitives that make a workspace self-operating. This is the canonical detail; SKILL.md keeps a pointer for the agent to load this file when needed.

## Table of contents

- [P1 — Conversation Bridge](#p1--conversation-bridge)
- [P2 — Control Gate](#p2--control-gate)
- [P3 — Linear Tickets](#p3--linear-tickets)
- [P4 — PR Pipeline](#p4--pr-pipeline)
- [P5 — Parallel Agents](#p5--parallel-agents)
- [P6 — Knowledge Bookkeeping](#p6--knowledge-bookkeeping)
- [P7 — CI Watcher + Productive Wait](#p7--ci-watcher--productive-wait)
- [P8 — Skill Freshness Check](#p8--skill-freshness-check)
- [P9 — Branch + Worktree Janitor](#p9--branch--worktree-janitor)
- [P10 — Worktree Hygiene Discipline](#p10--worktree-hygiene-discipline)
- [P11 — Empirical Feedback Loop](#p11--empirical-feedback-loop)
- [P12 — Persistent Loop Discipline](#p12--persistent-loop-discipline)
- [P13 — Dream Cycle Discipline](#p13--dream-cycle-discipline)
- [Cohesion narrative](#cohesion-narrative)
- [RCS L3 stability constraint](#rcs-l3-stability-constraint)

---

## P1 — Conversation Bridge

**Closes**: session amnesia. Each session would otherwise start from zero.

**How**: `Stop` and `Notification` hooks → `scripts/conversation-bridge-hook.sh` → `scripts/conversation-history.py` parses Claude Code's JSONL transcript → writes structured Obsidian docs to `*/docs/conversations/` → symlinked into `~/broomva-vault/10-Conversations/`.

**Invariant**: bridge stamp at `~/.cache/broomva-bridge-stamp` is < 24h stale. If stale, the agent is silently amnesic — fix immediately.

**Privacy**: a multi-pattern PII redaction pass (`_redact_pii()` in `conversation-history.py`) runs before every markdown write. Email addresses, secrets, credentials, sensitive identifiers are redacted to `[EMAIL_REDACTED]` etc.

---

## P2 — Control Gate

**Closes**: agents fluently running destructive shell commands the model didn't authorize.

**How**: `PreToolUse` hook → `scripts/control-gate-hook.sh` evaluates the pending tool call against `.control/policy.yaml` gates G1–G11. Blocks force-pushes, secret commits, cross-project writes, ungoverned merges, `rm -rf` on protected paths, `git reset --hard` without backup branch.

**Invariant**: G1–G4 are blocking and cannot be overridden. G5–G6 are soft (warn but allow). The `autonomous` profile relaxes only soft gates.

**Felt in real use**: this session's agents tried `git reset --hard` and `rm -rf /tmp/...` — both blocked correctly.

---

## P3 — Linear Tickets

**Closes**: invisible work. Autonomous work without tracking is unaccountable.

**How**: Linear MCP — agents call `save_issue` directly. State transitions Backlog → Todo → In Progress → Done track real progress. Symphony uses Linear as its dispatch source.

**Invariant**: no significant work without a ticket. State must reflect reality (do not mark Done until merged + verified).

**Memory rule**: `feedback_linear_workspace.md` — never use the Linear CLI (defaults to wrong workspace), always use the MCP server.

---

## P4 — PR Pipeline

**Closes**: merging unreviewed code or code with failing checks.

**How**: feature branch → `git push` → `gh pr create` → CI (lint/typecheck/security/preview) → iterate on failures → merge when green → production deploy triggers automatically.

**Invariant**: never merge with failing checks. Never `--no-verify`. CI must be green on `main` at all times.

**Composes with P9**: the PR pipeline is the gate; the CI watcher is the productive-wait + auto-heal layer that turns a long CI run into actionable feedback instead of dead time.

---

## P5 — Parallel Agents

**Closes**: sequential bottleneck on independent tasks.

**How**: `git worktree add` per agent — isolated checkouts on separate branches. Multiple `Agent` tool calls in one message run concurrently. Independent contexts merged via branches, not shared mutable state.

**Invariant**: agents must not write to the same files. Branch naming is unique per agent. Results merge to `main` only after individual verification.

**Worktree discipline lives in P10** — P5 provides the *mechanism*; P10 the *discipline*.

---

## P6 — Knowledge Bookkeeping

**Closes**: knowledge graphs without quality control degrade into noise.

**How**: `python3 skills/bookkeeping/scripts/bookkeeping.py run` — 7-stage pipeline: Ingest → Score → Scatter → Resolve → Promote → Synthesize → Lint. Items < 2/9 discarded. Items 3–6 get LLM second opinion. Items ≥ 7 fast-path promote. Nous gate (novelty + specificity + relevance, each 0–3).

**Invariant**: `research/entities/` never contains unscored items. All entity pages must pass lint (valid frontmatter, `core_claim` ≤ 140 chars, non-empty sources).

### P6 Reflexive Trigger Rule (binding on every agent)

Bookkeeping is a reflex, not a request. Agents must invoke `bookkeeping.py run` without being prompted in any of these situations:

1. Before committing a feature or page that *reads from* the graph (anything consuming `~/.config/bookkeeping/status.json`, `research/entities/`, or a `public/data/bookkeeping.json` snapshot — the data must be fresh at commit time).
2. Before committing a synced snapshot to a public surface (e.g., `apps/*/public/data/bookkeeping.json`).
3. At the close of any substantial work session that produced graph-relevant material (new names, decisions, concepts, partnerships, threads, design debates).

Mental checklist before declaring graph-dependent work done: *Did this session produce material that belongs in the graph? Does my feature read graph state? Am I about to commit a snapshot?* — yes to any → run bookkeeping.

---

## P7 — CI Watcher + Productive Wait

**Closes**: `sleep`-on-wait dead time. Agents lose 5–15 min per blocking operation — CI checks, deploy verifications, builds, long-running indexing operations. The primitive is *productive wait*: convert the block into work on the next priority.

**How (general primitive)**: spawn the blocking-wait observer via `run_in_background` so the agent gets an event-driven notification on completion. While the observer runs, the agent drains a context-scoped priority queue (`session > memory > graph > docs > linear`). On notification, classify the result and either advance or self-heal.

**Reference implementation — PR CI**: `python3 skills/p9/scripts/p9.py watch <pr> --background` spawns `gh pr checks --watch`. On bg-task notification, agent reads `p9 status` → on green, `p9 merge-ready` → defer to control metalayer for authorization. On red, `p9 heal --classify` → if classified+evaluator-positive, apply heal (PR-diff scope only) and start a new watch. Auto-merge actuator (`p9 auto-merge`) consults `.control/policy.yaml`'s `auto_merge:` block.

**Other waits the primitive applies to** (today: handled by direct check; on the roadmap to wire into `p9`):

- **Push-triggered dev/staging/prod deploys** — when the trigger isn't a PR (e.g., main-branch deploy on push), p9 currently only tracks PRs. Today's workaround: do a single direct check on the deploy URL/log after `git push`. Do *not* `sleep` waiting for the deploy; pull the next item from the wait-queue. Tracked as a P7 extension.
- **Long-running test suites / build pipelines** — same shape; observer is whatever produces the completion event (CLI exit code, webhook, log line).
- **External index / sync operations** — same shape, longer time horizons.

**Invariant**: never `sleep` on a blocking wait. Every failure produces (a) a `state.jsonl` event, (b) a Linear ticket, or (c) both — silent state drops are forbidden (exit 99). Heal actions are scoped to files in PR diff (where applicable). All setpoints (`max_concurrent_prs`, `max_attempts`, `stability_floor`, `classified_failure_types`) live in `.control/policy.yaml` and fail closed if missing.

**Skill name**: `broomva/p9` — historical name (was the 9th primitive when first crystallized; renaming would break every `npx skills add broomva/p9` install). Primitive number is now P7; skill repo name is stable at `p9`. The `broomva/p9` SKILL.md is the canonical implementation.

### P7 Reflexive Trigger Rule (binding on every agent)

P7 is a reflex, not a request. Agents must apply *productive-wait discipline* without being prompted in any of these situations:

1. **Immediately after `git push` that opens or updates a PR** — invoke `p9 watch <pr> --background` within the same response, before any other tool call. The watcher must be running before the agent considers the push "done."
2. **After `git push` that triggers a non-PR deploy** (e.g., a push to `main` that fires a deploy hook) — p9 doesn't track non-PR triggers yet. Do *one* direct check on the deploy result after kicking off the next high-priority work; never `sleep` waiting for it.
3. **Whenever the agent is tempted to `sleep` while a blocking operation runs** — hard ban. Pull from `p9 wait-queue pop` instead. If the queue is empty, do non-code productive work (research adjacent entities, validate doc cross-refs) until the bg-task notification fires.
4. **When a watcher's bg-task notification reports red CI** — invoke `p9 heal <pr> --classify` *before* re-pushing a fix or asking the user. If classified, apply the heal command (PR-diff scope only) and start a new watch. If unclassified, escalate via Linear and surface the failure.
5. **When `p9 status` reports `MERGE_READY`** — invoke `p9 auto-merge <pr>` rather than `gh pr merge` directly. The actuator consults `.control/policy.yaml`'s `auto_merge:` block; per the gates-are-trust principle, governance-class paths auto-merge when L3 trust gates pass (no special-case bypass).

Mental checklist before declaring wait-dependent work done: *What blocking operation am I waiting on? Is it a PR (use `p9 watch`) or a non-PR trigger (single direct check + drain queue)? Am I about to `sleep` or poll? Did I drain the wait-queue while waiting?*

---

## P8 — Skill Freshness Check

**Closes**: silent rot of `npx skills add` snapshots. Skills don't auto-update; without a nudge they go stale and sessions hit `error: unrecognized arguments: --foo` from out-of-date binaries.

**How**: `SessionStart` hook → `scripts/skill-freshness-hook.sh` checks the timestamp of `~/.config/broomva/p8/last-skill-update-check` (legacy `~/.config/broomva/p7/` still honored for in-place upgrades). If ≥ 7 days old (or never), prints a one-line nudge with refresh command + dismissal `touch`. Always exits 0.

**Invariant**: hook always exits 0. `BROOMVA_P8_THRESHOLD_DAYS` env var configurable (default 7; legacy `BROOMVA_P7_THRESHOLD_DAYS` still honored). Dismissal: run `npx skills update -g` then `touch ~/.config/broomva/p8/last-skill-update-check`.

---

## P9 — Branch + Worktree Janitor

**Closes**: squash-merged branches and dead worktrees accumulate. `git branch --merged` doesn't catch squash-merges (the branch tip isn't an ancestor of main).

**How**: `make janitor` (wraps `scripts/branch-janitor.sh`). Walks current repo (or all workspace repos with `--scope=workspace`). For each non-protected branch matching the include pattern (`feat/*,fix/*,chore/*,docs/*` by default): runs the canonical squash-merge detection — `git commit-tree <branch-tree> -p <merge-base>` produces a synthetic commit; `git cherry origin/main <synth>` reports if its patch is in main. If yes, branch is mergeable. Worktrees whose underlying branch is gone get pruned via `git worktree remove --force`.

**Invariant**: default `--dry-run` — pass `--apply` to actually delete. Never touches main, master, develop, HEAD, gh-pages, or any branch in `~/.config/broomva/p9-janitor/protected.txt` (legacy `~/.config/broomva/p8-janitor/` still honored). Currently-checked-out branch always skipped.

---

## P10 — Worktree Hygiene Discipline

**Closes**: dirty trees, half-finished branches, orphan worktrees accumulating across sessions and becoming slow leaks of merge conflicts and "what was I doing?" amnesia.

**How**: reasoning-enforced rule, not a hook. P5 provides the *mechanism* (git worktrees); P10 provides the *discipline* — when to use them, how to maintain hygiene during development, how to clean up after merge.

**Invariant**: after every PR merge, both the worktree (if any) and the branch are gone. Before starting any new substantial work, `git status` is clean (or the agent explicitly notes the dirty state and gets user direction). The "default to worktree" rule has documented exceptions — typo fixes, single-file doc edits, read-only research, work continuing an existing branch you already own — but those exceptions are evaluated and named, not assumed.

### P10 Reflexive Trigger Rule (binding on every agent)

P10 is a reflex, not a request. Agents must apply the following without being prompted:

1. Before writing the first file of any new substantial work — decide whether a worktree is needed and **state the choice in your response**. Default *yes* for new feature/spec/research, multi-file work, work that might take more than ten minutes, work that could conflict with other in-flight branches. Default *no* for typo fixes, single-file doc edits, read-only investigation, work continuing an existing branch.
2. Before pushing to remote — run `git status` mentally; if dirty with WIP that's not part of the PR, decide: *commit-as-WIP*, *stash with reason*, or *extract to a separate branch*. Don't push past lingering uncommitted state.
3. After PR merge — immediately run `make janitor` (P8) or `git worktree remove` + `git branch -D` directly. Never start a new work unit on top of a merged-but-uncleaned branch.
4. At SessionStart — when reviewing prior context, check `git worktree list` and `git branch`. If the previous session left orphan worktrees or stale merged branches, run `make janitor` *before* starting new work.

Mental checklist: *Did I decide on a worktree? Is `git status` clean? Are merged branches gone? Are there orphan worktrees from prior sessions?*

---

## P11 — Empirical Feedback Loop

**Closes**: the failure mode where the agent ships code that compiles, passes lint, and might even pass CI — but never actually does the thing the user asked for in the deployed environment.

**How**: composition of existing tools and skills, bound into a discipline:

| Validation surface | Mechanism | When |
|---|---|---|
| Server logs | `run_in_background` tailing dev server output | Always when work touches a running process |
| Browser E2E | `gstack` skill (fast headless) / `agent-browser` skill | UI / API / route changes |
| Visual diff | `before-and-after` skill | Before/after visible changes |
| Smoke tests | `make check` / project-specific | Pre-commit |
| Unit tests | Project test runner (vitest, pytest, cargo test) | During iteration, watch mode |
| Integration tests | Project test runner — across modules | Before push |
| Regression battery | `qa` / `dogfood` skill — systematic exploration + fix | Before merge |
| Deploy verification | Vercel preview URL → screenshot via `gstack` | After CI green, before claiming "shipped" |
| Audio diff | TTS comparison | When narration changes |
| Multi-agent observation | Parallel `Agent` calls watching different surfaces | Long-running work |

The agent picks the right subset, runs as parallel watchers via `run_in_background` where applicable, and **captures evidence** — not just exit codes, but actual screenshots, log snippets, response bodies, browser transcripts.

**Invariant**: before claiming any work *complete*, the agent has interacted with the deployed/running version (or stated explicitly why interaction wasn't possible). The interaction is captured (screenshot, log snippet, video clip, terminal output, response body) and surfaced in the response. *Reasoning isn't validation; interaction is.*

### P11 Reflexive Trigger Rule (binding on every agent)

P11 is a reflex, not a request. Agents must apply the following without being prompted:

1. Before writing the first file of substantial work — identify validation surfaces. *What does this expose? What does it log? What would a user click? What tests exist? What's the deploy preview URL?* State the validation plan as a contract.
2. During development of work touching a running process — keep at least one log-tail or watcher in `run_in_background`. Don't type-check blind.
3. Before claiming complete — exercise the change end-to-end. UI? Click through it via `gstack` / `agent-browser`. API? `curl` it. Background job? Trigger it. Capture evidence: screenshot, log snippet, response body, transcript line. The evidence is part of the response.
4. After deploy — capture deployed-state evidence. Vercel preview URL screenshot. Production log query. Live browser session. *Compile-time success is not deploy-time correctness.*
5. When CI or any test fails — capture full context first (logs + screenshots + last-known-good diff) before attempting a fix. The fix-without-context loop is how harness defects compound.
6. At session end — produce a *dogfood receipt*: what was actually exercised vs what was only claimed. The receipt feeds P1 and P6.

Mental checklist: *Did I interact with it? Did I capture evidence? Was the evidence multi-modal? Did I exercise it like a user would? Is the deploy actually correct, or just deployed?*

---

## P12 — Persistent Loop Discipline

**Closes**: long-horizon work decaying as the context window rots past ~100K tokens (the *"Dumb Zone"*). METR's Time Horizon 1.1 (Jan 2026) puts the **80%-reliability deployable horizon at ~1h on Opus 4.6** — a 14× reliability gap vs the 14.5h 50%-horizon. Above 1h, in-context loops fail.

**Skill name note**: P12's skill repo is `broomva/persist` — non-anthropomorphized rename of the pattern Geoffrey Huntley popularized as the "Ralph loop" (Jan 2026).

**How**: `python3 skills/persist/scripts/persist.py iterate <PROMPT.md>` substrate. Each iteration spawns a fresh agent context. State persists in the filesystem (PROMPT.md + git tree + state.jsonl). Validation backpressure from compilers/tests/linters, not model self-grading. Five-state machine: `SPAWNED → ITERATING (self-loop) → SUCCESS | BUDGET_EXHAUSTED | ABANDONED`. Default budget: 50 iterations / 14400s wall-clock (METR's 80%-horizon ceiling).

**Invariant**: state lives in the filesystem. Each iteration starts from PROMPT.md content, not conversation history. Validation backpressure is external. Each iteration is a fresh subprocess.

### P12 Reflexive Trigger Rule (binding on every agent)

P12 is a reflex, not a request. Apply without being prompted:

1. Before any work that may exceed ~1h of unsupervised agent time — write PROMPT.md, call `persist iterate`. Don't try >1h work in-context.
2. When session token usage crosses ~100K — restart, don't continue in the rotted context.
3. When the same fix has been attempted ≥3 times without convergence — stop in-context; spawn fresh persist loop.
4. When orchestrating long-horizon work — default to persist + periodic checkpoints; compose with P5 (one persist loop per worktree) and P9 (each iteration's PR uses `p9 watch`).
5. When the user says "run this in the background for an hour" — that's persist territory.

---

## P13 — Dream Cycle Discipline

**Closes**: the *shadow dream* corruption mode — consolidation runs that gather + consolidate + index without the **replay** phase. Without replay, dense lower-tier signal corrupts sparse upper-tier rules. Pattern documented in `research/entities/concept/multi-tier-dreaming.md` (scored 9/9, promoted 2026-04-30).

**How**: Reasoning-enforced. P13 has no dedicated substrate skill — it composes with primitives that already implement the dream shape:

| Tier crossing | Implementation | Status |
|---|---|---|
| Knowledge graph (raw → promoted entities) | P6 with `bookkeeping replay` | **Reference instance — shipped 2026-05-06** |
| Agent traces → plans (T0→T1) | Life autonomic compression | shipped (eager / shadow form) |
| Trace bundles → prompt/tool diffs (T1→T2) | Life askesis | designed, not yet shipped |
| Diffs → governance amendments (T2→T3) | Life anamnesis | proposed, not yet shipped |

The 5-phase canonical shape:

| Phase | Function |
|---|---|
| **Gather** | Collect a bounded bundle of dense lower-tier signal as a frozen, addressable artifact. |
| **Replay** | Re-execute the bundle against a *frozen substrate* — sandbox, world model, retrieval cache. |
| **Prune** | Reject replayed signal that fails the gate: no improvement, schema violation, regression. |
| **Consolidate** | Commit the kept signal as a sparse, structured update to the upper-tier substrate. Atomic and versioned. |
| **Index** | Re-validate the upper-tier resource graph: reference integrity, contradiction detection, garbage-collect orphans. |

Three independent observations converge on this shape — biological REM sleep, Anthropic's `/dream` skill, Physical Intelligence's knowledge-insulation training (Driess et al. 2025). The replay phase is the runtime form of stop-gradient.

**Invariant**: any agent-driven consolidation that crosses a cadence-tier boundary MUST replay against a frozen substrate before committing. If a consolidation primitive doesn't have a replay phase, it's a *shadow dream* and is unsafe — the agent's job is to either (a) use the dream-cycle form, or (b) explicitly justify why this consolidation is single-tier and doesn't need replay.

**Deferred per rule-of-three**: the `morpheus` crate (shared abstraction across implementations). Extract only when ≥2 dream instances ship end-to-end beyond P6+replay. Currently at 1.

### P13 Reflexive Trigger Rule (binding on every agent)

P13 is a reflex, not a request. Apply without being prompted:

1. Before any consolidation that promotes lower-tier signal to upper-tier rules — verify the consolidation primitive has a replay phase. If not, request the replay-extension before consolidating, or document why this case is single-tier.
2. For knowledge-graph promotion — use `bookkeeping replay` (not `bookkeeping run`) for substantial promotion runs.
3. For governance changes (L3 tier) — every PR in this workspace is a dream cycle: gather (PR description), replay (worktree + CI + doctor), prune (CI failures, doctor gaps), consolidate (squash merge), index (commit history).
4. When designing a NEW consolidation primitive — implement the 5-phase shape from day 1; don't ship shadow-dream form.
5. When you observe a new dream instance shipping — record it in `multi-tier-dreaming.md` (the rule-of-three counter for morpheus extraction).

---

## P14 — Dependency-Chain Reasoning Discipline

**Closes**: "think deeply through chain of dependencies" becoming a ritual phrase the agent acknowledges and then ignores; cascading breakage from changes that didn't trace downstream consumers.

**How**: Before any substantive write, agent enumerates concrete upstream (files, functions, types, contracts, deployed state this depends on) and concrete downstream (consumers, tests, CI gates, docs, in-flight PRs depending on this). Enumeration lives in the response or PR body — not the agent's head. File paths and function names, not vibes-level "I considered dependencies."

**Invariant**: No substantive write without a dep-chain enumeration in the response (or explicit "trivial change, single-file, no enumeration needed" carve-out with reason).

---

## P15 — State-Snapshot Before Action

**Closes**: Plans built on stale state — re-solving solved problems, conflicting with parallel work, missing in-flight PRs.

**How**: Before any plan, the agent surfaces `git status`, current branch, ahead/behind vs base, in-flight PRs (`gh pr list`), Linear ticket state for adjacent project, last bookkeeping run, last conversation-bridge run, last deploy state. The snapshot is part of the planning response, not deferred.

**Invariant**: Plans built on un-stated state are forbidden. Snapshot is the cheapest reflex in the pipeline.

---

## P16 — Crystallization Discipline (the Bstack Engine)

**Closes**: Recurring valuable patterns living only in the user's head — never promoted to skill / SKILL.md / AGENTS.md section / `.control/policy.yaml` gate.

**How**: The rule-of-three loop. Pattern recurs ≥3 times across sessions → propose promotion to skill / primitive / policy gate, gated by four conditions: ≥3 instances, concrete mechanism, stated invariant, stated failure mode. Candidate ledger lives in `research/entities/pattern/bstack-engine.md`.

**Invariant**: No primitive promoted to P-N status without all four gates satisfied. Aesthetic preferences are recorded but NOT promoted. P1–P15 are *outputs* of this loop; P16 names the loop itself.

---

## P17 — Lens-Routed Request Articulation

**Closes**: Flat-dispatch fan-out; agents performing tasks without the typed lens (legal review vs design vs research) that shapes the correct quality_bar. The naive-persona pattern ("act as a senior engineer") was debunked by 2026 PRISM/Zheng research (MMLU drops 71.6% → 66.3%).

**How**: Every substantive user input passes through `role/x` intake. Select lens(es) from `roles/<name>.md` registry by scoring signals (paths + prompt_keywords + branch + Linear labels, threshold ≥2). Load substantive context (files, conventions, domain checklist via `extends:` chain). Decide mode (`augment` / `rewrite` / `decompose`). P5 fan-out becomes a typed graph. `roles/_meta.md` is always loaded.

**Invariant**: No `act as X` persona rewrites — lenses load substantive context only. Lens selection is logged. Mode decision is surfaced unless `augment`. `decompose` requires user approval before P5 dispatch. New lenses promote to `status: active` only after per-lens rule-of-three (≥ 3 positive-outcome uses).

**Skill repo**: `broomva/role-x` (planned). Workspace ships `roles/` registry + governance; skill repo will own the executable surface.

---

## P18 — Format-Follows-Audience Discipline

**Closes**: Markdown-by-default for everything regardless of audience — long specs nobody reads, ASCII pseudo-diagrams when SVG would do, unicode-color-approximation when CSS would work. The format-default-failure mode where the agent produces more+longer markdown that humans bounce off past ~100 lines.

**How**: Format follows audience. At the moment of producing any documentation artifact, apply the audience test:

| Surface | Audience | Format |
|---|---|---|
| `SKILL.md`, `AGENTS.md`, `CLAUDE.md`, primitive contracts, `.control/policy.yaml` | LLM (system-prompt-loaded) | markdown |
| In-source comments (Rust `///`, TS JSDoc, Python `"""`) | both | language-native |
| `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md` | both (humans via GitHub render) | markdown |
| Entity pages in `research/entities/`, synthesis notes in `research/notes/` | LLM (bookkeeping pipeline loads) | markdown |
| Specs, plans, ADRs in `docs/specs/`, `docs/plans/`, `docs/adrs/` | human | **HTML** with diagrams, mockups, code snippets |
| PR explainers | human reviewer | **HTML** with annotated diff, color-coded findings |
| Reports, retrospectives, research syntheses for sharing | human leadership/team | **HTML** with SVG diagrams, embedded code |
| Design explorations | human | **HTML** with sliders, knobs, side-by-side, copy-as-prompt |
| Brainstorming outputs | human | **HTML** grid of variants with tradeoff labels |
| Custom editing interfaces | human (throwaway tools) | **HTML** purpose-built per task |

**Invariant**: Format follows audience, not habit. Every produced artifact > 50 lines OR with visual/interactive content has its format chosen by the audience test. ASCII pseudo-diagrams, unicode-color-approximation, and >100-line markdown specs without HTML companion are explicit anti-patterns. The 2-4× HTML generation cost is paid only on artifacts a human will actually read.

### P18 Reflexive Trigger Rule (binding on every agent)

1. Before producing any spec / plan / ADR / design exploration — default HTML. Path: `docs/specs/YYYY-MM-DD-<topic>.html`, etc.
2. Before producing a PR description for a substantive change (>200 LOC OR public API OR multi-file) — produce HTML PR-explainer artifact alongside the markdown body.
3. Before producing any report, retrospective, or research synthesis intended for human consumption — HTML with embedded SVG diagrams.
4. When tempted to ASCII-diagram, unicode-color, or hand-draw structure in markdown — STOP. SVG inside HTML is the correct primitive.
5. When editing `SKILL.md`, `AGENTS.md`, `CLAUDE.md`, `README.md`, `CHANGELOG.md`, primitive contracts, or LLM-loaded surfaces — markdown is correct.

**Origin**: trq212 (Claude Code team), *The Unreasonable Effectiveness of HTML* (May 2026). Five+ instances logged in `bstack-engine.md` candidate ledger (rule-of-three gate passes).

---

## P19 — Orchestration-Mechanism Selection Discipline

**Closes**: Implicit between-reflex handoffs ("continue please"); using the wrong mechanism for the work shape; the autonomous arc broken by missing-mechanism failure. Before P19, mechanism selection was implicit and agents defaulted to returning control to the user between reflexes — the exact ritual the `/autonomous` skill was created to resist.

**How**: At pre-flight, before any substantive autonomous work, apply the **2×2 decision matrix**:

|  | Within session | Across sessions |
|---|---|---|
| **External trigger** (event-driven) | **P7** `p9 watch --background` (CI/deploy/build) | **P12** `persist iterate PROMPT.md` (cross-context-rot, >1h) |
| **Internal trigger** (condition or time) | **`/goal <condition>`** (Haiku evaluator per turn) | **`/loop <interval>`** (Claude Code time-trigger) |

Decision logic:

1. Verifiable end state + bounded session + condition fits 4000 chars → `/goal <pipeline-completion-condition>`
2. External completion event blocking (CI, deploy, build) → P7 `p9 watch --background` + drain wait-queue
3. Time-triggered recurring routine → `/loop <interval> <slash-command>`
4. >1h work OR cross-session OR context window approaching ~100K → P12 `persist iterate PROMPT.md` with budget

**Composition is dynamic**: P12 iterations can invoke `/goal` for sub-tasks. `/goal`-driven sessions fire P7 watchers when CI is blocking. `/loop`-scheduled sessions can spawn P12 for the long-horizon piece. The orchestration tree grows by which mechanism owns which level of the work.

**Invariant**: No autonomous-continuation work without (a) an explicit mechanism choice surfaced in the response, and (b) a one-line justification matched to the 2×2 quadrant. Returning control mid-arc is the failure mode P19 prevents — there's a mechanism for every work shape, pick one.

### P19 Reflexive Trigger Rule (binding on every agent)

1. **Pre-flight of substantive autonomous work** — state chosen mechanism + cite 2×2 quadrant.
2. **Before returning control mid-arc** — verify no mechanism would keep the arc closed.
3. **At mechanism boundary crossings** (goal hits >1h, context ~100K) — explicit transition, not drift.
4. **When composing mechanisms** — surface the composition tree, don't compose silently.
5. **Tempted to type "continue please" / wait for user prompts** — STOP. That's the ritual P19 makes impossible.

**Origin**: Claude Code `/goal` shipped May 2026 (`code.claude.com/docs/en/goal`) — completes the 2×2 by adding the internal-condition/in-session corner. Five+ instances logged in `bstack-engine.md` candidate ledger.

---

## P20 — Cross-Model Adversarial Review Gate

**Closes**: Same-model echo chamber. The model that wrote the code cannot be the final judge of the code. Single-model planning + implementing + reviewing reproduces the model's own systematic biases — what `cross-model-agents` calls *slop* (over-engineered abstractions, unnecessary wrappers, template-paste patterns).

**How**: Before any substantive PR merges, fire a cross-model adversarial gate. Three strata, ordered by strength:

| Strata | Mechanism | When |
|---|---|---|
| **A** True cross-vendor | `codex exec -m gpt-5.4` reads diff + scores | Codex CLI installed |
| **B** Cross-context same-model | Fresh `Agent` subagent under devil's-advocate brief | Always available |
| **C** Composed existing skills | `superpowers:constructive-dissent`, `devils-advocate`, `pr-review-toolkit:*` (×5), `critique`, `premortem`, `plan-*-review` | Always — the toolkit P20 makes mandatory |

Scoring: anti-slop ≥ 7/10 to pass; max 3 fix rounds; verdict logged in PR comments + Linear ticket. Implementation: `broomva/cross-review` skill.

**Invariant**: substantive PRs (>200 LOC OR public API change OR multi-file OR governance-class) cannot merge without cross-model adversarial verdict ≥ 7/10. Self-review by the writing model is forbidden as the *sole* verdict. The gate fires *before* P4 auto-merge — not after merge as code review.

### P20 Reflexive Trigger Rule (binding on every agent)

1. **Before pushing substantive PRs** — fire the gate (Strata A if Codex, else B+C). Score + verdict precede push.
2. **When verdict < 7** — fix → rescore. Max 3 rounds. Round 3 failure → escalate to user.
3. **When the writer is the only model in the loop** — STOP. Strata B at minimum is mandatory.
4. **When tempted to skip P20 because "small PR"** — threshold is *substantive* (>200 LOC OR public API OR multi-file OR governance). Trivial PRs (typo fix, single-file doc) exempt; everything else fires.
5. **Composition** — P20 sits between P11 (validation) and P4 (auto-merge); does not replace either. PR-comment loop (autonomous Step 17) is downstream of P20.

**Origin**: [Dallionking/cross-model-agents](https://github.com/Dallionking/cross-model-agents) (May 2026) — 31-agent bidirectional Claude↔Codex review system with anti-slop scoring + pipeline hooks. P20 absorbs the *discipline* (cross-model gate as mandatory) while composing with existing bstack skills. Six+ instances logged in `bstack-engine.md` candidate ledger.

---

## Cohesion narrative

P11, P12, and P13 are structural siblings at different scales:

| Primitive | Discipline | Surface | Evidence | Scale |
|---|---|---|---|---|
| **P11** Empirical Feedback | "validate by interacting" | live deployed system | screenshots, logs, browser session | in-session (≤1h) |
| **P12** Persistent Loop | "restart fresh when context rots" | filesystem (PROMPT.md + git) | state.jsonl + each iteration's evidence | cross-session (>1h) |
| **P13** Dream Cycle | "consolidate by replaying" | frozen substrate | diff against frozen snapshot | tier-crossing |

The whole stack composes:

- **P4** (PR Pipeline) and **P9** (CI Watcher) catch what CI sees; **P11** catches what CI can't; **P13** catches what consolidation without replay can't.
- **P10** (Worktree Hygiene) keeps the working tree clean enough for empirical checks to be meaningful — same shape as P13's "frozen substrate" requirement at the knowledge layer.
- **P6** (Bookkeeping) is the first concrete implementation of P13's discipline — `bookkeeping replay` is the canonical reference dream cycle.
- **P12** (Persist) is the substrate for long-horizon work that needs P11/P13 discipline across many iterations.
- **P1** (Conversation Bridge) preserves dogfood receipts and dream-cycle audit trails across sessions.
- **P7** (Skill Freshness) ensures the validation/replay tools (gstack, bookkeeping, persist) are themselves current.
- **P8** (Janitor) ensures cleanup state is automatic so the next cycle starts from zero.

The thirteen primitives compose into the full autonomous development loop:

```
User intent → Linear ticket (P3) → Agent dispatched (P5)
  → Prior context loaded (P1) [+ P7 freshness check] [+ P10 cleanup audit]
  → Safety gates active (P2)
  → P10 worktree decision → P11 validation plan
  → IF long-horizon → P12 persist loop with PROMPT.md + budget
  → Code written + parallel watchers (P11 log-tails) → PR created (P4)
  → CI watched + heal loop (P9)
  → P11 deploy verification (preview URL, screenshots, browser session)
  → Merge → P10 post-merge cleanup via P8 janitor → Deploy
  → P13 dream cycle for any consolidation (P6 replay first; future Life dreams compose here)
  → P11 dogfood receipt → Session captured (P1) → Knowledge bookkept (P6)
  → System improved (EGRI)
```

---

## RCS L3 stability constraint

bstack's governance layer (`CLAUDE.md` + `AGENTS.md` + `.control/policy.yaml`) is the **Level 3 controller** in a Recursive Controlled Systems hierarchy with formal stability proofs:

| Level | System | Controller | Stability λ |
|---|---|---|---|
| L0 | External plant | Arcan agent loop | 1.455 |
| L1 | Agent internal | Autonomic homeostasis controller | 0.411 |
| L2 | Meta-control | EGRI loop engine | 0.069 |
| **L3** | **Governance** | **CLAUDE.md + AGENTS.md + policy.yaml** | **0.006** |

Composite stability: λᵢ > 0 at all levels ⟹ exponentially stable (Theorem 1, p0-foundations).

**The L3 stability margin is narrow on purpose.** Governance changes consume budget. If you rewrite AGENTS.md every session, the system destabilizes. If you observe patterns across sessions and crystallize rules slowly, it converges. The math is what justifies *"governance changes are rare and deliberate"* — it's not stylistic, it's a stability constraint.

Self-evolution protocol (the f₃ dynamics function):

1. Pattern observed across multiple sessions
2. Captured in conversation log (Stop hook — automatic via P1)
3. Crystallized in AGENTS.md (one PR, deliberate)
4. Enforced in `.control/policy.yaml` if mechanically gateable (one PR)
5. Doctor extended to check the new rule (one PR)
6. Future agents inherit the improvement
