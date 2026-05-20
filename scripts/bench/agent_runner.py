"""bench.agent_runner — Pluggable agent execution for the bench harness.

A runner takes a Task + a workspace directory and is expected to produce
deliverable files in that directory. The harness then passes those files
to the evaluator. Runners report token usage via the returned RunResult.

Three runners ship as of v0.11.0:

  - DryRunRunner         deterministic canned responses; no LLM cost; default
  - LiveProviderRunner   delegates to a `bench.providers.Provider` (e.g.
                         DatabricksGatewayProvider). Real LLM calls, real
                         token usage, real cost. Replaces the v0.10.0
                         StubLiveRunner that raised NotImplementedError.
  - StubLiveRunner       kept as alias for backwards-compat: returns the
                         LiveProviderRunner result when a provider is
                         supplied, else raises a migration-message error
                         pointing to the spec.

A runner's job is intentionally narrow: produce the deliverable files +
report a TokenUsage struct. Skill telemetry (selections/applied/...) is
captured separately by the orchestrator.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from bench.task_loader import Task


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0
    cost_usd: float = 0.0
    # Optional per-source attribution (`agent`, `skill_select`, ...). Mirrors
    # OpenSpace's `gdpval_bench/token_tracker.py` taxonomy but stays stdlib.
    by_source: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunResult:
    task_id: str
    runner: str
    duration_seconds: float
    tokens: TokenUsage
    deliverable_paths: list[Path] = field(default_factory=list)
    exit_status: str = "success"  # success | failure | timeout
    error: Optional[str] = None
    # Skill telemetry — populated by orchestrator from side-channel log;
    # runner may leave empty.
    skills_available: list[str] = field(default_factory=list)
    skills_selected: list[str] = field(default_factory=list)
    skills_applied: list[str] = field(default_factory=list)
    skills_fallback: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "runner": self.runner,
            "duration_seconds": round(self.duration_seconds, 4),
            "tokens": self.tokens.to_dict(),
            "deliverables": [str(p.name) for p in self.deliverable_paths],
            "exit_status": self.exit_status,
            "error": self.error,
            "skills": {
                "available": self.skills_available,
                "selected": self.skills_selected,
                "applied": self.skills_applied,
                "fallback": self.skills_fallback,
            },
        }


class AgentRunner(ABC):
    """Plug-in surface for bench agents. A runner is stateless w.r.t. tasks."""

    name: str = "abstract"

    @abstractmethod
    def run(self, task: Task, workspace: Path, phase: int) -> RunResult:
        """Run `task` in `workspace`. Return RunResult.

        `phase` is 1 (cold) or 2 (warm). Runners MAY adjust behavior per phase
        (e.g. for a deterministic skill-evolution simulation), but must NOT
        depend on it for correctness — the orchestrator owns phase-specific
        state (skill snapshots, telemetry counters, etc).
        """


class DryRunRunner(AgentRunner):
    """Deterministic canned responses. Used by `--dry-run` (default).

    Simulates the harness end-to-end with no LLM calls. Phase 2 outputs are
    intentionally a bit better than Phase 1 outputs to verify the comparison
    pipeline detects deltas (mimicking what an evolved-skills run should
    produce). The deltas are small and the canned numbers are fixed; this is
    a substrate test, not a model claim.
    """

    name = "dry-run"

    # Canned per-task, per-phase deliverable bodies. Phase-2 bodies include
    # one extra signal (priority token, sentence, primitive name) to verify
    # the rubric checker registers an improvement.
    _CANNED: dict[str, dict[int, str]] = {
        "ticket-triage-001": {
            1: (
                "# Title\nCI watchdog not auto-starting after push.\n\n"
                "## Context\nAfter a push to an open PR, the agent currently relies on the user to start a watcher. "
                "This creates an interactive babysitting loop the user has called out.\n\n"
                "## Acceptance\n- Watcher starts automatically post-push.\n"
            ),
            2: (
                "# Title\nCI watchdog not auto-starting after push (P9 reflex gap).\n\n"
                "## Context\nAfter a push to an open PR, the agent today relies on the user to start a watcher. "
                "This violates P9 Wait's productive-wait discipline and creates an interactive babysitting loop.\n\n"
                "## Acceptance\n- Watcher starts automatically post-push via `p9 watch <pr> --background`.\n"
                "- Failure classifier wired to self-heal known categories.\n\n"
                "Priority: High\n"
            ),
        },
        "diff-summary-002": {
            1: (
                "## Release Note\n"
                "Adds a crystallize script that scans conversation transcripts. "
                "It surfaces patterns recurring across sessions. "
                "Includes tests and fixtures.\n"
            ),
            2: (
                "## Release Note\n"
                "Adds a `crystallize` script that scans `docs/conversations/*.md` for phrases recurring across 3+ sessions. "
                "It co-locates them with failure-mode and repetition-acknowledgement keywords to surface P16 rule-of-three candidates without auto-promoting. "
                "Ships with a bash dispatcher, 14 canary assertions, and 6 fixture conversations.\n"
            ),
        },
        "primitive-match-003": {
            1: (
                "Primitive: P6.\nReason: The session produced material that should be promoted into the graph.\n"
            ),
            2: (
                "Primitive: P6 (Bookkeeping).\n"
                "Reason: Substantial graph-relevant material was produced but the pipeline didn't index it, so the next session started without those entities.\n"
            ),
        },
    }

    # Fixed canned token counts — Phase 2 lower than Phase 1 to validate the
    # comparison computes the expected delta direction.
    _TOKENS: dict[str, dict[int, TokenUsage]] = {
        "ticket-triage-001": {
            1: TokenUsage(prompt_tokens=420, completion_tokens=180, total_tokens=600, llm_calls=2, cost_usd=0.012),
            2: TokenUsage(prompt_tokens=300, completion_tokens=120, total_tokens=420, llm_calls=1, cost_usd=0.008),
        },
        "diff-summary-002": {
            1: TokenUsage(prompt_tokens=380, completion_tokens=140, total_tokens=520, llm_calls=2, cost_usd=0.010),
            2: TokenUsage(prompt_tokens=260, completion_tokens=100, total_tokens=360, llm_calls=1, cost_usd=0.007),
        },
        "primitive-match-003": {
            1: TokenUsage(prompt_tokens=200, completion_tokens=80, total_tokens=280, llm_calls=1, cost_usd=0.005),
            2: TokenUsage(prompt_tokens=160, completion_tokens=60, total_tokens=220, llm_calls=1, cost_usd=0.004),
        },
    }

    def run(self, task: Task, workspace: Path, phase: int) -> RunResult:
        start = time.monotonic()
        canned_body = self._CANNED.get(task.task_id, {}).get(phase)
        if canned_body is None:
            canned_body = (
                f"[dry-run] Task {task.task_id} phase {phase}: no canned response. "
                f"Add an entry in DryRunRunner._CANNED to extend coverage.\n"
            )
        deliverables: list[Path] = []
        for name in task.deliverable_files or [f"{task.task_id}.md"]:
            out_path = workspace / name
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(canned_body, encoding="utf-8")
            deliverables.append(out_path)
        tokens = self._TOKENS.get(task.task_id, {}).get(
            phase, TokenUsage(total_tokens=500, llm_calls=1, cost_usd=0.01)
        )
        # Simulate skill selection: Phase 2 "selects" expected skills (warm),
        # Phase 1 leaves them unselected (cold start).
        skills_selected = list(task.expected_skills) if phase == 2 else []
        skills_applied = list(task.expected_skills) if phase == 2 else []
        # Persist a per-task run log entry next to the deliverables for the
        # token tracker / orchestrator to consume (mirrors OpenSpace's
        # `conversations.jsonl` pattern but compact + stdlib).
        log_path = workspace / f"{task.task_id}.runlog.jsonl"
        log_path.write_text(
            json.dumps(
                {
                    "task_id": task.task_id,
                    "runner": self.name,
                    "phase": phase,
                    "tokens": tokens.to_dict(),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return RunResult(
            task_id=task.task_id,
            runner=self.name,
            duration_seconds=time.monotonic() - start,
            tokens=tokens,
            deliverable_paths=deliverables,
            exit_status="success",
            skills_available=list(task.expected_skills),
            skills_selected=skills_selected,
            skills_applied=skills_applied,
            skills_fallback=[],
        )


class LiveProviderRunner(AgentRunner):
    """Real LLM runner — delegates to a `bench.providers.Provider`.

    The runner builds a single user message from the task prompt, calls the
    provider's `chat()`, writes the response body to the first declared
    deliverable file, and reports real token usage + estimated cost.

    Skill simulation for the two-phase protocol is intentionally minimal:
    Phase 2 prepends a "warm" system message listing the task's expected
    skills. In v0.11.0 this is *not* a measurement of skill self-evolution
    yet (that requires the FIX/DERIVED/RETIRE wire from BRO-1205 followups);
    it is a deterministic phase signal so the two-phase comparison harness
    has something non-trivial to compare against the cold start.

    Usage (constructor wiring lives in orchestrator.py):

        from bench.providers import get_provider
        provider = get_provider("databricks")
        runner = LiveProviderRunner(provider=provider, model="databricks-claude-haiku-4-5")
        result = runner.run(task, workspace, phase=1)
    """

    name = "live"

    def __init__(self, provider: object, model: str, max_tokens: int = 2048) -> None:
        # `provider` typed as `object` (not `Provider`) to keep the agent_runner
        # module free of mandatory imports from bench.providers — circular-
        # import guard. Real type is `bench.providers.Provider`.
        self._provider = provider
        self._model = model
        self._max_tokens = max_tokens

    def run(self, task: Task, workspace: Path, phase: int) -> RunResult:
        from bench.providers import ChatMessage  # local — break import cycle

        start = time.monotonic()
        # System message: minimal in Phase 1 (cold), enriched in Phase 2 (warm)
        # with the task's expected skills as a "memory" of what the agent
        # learned in the prior phase. This is deterministic phase signal,
        # not a real skill-engine integration.
        if phase == 2 and task.expected_skills:
            system_text = (
                "You are a careful, concise assistant. From prior runs you "
                "have access to these skills: "
                f"{', '.join(task.expected_skills)}. Apply them when relevant."
            )
        else:
            system_text = "You are a careful, concise assistant."
        messages = [
            ChatMessage(role="system", content=system_text),
            ChatMessage(role="user", content=task.prompt),
        ]
        try:
            completion = self._provider.chat(  # type: ignore[attr-defined]
                messages=messages,
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=0.0,
            )
        except Exception as exc:
            return RunResult(
                task_id=task.task_id,
                runner=self.name,
                duration_seconds=time.monotonic() - start,
                tokens=TokenUsage(),
                deliverable_paths=[],
                exit_status="failure",
                error=f"provider error: {type(exc).__name__}: {exc}",
            )
        # Write deliverables. First declared file gets the full body; if more
        # are declared they get the same content (test rubrics typically only
        # check the first; we don't fabricate per-file content).
        deliverables: list[Path] = []
        for name in task.deliverable_files or [f"{task.task_id}.md"]:
            out_path = workspace / name
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(completion.content, encoding="utf-8")
            deliverables.append(out_path)
        # Cost estimation. None when model is unknown — recorded as 0.0 here
        # but the orchestrator's --budget-usd path refuses to start unless
        # the user opts in with --allow-unknown-cost, so this 0.0 can never
        # silently defeat the cap. The by_source dict tags this entry as
        # cost-unknown so REPORT.md can surface the gap.
        from bench.providers.base import estimate_cost_usd  # local

        cost = estimate_cost_usd(self._model, completion.usage)
        cost_known = cost is not None
        by_source: dict[str, int] = {"agent": completion.usage.total_tokens}
        if not cost_known:
            # Marker for downstream consumers (REPORT.md, comparison).
            by_source["cost_unknown"] = 1
        tokens = TokenUsage(
            prompt_tokens=completion.usage.prompt_tokens,
            completion_tokens=completion.usage.completion_tokens,
            total_tokens=completion.usage.total_tokens,
            llm_calls=1,
            cost_usd=float(cost) if cost_known else 0.0,
            by_source=by_source,
        )
        skills_selected = list(task.expected_skills) if phase == 2 else []
        skills_applied = list(task.expected_skills) if phase == 2 else []
        # Per-task runlog mirroring DryRunRunner — keeps the side-channel
        # contract for any downstream telemetry consumer.
        log_path = workspace / f"{task.task_id}.runlog.jsonl"
        log_path.write_text(
            json.dumps(
                {
                    "task_id": task.task_id,
                    "runner": self.name,
                    "phase": phase,
                    "model": completion.model,
                    "finish_reason": completion.finish_reason,
                    "tokens": tokens.to_dict(),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return RunResult(
            task_id=task.task_id,
            runner=self.name,
            duration_seconds=time.monotonic() - start,
            tokens=tokens,
            deliverable_paths=deliverables,
            exit_status="success",
            skills_available=list(task.expected_skills),
            skills_selected=skills_selected,
            skills_applied=skills_applied,
            skills_fallback=[],
        )


class StubLiveRunner(AgentRunner):
    """Legacy stub. Retained for backwards-compat with v0.10.0 callers.

    v0.11.0 ships `LiveProviderRunner` as the real live path. This class
    only fires when `get_runner("live")` is called without a provider — the
    orchestrator never goes through that path in v0.11.0+, but external
    callers might.
    """

    name = "live-stub"

    def run(self, task: Task, workspace: Path, phase: int) -> RunResult:
        raise NotImplementedError(
            "StubLiveRunner is a legacy v0.10.0 placeholder. v0.11.0 "
            "replaces it with LiveProviderRunner — call "
            "`get_runner('live', provider=..., model=...)` or use "
            "`bstack bench run --runner live --provider databricks "
            "--model <name>`. See references/provider-standards.md."
        )


def get_runner(
    name: str,
    *,
    provider: object = None,
    model: str = "",
    max_tokens: int = 2048,
    **kwargs: object,
) -> AgentRunner:
    if name in ("dry-run", "dryrun", "canned"):
        return DryRunRunner()
    if name == "live":
        if provider is None or not model:
            return StubLiveRunner()
        return LiveProviderRunner(provider=provider, model=model, max_tokens=max_tokens)
    # Back-compat aliases — accept old names, route to live with provider.
    if name in ("claude-code", "vanilla-claude", "codex"):
        if provider is None or not model:
            return StubLiveRunner()
        return LiveProviderRunner(provider=provider, model=model, max_tokens=max_tokens)
    raise ValueError(
        f"Unknown runner '{name}'. Available: dry-run, live."
    )
