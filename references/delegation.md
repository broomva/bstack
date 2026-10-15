# Tiered delegation

How an orchestrator decides which model does a piece of work, what the work is given, how its output is
checked, and how the plan changes as the work reveals itself. It refines Fanout (P5) and Orchestrate (P19);
it is not a new primitive. The base tick (`orchestrator-tick.md` §8) applies it to every launch. Any
session that delegates — an `/autonomous` arc spawning subagents, a fleet peer, a Maestro run — applies
it to the agents it starts.

The overlay names the implementation. In the Broomva workspace that is `scripts/delegation.py`
(fixture tests `scripts/test_delegation.py`, mutation proof `scripts/mutate_delegation.py`). Model names
live only in its `LADDER`, so a model release is one edit, not a hunt through prose.

## Tiers are roles

| Tier | Does | Verified by |
|---|---|---|
| `mechanical` | Drives that change no code: rerun, update-branch, merge on green, archive, cleanup, close-out, relay, resume | The done check's exit code |
| `standard` | Implementation and research **from a spec or a bounded brief** | The done check; P20 when substantive |
| `frontier` | Design: specs, ADRs, plans, agent graphs. Governance-class changes, one-way doors. Every P20 reviewer | The spec-contract rubric (design), the full P20 panel (governance) |
| `apex` | Frontier work that frontier already failed, or a node that states why it needs more | As frontier |

Thinking follows the tier (low · medium · high · high), and governance work runs frontier at `xhigh`. In a
saving capacity mode (the tick's cadence is conserve, critical or read-only), thinking drops one step; the
tier never drops, and governance work and reviews keep theirs. Quota bends effort, never reach or the
quality floor.

## Assess, deterministically

`assess` reads only what a brief can state before any work is done: the kind, the intended paths, an LOC
estimate, the file and repo counts, a public-API flag, a one-way-door flag, and whether a spec exists. It
returns the executor tier, an author tier when something must be written first, the verifier and the
reasons. The rules, in order:

1. A mechanical kind that changes no code and touches no governance path → `mechanical`.
2. `design`, `decompose`, `review` → `frontier`.
3. A governance-class path (any `require_human` rule in the policy, plus the controller paths: loop
   prompts, `.control/`, this file, the tick, the assessor itself) or a one-way door → `frontier`, full P20.
4. More than one repo → needs a frontier `decompose` node first: one node per repo.
5. Substantive (>200 LOC, multi-file or public API) → `standard` with P20; **with no spec, a frontier
   design node writes one first** (its done check is `spec_check.py` exit 0).
6. Otherwise → `standard`, verified by its done check.

The tier selects **who reasons**; it never widens **what may be reached**. Scope, merge rule and gates
come from the brief and the policy, the same at every tier. A router that lets "this looks easy" lower
oversight while reach stays fixed is the failure named in the knowledge graph's
`model-routing-is-a-switching-signal`.

## Frontier writes, standard executes

The expensive model's best use is the artifact that makes the cheap model sufficient: a spec that
settles the design calls, an ADR that records the one-way doors, a graph that names each node's inputs
and done check. A standard executor reads those first and works from them. When the inputs do not settle
a call, that is a raise, not a guess.

## Agent graphs

Work that is more than one node is planned as a graph (`docs/plans/<date>-<slug>.graph.yaml`): nodes with
a kind, signals, inputs, produced files, dependencies, a done check, a verifier and a `spawn_default`.
`plan <graph>` validates it before anything launches: ids unique and acyclic; every node has a done
check; no node below its assessed floor; any node above it says why; apex says why; the verifier at
least as strong as required; reviewers at frontier; a substantive node's spec produced by an upstream
design node or already on disk under `docs/specs|adrs|plans/`. It prints each node's Maestro fields
(`tier`, `provider`, `thinking`), and `brief <graph> <node>` prints the block that node's brief carries.

The graph is a living file. The orchestrator re-runs `plan` whenever it adds, splits or re-tiers a node.

## Raises: any depth, always visible

The assessment is a floor and a starting point, not a cap. Any agent, at any depth, may start a
higher-tier agent when the work outgrows what was assessed: a design call the inputs do not settle, a
governance path, a one-way door, a failed verification, or a direction the work opened that nobody
foresaw. It hands that part up with what it learned, keeps the rest, and writes one line per raise in
its final message:

```
TIER-RAISE: standard -> frontier: the migration is not reversible once rows are rewritten
```

Raises are measured, not just declared: Maestro reads each run's transcripts and records `raises` and
`maxSubagentTier` on its `run.economy` event. The orchestrator closes the loop:

- the item's remaining work is re-assessed with the observed tier as its floor;
- a raise with no `TIER-RAISE:` line is reported: a mis-assessment nobody explained;
- a kind that keeps raising (the economy's re-plan signal) gets a higher floor in the assessor, through a
  governance-class PR.

A failed verification is the other feedback: the relaunch goes one rung up, frontier at most, apex only
with a stated reason. The tier is fixed within a run (the dwell), so the system switches at run
boundaries, where the orchestrator sees it.

## Reviewers are pinned

P20 reviewers, and Explore runs that serve a review, run at frontier or cross-vendor whatever tier the
work ran at. Verification is not where tokens are saved: a cheaper reviewer is a weaker gate, and the
measured cost of the fleet is long contexts, not reviews.

## Economy by tier

Maestro records per run: tier (declared, or inferred from the model), tokens from the transcript,
subagent tokens by model, raises. It aggregates per tier — runs, approved, tokens per approved run,
first-pass rate, escalations, raises — in its Fleet view and in `economy.json`, which the tick reads
daily. The question it answers is whether a tier's output passes, and at what cost, so the floors move
on evidence rather than on taste.

## The brief block

Every delegated brief carries, under the §8 fields:

- its tier, model and thinking, fixed for the run;
- the inputs to read first;
- the done check and the verifier;
- the default tier for agents it starts, with the model passed explicitly, and the instruction to copy
  the block into every brief it writes;
- the raise rule and the `TIER-RAISE:` line;
- the reviewer pin;
- that the tier changes nothing about scope, merge rule or gates.
