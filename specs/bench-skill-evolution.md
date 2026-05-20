---
spec_id: bench-skill-evolution
title: "bstack bench — Skill-Evolution Benchmark Substrate"
status: draft
created: 2026-05-20
author: claude-sonnet-4-7 (under broomva direction)
sources:
  - "research/entities/project/openspace.md"
  - "research/entities/concept/skill-self-evolution.md"
  - "research/notes/2026-05-20-openspace-evolver-synthesis.md"
  - "external/OpenSpace/gdpval_bench/run_benchmark.py"
companion_skills:
  - bookkeeping (P6)
  - p9 (productive-wait while benchmark runs)
  - persist (P12 — multi-hour benchmark runs)
---

# `bstack bench` — Skill-Evolution Benchmark Substrate

> **Audience**: bstack maintainers (agent-readable substrate per P18).
> **Status**: Draft spec — not yet implemented. Proposed scaffolding for a new `bstack bench` subcommand.

## Motivation

Bstack has L3 stability margins (λ₃ ≈ 0.006) and a 20-primitive composition graph but **no empirical performance number**. We can argue that P16 Crystallize reduces token waste; we cannot *measure* it. Without a benchmark substrate every P-primitive promotion is faith-based, and every claim like "skills cache reasoning" is unfalsifiable.

OpenSpace (HKUDS) ships exactly this substrate as `gdpval_bench/`. Their headline numbers (4.2× higher earned income vs ClawWork baseline, 46% Phase 2 token reduction) are the existence proof. We don't need to match the numbers — we need to make our own claim about bstack agents falsifiable.

## Goals

1. **Reproducible per-task quality and token-cost measurement** for any bstack-driven agent (Claude Code, codex, future life-claude launcher).
2. **Two-phase protocol** to isolate the marginal value of evolved skills: Phase 1 (cold) → snapshot skills state → Phase 2 (warm).
3. **Per-skill telemetry capture** during benchmark runs — selections / applied / completions / fallbacks per skill per task.
4. **Cross-agent comparability** — same harness, swappable agent runners, same task set, same LLM-judge rubric.
5. **Apple-to-apple bstack vs baseline comparison** — bstack-instrumented Claude Code vs vanilla Claude Code on identical tasks.

## Non-Goals

- **Not** a port of OpenSpace's `OpenSpaceConfig` or `execute()` API — those are OpenSpace-coupled.
- **Not** matching OpenSpace's exact GDPVal numbers — different agent, different model, different harness.
- **Not** auto-improving skills during a benchmark run (that's the per-skill telemetry's *consumer*, not the benchmark itself).
- **Not** a production deployment platform — purely a measurement substrate.

## Architecture

```
bstack/
├── bin/
│   └── bstack-bench                    # NEW — CLI entry point
├── scripts/
│   ├── bench/
│   │   ├── __init__.py                 # NEW
│   │   ├── main.py                     # NEW — orchestrator (Phase 1 → snapshot → Phase 2)
│   │   ├── task_loader.py              # NEW — load GDPVal subset or bstack-native tasks
│   │   ├── token_tracker.py            # NEW — litellm CustomLogger callback (port from OpenSpace)
│   │   ├── agent_runner.py             # NEW — pluggable: claude / codex / life-claude
│   │   ├── evaluator.py                # NEW — LLM-as-judge against per-task rubric
│   │   ├── skill_snapshot.py           # NEW — tarball ~/.claude/skills/ between phases
│   │   └── comparison.py               # NEW — Phase 1 vs Phase 2 delta table
│   └── crystallize.py                  # EXISTING — feeds new skills from P16
├── specs/
│   └── bench-skill-evolution.md        # THIS FILE
└── tests/
    └── bench/
        ├── test_orchestrator.py        # NEW
        ├── test_token_tracker.py       # NEW
        └── test_agent_runner.py        # NEW
```

State directory (matches bstack convention):

```
~/.config/bstack/bench/
├── runs/
│   └── <run-id>/
│       ├── config.json                 # frozen run config (model hash, task set, agent)
│       ├── phase1_results.jsonl        # one task per line
│       ├── phase1_skills_snapshot.tar.gz
│       ├── phase2_results.jsonl
│       └── comparison.json             # delta summary
└── skill-metrics.sqlite                # per-skill counters (shared with bstack-metrics)
```

