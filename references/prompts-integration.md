# bstack — Prompts Library Integration

bstack treats the broomva.tech prompts library as a **shared knowledge surface across agents**, not a private repository. Every bstack-driven workspace gets two things from this surface:

1. **Reusable directives** — versioned, parameterized prompts that any agent can pull and apply (code review, deep research, repo audits, refactoring, etc.).
2. **An evaluation engine** — every pull and every completion writes a typed row to `prompt_invocation`, so the platform learns which prompts compound and which decay. The agent reading this *closes its own evaluation loop* by following the mandate below.

This file is the canonical bstack guide. The skill-level mandate lives in the `broomva-cli` skill SKILL.md and is reproduced here for reflexive reference.

---

## When a bstack-driven agent should reach for the prompt library

Use existing prompts when you're about to do work that has a **named pattern in the library**. Common cases:

| User asks for / Agent needs | Reach for |
|---|---|
| Code review of a diff or file | `code-review-agent` |
| Deep architectural analysis of a new repo / area | `codebase-deep-analysis` or `ecosystem-repo-architect` |
| First-principles platform redesign | `ai-native-platform-architect` |
| Rigorous "think harder" framing | `deep-thinking-directive` |
| Multi-source research with citations | `deep-research-agent` |
| Autonomous PR review + resolution | `pr-review-agent` |
| Security attack-surface assessment | `security-attack-surface` |
| Refactor a brittle data pipeline | `secure-refactor-agent` |
| Brutally honest strategic advice | `brutally-honest-advisor` |
| Bootstrapping the agent harness in a fresh repo | `bstack-control-harness-bootstrap` |

Discover what's available with `broomva prompts list --metrics --sort skill_invokes` — the highest-invocation prompts are the proven ones.

**Do not reach for a prompt when**: the work is one-off, the directive would be longer than the work itself, or you'd be reinventing existing context that the surrounding session already carries.

---

## The mandate (5 steps, MANDATORY)

When you do reach for a prompt, follow these steps exactly. Skipping step 4 breaks the evaluation loop and the library can't learn.

### Step 1 — Tag the session as a skill invocation

Once per session (terminal or agent):

```bash
export BROOMVA_SOURCE=skill
```

This sets `source='skill'` on every invocation row. Without it, the row would carry `source='cli'` and the evals dashboard would conflate skill-driven runs with terminal-driven ones. Terminal users default to `cli`; programmatic wrappers should set `api`.

### Step 2 — Pull the prompt with `--json`

```bash
broomva prompts pull <slug> --json 2>&1 | tee /tmp/broomva-last.json
```

The CLI writes the prompt body to `<slug>.md` (or `-o path`) and emits a single JSON line on stderr:

```json
{"invocation_id":"<uuid>","prompt_slug":"<slug>","prompt_version":"<v>","posted":true}
```

Capture the `invocation_id` — you'll need it for step 4. The pull fires `POST /api/invocations` server-side; that row's `status='pulled'` until you complete it.

### Step 3 — Use the prompt body

Treat the body as your system instructions for the task the user gave you. Apply it directly to the work. Do not paraphrase or shorten the body unless the user explicitly asks for a condensed form.

### Step 4 — Complete the invocation (MANDATORY)

After you finish the work the prompt asked for:

```bash
broomva prompts complete <invocation_id> \
  --status completed \
  --model <your-model-name> \
  --latency-ms <wall-clock-ms> \
  --tokens-in <input-tokens> \
  --tokens-out <output-tokens>
```

This PATCHes the row to `status='completed'` and the server computes `cost_usd` from the model + token counts via the static pricing table. Use `--status failed` if you could not complete the work and include `--error-message <reason>`.

**This is how prompts get evaluated.** A `pulled` row that never gets completed is invisible to the eval engine. The 24h sweeper eventually flips it to `abandoned`, but that's a quality signal, not a useful run.

### Step 5 — Capture feedback if the user gave it

If the user gave a clear thumbs up/down in the conversation:

```bash
broomva prompts feedback <invocation_id> \
  --slug <slug> \
  --signal up \
  --text "<user's words, max 2000 chars>"
```

Optional but high-signal — explicit feedback weights more than pass-rate in the eval ranking.

---

## Discovery — finding the right prompt

```bash
# Browse all prompts (basic table)
broomva prompts list

# Find the most-invoked prompts (proven by usage)
broomva prompts list --metrics --sort skill_invokes

# Filter by category or tag
broomva prompts list --category agent-instructions
broomva prompts list --tag security
broomva prompts list --model claude-opus-4.5

# Get one prompt
broomva prompts get <slug>          # rendered with frontmatter
broomva prompts get <slug> --raw    # body only
broomva prompts get <slug> --json   # full JSON

# Browse the web UI
open https://broomva.tech/prompts
```

If no existing prompt fits, write one — and **push it to the library so future sessions inherit it**:

