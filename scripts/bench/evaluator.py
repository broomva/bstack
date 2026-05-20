"""bench.evaluator — Score deliverables against a task's rubric.

Two evaluator modes ship in v0.10.0:

  - RubricMatchEvaluator  Deterministic rubric matching against simple checks
                          (has_section / sentence_count_at_least /
                          bullet_count_at_least / contains_any). No LLM cost.
                          Used by --dry-run.
  - StubLLMJudgeEvaluator Placeholder for the future LLM-as-judge implementation.
                          Raises NotImplementedError until SDK + key available.

Quality score is `weighted_passes / sum_of_weights` in [0.0, 1.0].
Payment follows OpenSpace + ClawWork's 0.6 cliff: if `quality < 0.6`,
payment = 0.0; else `task_value_usd`.

Cross-Review (P20) discipline: when LLM-as-judge ships, the judge model
MUST differ from the agent model. Enforcement lives in orchestrator.py.

Stdlib only.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .task_loader import Task


# 0.6 quality cliff — payment is 0 below this; full task_value above.
QUALITY_CLIFF = 0.6


@dataclass
class EvaluationResult:
    task_id: str
    quality_score: float  # 0.0–1.0
    quality_score_10: float  # 0.0–10.0 (display)
    payment_usd: float  # before cliff
    actual_payment_usd: float  # after cliff (0 if below QUALITY_CLIFF)
    rubric_breakdown: dict[str, float] = field(default_factory=dict)
    feedback: str = ""
    evaluator: str = ""

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "quality_score": round(self.quality_score, 4),
            "quality_score_10": round(self.quality_score_10, 2),
            "payment_usd": round(self.payment_usd, 4),
            "actual_payment_usd": round(self.actual_payment_usd, 4),
            "rubric_breakdown": {k: round(v, 4) for k, v in self.rubric_breakdown.items()},
            "feedback": self.feedback,
            "evaluator": self.evaluator,
        }


class Evaluator(ABC):
    """Plug-in surface for bench evaluators."""

    name: str = "abstract"

    @abstractmethod
    def evaluate(
        self, task: Task, deliverables: list[Path]
    ) -> EvaluationResult:
        """Score `deliverables` against `task.rubric_json`."""


def _read_text(paths: list[Path]) -> str:
    """Concatenate text content of all deliverables. Robust to missing files."""

    parts: list[str] = []
    for p in paths:
        if not p.is_file():
            continue
        try:
            parts.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(parts)


def _split_sentences(text: str) -> list[str]:
    """Best-effort sentence splitter (stdlib-only — no NLTK).

    Splits on `. `, `? `, `! ` followed by a capital letter, or on newlines that
    terminate a non-empty trimmed line. Good enough for short deliverables.
    """

    pieces: list[str] = []
    for chunk in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text):
        for line in chunk.splitlines():
            stripped = line.strip()
            if stripped and stripped.rstrip(".!?:;") and not stripped.startswith("#"):
                pieces.append(stripped)
    return pieces


def _check_has_section(text: str, section: str) -> bool:
    """Return True if `text` contains a Markdown header matching `section`.

    Section match is case-insensitive substring on header text (e.g. `## Acceptance`
    matches section="acceptance"). Also matches the literal word as a non-header.
    """

    needle = section.lower()
    for line in text.splitlines():
        if line.lstrip().startswith("#") and needle in line.lower():
            return True
    return needle in text.lower()


def _check_sentence_count(text: str, min_count: int) -> bool:
    return len(_split_sentences(text)) >= min_count


def _check_bullet_count(text: str, min_count: int) -> bool:
    bullets = [
        line for line in text.splitlines() if line.lstrip().startswith(("-", "*"))
    ]
    return len(bullets) >= min_count


def _check_contains_any(text: str, tokens: list[str]) -> bool:
    return any(tok.lower() in text.lower() for tok in tokens)


def _apply_criterion(criterion: dict[str, Any], text: str) -> bool:
    check = criterion.get("check", "")
    if check == "has_section":
        return _check_has_section(text, criterion.get("section", ""))
    if check == "sentence_count_at_least":
        return _check_sentence_count(text, int(criterion.get("min", 1)))
    if check == "bullet_count_at_least":
        return _check_bullet_count(text, int(criterion.get("min", 1)))
    if check == "contains_any":
        return _check_contains_any(text, list(criterion.get("tokens", [])))
    # Unknown checks fail loudly via feedback rather than silently passing.
    return False


class RubricMatchEvaluator(Evaluator):
    """Deterministic rubric matching. Used by --dry-run.

    Iterates the task's `rubric_json.criteria` list; each criterion is a
    dict with `id`, `check`, `weight`, and check-specific fields. The
    weighted pass rate is the quality score.
    """

    name = "rubric-match"

    def evaluate(
        self, task: Task, deliverables: list[Path]
    ) -> EvaluationResult:
        text = _read_text(deliverables)
        criteria = list(task.rubric_json.get("criteria", []))
        if not criteria:
            return EvaluationResult(
                task_id=task.task_id,
                quality_score=0.0,
                quality_score_10=0.0,
                payment_usd=0.0,
                actual_payment_usd=0.0,
                feedback="No rubric criteria attached to task.",
                evaluator=self.name,
            )
        breakdown: dict[str, float] = {}
        weighted_passes = 0.0
        weight_total = 0.0
        failures: list[str] = []
        for c in criteria:
            cid = str(c.get("id", "anon"))
            weight = float(c.get("weight", 1.0))
            weight_total += weight
            passed = _apply_criterion(c, text)
            breakdown[cid] = weight if passed else 0.0
            if passed:
                weighted_passes += weight
            else:
                failures.append(cid)
        quality = (weighted_passes / weight_total) if weight_total else 0.0
        payment_usd = task.task_value_usd
        actual = payment_usd if quality >= QUALITY_CLIFF else 0.0
        feedback = (
            f"Passed {len(criteria) - len(failures)}/{len(criteria)} criteria. "
            f"Failed: {', '.join(failures) or 'none'}."
        )
        return EvaluationResult(
            task_id=task.task_id,
            quality_score=quality,
            quality_score_10=round(quality * 10.0, 2),
            payment_usd=payment_usd,
            actual_payment_usd=actual,
            rubric_breakdown=breakdown,
            feedback=feedback,
            evaluator=self.name,
        )


class StubLLMJudgeEvaluator(Evaluator):
    """Placeholder for the LLM-as-judge evaluator.

    Wiring blocked on:
      - `anthropic` SDK availability OR `claude --print` CLI in PATH
      - `ANTHROPIC_API_KEY` in env
      - P20 Cross-Review constraint: judge model MUST differ from agent model
    """

    name = "llm-judge-stub"

    def evaluate(
        self, task: Task, deliverables: list[Path]
    ) -> EvaluationResult:
        raise NotImplementedError(
            "LLM judge is not wired yet. v0.10.0 ships rubric-match only. "
            "See specs/bench-skill-evolution.md §Evaluator for the live spec."
        )


def get_evaluator(name: str) -> Evaluator:
    if name in ("rubric", "rubric-match", "deterministic"):
        return RubricMatchEvaluator()
    if name in ("llm", "llm-judge"):
        return StubLLMJudgeEvaluator()
    raise ValueError(
        f"Unknown evaluator '{name}'. Available: rubric-match, llm-judge (stub)."
    )
