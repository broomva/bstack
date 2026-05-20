"""bench.agent_runner — Pluggable agent execution for the bench harness.

A runner takes a Task + a workspace directory and is expected to produce
deliverable files in that directory. The harness then passes those files
to the evaluator. Runners report token usage via the returned RunResult.

Stdlib only. Two runners ship in v0.10.0:

  - DryRunRunner    deterministic canned responses; no LLM cost
  - StubLiveRunner  raises NotImplementedError with a clear migration path
                    (anthropic SDK + ANTHROPIC_API_KEY needed)

A runner's job is intentionally narrow: produce the deliverable files +
report a TokenUsage struct. Skill telemetry (selections/applied/...) is
captured separately by the orchestrator from the side-channel run log.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .task_loader import Task


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


class StubLiveRunner(AgentRunner):
    """Placeholder for the future live runner.

    v0.10.0 ships dry-run only. Live mode unblocks when:
      1. `anthropic` SDK is installed (or `claude --print` CLI is in PATH), and
      2. `ANTHROPIC_API_KEY` is set in the environment.

    Once both hold, this stub is replaced with a real subprocess invocation of
    the agent (claude / codex / vanilla-anthropic). See specs/bench-skill-evolution.md.
    """

    name = "live-stub"

    def run(self, task: Task, workspace: Path, phase: int) -> RunResult:
        raise NotImplementedError(
            "Live mode is not wired yet. v0.10.0 ships dry-run only. "
            "See specs/bench-skill-evolution.md for the live-runner spec. "
            "Re-run with --dry-run for the substrate smoke."
        )


def get_runner(name: str) -> AgentRunner:
    if name in ("dry-run", "dryrun", "canned"):
        return DryRunRunner()
    if name in ("live", "claude-code", "vanilla-claude", "codex"):
        return StubLiveRunner()
    raise ValueError(
        f"Unknown runner '{name}'. Available: dry-run, live (stub)."
    )
