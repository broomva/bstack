#!/usr/bin/env python3
"""bstack bench orchestrator — two-phase skill-evolution benchmark harness.

Spec: specs/bench-skill-evolution.md
Ticket: BRO-1205

Subcommands:
  run [--tasks SET] [--runner R] [--evaluator E]
      [--phase {1|2|both}] [--dry-run] [--budget-usd N] [--resume RUN_ID]
  compare [--run-id RUN_ID]
  tasks list
  status [--run-id RUN_ID]
  --help | -h

Phase protocol (specs/bench-skill-evolution.md §Phase Protocol):
  Phase 1 (cold) — empty skill snapshot, run all tasks, capture results.
  Snapshot  — tarball workspace/.bstack-skills/ between phases.
  Phase 2 (warm) — restore skills snapshot, re-run all tasks.
  Comparison — Phase 1 vs Phase 2 deltas (tokens, quality, duration).

State directory: ~/.config/bstack/bench/runs/<run-id>/
  - config.json
  - phase1_results.jsonl, phase1_skills_snapshot.tar.gz
  - phase2_results.jsonl
  - comparison.json
  - REPORT.md

Stdlib only. No third-party deps. Anthropic SDK / litellm hooks are
deliberately not imported here; live mode lives in a future PR per
specs/bench-skill-evolution.md §Phasing.

Exit codes:
  0  success
  2  invalid arguments
  3  task set not found
  4  budget exceeded mid-run
  5  resume run-id not found
  6  all task runs failed (structurally broken — see stderr for runner messages)
  7  compare requires both phase 1 + phase 2 results
  8  P20 model-isolation violation (judge model equals agent model without
     --allow-same-judge-model)
  9  provider not configured (missing required env vars)
 10  provider SDK not installed (e.g. `pip install openai`)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tarfile
import time
import uuid
from pathlib import Path
from typing import Optional


# Add parent so `python3 orchestrator.py` works without installation.
_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

# Local imports (after sys.path setup).
from bench.agent_runner import AgentRunner, get_runner  # noqa: E402
from bench.evaluator import Evaluator, get_evaluator  # noqa: E402
from bench.task_loader import (  # noqa: E402
    Task,
    TaskSetNotFound,
    list_task_sets,
    load_task_set,
)


def _runs_root() -> Path:
    """Resolve the runs directory, honoring `BSTACK_BENCH_HOME`."""

    override = os.environ.get("BSTACK_BENCH_HOME")
    if override:
        return Path(override) / "runs"
    return Path.home() / ".config" / "bstack" / "bench" / "runs"


def _new_run_id() -> str:
    """ULID-ish lexicographic run id: YYYYMMDDTHHMMSS-<shortuuid>."""

    ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    return f"{ts}-{uuid.uuid4().hex[:8]}"


def _ensure_run_dir(run_id: str) -> Path:
    d = _runs_root() / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _phase_workspaces_dir(run_dir: Path, phase: int) -> Path:
    return run_dir / f"phase{phase}-workspaces"


def _phase_results_path(run_dir: Path, phase: int) -> Path:
    return run_dir / f"phase{phase}_results.jsonl"


def _skills_snapshot_path(run_dir: Path) -> Path:
    return run_dir / "phase1_skills_snapshot.tar.gz"


# ---------------------------------------------------------------------------
# Skill-snapshot simulation. In a real run, this tarballs ~/.claude/skills/.
# In dry-run, we mint a tiny synthetic directory to verify the snapshot path
# end-to-end without touching the user's actual skills.

def _simulate_phase1_skill_dir(run_dir: Path) -> Path:
    skills_dir = run_dir / "phase1-skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    # Three fake skills, one per smoke task — same names the rubric mentions.
    for slug in ("p9", "crystallize", "bookkeeping"):
        path = skills_dir / slug
        path.mkdir(exist_ok=True)
        (path / "SKILL.md").write_text(
            f"---\nname: {slug}\ndescription: synthetic skill for bench dry-run\n---\n",
            encoding="utf-8",
        )
    return skills_dir


def _snapshot_skills(skills_dir: Path, dest: Path) -> None:
    if not skills_dir.is_dir():
        return
    with tarfile.open(dest, "w:gz") as tar:
        tar.add(skills_dir, arcname="skills")


# ---------------------------------------------------------------------------
# Per-task run (single phase, single task).

def _run_task(
    task: Task,
    runner: AgentRunner,
    evaluator: Evaluator,
    workspace_root: Path,
    phase: int,
) -> dict:
    workspace = workspace_root / task.task_id
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        run_result = runner.run(task, workspace, phase)
    except NotImplementedError as exc:
        # Stub runners raise NotImplementedError with a clear migration
        # message. Surface that to stderr so the caller sees it without
        # having to read the per-task JSONL.
        print(f"bstack bench: runner '{runner.name}' not wired: {exc}", file=sys.stderr)
        return {
            "task_id": task.task_id,
            "phase": phase,
            "exit_status": "failure",
            "error": f"runner NotImplementedError: {exc!s}",
        }
    except Exception as exc:
        print(f"bstack bench: runner '{runner.name}' raised {type(exc).__name__}: {exc}", file=sys.stderr)
        return {
            "task_id": task.task_id,
            "phase": phase,
            "exit_status": "failure",
            "error": f"runner exception: {exc!s}",
        }
    if run_result.exit_status != "success":
        return {**run_result.to_dict(), "phase": phase}
    # Symmetric stub-handling for the evaluator: StubLLMJudgeEvaluator
    # raises NotImplementedError; surface it the same way as the runner so
    # the all-failed → exit 6 path catches structurally-unwired evaluators
    # (P20 round-1 fix).
    try:
        eval_result = evaluator.evaluate(task, run_result.deliverable_paths)
    except NotImplementedError as exc:
        print(
            f"bstack bench: evaluator '{evaluator.name}' not wired: {exc}",
            file=sys.stderr,
        )
        return {
            **run_result.to_dict(),
            "phase": phase,
            "exit_status": "failure",
            "error": f"evaluator NotImplementedError: {exc!s}",
        }
    except Exception as exc:
        print(
            f"bstack bench: evaluator '{evaluator.name}' raised "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return {
            **run_result.to_dict(),
            "phase": phase,
            "exit_status": "failure",
            "error": f"evaluator exception: {exc!s}",
        }
    return {
        **run_result.to_dict(),
        "phase": phase,
        "evaluation": eval_result.to_dict(),
    }


# ---------------------------------------------------------------------------
# Phase loop.

def _run_phase(
    tasks: list[Task],
    runner: AgentRunner,
    evaluator: Evaluator,
    run_dir: Path,
    phase: int,
    budget_usd: Optional[float],
    completed_task_ids: set[str],
) -> tuple[list[dict], float]:
    workspace_root = _phase_workspaces_dir(run_dir, phase)
    workspace_root.mkdir(parents=True, exist_ok=True)
    results_path = _phase_results_path(run_dir, phase)
    spent = 0.0
    new_results: list[dict] = []
    with results_path.open("a", encoding="utf-8") as out:
        for task in tasks:
            if task.task_id in completed_task_ids:
                continue
            result = _run_task(task, runner, evaluator, workspace_root, phase)
            spent += float(
                result.get("tokens", {}).get("cost_usd", 0.0) if isinstance(result, dict) else 0.0
            )
            out.write(json.dumps(result) + "\n")
            out.flush()
            new_results.append(result)
            if budget_usd is not None and spent > budget_usd:
                print(
                    f"  ⚠ budget exceeded ({spent:.4f} > {budget_usd:.4f} USD); "
                    f"stopping phase {phase} early.",
                    file=sys.stderr,
                )
                return new_results, spent
    return new_results, spent


def _read_existing_results(path: Path) -> list[dict]:
    """Read JSONL results, last-write-wins per task_id (P20 round-1 fix).

    A task that failed then re-ran successfully writes two rows under the
    same task_id; without deduplication, `_aggregate` double-counts.
    Last-write-wins preserves the resume-completion contract (the success
    row, if any, is last) while keeping aggregates honest.
    """

    if not path.is_file():
        return []
    by_id: dict[str, dict] = {}
    order: list[str] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            tid = obj.get("task_id")
            if not isinstance(tid, str):
                continue
            if tid not in by_id:
                order.append(tid)
            by_id[tid] = obj
    return [by_id[t] for t in order]


# ---------------------------------------------------------------------------
# Comparison + report.

def _aggregate(results: list[dict]) -> dict:
    total_tokens = 0
    total_cost = 0.0
    total_quality = 0.0
    total_payment = 0.0
    n_with_eval = 0
    for r in results:
        tok = r.get("tokens") or {}
        total_tokens += int(tok.get("total_tokens", 0) or 0)
        total_cost += float(tok.get("cost_usd", 0.0) or 0.0)
        ev = r.get("evaluation") or {}
        if ev:
            total_quality += float(ev.get("quality_score", 0.0) or 0.0)
            total_payment += float(ev.get("actual_payment_usd", 0.0) or 0.0)
            n_with_eval += 1
    return {
        "task_count": len(results),
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 4),
        "mean_quality": round(total_quality / n_with_eval, 4) if n_with_eval else 0.0,
        "total_payment_usd": round(total_payment, 4),
    }


def _compare(phase1: list[dict], phase2: list[dict]) -> dict:
    agg1 = _aggregate(phase1)
    agg2 = _aggregate(phase2)
    tokens_ratio = (
        round(agg2["total_tokens"] / agg1["total_tokens"], 4)
        if agg1["total_tokens"]
        else None
    )
    quality_delta = round(agg2["mean_quality"] - agg1["mean_quality"], 4)
    per_task: list[dict] = []
    by_id = {r["task_id"]: r for r in phase2}
    for r1 in phase1:
        r2 = by_id.get(r1["task_id"])
        t1 = (r1.get("tokens") or {}).get("total_tokens", 0)
        t2 = (r2.get("tokens") or {}).get("total_tokens", 0) if r2 else 0
        q1 = (r1.get("evaluation") or {}).get("quality_score", 0.0)
        q2 = (r2.get("evaluation") or {}).get("quality_score", 0.0) if r2 else 0.0
        per_task.append(
            {
                "task_id": r1["task_id"],
                "tokens_phase1": t1,
                "tokens_phase2": t2,
                "tokens_ratio_p2_over_p1": round(t2 / t1, 4) if t1 else None,
                "quality_phase1": round(q1, 4),
                "quality_phase2": round(q2, 4),
                "quality_delta": round(q2 - q1, 4),
            }
        )
    return {
        "phase1": agg1,
        "phase2": agg2,
        "phase2_tokens_over_phase1": tokens_ratio,
        "phase2_quality_minus_phase1": quality_delta,
        "per_task": per_task,
    }


def _write_report(run_dir: Path, run_id: str, config: dict, cmp_: dict) -> Path:
    report = run_dir / "REPORT.md"
    p1, p2 = cmp_["phase1"], cmp_["phase2"]
    lines: list[str] = []
    lines.append(f"# bstack bench — {run_id}\n")
    lines.append(f"Generated by `bstack bench compare`. Run config:\n")
    lines.append("```json")
    lines.append(json.dumps(config, indent=2))
    lines.append("```\n")
    lines.append("## Aggregate\n")
    lines.append("| metric | phase 1 (cold) | phase 2 (warm) | Δ |")
    lines.append("|---|---|---|---|")
    lines.append(
        f"| tasks | {p1['task_count']} | {p2['task_count']} | "
        f"{p2['task_count'] - p1['task_count']} |"
    )
    lines.append(
        f"| total tokens | {p1['total_tokens']} | {p2['total_tokens']} | "
        f"ratio = {cmp_['phase2_tokens_over_phase1']} |"
    )
    lines.append(
        f"| total cost (USD) | {p1['total_cost_usd']} | {p2['total_cost_usd']} | "
        f"{round(p2['total_cost_usd'] - p1['total_cost_usd'], 4)} |"
    )
    lines.append(
        f"| mean quality | {p1['mean_quality']} | {p2['mean_quality']} | "
        f"{cmp_['phase2_quality_minus_phase1']} |"
    )
    lines.append(
        f"| total payment (USD) | {p1['total_payment_usd']} | {p2['total_payment_usd']} | "
        f"{round(p2['total_payment_usd'] - p1['total_payment_usd'], 4)} |"
    )
    lines.append("")
    lines.append("## Per-task\n")
    lines.append("| task | tok ph1 | tok ph2 | ratio | qual ph1 | qual ph2 | Δq |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in cmp_["per_task"]:
        lines.append(
            f"| {r['task_id']} | {r['tokens_phase1']} | {r['tokens_phase2']} | "
            f"{r['tokens_ratio_p2_over_p1']} | {r['quality_phase1']} | "
            f"{r['quality_phase2']} | {r['quality_delta']} |"
        )
    lines.append("")
    lines.append("## Interpretation\n")
    lines.append(
        "- `ratio < 1.0` means Phase 2 used fewer tokens than Phase 1 — evidence "
        "that warm skills cached reasoning."
    )
    lines.append(
        "- `Δq > 0` means Phase 2 quality improved with warm skills."
    )
    lines.append(
        "- This is a substrate report. Dry-run numbers come from canned responses; "
        "see specs/bench-skill-evolution.md §Reproducibility before reading anything "
        "into a single run."
    )
    lines.append("")
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


# ---------------------------------------------------------------------------
# Subcommand handlers.

def cmd_run(args: argparse.Namespace) -> int:
    try:
        tasks = load_task_set(args.tasks)
    except TaskSetNotFound as exc:
        print(f"bstack bench: {exc}", file=sys.stderr)
        return 3
    # Build runner + evaluator. Live runner and llm-judge evaluator need a
    # provider + model; dry-run + rubric-match don't.
    needs_provider = args.runner in ("live", "claude-code", "vanilla-claude", "codex") \
        or args.evaluator in ("llm", "llm-judge")
    provider = None
    if needs_provider:
        if not args.provider:
            print(
                "bstack bench: --provider required when --runner=live or "
                "--evaluator=llm-judge. Available: databricks, mock. "
                "See references/provider-standards.md.",
                file=sys.stderr,
            )
            return 2
        if not args.model:
            print(
                "bstack bench: --model required when --runner=live or "
                "--evaluator=llm-judge. Example: --model databricks-claude-haiku-4-5.",
                file=sys.stderr,
            )
            return 2
        # P20 enforcement: when llm-judge is in play, judge model must differ
        # from agent model unless --allow-same-judge-model is passed with a
        # rationale. This catches the same-model-echo-chamber failure at the
        # CLI layer, before any LLM cost is incurred.
        if args.evaluator in ("llm", "llm-judge"):
            judge_model = args.judge_model or args.model
            if judge_model == args.model and not args.allow_same_judge_model:
                print(
                    f"bstack bench: P20 violation — judge model "
                    f"({judge_model!r}) equals agent model ({args.model!r}). "
                    "Pass a different --judge-model, OR pass "
                    "--allow-same-judge-model with a rationale string "
                    "(e.g. --allow-same-judge-model='smoke test only').",
                    file=sys.stderr,
                )
                return 8
        # Instantiate provider. Surface configured/installed failures via
        # distinct exit codes so callers can scriptize remediation.
        from bench.providers import (  # local — keep top-level light
            ProviderNotConfigured,
            ProviderNotInstalled,
            get_provider,
        )
        try:
            provider = get_provider(args.provider)
        except KeyError as exc:
            print(f"bstack bench: {exc}", file=sys.stderr)
            return 2
        except ProviderNotInstalled as exc:
            print(f"bstack bench: provider SDK missing: {exc}", file=sys.stderr)
            return 10
        except ProviderNotConfigured as exc:
            print(f"bstack bench: provider not configured: {exc}", file=sys.stderr)
            return 9
    runner = get_runner(args.runner, provider=provider, model=args.model or "")
    evaluator = get_evaluator(
        args.evaluator,
        provider=provider,
        model=args.judge_model or args.model or "",
    )
    if args.resume:
        run_id = args.resume
        run_dir = _runs_root() / run_id
        if not run_dir.is_dir():
            print(f"bstack bench: resume run-id '{run_id}' not found at {run_dir}", file=sys.stderr)
            return 5
    else:
        run_id = _new_run_id()
        run_dir = _ensure_run_dir(run_id)
    config_path = run_dir / "config.json"
    config = {
        "run_id": run_id,
        "tasks_set": args.tasks,
        "task_count": len(tasks),
        "runner": runner.name,
        "evaluator": evaluator.name,
        "phase": args.phase,
        "dry_run": args.dry_run,
        "budget_usd": args.budget_usd,
        "provider": args.provider,
        "model": args.model,
        "judge_model": args.judge_model or args.model,
        "allow_same_judge_model_rationale": args.allow_same_judge_model,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"bstack bench: run={run_id}  tasks={args.tasks}  runner={runner.name}  "
          f"evaluator={evaluator.name}  phase={args.phase}  dry_run={args.dry_run}")

    phases_to_run: list[int]
    if args.phase == "both":
        phases_to_run = [1, 2]
    else:
        phases_to_run = [int(args.phase)]

    spent = 0.0
    # P20 round-1 fix: budget must survive resume. Sum cost_usd from every
    # already-written result so the per-phase budget check counts prior
    # spend. Without this, `--resume --budget-usd 0.01` accepts a fresh
    # $0.01 each session, defeating the cap.
    if args.resume:
        for prior_phase in (1, 2):
            for prior in _read_existing_results(_phase_results_path(run_dir, prior_phase)):
                spent += float((prior.get("tokens") or {}).get("cost_usd", 0.0) or 0.0)
        if spent > 0:
            print(f"  → resume: prior cost ≈ ${spent:.4f} counted toward budget")
        if args.budget_usd is not None and spent > args.budget_usd:
            print(
                f"bstack bench: prior cost ${spent:.4f} already exceeds budget "
                f"${args.budget_usd}; refusing to start.",
                file=sys.stderr,
            )
            return 4
    total_attempted = 0
    total_failed = 0
    for phase in phases_to_run:
        existing = _read_existing_results(_phase_results_path(run_dir, phase))
        completed = {r["task_id"] for r in existing if r.get("exit_status") == "success"}
        print(f"  → phase {phase} ({len(tasks) - len(completed)} of {len(tasks)} tasks)")
        new_results, phase_cost = _run_phase(
            tasks=tasks,
            runner=runner,
            evaluator=evaluator,
            run_dir=run_dir,
            phase=phase,
            budget_usd=(args.budget_usd - spent) if args.budget_usd is not None else None,
            completed_task_ids=completed,
        )
        spent += phase_cost
        total_attempted += len(new_results)
        total_failed += sum(1 for r in new_results if r.get("exit_status") != "success")
        if phase == 1:
            # Snapshot synthetic skills dir between phases.
            skills_dir = _simulate_phase1_skill_dir(run_dir)
            _snapshot_skills(skills_dir, _skills_snapshot_path(run_dir))
            print(f"  → snapshotted skills to {_skills_snapshot_path(run_dir).name}")
        print(f"  ✓ phase {phase} complete — {len(new_results)} new results; "
              f"cumulative cost ≈ ${spent:.4f}")
        if args.budget_usd is not None and spent > args.budget_usd:
            print(f"  ⚠ run halted on budget cap (${args.budget_usd}).")
            return 4
    # All-tasks-failed → exit non-zero. Surfaces structurally-broken runs
    # (e.g. live runner stub firing without SDK/key) instead of silently
    # producing an empty REPORT.md.
    if total_attempted > 0 and total_failed == total_attempted:
        print(
            f"bstack bench: all {total_attempted} task runs failed — "
            "see stderr for runner messages.",
            file=sys.stderr,
        )
        return 6
    # Comparison + report if both phases done.
    if args.phase == "both":
        return _emit_compare(run_dir, run_id, config)
    print(f"bstack bench: run dir = {run_dir}")
    return 0


def _emit_compare(run_dir: Path, run_id: str, config: dict) -> int:
    p1 = _read_existing_results(_phase_results_path(run_dir, 1))
    p2 = _read_existing_results(_phase_results_path(run_dir, 2))
    # P20 round-1 fix: refuse to compare when either phase is empty.
    # Previously `_compare` happily emitted "Phase 2 = 0 tokens" and the
    # report showed phantom regression. A benchmark substrate that
    # silently produces noise as data is worse than no substrate.
    if not p1 or not p2:
        print(
            f"bstack bench: compare requires both phases "
            f"(phase1={len(p1)} task(s), phase2={len(p2)} task(s)). "
            "Run with --phase both, or finish both phases separately before compare.",
            file=sys.stderr,
        )
        return 7
    cmp_ = _compare(p1, p2)
    (run_dir / "comparison.json").write_text(
        json.dumps(cmp_, indent=2) + "\n", encoding="utf-8"
    )
    report = _write_report(run_dir, run_id, config, cmp_)
    print(f"bstack bench: report → {report}")
    print(f"  tokens P2/P1 = {cmp_['phase2_tokens_over_phase1']}  "
          f"Δquality = {cmp_['phase2_quality_minus_phase1']}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    runs = sorted(_runs_root().glob("*"), key=lambda p: p.name, reverse=True)
    if args.run_id:
        run_dir = _runs_root() / args.run_id
    elif runs:
        run_dir = runs[0]
    else:
        print("bstack bench: no runs found.", file=sys.stderr)
        return 5
    if not run_dir.is_dir():
        print(f"bstack bench: run '{run_dir.name}' not found.", file=sys.stderr)
        return 5
    config_path = run_dir / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    return _emit_compare(run_dir, run_dir.name, config)


def cmd_tasks(args: argparse.Namespace) -> int:
    if args.subaction == "list":
        sets = list_task_sets()
        if not sets:
            print("(no task sets registered)")
        else:
            for name in sets:
                tasks = load_task_set(name)
                print(f"{name}  ({len(tasks)} tasks)")
        return 0
    print(f"bstack bench: unknown tasks action '{args.subaction}'", file=sys.stderr)
    return 2


def cmd_status(args: argparse.Namespace) -> int:
    runs = sorted(_runs_root().glob("*"), key=lambda p: p.name, reverse=True)
    if args.run_id:
        candidates = [_runs_root() / args.run_id]
    else:
        candidates = runs[:5]
    if not candidates or not candidates[0].is_dir():
        print("(no runs)")
        return 0
    for run_dir in candidates:
        if not run_dir.is_dir():
            continue
        config_path = run_dir / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
        p1 = _read_existing_results(_phase_results_path(run_dir, 1))
        p2 = _read_existing_results(_phase_results_path(run_dir, 2))
        report = run_dir / "REPORT.md"
        print(
            f"{run_dir.name}  runner={config.get('runner','?')}  "
            f"tasks={config.get('tasks_set','?')}  "
            f"phase1={len(p1)}  phase2={len(p2)}  "
            f"report={'yes' if report.is_file() else 'no'}"
        )
    return 0


# ---------------------------------------------------------------------------
# CLI.

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bstack bench",
        description=(
            "Skill-evolution benchmark harness (BRO-1205). "
            "Two-phase protocol: cold → snapshot skills → warm → compare."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    # run
    run = sub.add_parser("run", help="Run a bench against a task set.")
    run.add_argument("--tasks", default="bstack-smoke", help="Task set name (default: bstack-smoke).")
    run.add_argument("--runner", default="dry-run", help="Agent runner: dry-run | live (stub).")
    run.add_argument("--evaluator", default="rubric-match", help="Evaluator: rubric-match | llm-judge.")
    run.add_argument("--phase", default="both", choices=["1", "2", "both"], help="Phase(s) to run.")
    run.add_argument("--dry-run", action="store_true", default=True, help="Dry-run mode (default). Use --no-dry-run to opt out.")
    run.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    run.add_argument("--budget-usd", type=float, default=None, help="Halt run when cumulative cost exceeds this (USD).")
    run.add_argument("--resume", default=None, help="Resume an existing run-id.")
    # Provider abstraction (v0.11.0). When --runner=live or --evaluator=llm-judge,
    # --provider + --model are required.
    run.add_argument("--provider", default=None, help="LLM provider: databricks | mock (required for live runner / llm-judge).")
    run.add_argument("--model", default=None, help="Agent model name (e.g. databricks-claude-haiku-4-5).")
    run.add_argument("--judge-model", default=None, help="Judge model name (must differ from --model unless --allow-same-judge-model).")
    run.add_argument("--allow-same-judge-model", default=None, help="Rationale string allowing judge model == agent model (P20 override).")
    run.set_defaults(func=cmd_run)

    # compare
    cmp_ = sub.add_parser("compare", help="Compare Phase 1 vs Phase 2 for a run.")
    cmp_.add_argument("--run-id", default=None, help="Run id to compare (default: latest).")
    cmp_.set_defaults(func=cmd_compare)

    # tasks
    tasks = sub.add_parser("tasks", help="Inspect available task sets.")
    tasks_sub = tasks.add_subparsers(dest="subaction", required=True)
    tasks_list = tasks_sub.add_parser("list", help="List registered task sets.")
    tasks_list.set_defaults(func=cmd_tasks, subaction="list")

    # status
    st = sub.add_parser("status", help="Show recent run summaries.")
    st.add_argument("--run-id", default=None)
    st.set_defaults(func=cmd_status)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nbstack bench: interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
