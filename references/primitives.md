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

**Composes with P7**: the PR pipeline is the gate; the CI watcher is the productive-wait + auto-heal layer that turns a long CI run into actionable feedback instead of dead time.

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

**Closes**: `sleep`-on-CI dead time. Agents losing 5–15 min/PR.

**How**: `python3 skills/p9/scripts/p9.py watch <pr> --background` spawns `gh pr checks --watch` via `run_in_background`. While the watcher runs, agent drains a context-scoped queue (`session > memory > graph > docs > linear`). On bg-task notification, agent reads `p9 status` → on green, `p9 merge-ready` → defer to control metalayer for authorization. On red, `p9 heal --classify` → if classified+evaluator-positive, apply heal (PR-diff scope only) and start new watch. Auto-merge actuator (`p9 auto-merge`) consults `.control/policy.yaml`'s `auto_merge:` block with governance-paths-always-block safety pre-pass.

**Invariant**: never `sleep` on CI. Every failure produces (a) a `state.jsonl` event, (b) a Linear ticket, or (c) both — silent state drops are forbidden (exit 99). Heal actions are scoped to files in PR diff. All setpoints (`max_concurrent_prs`, `max_attempts`, `stability_floor`, `classified_failure_types`) live in `.control/policy.yaml` and fail closed if missing.

**Skill name note**: P7's skill repo is `broomva/p9` — historical name from when it was the ninth primitive.

### P7 Reflexive Trigger Rule (binding on every agent)

P7 is a reflex, not a request. Agents must invoke `p9 watch <pr> --background` without being prompted in any of these situations:

1. Immediately after `git push` that opens or updates a PR — within the same response, before any other tool call. The watcher must be running before the agent considers the push "done."
2. Whenever the agent is tempted to `sleep` while CI runs — `sleep` on a CI wait is a hard ban. Pull from `p9 wait-queue pop` instead. If queue empty, do non-code productive work until bg-task notification fires.
3. When a watcher's bg-task notification reports red CI — invoke `p9 heal <pr> --classify` *before* re-pushing a fix or asking the user. If classified, apply the heal command (PR-diff scope only) and start a new watch. If unclassified, escalate via Linear and surface the failure.
4. When `p9 status` reports `MERGE_READY` — invoke `p9 auto-merge <pr>` rather than `gh pr merge` directly. The actuator consults `.control/policy.yaml`'s `auto_merge:` block, blocks governance-class paths automatically, and only auto-merges branch classes explicitly allowlisted.

Mental checklist before declaring CI-dependent work done: *Did I push? Is there a watcher running for this PR? Am I about to `sleep` or poll? Did I drain the wait-queue while waiting?*

---

## P8 — Skill Freshness Check

**Closes**: silent rot of `npx skills add` snapshots. Skills don't auto-update; without a nudge they go stale and sessions hit `error: unrecognized arguments: --foo` from out-of-date binaries.

**How**: `SessionStart` hook → `scripts/skill-freshness-hook.sh` checks the timestamp of `~/.config/broomva/p8/last-skill-update-check`. If ≥ 7 days old (or never), prints a one-line nudge with refresh command + dismissal `touch`. Always exits 0.

**Invariant**: hook always exits 0. `BROOMVA_P8_THRESHOLD_DAYS` env var configurable (default 7). Dismissal: run `npx skills update -g` then `touch ~/.config/broomva/p8/last-skill-update-check`.

---

## P9 — Branch + Worktree Janitor

**Closes**: squash-merged branches and dead worktrees accumulate. `git branch --merged` doesn't catch squash-merges (the branch tip isn't an ancestor of main).

**How**: `make janitor` (wraps `scripts/branch-janitor.sh`). Walks current repo (or all workspace repos with `--scope=workspace`). For each non-protected branch matching the include pattern (`feat/*,fix/*,chore/*,docs/*` by default): runs the canonical squash-merge detection — `git commit-tree <branch-tree> -p <merge-base>` produces a synthetic commit; `git cherry origin/main <synth>` reports if its patch is in main. If yes, branch is mergeable. Worktrees whose underlying branch is gone get pruned via `git worktree remove --force`.

