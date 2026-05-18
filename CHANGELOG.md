# Changelog

## 0.7.0 — 2026-05-18

### Companion skill auto-install (Phase 4 of substrate completion)

The 31-skill companion roster is now a canonical YAML file with a `bstack skills` subcommand that installs, checks, and lists. Closes the install-the-roster gap — previously the bash `ROSTER=(...)` array in `SKILL.md` was *checked* but never *installed*.

- **NEW** `references/companion-skills.yaml` — **canonical companion-skill roster** (31 skills). Per entry: `name`, `repo`, `category`, optional `primitive` (P-id this skill embodies), optional `required` flag, `introduced_in`, `description`. Single source of truth.
- **NEW** `schemas/companion-skills.v1.json` — JSON Schema for the roster YAML.
- **NEW** `bin/bstack-skills` — roster manager subcommand dispatcher:
  - `bstack skills install [--all] [--interactive] [--required-only] [--dry-run]` — installs missing (or all) skills via `npx --yes skills add <repo>`. Idempotent default (skip already-installed). `--dry-run` shows what would happen. `--required-only` installs the `required: true` subset only (fast onboarding path).
  - `bstack skills status [--json]` — text or JSON output of installed/missing/total + per-skill version (reads `~/.agents/skills/<name>/VERSION` or `SKILL.md` frontmatter).
  - `bstack skills list [--json] [--required-only]` — print declared roster without touching filesystem.
- **EDIT** `bin/bstack` — register `skills` subcommand + new help entry under `Observability:`.
- **NEW** `tests/skills-roster.test.sh` — 8 fixture-based tests covering: YAML validation, list count, `--required-only` filter, list `--json` shape, status text + JSON, install `--dry-run` non-invocation, install with mock-npx invocation tracking. Added to vetted CI suite.

### Env overrides

- `BSTACK_SKILLS_YAML` — override roster YAML path (test fixtures)
- `BSTACK_NPX_CMD` — override the `npx --yes skills add` invocation (test mocks)
- `BSTACK_DIR` — override bstack root

### Closes gaps from substrate completion spec §4

- **4.3.1** — 31 companion skills checked but not auto-installed → `bstack skills install` shipped
- **4.4.4** — ROSTER count drift ("27" descriptor vs 31 array entries) → canonical YAML resolves; `SKILL.md` ROSTER stays in place for backward compat (broomva.tech install script depends on it; CI lint catches drift in future phase)

### Out of scope (deferred)

- `references/primitives.md` regen from `primitives.yaml` (Phase 3 deferred to follow-up — same pattern applies here for `SKILL.md` ROSTER ↔ `companion-skills.yaml`)
- `bstack onboard --json <answers>` for pre-filled onboarding (mentioned in Phase 4 spec but separable; will land in 0.7.1 if needed)
- `scripts/bootstrap.sh` calling `bstack skills install --suggest` automatically — current behavior is informational (status + list); auto-install on first-time setup would surprise users. Deferred to Phase 5 doctor-extensions PR which can add a confirm-and-install flow.

### SLO targets (introduced)

- `bstack skills install` (clean machine, 31 skills): p50 < 60s, p99 < 180s (network bound)
- `bstack skills status` (filesystem-only): p50 < 200ms, p99 < 500ms
- `bstack skills list`: p50 < 100ms, p99 < 300ms (YAML parse only)

Spec reference: §6 Phase 4 of [specs/2026-05-18-substrate-completion.md](specs/2026-05-18-substrate-completion.md).

## 0.6.0 — 2026-05-18

### Schema versioning + canonical primitive registry (Phase 3 of substrate completion)

The substrate's declarative surfaces (`.control/policy.yaml`, primitive metadata) are now schema-versioned. Workspaces can be validated against the bstack contract via `bstack doctor` (extended) — and migrations are framework-ready for future v2.

