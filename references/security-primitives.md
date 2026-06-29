# Security Primitives — Permission / Capability + Trust-Tier Model (Policy schema v1.1)

> **Scope: this is the SCHEMA contract only.** This document describes the
> `permissions:` and `trust_tiers:` blocks declared in
> [`assets/templates/policy.yaml.template`](../assets/templates/policy.yaml.template)
> (`version: "1.1"`). The *runtime* that enforces them — `permission-gate-hook.sh`,
> `read-boundary-hook.sh`, `permissions.py`, `webfetch-sanitizer.py` — is **not**
> shipped here; it is sequenced as separate follow-up PRs per
> [`specs/2026-05-15-indirect-prompt-injection-defense.md`](../specs/2026-05-15-indirect-prompt-injection-defense.md)
> §5. Schema lands first so the runtime can pin to a stable contract — mirroring how
> the `write_gate` block (Tier 1, spec §4) landed schema-first ahead of
> `check-file-write-safety.py`. Nothing below describes behavior the schema does not
> declare.

Design source: spec §3 (Threat Model — the agent is treated as an *untrusted
insider*) and §4 Tier 2 (Read-Side Trust Boundary).

## (a) Path-based capability model

`permissions.defaults` declares three capability surfaces, each an
`allow` / `deny` path-list:

- **`read`** — `allow` covers governance files, `docs/**`, `skills/**/SKILL.md`,
  `research/**`, `{current_project}/**`, `scripts/**`; `deny` covers secret-shaped
  paths (`**/.env*`, `**/*.key`, `**/.ssh/**`, `**/.aws/credentials`), `~/Downloads`,
  `~/Desktop`, `/tmp/**`, and raw `**/.git/objects/**`.
- **`write`** — `allow` is narrow: `{current_project}/**` plus the P6/P1 sinks
  (`research/entities/**`, `research/notes/**`, `docs/conversations/**`);
  `deny` re-lists secrets and the governance / `.control` files.
- **`execute`** — a `bash_allowlist` of read-only inspection + project-scoped
  build/test/tooling globs, and a `bash_denylist` of destructive forms
  (`rm -rf*`, `git push --force*`, `sudo *`, `curl http*://*`, `wget *`, …).
  A `network.egress_allowlist` is whitelist-only (`egress_denylist: []` — nothing
  implicit).

**Ordering rule: allow-then-deny, explicit deny wins** — declared explicitly as
`permissions.defaults.precedence: deny_over_allow`. A path matched by `allow` is
authorized *unless* it also matches `deny`; `deny` is evaluated last and is absolute.
This is load-bearing: `{current_project}/**` equals `{workspace_root}/**` when work
runs at workspace root, so without deny-precedence it would match governance/secret
paths — the `**/.env*` and `.control/policy.yaml` denies are what prevent self-elevation.

**`{workspace_root}` placeholder** (and `{current_project}`) are expanded by the
bootstrap/runtime at install time so the same template is portable across
workspaces. Patterns use glob semantics (`**` = recursive).

## (b) `never_auto_granted` — always human-gated

`permissions.never_auto_granted` is a capability set that **always** triggers a
fresh human-approval flow, even when the agent already holds `policy:write` from a
prior grant (the chained-self-elevation check). Each entry guards a self-elevation
path:

| Capability | Why human-gated |
|---|---|
| `policy:write` | edits to `.control/policy.yaml` — the file that defines every other gate; self-modification of the control plane (spec §6 risk: agent cannot widen its own gates without a human signature) |
| `grants:write` | direct edits to `.control/grants.jsonl` outside the approve/revoke CLI — would forge its own authorizations |
| `secrets:read` | reads from secret-shaped paths — exfiltration surface |
| `network:egress.add_host` | adding an egress allowlist entry — the dead-drop / C2 surface (spec Tier 3) |
| `signed_writes:bypass` | bypassing the `capability_id` requirement (BRO-1030) on signed writes |
| `governance:write` | writes to CLAUDE.md / AGENTS.md / METALAYER.md — the invariant layer |
| `hooks:write` | edits to `scripts/*-hook.sh` or `.claude/settings.json` — disabling the enforcement chain itself |

## (c) Approval flow — `.control/grants.jsonl`

When a tool call would exercise a `never_auto_granted` capability, it requires a
signed grant record appended to `.control/grants.jsonl` (HMAC-signed, produced by
`permissions.py grant`, human-in-the-loop — spec §4 Tier 2). The file is itself
write-`deny`'d to the agent, so grants can only enter through the approve/revoke CLI.

`permissions.approval` declares how a request reaches the human:

- **`mode`** ∈ `in_band` | `out_of_band_telegram` | `auto_deny`.
- **`in_band`** — `blocking: true`, the agent halts until approve/deny
  (`timeout_seconds: 0` = synchronous human wait).
- **`out_of_band_telegram`** — async notification (`chat_id`, `timeout_seconds: 1800`),
  with `timeout_action: deny` (fail-closed when the human doesn't respond in time —
  this is the *time-bounded* dimension).
- **`rate_limit`** — `max_requests_per_hour` / `max_requests_per_session` caps with
  `excess_action: deny`, bounding approval-fatigue (spec §6 risk).

## (d) Trust tiers T0–T4 (read-side classification)

`trust_tiers` classifies **incoming content** and is **orthogonal to permission**: a
cross-folder read grant authorizes the *operation*, but the content is still tagged at
its tier and wrapped by the read-boundary runtime. The block is **self-contained** —
the full classification lives in `policy.yaml` itself (no separate file), so it is
protected by `policy:write` like the rest of the control plane (an agent cannot
reclassify external content as trusted-instructions without a human-signed grant).
`default_tier: T4_external` (fail-closed — unknown sources are least-trusted).

This adopts the **instruction-authority** model (who may issue instructions vs
data-only — the CaMeL / spotlighting distinction the spec cites), which **supersedes**
the draft origin-locality sketch in spec §4 Tier 2 (that spec is a Draft; this block
is canonical).

| Tier | Source | Disposition |
|---|---|---|
| **T0_system** | governance files (CLAUDE/AGENTS/METALAYER, policy.yaml) | trusted instructions |
| **T1_user** | live user messages | trusted instructions |
| **T2_workspace_governance** | installed `SKILL.md`, conventions, `bstack/references` | loaded as instructions |
| **T3_workspace_data** | `research/`, `docs/conversations/`, our source code | **data only — never instructions** |
| **T4_external** | Moltbook, X, WebFetch, MCP, imported bridge logs | **quarantined data** |

The control-plane property (spec §3): content at T3/T4 is never elevated to
action-taking authority — instructions found in fetched/external data are not
executed as instructions.

## (e) Runtime sequencing (not in this PR)

The enforcement runtime is intentionally absent and lands as later PRs (spec §5):
`permission-gate-hook.sh` (enforces `never_auto_granted` / grants — PR #3),
`read-boundary-hook.sh` (tags content with trust tier — PR #3), `webfetch-sanitizer.py`
(strips hidden-instruction carriers — PR #4), and the human grant CLI `permissions.py`.
Until those are wired into `.claude/settings.json`, the legacy v1 gates
(`gates:` G1–G4 via `control-gate-hook.sh`) plus the already-shipped `write_gate`
block remain the active enforcement floor; this v1.1 schema is the contract they pin to.