```bash
broomva prompts push <file.md> --create
```

The MDX file needs frontmatter (`title`, `category`, `model`, `version`, `tags`, `visibility`). Categories: `system-prompts`, `agent-instructions`, `templates`, `chains`, `evaluators`.

---

## Source attribution at a glance

Every invocation row carries a `source` enum that tells the eval engine where the work came from:

| Source | When | Set how |
|---|---|---|
| `skill` | Claude Code session via the broomva-cli skill | `export BROOMVA_SOURCE=skill` |
| `cli` | Terminal user typing `broomva prompts pull ...` | default (no env) |
| `api` | Programmatic wrapper around the CLI | `export BROOMVA_SOURCE=api` |
| `web` | "Copy" button on broomva.tech/prompts/[slug] | server-side, automatic |

Anonymous web copies still write rows (`user_id=null`, only daily-salted `client_ip_hash`). Skill and CLI invocations from authenticated CLI users carry `user_id` from the bearer token.

---

## Environment variables

| Variable | Purpose |
|---|---|
| `BROOMVA_TOKEN` | Bearer auth for the CLI. Get one via `broomva auth login` (device-code flow). |
| `BROOMVA_SOURCE` | `cli` (default) / `skill` / `api`. Sets `source` on every invocation row. |
| `BROOMVA_TELEMETRY_DISABLED=1` | Opt out — no rows are written. Use sparingly; the eval engine starves without volume. |
| `BROOMVA_TELEMETRY_RAW_VARS=1` | Admin-only: send raw variable values instead of SHA-256 hashed. |
| `BROOMVA_API_BASE` | Override API host (default `https://broomva.tech`). |
| `BROOMVA_SESSION_PATH` | Override the per-shell session-id cache (default `~/.broomva/session`). |

---

## Interaction with bstack primitives

The prompt library composes cleanly with the existing primitive contract:

- **P1 (Conversation Bridge)** — when an agent pulls a prompt and applies it, the entire session is captured in the conversation log. The invocation id appears in the bridge's structured docs, providing a backpointer from "this conversation used prompt X" to the eval engine's run feed.
- **P4 (PR Pipeline)** — when `pr-review-agent` runs on a PR, it should complete the invocation with the real review outcome. Future PR-review prompt versions then have measurable pass rates.
- **P6 (Knowledge Bookkeeping)** — synthesis-worthy prompts (those that compound across multiple agents) get promoted to entity pages in `research/entities/concept/` and `research/entities/pattern/`. The prompt library is the *runtime registry*; the knowledge graph is the *crystallized form*.
- **P11 (Empirical Feedback Loop)** — completing the invocation with real `tokens_in/out`, `latency_ms`, `error_message` is the same discipline as P11: validate with measurable outcomes, not vibes. Step 4 of the mandate IS P11 for prompt invocations.
- **P13 (Dream Cycle)** — the eval engine's evolving rankings are a dream-tier substrate (slow, sparse, high-trust). Per-run telemetry is the dense lower-tier signal; "promote prompt X to a v2 with these tweaks" is the kind of decision that should follow a replay-against-frozen-snapshot pattern, not a live overwrite.

---

## Common traps

- **Pulling a prompt and not completing it.** The most common silent failure. The CLI emits the invocation id loudly; capture it. Set a TodoWrite reminder if the work is long-running.
- **Completing with fake token counts.** The cost computation depends on real numbers. If you don't know exact counts, estimate honestly — never zero-fill.
- **Treating the prompt body as a suggestion.** The directive is a directive. If it conflicts with the user's actual request, surface the conflict — don't silently water down the prompt.
- **Forking a prompt by writing a new variant locally.** If you find yourself editing a pulled prompt to fit your task, that's a signal to push a v2 (or a new slug) to the library so the change is durable.
- **Using `--telemetry-disabled` to silence the beacon.** Defeats the entire point. Only use when working with sensitive content the eval engine should not see, and document the reason.

---

## Quick reference (printable)

```
SETUP:    export BROOMVA_SOURCE=skill                       # once per session
PULL:     broomva prompts pull <slug> --json 2>&1 | tee /tmp/broomva-last.json
WORK:     <use the prompt body as instructions>
COMPLETE: broomva prompts complete <id> --status completed \
            --model <name> --latency-ms <ms> --tokens-in <n> --tokens-out <m>
FEEDBACK: broomva prompts feedback <id> --slug <slug> --signal up --text "..."
LIST:     broomva prompts list --metrics --sort skill_invokes
```

---

## See also

- `broomva-cli` skill SKILL.md — the source-of-truth mandate, mirrored here
- `docs/superpowers/specs/2026-05-09-prompts-eval-engine-design.md` — full design spec for the eval engine
- broomva.tech/prompts — the browsable web surface
- `bstack-control-harness-bootstrap` (slug) — the prompt to use when standing up a fresh agent harness