- **NEW** `schemas/setpoint.v1.json` — single setpoint shape (id pattern, target, alert bands, severity, owner, introduced_in).
- **NEW** `schemas/gate.v1.json` — single gate shape (id, rule, pattern, enforcement, severity, profiles, bypass_audit).
- **NEW** `schemas/primitives.v1.json` — primitive registry shape (id, short_name, mechanism, invariant, failure_mode, rule_of_three, skill_repo, introduced_in).
- **NEW** `schemas/policy.v1.json` — full `.control/policy.yaml` shape composing setpoint + gate via `$ref`.
- **NEW** `references/primitives.yaml` — **canonical machine-readable registry of all 20 primitives**. Single source of truth. Validates against `primitives.v1.json`. Each entry carries id, short_name, mechanism.type + .spec, invariant, failure_mode, introduced_in.
- **NEW** `assets/templates/METALAYER.md.template` — bstack-shipped METALAYER template scaffolded by `bstack bootstrap`. Was previously workspace-only; closes Gap 4.1.4 from the substrate completion spec.
- **NEW** `scripts/migrate.sh` — schema migration framework. `v1 → v1` is identity (no-op); future `vN → vN+1` migrations registered via dispatch table. Backs up policy.yaml to `.bak.<epoch>` before any structural change. Respects `--dry-run` and `--apply-all`.
- **CHANGED** `scripts/doctor.sh` — new **Section 10: schema validation**. Validates `references/primitives.yaml` against `primitives.v1.json` and workspace `.control/policy.yaml` against `policy.v1.json` (with `$ref` resolver). Degrades gracefully when `python3` / `jsonschema` / `PyYAML` are absent.
- **NEW** `tests/schema-validation.test.sh` — 8 fixture-based tests: all 4 schemas valid draft-07, primitives.yaml validates, policy.yaml.template validates, invalid setpoint/gate/primitive rejected, migrate.sh v1→v1 no-op, migrate.sh `--dry-run` is non-mutating. Added to vetted CI suite.

### Backward-compat consideration

The current `policy.yaml.template` uses `severity: warn` and (in some downstream workspaces) `severity: soft`. To accept these in v1 without breaking existing workspaces, both schemas list them as v1-era synonyms (canonical: `informational` and `advisory`). Schema v2 will enforce canonical names — a migration in `scripts/migrate.sh` will rename automatically.

### What's NOT included this PR

- `references/primitives.md` auto-generation from `primitives.yaml` — deferred to a follow-up. The YAML is the canonical machine-readable source; the prose `.md` remains hand-written for now. Generator could be added in v0.6.1 if drift becomes an issue.
- `validate-release.yml` schema-check extension — `bstack doctor` Section 10 covers the local check; a CI-side gate is deferred to Phase 5 (doctor extensions in CI lane).

### Closes gaps from the substrate completion spec

- 4.1.4 — `METALAYER.md` workspace-only → now templated
- 4.4.2 — no policy.yaml schema → 4 schemas shipped
- 4.4.3 — no deprecation path for retired primitives → `deprecated_in` / `retired_in` fields added
- 4.6.1 — contracts not formalized as schemas → 4 schemas formalize 4 of the 8 contracts (Setpoint, Gate, Primitive, Plant-via-policy.yaml)

### SLO targets (introduced)

- `bstack doctor` schema validation: p99 < 2s (4 schemas, ~20 primitives, 15 setpoints, 11 gates).
- `scripts/migrate.sh` v1→v1 identity: p99 < 200ms.
- CI schema validation step: p99 < 5s.

Spec reference: §6 Phase 3 of [specs/2026-05-18-substrate-completion.md](specs/2026-05-18-substrate-completion.md).

## 0.5.0 — 2026-05-18

### Substrate status surface (Phase 2 of substrate completion)

The metrics pipeline shipped in v0.4.0 now has a readable face. A single command surfaces substrate health across 8 dimensions — Plant, Setpoints, Gates, Primitives, Companion skills, Bridge, RCS stability, Last upgrade — in colored text for humans or JSON for tooling.

