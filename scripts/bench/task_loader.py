"""bench.task_loader — Load bstack bench task sets from JSONL.

A task set is a JSONL file (one task per line) at
`scripts/bench/tasks/<set>.jsonl`. Each task carries:

  task_id, task_set, occupation, sector, prompt,
  reference_files, deliverable_files, rubric_json,
  task_value_usd, expected_skills

External / vendored task sets (e.g. a GDPVal subset) can ship under the
same directory; the loader treats all `.jsonl` files there as task sets.

Stdlib only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# `scripts/bench/__file__` -> `<bstack-repo>/scripts/bench`
_TASKS_DIR = Path(__file__).resolve().parent / "tasks"


@dataclass
class Task:
    """Canonical bench task record. Mirrors GDPVal shape with bstack extensions."""

    task_id: str
    task_set: str
    occupation: str
    sector: str
    prompt: str
    reference_files: list[str] = field(default_factory=list)
    deliverable_files: list[str] = field(default_factory=list)
    rubric_json: dict[str, Any] = field(default_factory=dict)
    task_value_usd: float = 0.0
    expected_skills: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Task":
        return cls(
            task_id=raw["task_id"],
            task_set=raw["task_set"],
            occupation=raw.get("occupation", ""),
            sector=raw.get("sector", ""),
            prompt=raw["prompt"],
            reference_files=list(raw.get("reference_files", [])),
            deliverable_files=list(raw.get("deliverable_files", [])),
            rubric_json=dict(raw.get("rubric_json", {})),
            task_value_usd=float(raw.get("task_value_usd", 0.0)),
            expected_skills=list(raw.get("expected_skills", [])),
        )


class TaskSetNotFound(FileNotFoundError):
    """Raised when a requested task set name has no `.jsonl` file."""


def tasks_dir() -> Path:
    """Return the canonical task-sets directory.

    Override via `BSTACK_BENCH_TASKS_DIR` env var (used by tests).
    """

    import os

    override = os.environ.get("BSTACK_BENCH_TASKS_DIR")
    if override:
        return Path(override)
    return _TASKS_DIR


def list_task_sets() -> list[str]:
    """Return the sorted list of available task set names (no `.jsonl` suffix)."""

    d = tasks_dir()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.jsonl"))


def load_task_set(name: str) -> list[Task]:
    """Load and parse a task set by name.

    Raises TaskSetNotFound if the file does not exist.
    Each non-empty line must parse as a JSON object.
    """

    path = tasks_dir() / f"{name}.jsonl"
    if not path.is_file():
        raise TaskSetNotFound(
            f"Task set '{name}' not found at {path}. "
            f"Available: {', '.join(list_task_sets()) or '(none)'}"
        )
    tasks: list[Task] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{lineno}: invalid JSON: {exc.msg}"
                ) from exc
            try:
                tasks.append(Task.from_dict(obj))
            except KeyError as exc:
                raise ValueError(
                    f"{path}:{lineno}: missing required key {exc}"
                ) from exc
    return tasks
