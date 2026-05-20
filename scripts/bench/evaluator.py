"""bench.evaluator — Score deliverables against a task's rubric.

Three evaluator modes as of v0.11.0:

  - RubricMatchEvaluator  Deterministic rubric matching against simple checks
                          (has_section / sentence_count_at_least /
                          bullet_count_at_least / contains_any). No LLM cost.
                          Used by --dry-run.
  - LLMJudgeEvaluator     Real LLM judge — delegates to a `bench.providers.Provider`
                          with a structured JSON-output prompt. Quality score
                          is the weighted pass rate from the judge's
                          per-criterion verdicts. Replaces v0.10.0's stub.
  - StubLLMJudgeEvaluator Legacy stub kept for backwards-compat.

Quality score is `weighted_passes / sum_of_weights` in [0.0, 1.0].
Payment follows OpenSpace + ClawWork's 0.6 cliff: if `quality < 0.6`,
payment = 0.0; else `task_value_usd`.

Cross-Review (P20) discipline: the orchestrator enforces judge model ≠
agent model unless `--allow-same-judge-model` is passed with rationale.
That enforcement is at the CLI layer; the evaluator class itself accepts
whatever model the orchestrator hands it.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bench.task_loader import Task


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


class LLMJudgeEvaluator(Evaluator):
    """LLM-as-judge — delegates to a provider, expects structured JSON output.

    The judge receives:
      - The task prompt (context for what was asked)
      - The deliverable content (concatenated)
      - The rubric `criteria` list (each with id + description-from-check + weight)

    It returns a JSON object: `{ "criteria": [ {"id": "...", "pass": bool,
    "reason": "..."} ], "overall_feedback": "..." }`. We parse it, compute
    the weighted pass rate, apply the 0.6 cliff, and return EvaluationResult.

    Failure modes handled:
      - Judge returns invalid JSON → score 0.0, feedback explains the parse
        failure, evaluator name suffixed with "(parse-fail)" for traceability.
      - Judge upstream error → propagates as ProviderError; orchestrator
        catches and records exit_status="failure".
      - Unknown criterion check types → silently skipped by judge; bench
        passes through whatever the judge returns.

    Cross-Review (P20) note: this class does NOT enforce judge ≠ agent
    model — that's the orchestrator's responsibility. A judge that is the
    same model as the agent is a *legal* configuration here (some smoke
    tests need it) but the CLI rejects it without explicit override.
    """

    name = "llm-judge"

    # Compiled once; matches the first JSON object in a string. Used as a
    # fallback when the judge wraps the JSON in prose or fences.
    _JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")

    def __init__(self, provider: object, model: str, max_tokens: int = 2048) -> None:
        self._provider = provider
        self._model = model
        self._max_tokens = max_tokens

    def evaluate(
        self, task: Task, deliverables: list[Path]
    ) -> EvaluationResult:
        from bench.providers import ChatMessage  # local — break import cycle

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
        deliverable_text = _read_text(deliverables)
        prompt = self._build_judge_prompt(task, deliverable_text, criteria)
        try:
            completion = self._provider.chat(  # type: ignore[attr-defined]
                messages=[
                    ChatMessage(
                        role="system",
                        content=(
                            "You are a strict but fair quality evaluator. "
                            "Return ONLY valid JSON matching the requested schema. "
                            "No prose, no markdown fences, no commentary."
                        ),
                    ),
                    ChatMessage(role="user", content=prompt),
                ],
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=0.0,
            )
        except Exception as exc:
            # Surface as a parse-fail-shaped result so the orchestrator's
            # all-failed → exit 6 gate still fires.
            return EvaluationResult(
                task_id=task.task_id,
                quality_score=0.0,
                quality_score_10=0.0,
                payment_usd=task.task_value_usd,
                actual_payment_usd=0.0,
                feedback=f"Judge provider error: {type(exc).__name__}: {exc}",
                evaluator=f"{self.name}(provider-error)",
            )
        verdict = self._parse_judge_json(completion.content)
        if verdict is None:
            return EvaluationResult(
                task_id=task.task_id,
                quality_score=0.0,
                quality_score_10=0.0,
                payment_usd=task.task_value_usd,
                actual_payment_usd=0.0,
                feedback=(
                    "Judge returned non-JSON; treating as full failure. "
                    f"Raw (first 200 chars): {completion.content[:200]!r}"
                ),
                evaluator=f"{self.name}(parse-fail)",
            )
        # P20 round-1: detect judge ID hallucination. If the judge returns
        # criterion IDs that aren't in the rubric, we'd silently score 0
        # against the rubric IDs (verdicts.get(cid, False) returns False).
        # That hides judge confusion behind score=0.0 + positive
        # overall_feedback. Surface it via evaluator name suffix +
        # feedback prefix so the report shows the mismatch.
        rubric_ids = {str(c.get("id", "anon")) for c in criteria}
        verdict_entries = verdict.get("criteria", []) or []
        judge_ids = {
            str(v.get("id", "")) for v in verdict_entries if isinstance(v, dict)
        }
        unknown_judge_ids = judge_ids - rubric_ids
        missing_rubric_ids = rubric_ids - judge_ids
        verdicts = {
            str(c.get("id", "")): bool(c.get("pass", False))
            for c in verdict_entries
            if isinstance(c, dict)
        }
        breakdown: dict[str, float] = {}
        weighted_passes = 0.0
        weight_total = 0.0
        failures: list[str] = []
        for c in criteria:
            cid = str(c.get("id", "anon"))
            weight = float(c.get("weight", 1.0))
            weight_total += weight
            passed = verdicts.get(cid, False)
            breakdown[cid] = weight if passed else 0.0
            if passed:
                weighted_passes += weight
            else:
                failures.append(cid)
        quality = (weighted_passes / weight_total) if weight_total else 0.0
        payment = task.task_value_usd
        actual = payment if quality >= QUALITY_CLIFF else 0.0
        # Suffix evaluator name with id-mismatch when the judge invented IDs
        # or omitted rubric IDs entirely. Both are signals the judge wasn't
        # answering the rubric we asked about.
        evaluator_name = self.name
        mismatch_notes: list[str] = []
        if unknown_judge_ids:
            mismatch_notes.append(
                f"judge returned unknown IDs: {sorted(unknown_judge_ids)}"
            )
        if missing_rubric_ids and len(missing_rubric_ids) == len(rubric_ids):
            # Judge didn't return any of our IDs — full mismatch.
            mismatch_notes.append(
                f"judge omitted ALL rubric IDs (expected: {sorted(rubric_ids)})"
            )
        elif missing_rubric_ids:
            mismatch_notes.append(
                f"judge omitted rubric IDs: {sorted(missing_rubric_ids)}"
            )
        if mismatch_notes:
            evaluator_name = f"{self.name}(id-mismatch)"
        feedback_parts: list[str] = []
        if mismatch_notes:
            feedback_parts.append("[ID-MISMATCH] " + "; ".join(mismatch_notes))
        judge_feedback = str(verdict.get("overall_feedback", ""))
        if judge_feedback:
            feedback_parts.append(judge_feedback)
        if not feedback_parts:
            feedback_parts.append(
                f"Judge passed {len(criteria) - len(failures)}/{len(criteria)} criteria. "
                f"Failed: {', '.join(failures) or 'none'}."
            )
        return EvaluationResult(
            task_id=task.task_id,
            quality_score=quality,
            quality_score_10=round(quality * 10.0, 2),
            payment_usd=payment,
            actual_payment_usd=actual,
            rubric_breakdown=breakdown,
            feedback=" | ".join(feedback_parts),
            evaluator=evaluator_name,
        )

    def _build_judge_prompt(
        self, task: Task, deliverable_text: str, criteria: list[dict[str, Any]]
    ) -> str:
        # Describe each criterion's intent based on its check type so the
        # judge has concrete bar (the rubric JSON is bench-internal; the
        # judge needs human-readable criterion descriptions).
        crit_lines: list[str] = []
        for c in criteria:
            cid = c.get("id", "anon")
            check = c.get("check", "")
            weight = c.get("weight", 1.0)
            desc = _criterion_description(c)
            crit_lines.append(f"  - id: {cid}\n    check: {check}\n    weight: {weight}\n    intent: {desc}")
        crit_block = "\n".join(crit_lines) or "  (no criteria)"
        # Cap deliverable length to keep prompts manageable.
        deliverable_excerpt = deliverable_text[:6000]
        return (
            f"# Task being evaluated\n\n"
            f"## Original task prompt\n{task.prompt}\n\n"
            f"## Deliverable produced by the agent\n```\n{deliverable_excerpt}\n```\n\n"
            f"## Rubric criteria — judge each as pass/fail\n{crit_block}\n\n"
            f"# Output schema (return EXACTLY this shape, no prose, no fences):\n"
            "{\n"
            '  "criteria": [\n'
            '    {"id": "<criterion-id>", "pass": true|false, "reason": "<one sentence>"}\n'
            "  ],\n"
            '  "overall_feedback": "<2-3 sentence summary>"\n'
            "}"
        )

    def _parse_judge_json(self, raw: str) -> dict[str, Any] | None:
        """Parse the judge's response. Returns None on unrecoverable failure."""

        raw = raw.strip()
        # Strip common markdown fences.
        if raw.startswith("```"):
            raw = raw.strip("`")
            # Drop leading "json" line if present.
            first_newline = raw.find("\n")
            if first_newline >= 0:
                raw = raw[first_newline + 1 :]
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        # Last resort: find the first {...} block and try again.
        match = self._JSON_OBJECT_RE.search(raw)
        if match:
            try:
                obj = json.loads(match.group(0))
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                return None
        return None