## CLI Surface

```bash
bstack bench run [--tasks SET] [--runner R] [--evaluator E] \
                 [--phase {1|2|both}] [--budget-usd N] [--resume RUN_ID] \
                 [--no-dry-run]
bstack bench compare [--run-id RUN_ID]      # Phase 1 vs Phase 2 → REPORT.md
bstack bench tasks list                     # registered task sets
bstack bench status [--run-id RUN_ID]       # recent run summaries
```

### Exit codes (v0.10.0 MVP)

| Code | Meaning |
|---|---|
| 0 | Success |
| 2 | Invalid arguments |
| 3 | Task set not found |
| 4 | Budget exceeded mid-run (or prior spend already exceeds budget on resume) |
| 5 | Resume / status run-id not found |
| 6 | All task runs failed (structurally broken — e.g. stub runner without SDK) |
| 7 | Compare requires both phase 1 + phase 2 results |

**Task sets** (registered in `bstack/scripts/bench/task_sets.json`):
- `gdpval-50` — OpenSpace's 50-task subset (vendored from HF `openai/gdpval`)
- `gdpval-220` — full GDPVal (downloaded on first use)
- `bstack-smoke` — 10 bstack-native tasks (Linear ticket triage, PR review, doc generation, etc.)

**Agents** (registered via `agent_runner.py` plugin protocol):
- `claude-code` — invokes `claude --print "<task>"` in a temp workspace
- `claude-code-bstack` — same, but bootstraps bstack in the temp workspace first
- `codex` — invokes `codex` CLI (via codex-rescue agent if available)
- `vanilla-claude` — direct Anthropic SDK call, no harness (control group)

## Task Schema

Vendored from GDPVal, normalized to bstack canonical form:

```json
{
  "task_id": "uuid-string",
  "task_set": "gdpval-50 | bstack-smoke | ...",
  "occupation": "string",
  "sector": "string",
  "prompt": "string (full task description)",
  "reference_files": ["relative/paths"],
  "deliverable_files": ["expected/output/paths"],
  "rubric_json": { "criteria": [...] },
  "task_value_usd": 0.0,
  "expected_skills": ["optional list of skills expected to fire"]
}
```

`expected_skills` is a bstack extension — lets the harness verify which skills were *available* and which were *selected* during each task.

## Per-Task Result Schema

```json
{
  "task_id": "...",
  "phase": 1 | 2,
  "run_id": "...",
  "agent": "claude-code-bstack",
  "model": "claude-sonnet-4-7",
  "model_hash": "sha256:...",
  "duration_seconds": 187.4,
  "tokens": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "llm_calls": 0,
    "cost_usd": 0.0,
    "agent_prompt_tokens": 0,
    "agent_completion_tokens": 0
  },
  "evaluation": {
    "score_quality": 0.0,
    "score_10": 0.0,
    "payment_usd": 0.0,
    "actual_payment_usd": 0.0,
    "evaluator_feedback": "string",
    "rubric_breakdown": { "criterion_id": score, ... }
  },
  "skills": {
    "available": ["skill-id", ...],
    "selected": ["skill-id", ...],
    "applied": ["skill-id", ...],
    "fallback": ["skill-id", ...]
  },
  "deliverables": ["actual/output/files"],
  "exit_status": "success | failure | timeout"
}
```

## Phase Protocol

### Phase 1 — Cold Run

1. Wipe `~/.config/bstack/bench/runs/<run-id>/phase1_skills/` (start with empty skill state for the test agent).
2. Optionally pre-seed with `--seed-skills <path-to-snapshot>` for non-cold-start comparisons.
3. For each task in the task set:
   - Create `runs/<run-id>/tasks/<task-id>/workspace/` (isolated per task).
   - Copy reference files into workspace.
   - Invoke agent via `agent_runner.run(task, workspace_dir, model)`.
   - Capture token usage via `token_tracker` (litellm callback).
   - Discover deliverables (files created in workspace not in reference set).
   - Run `evaluator.evaluate(task, deliverables)` → quality score + payment.
   - Increment skill telemetry counters in `skill-metrics.sqlite`.
   - Append per-task result to `phase1_results.jsonl`.
4. Snapshot skills: `tar -czf phase1_skills_snapshot.tar.gz ~/.claude/skills/`.

### Snapshot Inspection (between phases)