- **NEW** `bin/bstack-status` — health summary dispatcher.
  - `bstack status` — colored text summary (ANSI-aware, TTY-detecting; `--no-color` opt-out).
  - `bstack status --json` — single JSON object: `{bstack_version, workspace, profile, generated_at, setpoints, summary}` with `summary` carrying derived counts (`setpoints_in_target`, `primitives`, `gates_total`, `gate_bypass_attempts_24h`, `rcs_l3_lambda`, `last_upgrade`). Intended for CI / external consumers / status badges.
  - `bstack status --setpoint S<n>` — detailed single-setpoint view (text or `--json`).
  - `bstack status --aggregate` — placeholder for Phase 8 federation; exits 3 with explanation.
  - `--no-collect` — render from cached `~/.bstack/metrics/latest.json` even if stale (default behavior auto-runs `bstack metrics collect` when the cache is missing or > 5 min old).
- **EDIT** `bin/bstack` — register `status` subcommand. New `Observability:` `--help` line.
- **NEW** `tests/status-surface.test.sh` — 8 fixture-based tests covering: all 8 sections render, `--json` shape, `--setpoint` text + JSON modes, unknown-setpoint error, `--aggregate` placeholder, `--no-color` strips ANSI, auto-collect on stale cache. Added to vetted CI suite.

### Composition with v0.4.0

`bstack-status` is a pure reader. It calls `bstack metrics collect` itself when needed so users running `bstack status` cold (e.g. fresh install) still see a populated panel. Cache TTL (5 min for status's own collection trigger; 60s for metrics's own cache) keeps repeated invocations cheap.

### Data sources

| Section | Source |
|---|---|
| Plant | derived from S11 (governance files) + S12 (hooks wired) in `~/.bstack/metrics/latest.json` |
| Setpoints | all setpoints in `latest.json`, classified vs alert thresholds, counted as `in_target/measured` |
| Gates | grep on `.control/policy.yaml` for `^\s+- id: G[0-9]+` |
| Primitives | grep on `CLAUDE.md` (workspace) or `assets/templates/CLAUDE.md.template` for primitive table rows; falls back to 20 |
| Companion skills | S10 in `latest.json` |
| Bridge | S13 in `latest.json` |
| RCS stability | parse `~/<workspace>/research/rcs/data/parameters.toml` for `l3` lambda (gracefully degrades when absent) |
| Last upgrade | `~/.bstack/just-upgraded-from` (if present) + `~/.bstack/last-update-check` cache |

### Bug fix from earlier development (caught in smoke)

The first iteration of `primitive_count()` used `grep -cE PATTERN file || echo 0` which double-emits `"0\n0"` on BSD/macOS grep (where `grep -c` exits 1 on zero matches AND outputs `0`). Replaced with `|| true` + `tr -d` whitespace + default. Same fix applied to `gate_count()` and `gate_bypass_count_24h()`. The regex was also corrected from `^\| \*\*P[0-9]+\*\*` (matches no rows) to `^\| P[0-9]+ \|` (matches actual table format).

### SLO targets (introduced)

- `bstack status` (cached metrics): p50 < 500ms, p99 < 1s
- `bstack status` (cold, auto-collects first): p50 < 2.5s, p99 < 6s
- `bstack status --setpoint <S-id>`: p50 < 100ms, p99 < 300ms (single jq read)

Spec reference: §6 Phase 2 of [specs/2026-05-18-substrate-completion.md](specs/2026-05-18-substrate-completion.md).

## 0.4.0 — 2026-05-18

### Setpoint measurement pipeline (Phase 1 of substrate completion)

The first measurable substrate. Every setpoint declared in `.control/policy.yaml` that the substrate can compute now has a measurement script, and a new top-level CLI surfaces them.

- **NEW** `bin/bstack-metrics` — the measurement dispatcher.
  - `bstack metrics collect [--no-cache] [--json]` — runs every `scripts/metrics/measure-S<n>.sh` discovered in the bstack install, aggregates outputs into `~/.bstack/metrics/latest.json` under `{generated_at, setpoints}`. TTL-cached (default 60s).
  - `bstack metrics observe <S-id>` — single-setpoint JSON to stdout. Useful for `--setpoint` queries in Phase 2's `bstack status`.
  - Per-script timeout: 2s (configurable via `BSTACK_METRICS_TIMEOUT`). Failing scripts produce `{value: null, error: <kind>}` rather than blocking the whole run.
