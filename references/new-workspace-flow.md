# New-workspace flow — what happens on first install

The concrete sequence from `git clone` to a fully-wired RCS-closed workspace.

## Install + onboard (one command each)

```bash
git clone https://github.com/broomva/bstack.git && cd bstack   # bstack is a CLI, not a skill
./bin/bstack onboard                  # wizard: workspace · profile · life · auto-merge
# — or, without the wizard —
./bin/bstack bootstrap                # scaffold governance + wire hooks + install roster + wire the loop
```

Both paths wire the RCS control loop. `bootstrap.sh` scaffolds governance files (incl. `.control/arcs.yaml`), wires the base hooks, then — in **Phase 3.5** — calls `install-rcs-stability.sh` to deploy the multi-layer audit + enforcement plumbing. `onboard.sh` additionally runs the wizard and detects the tech stack. Skip loop wiring with `BSTACK_SKIP_RCS=1` (governance-only bootstrap). Previously only `onboard` wired the loop; `bootstrap` left it open — that split-brain is closed as of 0.22.0.

## Files deployed into the workspace

| Path | Source | Purpose |
|---|---|---|
| `CLAUDE.md`, `AGENTS.md`, `.control/policy.yaml`, `METALAYER.md` | `assets/templates/*.template` | Governance substrate (Development Philosophy section, P-row primitives table, reflexive trigger rules, gate config). The philosophy section states the four guiding principles (think-before-coding · simplicity-first · surgical-changes · goal-driven) and backs each with the primitive(s) that hold it, so downstream development inherits the *intent* behind the primitives — and the user can extend it with project-specific principles. |
| `.control/arcs.yaml` | `arcs.yaml.template` | Closure-contract arcs — the workspace's own editable loop definitions (5-tuple). Scaffolded by `bootstrap.sh` Phase 2 |
| `.githooks/pre-commit` | `githook-pre-commit-l3-rate.sh.template` | G1 — blocks `git commit` over τ_a₃ L3 commit rate (bypassable with `--no-verify`) |
| `.github/workflows/l3-stability.yml` | `gh-workflow-l3-stability.yml.template` | G2 — runs `compute-lambda` + `l3-rate-gate` on every PR touching L3 paths; comments verdict |
| `.claude/settings.json` (merged) | `settings.json.l3-stability-hook.snippet` + `settings.json.multi-layer-hooks.snippet` + `settings.json.snippet` | PreToolUse `L3-G0`, Stop `loop-sensor` (the real `leverage-sensor.py` — replaces the fake `L0-audit`/`L1-audit` hooks that read fields Claude Code never emits), SessionStart `loop-wire` (actuation) |
| `.control/rcs-parameters.toml` | `rcs-parameters.toml.template` | γ/L_θ/ρ/L_d/η/β/τ̄/ν/τ_a per layer + `[derived.lambda]` cache + `[gates.l3_paths]` patterns |
| `.control/audit/` (empty dir) | — | Accrues `l0-tools.jsonl`, `l1-reflexes.jsonl`, `l2-promotions.jsonl`, `l3-edits.jsonl` as events fire |
| `AGENTS.md` `## Dogfood Plan (Stack: <detected>)` | Auto-filled by `onboard.sh` | Stack-keyed dogfood plan stub for the agent to fill before substantive work |

## What fires automatically from session 1

- **Every session end** → Stop hook → `leverage-sensor.py` reads raw session transcripts (throttled to recompute ≤ every 6h) → derives 6 metrics tagged by RCS level (L0 tool-error/read-before-edit/permission-bypass · L1 continue-nudges · L2 kg-load · L3 meta-work ratio) + a per-level closure verdict → writes `.control/leverage-state.json`. Every number is a transcript *fact* (`tool_result.is_error`, `message.content[].type`), so the sensor is causally independent of the agent it grades (h ⟂ U) — unlike the retired `l0-tools`/`l1-reflexes` hooks, which read `latency_ms`/`tool_call_count` (fields CC never emits → 100% null/zero) and grepped the agent's own prose.
- **Every session start** → SessionStart hook → `knowledge-wakeup-hook.sh` renders the cached snapshot and injects the worst-gap metric + its named corrective actuator (and any not-closed / unsigned-reference warning) into the new context — the closure wire that makes the next session start by knowing its own top failure mode.
- **Every Edit/Write to an L3 path** (CLAUDE.md, AGENTS.md, .control/policy.yaml, .control/rcs-parameters.toml, METALAYER.md) → PreToolUse hook → warning + `.control/audit/l3-edits.jsonl` entry (does not block)
- **Every `git commit`** touching L3 paths → `.githooks/pre-commit` → counts L3 commits in last τ_a₃ window → exits 1 if > budget
- **Every PR** touching L3 paths → GH Actions workflow → posts stability report comment + status check (fails if any λᵢ ≤ 0)