5. Run `bstack metrics --since "phase1 start"` to surface which skills evolved during Phase 1.
6. Optional: `bstack crystallize --review` to inspect promotion candidates without applying.
7. Optional: apply manual edits to skills based on Phase 1 observations.

### Phase 2 — Warm Run

8. Re-run all tasks from Phase 1 with the same agent + model.
9. Capture same metrics.
10. Append per-task result to `phase2_results.jsonl`.

### Comparison

11. `comparison.py` computes:
    - Per-task token delta (Phase 2 / Phase 1 ratio)
    - Per-task quality delta
    - Per-task duration delta
    - Aggregate token reduction (the headline number — "X% of Phase 1 tokens")
    - Aggregate quality improvement
    - Per-skill contribution: tasks where skill was selected → quality lift vs baseline
12. Output: `comparison.json` + Markdown report at `runs/<run-id>/REPORT.md`.

## Token Tracking (port from OpenSpace)

`token_tracker.py` registers a `litellm.CustomLogger` (matches OpenSpace's design verbatim — no bstack-specific changes needed):

```python
import litellm
import contextvars
from dataclasses import dataclass, field

current_task: contextvars.ContextVar[str] = contextvars.ContextVar("current_task")
current_source: contextvars.ContextVar[str] = contextvars.ContextVar("current_source", default="agent")

@dataclass
class TokenStats:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0
    cost_usd: float = 0.0
    by_source: dict = field(default_factory=dict)  # agent | skill_select | evaluator | summarizer

_stats_by_task: dict[str, TokenStats] = {}

class TokenLogger(litellm.integrations.custom_logger.CustomLogger):
    def log_success_event(self, kwargs, response, start_time, end_time):
        task_id = current_task.get(None)
        if not task_id:
            return
        stats = _stats_by_task.setdefault(task_id, TokenStats())
        usage = response.usage
        source = current_source.get()
        stats.prompt_tokens += usage.prompt_tokens
        stats.completion_tokens += usage.completion_tokens
        stats.total_tokens += usage.total_tokens
        stats.llm_calls += 1
        stats.cost_usd += response._hidden_params.get("response_cost", 0.0)
        stats.by_source.setdefault(source, TokenStats()).total_tokens += usage.total_tokens

litellm.callbacks = [TokenLogger()]
```

**Concurrent-mode safety**: `contextvars` routes per-task automatically when async tasks are awaited in parallel. Matches OpenSpace's `gdpval_bench/token_tracker.py:261-279`.

## Evaluator (LLM-as-Judge)

`evaluator.py` invokes an LLM judge against the task's `rubric_json`:

```python
def evaluate(task: Task, deliverables: list[Path], judge_model: str = "claude-sonnet-4-7"):
    prompt = build_evaluation_prompt(task.rubric_json, task.prompt, deliverables)
    response = anthropic.messages.create(model=judge_model, messages=[...])
    return parse_evaluation(response)
    # Returns: (quality_score 0-1, payment_usd, feedback_string, rubric_breakdown)
```

**Cliff threshold**: `quality_score < 0.6 → payment = 0` (matches OpenSpace + ClawWork policy).

**Judge model isolation**: judge LLM is NEVER the same as the agent LLM (P20 Cross-Review extension to benchmark substrate). If agent runs `claude-sonnet-4-7`, judge must run a different model (Opus, GPT-5, Gemini) to avoid same-model self-validation.

## Reproducibility Discipline

OpenSpace's gaps we will close:

1. **Pin model versions to hashes** — Anthropic returns a stable model ID per call. Capture it. If two runs invoke "claude-sonnet-4-7" and the underlying model checkpoint changed, the comparison is invalid.
2. **Seed every RNG that exists** — Python `random`, NumPy, hash randomization. Set via env var captured in `config.json`.
3. **Vendor the task set into the repo** — don't depend on HuggingFace at run time. `gdpval-50` lives at `bstack/scripts/bench/tasks/gdpval-50.jsonl`.
4. **Capture all env vars that affect agent behavior** — `ANTHROPIC_*`, `OPENROUTER_*`, etc. Stored in `config.json`.
5. **Resume capability** — `--resume RUN_ID` skips completed tasks via `phase{1,2}_results.jsonl`.

## Composition with Existing Primitives

| Primitive | Role |
|---|---|
| **P1 Bridge** | Skill telemetry counters are incremented via the same Stop hook P1 uses |
| **P3 Tickets** | Each `bstack bench` run gets a Linear ticket; results attach there |
| **P6 Bookkeeping** | Benchmark results feed `research/entities/discovery/` — each notable finding is an entity |
| **P9 Wait** | `bstack bench run --workers 8` is a long-blocking op → `p9 watch` while it runs, drain backlog |
| **P11 Empirical** | This *is* the substrate P11 was waiting for — interactive measurement of agent quality |
| **P12 Persist** | Multi-hour benchmark runs use `persist iterate` for cross-context durability |
| **P16 Crystallize** | Benchmark results feed evidence for FIX/DERIVED/RETIRE sub-modes |
| **P18 Audience** | Per-run REPORT.md is Category B (Markdown canonical, HTML projection on demand) |
| **P20 Cross-Review** | Judge model MUST differ from agent model — single-model self-validation banned |

## Phasing

**MVP (Phase 1 of implementation)** — ~3-4 days:
- Orchestrator + task loader (gdpval-50 subset only)
- `claude-code` and `vanilla-claude` agent runners
- Token tracker (port verbatim from OpenSpace)
- Simple LLM-as-judge evaluator (no rubric breakdown — just quality 0-1)
- Comparison report (Markdown)

**Phase 2** — ~2 days:
- Per-skill telemetry capture + skill snapshot tarball
- `bstack-smoke` bstack-native task set (10 tasks)
- Resume capability

**Phase 3** — ~2 days:
- Multi-agent runner (`codex` plugin)
- Cross-Review (P20) judge-model isolation enforcement
- HTML report (Category B projection)

**Phase 4** — open-ended:
- Wire benchmark results into P16 sub-mode triggers
- Cloud upload of run results for cross-machine comparison

## Open Decisions

- **Task vendoring license**: GDPVal is OpenAI-published; their redistribution terms are unclear. Either (a) ship a download script, (b) write our own task set, (c) vendor a subset under fair-use research justification.
- **Workspace isolation**: per-task workspace dir is necessary for deliverable discovery; should it also be a fresh git worktree? Trade-off: isolation vs setup cost.
- **Judge cost budget**: at 50 tasks × 1 LLM-judge call each + a few retries, ~$5-10 per benchmark run on Opus-class judges. Acceptable; surface via `--dry-run` cost estimate.
- **Bstack-smoke task set**: needs ~10 well-scoped bstack-native tasks with rubrics. Candidates: Linear ticket triage, PR diff review, conversation bridge synthesis, deploy log analysis, skill creation from request. Each ~$0 task_value_usd (no economic anchor) — use pure quality score.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| LLM-judge variance > skill-evolution signal | Run judge 3× per task, take median; report variance in REPORT.md |
| Workspace contamination across tasks | Each task gets isolated tempdir; cleaned between runs |
| Token tracker miscounts on retries | OpenSpace's litellm callback only fires on success; explicitly track failures separately |
| Model-version drift between Phase 1 and Phase 2 | Capture model hash at start of each task; abort run if it changes mid-run |
| GDPVal task ambiguity | Vendor a curated subset where rubrics are unambiguous; flag ambiguous tasks for exclusion |
| Skills evolve mid-run (uncontrolled) | Snapshot skills tarball at Phase 1 end; restore deterministically before Phase 2 |

## Out of Scope (for this spec)

- The skill *evolver* itself (FIX/DERIVED auto-repair) — that's a separate spec, gated on this substrate landing first.
- The cloud skill registry equivalent — bstack already has `npx skills add`; no need for a new distribution path.
- Per-task interactive replay UI — could land later as a Category C HTML artifact.

## References

- **OpenSpace harness** (the port source): `external/OpenSpace/gdpval_bench/run_benchmark.py`, `token_tracker.py`, `calc_subset_performance.py`
- **GDPVal dataset**: `https://huggingface.co/datasets/openai/gdpval`
- **ClawWork evaluator** (LLM-as-judge reference): linked from OpenSpace's `LLMEvaluator.evaluate_artifact()`
- **Bstack KG entities**: `[[openspace]]`, `[[skill-self-evolution]]`, `[[bstack-engine]]`, `[[promotion-gate]]`
- **Synthesis note**: `research/notes/2026-05-20-openspace-evolver-synthesis.md`