- **NEW** `scripts/metrics/measure-S<n>.sh` — six substrate-measurable setpoints from `.control/policy.yaml`:
  - `S10 bstack_skills_installed` — union count under `~/.agents/skills` + `~/.claude/skills`.
  - `S11 governance_files_present` — checks CLAUDE.md + AGENTS.md + METALAYER.md + .control/policy.yaml + schemas/. Reports missing list.
  - `S12 hooks_wired` — pre-commit + Stop hook + PreToolUse hook. Reports present/missing arrays.
  - `S13 bridge_freshness_seconds` — mtime of `~/.cache/broomva-bridge-stamp`. `null` with `error: stamp-missing` when never run.
  - `S14 conversation_sessions_indexed` — count of `docs/conversations/session-*.md`.
  - `S15 pii_redaction_active` — greps `_redact_pii`/`redact_pii`/`redactPII` in the workspace bridge script. Returns `1` (true) or `0` (false).
- **EDIT** `bin/bstack` — register `metrics` subcommand in the dispatcher. New `Observability:` section in `bstack --help`.
- **NEW** `tests/metrics-pipeline.test.sh` — 7 fixture-based tests covering: aggregate shape, single-setpoint observe, TTL cache honored, --no-cache bypass, per-script JSON validity + id matching, missing-script error, top-level JSON output shape. Added to vetted CI suite.
- **EDIT** `VERSION` → `0.4.0`.

### Output contract

Each `measure-S<n>.sh` emits one line of JSON on stdout matching:

```json
{
  "id": "S<n>",
  "name": "<snake_case_setpoint_name>",
  "value": <number|bool|null>,
  "target": <number|null>,
  "alert_below": <number>,   // OR alert_above
  "severity": "blocking" | "informational",
  "unit": "count" | "seconds" | "bool" | "ratio",
  "... optional fields ..."
}
```

The aggregate `latest.json` wraps these in:

```json
{
  "generated_at": "<iso8601>",
  "setpoints": {
    "S10": { ... },
    "S11": { ... },
    "...": { ... }
  }
}
```

This shape is informal in v0.4.0 and formalized via JSON Schema in v0.6.0 (Phase 3).

### Out of scope (Phase 1)

S1, S2, S4, S5, S6, S7, S8, S9 — measurement requires substrate-external infrastructure (CI history, EGRI runtime, shield instrumentation). They're simply absent from the metrics output until later phases stand up the supporting telemetry. Their presence in `policy.yaml` remains declarative.

### SLO targets (introduced this release)

- `bstack metrics collect` (full, cold): p50 < 2s, p99 < 5s
- `bstack metrics observe <S-id>` (single): p50 < 200ms, p99 < 1s
- Per-script timeout: 2s, fail-soft to structured error

Spec reference: §6 Phase 1 of [specs/2026-05-18-substrate-completion.md](specs/2026-05-18-substrate-completion.md).

## 0.3.1 — 2026-05-18

### Auto-release on merge-to-main

Closes the last manual step in the release workflow. When a PR bumping VERSION merges to main, GitHub Actions now tags `vX.Y.Z` and creates the GitHub Release automatically — using the matching `## X.Y.Z` section of `CHANGELOG.md` as the release body.