**Invariant**: default `--dry-run` — pass `--apply` to actually delete. Never touches main, master, develop, HEAD, gh-pages, or any branch in `~/.config/broomva/p9-janitor/protected.txt`. Currently-checked-out branch always skipped.

---

## P10 — Worktree Hygiene Discipline

**Closes**: dirty trees, half-finished branches, orphan worktrees accumulating across sessions and becoming slow leaks of merge conflicts and "what was I doing?" amnesia.

**How**: reasoning-enforced rule, not a hook. P5 provides the *mechanism* (git worktrees); P10 provides the *discipline* — when to use them, how to maintain hygiene during development, how to clean up after merge.

**Invariant**: after every PR merge, both the worktree (if any) and the branch are gone. Before starting any new substantial work, `git status` is clean (or the agent explicitly notes the dirty state and gets user direction). The "default to worktree" rule has documented exceptions — typo fixes, single-file doc edits, read-only research, work continuing an existing branch you already own — but those exceptions are evaluated and named, not assumed.

### P10 Reflexive Trigger Rule (binding on every agent)

P10 is a reflex, not a request. Agents must apply the following without being prompted:

1. Before writing the first file of any new substantial work — decide whether a worktree is needed and **state the choice in your response**. Default *yes* for new feature/spec/research, multi-file work, work that might take more than ten minutes, work that could conflict with other in-flight branches. Default *no* for typo fixes, single-file doc edits, read-only investigation, work continuing an existing branch.
2. Before pushing to remote — run `git status` mentally; if dirty with WIP that's not part of the PR, decide: *commit-as-WIP*, *stash with reason*, or *extract to a separate branch*. Don't push past lingering uncommitted state.
3. After PR merge — immediately run `make janitor` (P9) or `git worktree remove` + `git branch -D` directly. Never start a new work unit on top of a merged-but-uncleaned branch.
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
4. When orchestrating long-horizon work — default to persist + periodic checkpoints; compose with P5 (one persist loop per worktree) and P7 (each iteration's PR uses `p9 watch`).
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

## Cohesion narrative

P11, P12, and P13 are structural siblings at different scales:

| Primitive | Discipline | Surface | Evidence | Scale |
|---|---|---|---|---|
| **P11** Empirical Feedback | "validate by interacting" | live deployed system | screenshots, logs, browser session | in-session (≤1h) |
| **P12** Persistent Loop | "restart fresh when context rots" | filesystem (PROMPT.md + git) | state.jsonl + each iteration's evidence | cross-session (>1h) |
| **P13** Dream Cycle | "consolidate by replaying" | frozen substrate | diff against frozen snapshot | tier-crossing |

The whole stack composes:

- **P4** (PR Pipeline) and **P7** (CI Watcher) catch what CI sees; **P11** catches what CI can't; **P13** catches what consolidation without replay can't.
- **P10** (Worktree Hygiene) keeps the working tree clean enough for empirical checks to be meaningful — same shape as P13's "frozen substrate" requirement at the knowledge layer.
- **P6** (Bookkeeping) is the first concrete implementation of P13's discipline — `bookkeeping replay` is the canonical reference dream cycle.
- **P12** (Persist) is the substrate for long-horizon work that needs P11/P13 discipline across many iterations.
- **P1** (Conversation Bridge) preserves dogfood receipts and dream-cycle audit trails across sessions.
- **P8** (Skill Freshness) ensures the validation/replay tools (gstack, bookkeeping, persist) are themselves current.
- **P9** (Janitor) ensures cleanup state is automatic so the next cycle starts from zero.

The thirteen primitives compose into the full autonomous development loop:

```
User intent → Linear ticket (P3) → Agent dispatched (P5)
  → Prior context loaded (P1) [+ P8 freshness check] [+ P10 cleanup audit]
  → Safety gates active (P2)
  → P10 worktree decision → P11 validation plan
  → IF long-horizon → P12 persist loop with PROMPT.md + budget
  → Code written + parallel watchers (P11 log-tails) → PR created (P4)
  → CI watched + heal loop (P7)
  → P11 deploy verification (preview URL, screenshots, browser session)
  → Merge → P10 post-merge cleanup via P9 janitor → Deploy
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