## What `bstack doctor` reports

§1–§13 v0.13.0 substrate checks · **§4b Development Philosophy advisory** (informational since 0.25.0 — flags an AGENTS.md that predates the 0.24.0 templated section; backfill with `bstack repair`; never a GAP, never fails `--strict`) · §14 RCS λ compute + drift · §15 G0/G1/G2 wiring · §16 L0 tool-call audit summary · §17 L1 reflex compliance · §18 L2 promotion throttle · §19 multi-layer composite health (`L0=stable L1=stable L2=stable L3=stable` form) · §20 federation registry · §21 closure-contract arcs · §22 composite-ω drift trend · **§23 control-loop closure verdict** — now **content-aware**: it reads the leverage-sensor's own per-RCS-level verdict in `.control/leverage-state.json` and answers *is the loop wired + is the sensor actually alive + is every level (L0–L3) producing live signal + is the reference authored?* A dead/fake sensor (all metrics null over 0 sessions) now **FAILS** as a gap instead of passing as "closing" — the blind-checker bug is closed. States: not-wired / wired-but-idle / **sensor-DEAD (gap)** / **open-at-level-N (gap)** / closed (+ a warning if the reference `r0` is still `bstack-default`, i.e. endogenous). §16–§18 (the older per-layer audit summaries that fed the retired l0/l1 hooks) go informational-only on new installs and are superseded by §23's content-aware verdict; repointing `compute-lambda` off those legacy logs is a tracked follow-up. New workspaces show §23 as informational ("wired but idle") until the first Stop-hook run; for CI lanes that must fail on an idle loop, run `BSTACK_LOOP_STRICT=1 doctor.sh --strict` — `BSTACK_LOOP_STRICT=1` records the gap but only `--strict` changes the exit code, so **both** are required.

## Common gotchas

- **Python < 3.11** — λ computation needs `tomllib`; scripts degrade gracefully but lose the math. Install Python 3.11+ before onboarding.
- **Bstack install path is captured at onboard time** in `.claude/settings.json` hook `command` strings. If you move bstack (e.g. `~/.claude/skills/` → `~/.agents/skills/`), re-run `bstack repair`.
- **Pre-existing `.githooks/pre-commit`** — installer preserves your hook as `.githooks/pre-commit.local` and chains to it. Custom `core.hooksPath` is detected; manual merge surfaced as `[warn]`.
- **GH Actions workflow** clones bstack at `https://github.com/broomva/bstack`. Air-gapped repos need to vendor `.agents/skills/bstack/` into the repo.
- **Branch protection** isn't auto-configured; add `L3 stability gate / stability-check` to required checks in repo settings manually.

## Re-run + repair

`bstack onboard --force` redoes the wizard. `bstack repair` detects missing pieces (G0/G1/G2 hooks, audit dir, parameters.toml) and re-runs the relevant installer. Both are idempotent — existing files are preserved unless `--force` is passed; settings.json merges are structurally idempotent via `_bstack_primitive` markers.

`bstack repair` also **backfills newly-templated *content*** into existing governance files where the scaffold's never-overwrite policy would otherwise skip it. Since 0.25.0 it inserts the `## Development Philosophy` section (templated in 0.24.0) into a pre-existing `AGENTS.md`/`CLAUDE.md` — runs before the "fully bstack-compliant" early-exit (like the hook merge), is insert-only + idempotent, and skips with a warning if the `## Bstack Core Automation Primitives` anchor is absent (never guesses a location).

## See also

- `references/primitives.md` §P11 — Empirical Feedback Loop discipline
- `references/dogfood-patterns.md` — per-stack cookbook (Tauri+sidecar / Next.js / Expo RN / Rust CLI / REST API / MCP server)
- `~/broomva/docs/reports/2026-05-22-multi-layer-closure-spec.html` — architecture spec
- `~/broomva/docs/reports/2026-05-22-autonomous-flow-achieved.html` — /autonomous 21-reflex composition