- **NEW** `.github/workflows/release.yml` — triggers on `push: branches: [main]` with `paths: [VERSION]`. Reads VERSION, checks if `vX.Y.Z` already exists (idempotent: skips silently if so), extracts the matching CHANGELOG section, creates the annotated tag, pushes it, and runs `gh release create`. The release title is the first `### ` heading inside the section, falling back to the tag. Composes with `validate-release.yml` (PR gate) so this workflow trusts that the merged VERSION is semver, monotonic, and has a CHANGELOG section.
- **CHANGED** `bin/bstack` `release tag` — the clean-tree precondition now only blocks on **modified or staged tracked files**. Untracked files (e.g. workspace-level `.agents/`, `skills-lock.json`, scratch artifacts) no longer prevent the manual helper from running, so it works in a normal development checkout. The error message now lists the offending paths instead of saying "dirty" with no detail.

### Self-validation

This release validates itself: when this PR merges, `release.yml` fires for the first time and creates v0.3.1 automatically — no manual tag or `gh release create` needed.

## 0.3.0 — 2026-05-18

### SessionStart auto-upgrade (push-to-main → live-on-next-session)

bstack now upgrades itself in the background when you start a new Claude Code session, provided you have a git-checkout install and have not opted out. Previously the update check only fired when the user invoked `/bstack` — installs that never invoked the skill stayed pinned forever.

- **NEW** `scripts/bstack-autoupdate-hook.sh` — SessionStart hook. Calls `bstack-update-check` (cached, ≤ 5s curl), and if `UPGRADE_AVAILABLE` is reported, runs `git stash && git fetch && git reset --hard origin/main` in the background so the next Claude session picks up the new release. Writes `~/.bstack/just-upgraded-from` so the preamble's `JUST_UPGRADED` path fires. Log at `~/.bstack/auto-upgrade.log`.
- **CHANGED** `assets/templates/settings.json.snippet` — adds the new SessionStart entry (tagged `_bstack_primitive: "P7"`, timeout 10s, ordered before the existing freshness + role-x hooks). Downstream installs running `bstack repair` (≥ 0.2.3) pick it up idempotently.

### Behavior change: `auto_upgrade` defaults to `true`

This is the silently-different default the bump to 0.3.0 captures. To opt out:

```bash
bstack config set auto_upgrade false   # persistent
# or
BSTACK_AUTO_UPGRADE=0                  # per-session env override
```

### Safety constraints

- **Git installs only.** Vendored installs (no `.git`) get an informational message — no auto-write. The destructive `mv + clone` upgrade path requires user confirmation through `/bstack-upgrade`.
- **`git stash push -u`** preserves any uncommitted local edits to the skill dir. They land in the stash; the auto-upgrade log records the stash message.
- **Backgrounded.** The fetch + reset runs detached so SessionStart's 10s timeout is never the bottleneck. The hook itself only does the cached check + spawn.
- **Cache TTL preserved.** `bstack-update-check` caches `UPGRADE_AVAILABLE` results for 12h, so the hook only does network work at most ~twice/day.

### Migration

Existing installs upgrading to 0.3.0 need the new SessionStart hook wired into their `.claude/settings.json`. Two paths:

1. **Automatic** (recommended) — run `bstack repair` once after upgrading. The merge logic from 0.2.3 picks up the new snippet entry idempotently.
2. **Manual** — run `bstack bootstrap` to re-scaffold from snippet (idempotent, never overwrites).

For installs that *do not want* auto-upgrade as the default, run `bstack config set auto_upgrade false` before upgrading to 0.3.0 (or immediately after — the hook honors the config from its first invocation).

## 0.2.3 — 2026-05-18

### `bstack repair` merges missing hooks (chicken-and-egg fix)

Closes the upgrade gap where a new hook shipped in `assets/templates/settings.json.snippet` would not reach existing installs unless they manually re-ran `bstack bootstrap`. Now `bstack repair` idempotently merges any missing hook entries from the snippet into the user's `.claude/settings.json`. Existing entries are never overwritten or reordered.