def _criterion_description(c: dict[str, Any]) -> str:
    """Human-readable description of a rubric criterion for the LLM judge."""

    check = c.get("check", "")
    if check == "has_section":
        return f"Output must contain a section/header for '{c.get('section', '')}'."
    if check == "sentence_count_at_least":
        return f"Output must contain at least {c.get('min', 1)} sentences."
    if check == "bullet_count_at_least":
        return f"Output must contain at least {c.get('min', 1)} bullet points."
    if check == "contains_any":
        toks = c.get("tokens", [])
        return f"Output must contain at least one of: {', '.join(toks)}."
    return f"Unknown criterion check type {check!r}."


class StubLLMJudgeEvaluator(Evaluator):
    """Legacy v0.10.0 stub. Retained for backwards-compat.

    v0.11.0+ uses `LLMJudgeEvaluator` with a real provider. This class only
    fires when `get_evaluator("llm-judge")` is called without a provider —
    the orchestrator never goes through that path in v0.11.0+.
    """

    name = "llm-judge-stub"

    def evaluate(
        self, task: Task, deliverables: list[Path]
    ) -> EvaluationResult:
        raise NotImplementedError(
            "StubLLMJudgeEvaluator is a legacy v0.10.0 placeholder. "
            "v0.11.0 replaces it with LLMJudgeEvaluator — call "
            "`get_evaluator('llm-judge', provider=..., model=...)` or use "
            "`bstack bench run --evaluator llm-judge --provider databricks "
            "--judge-model <name>`. See references/provider-standards.md."
        )


def get_evaluator(
    name: str,
    *,
    provider: object = None,
    model: str = "",
    max_tokens: int = 2048,
    **kwargs: object,
) -> Evaluator:
    if name in ("rubric", "rubric-match", "deterministic"):
        return RubricMatchEvaluator()
    if name in ("llm", "llm-judge"):
        if provider is None or not model:
            return StubLLMJudgeEvaluator()
        return LLMJudgeEvaluator(provider=provider, model=model, max_tokens=max_tokens)
    raise ValueError(
        f"Unknown evaluator '{name}'. Available: rubric-match, llm-judge."
    )