- **CHANGED** `scripts/repair.sh` — new `merge_hooks_into_settings` function (Python-driven, mirrors `bootstrap.sh`'s merge logic). Runs unconditionally on every `bstack repair` invocation, *before* doctor's "fully bstack-compliant" early-exit, so a compliant workspace still picks up newly-templated hooks. Silent when everything is in sync. Respects `--dry-run` and `--apply-all`. If `.claude/settings.json` is absent it scaffolds from snippet (replacing the previous "suggest re-running bootstrap" placeholder).
- **NEW** `tests/repair-merge-hooks.test.sh` — 5 fixture-based tests: (1) missing template hook is added, (2) existing user customization preserved, (3) re-running on synced settings is a no-op, (4) absent settings.json is scaffolded, (5) `--dry-run` reports without writing.
- **CHANGED** `.github/workflows/ci.yml` — new `tests` job runs every `tests/*.test.sh` on every PR. Also adds `SC2034` to the shellcheck exclude list (didn't land in 0.2.2's squash due to merge timing; CI would otherwise re-fail on pre-existing unused-vars in `scripts/doctor.sh` and `scripts/statusline-command.sh`).

### Why this is a 0.2.3 (patch) and not 0.3.0

The behavior change is additive — `bstack repair` was already part of the workspace lifecycle. Existing scripted callers (`bstack repair --apply-all` in CI flows) see no breaking change; they just get a more thorough run. The merge function is idempotent, so re-running is safe.

### Unblocks

This is the **prerequisite for 0.3.0** (SessionStart auto-upgrade hook). Without it, the new SessionStart entry shipped in 0.3.0's `settings.json.snippet` wouldn't propagate to existing installs without manual `bstack bootstrap`. With it: `bstack repair` is enough.

## 0.2.2 — 2026-05-18

### Release infrastructure

First formal release with proper OSS tooling. Establishes the foundation that 0.2.3 (`bstack repair` merges hooks) and 0.3.0 (SessionStart auto-upgrade) build on.

- **NEW** `bin/bstack` — top-level CLI dispatcher. Subcommands: `doctor`, `validate`, `repair`, `bootstrap`, `onboard`, `revamp`, `upgrade`, `config`, `update-check`, `wave`, `release tag`, `version`. Existing sub-binaries (`bstack-config`, `bstack-update-check`, `bstack-wave`) remain callable directly — the dispatcher is additive. `bstack release tag` is a maintainer helper that validates the tree, tags `vX.Y.Z`, pushes the tag, and creates the GitHub Release with the matching CHANGELOG section as notes.
- **NEW** `CONTRIBUTING.md` — contribution guide: branch/PR shape, Conventional Commits, primitive-promotion rule, local validation steps.
- **NEW** `RELEASE.md` — semver policy (pre-1.0: minor = potentially breaking), release checklist, retroactive-tag history, cadence guidance, update-check transport docs.
- **NEW** `.github/workflows/ci.yml` — shellcheck on `scripts/*.sh` and `bin/*`, JSON validation for `assets/templates/*.snippet`, `bstack doctor --quiet` on templated fixtures.
- **NEW** `.github/workflows/validate-release.yml` — PR check: if `VERSION` changed, `CHANGELOG.md` must have a matching `## X.Y.Z` section and the version must monotonically increase.
- **CHANGED** `bin/bstack-update-check` — primary source is now the GitHub Releases API (`/repos/broomva/bstack/releases/latest`), with raw `VERSION` on `main` as fallback. **This means dev-branch VERSION bumps no longer leak to downstream installs as "available upgrades"** — only tagged releases do. Two new env vars: `BSTACK_RELEASES_URL` (primary), `BSTACK_REMOTE_URL` (fallback, unchanged behavior).
- **HISTORY** `v0.2.0` and `v0.2.1` tags + GitHub Releases created retroactively on 2026-05-18 to give the update-check transport a stable anchor.

### Migration

None required. Existing installs continue to work — the API-first transport falls back to the raw `VERSION` URL on any failure, so behavior degrades gracefully.

## 0.2.1 — 2026-05-16

### Drop legacy fallback shims from the 0.2.0 renumber

The legacy env vars (`BROOMVA_P8_HOME`, `BROOMVA_P9_JANITOR_HOME`,
`BROOMVA_P8_THRESHOLD_DAYS`) and config-dir fallbacks (`~/.config/broomva/p8/`,
`~/.config/broomva/p9-janitor/`) that 0.2.0 added "for in-place upgrades" are
removed. The canonical paths (`p7/`, `p8-janitor/`) and env vars
(`BROOMVA_P7_HOME`, `BROOMVA_P8_JANITOR_HOME`, `BROOMVA_P7_THRESHOLD_DAYS`) are
the only paths/vars the scripts honor. The legacy-callout prose is stripped
from every governance and substrate document.

**Migration for users upgrading from < 0.2.1**: one-time `mv` of any existing
state into the canonical location:

```bash
[ -d ~/.config/broomva/p8 ] && mv ~/.config/broomva/p8 ~/.config/broomva/p7
[ -d ~/.config/broomva/p9-janitor ] && mv ~/.config/broomva/p9-janitor ~/.config/broomva/p8-janitor
```

After migration: the freshness stamp + janitor `protected.txt` are read from
the canonical paths. No shims in the codebase.

## 0.2.0 — 2026-05-16

### Primitive renumber: Wait moves back to P9 (skill-name↔primitive-number alignment)

The productive-wait primitive (`broomva/p9` skill) is now **P9 (Wait)** again,
matching the skill repo name. Freshness moves to **P7**, Janitor moves to **P8**.

| Before | After |
|---|---|
| P7 = Wait (`broomva/p9` skill — historical name) | P7 = Skill Freshness |
| P8 = Skill Freshness | P8 = Branch + Worktree Janitor |
| P9 = Branch + Worktree Janitor | P9 = Wait (`broomva/p9` skill — name matches number) |

**Rationale**: the interim P8-Freshness / P9-Janitor numbering broke the Name (Pn)
recall key for Wait — the `broomva/p9` skill name no longer matched any primitive
number, producing the exact "numeric soup" failure mode the naming rule (added
in 0.1.x) was designed to prevent. Memory feedback file `feedback_p9_reflexive.md`
became ambiguous (was it about the *skill* or the *primitive number*?). The
2026-05-16 renumber restores alignment so Wait = P9 = `broomva/p9` — primitive
number, primitive name, and skill repo name all agree.

**Migration**: 0.2.0 shipped with legacy fallbacks for in-place upgrades from
the interim numbering. **0.2.1 removes those fallbacks** — see the 0.2.1 entry
above for the one-time `mv` users on the interim numbering should run.

### Naming convention rule propagated to bstack-loaded surfaces

The "use `Name (Pn)` form in agent prose, never bare `Pn`" rule (already in
workspace CLAUDE.md + AGENTS.md) is now restated in `SKILL.md` (naming-convention
subsection after the primitives table) and `references/primitives.md` (top of
file, before TOC). When `/bstack` fires and agents load these surfaces, the rule
is visible at the entry point — closing the gap where agents reverted to bare
`Pn` because the rule was buried only in workspace governance files.

### Doctor extensions

- **New Section 9 — Naming convention propagation**: `scripts/doctor.sh` now lints
  that CLAUDE.md + AGENTS.md contain (a) the `Name (Pn)` naming rule in prose,
  and (b) a **Short-name index** line with exactly 20 entries. Closes the
  silent-drift failure mode where governance edits could leave the index
  mismatched with the primitive count.
- **Primitive-number checks updated**: Section 3 (AGENTS.md primitive sections),
  Section 4 (reflexive trigger rules), Section 6 (hook wiring labels), and
  Section 7 (script paths + labels) all reflect the new canonical ordering.

## 0.1.0

- Initial versioned release with auto-update mechanism
- Added `bin/bstack-update-check` — periodic version check with caching, snooze, and auto-upgrade support
- Added `bin/bstack-config` — read/write `~/.bstack/config.yaml` for persistent preferences
- Added `bstack-upgrade/SKILL.md` — inline upgrade flow with 4 user options
- Preamble now checks for updates before running skill detection
- 27 skills across 7 layers: Foundation, Memory, Orchestration, Research, Design, Platform, Strategy
