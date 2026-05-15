#!/usr/bin/env python3
"""bstack wave — parallel sub-phase dispatch for Claude Code agent view.

Stdlib-only. See docs/superpowers/specs/2026-05-13-bstack-wave-design.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}-")


class WaveError(Exception):
    """Raised for any user-facing wave failure. Always carries a clear message."""


_FM_DELIM = "---"


def parse_plan_frontmatter(plan_path: Path) -> dict[str, str]:
    """Parse the `wave:` block of a plan file's YAML frontmatter.

    Returns a flat dict with keys: worktree, branch, base, slug, linear.
    `worktree` and `branch` are required; others are optional (may be missing).

    Raises WaveError with a clear message on:
      - missing/unreadable file
      - no `---` frontmatter block at top
      - frontmatter present but no `wave:` key
    """
    p = Path(plan_path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise WaveError(f"plan file not found: {p}") from exc

    if not text.startswith(_FM_DELIM):
        raise WaveError(f"{p}: no frontmatter block (file does not start with ---)")

    lines = text.splitlines()
    if lines[0].strip() != _FM_DELIM:
        raise WaveError(f"{p}: no frontmatter block")
    close = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == _FM_DELIM:
            close = i
            break
    if close is None:
        raise WaveError(f"{p}: unterminated frontmatter (no closing ---)")

    fm_lines = lines[1:close]

    wave_start = None
    for i, line in enumerate(fm_lines):
        if line.rstrip() == "wave:":
            wave_start = i
            break
    if wave_start is None:
        raise WaveError(f"{p}: frontmatter has no `wave:` block")

    out: dict[str, str] = {}
    for line in fm_lines[wave_start + 1:]:
        if not line.strip():
            continue
        if not line.startswith("  "):
            break
        kv = line.strip()
        if ":" not in kv:
            continue
        key, _, value = kv.partition(":")
        value = value.strip()
        if "#" in value:
            value = value.split("#", 1)[0].strip()
        out[key.strip()] = value

    for required in ("worktree", "branch"):
        if required not in out:
            raise WaveError(f"{p}: wave.{required} is required")

    return out


MANIFEST_SCHEMA_VERSION = 1


@dataclass
class PlanEntry:
    slug: str
    plan_path: str
    worktree: str
    branch: str
    base: str
    linear: str | None
    agent_pid: int | None
    launched_at: str | None


@dataclass
class Manifest:
    wave_id: str
    name: str | None
    created_at: str
    repo_root: str
    plans: list[PlanEntry] = field(default_factory=list)


def write_manifest(wave_dir: Path, m: Manifest) -> None:
    wave_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "wave_id": m.wave_id,
        "name": m.name,
        "created_at": m.created_at,
        "repo_root": m.repo_root,
        "plans": [asdict(p) for p in m.plans],
    }
    (wave_dir / "manifest.json").write_text(
        json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )


def read_manifest(wave_dir: Path) -> Manifest:
    mf = wave_dir / "manifest.json"
    if not mf.exists():
        raise WaveError(f"no manifest.json in {wave_dir}")
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WaveError(f"{mf}: invalid JSON: {exc}") from exc
    sv = data.get("schema_version")
    if sv != MANIFEST_SCHEMA_VERSION:
        raise WaveError(
            f"{mf}: unknown schema_version={sv} (expected {MANIFEST_SCHEMA_VERSION})"
        )
    plans = [PlanEntry(**p) for p in data.get("plans", [])]
    return Manifest(
        wave_id=data["wave_id"],
        name=data.get("name"),
        created_at=data["created_at"],
        repo_root=data["repo_root"],
        plans=plans,
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cache_dir() -> Path:
    """Root of wave state. Overridable via $BSTACK_WAVE_CACHE_DIR for tests."""
    override = os.environ.get("BSTACK_WAVE_CACHE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "bstack" / "wave"


def wave_dir(wave_id: str) -> Path:
    return cache_dir() / wave_id


def append_status_event(wd: Path, slug: str, event: str, extras: dict) -> None:
    """Append one JSON line to <slug>.status.jsonl. Validates slug exists in manifest."""
    m = read_manifest(wd)
    slugs = {p.slug for p in m.plans}
    if slug not in slugs:
        raise WaveError(f"slug {slug!r} not in manifest of wave {m.wave_id}")
    payload = {"ts": _utc_now_iso(), "event": event}
    payload.update(extras)
    jl = wd / f"{slug}.status.jsonl"
    with jl.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")


def mint_wave_id() -> str:
    """Return wave_<unix-seconds>_<4-char-base36-random>.

    Stdlib-only (no ULID dep). Unix seconds give total ordering at 1-second
    granularity; secrets.token_hex(2) gives 4 hex chars = ~65k collision space
    per second.
    """
    return f"wave_{int(time.time())}_{secrets.token_hex(2)}"


def derive_slug(plan_path: Path) -> str:
    """Slug = filename without date prefix and .md extension."""
    name = Path(plan_path).stem  # strips .md
    return _DATE_PREFIX.sub("", name)


def _cmd_report(args) -> int:
    wd = wave_dir(args.wave)
    if not wd.exists():
        raise WaveError(f"wave {args.wave} not found at {wd}")
    extras: dict = {}
    for k in ("branch", "head", "pr", "phase", "reason", "task"):
        v = getattr(args, k, None)
        if v is not None:
            extras[k] = v
    merge_sha = getattr(args, "merge_sha", None)  # argparse maps --merge-sha
    if merge_sha is not None:
        extras["merge_sha"] = merge_sha
    append_status_event(wd, args.plan, args.event, extras)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bstack wave")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dispatch")
    sub.add_parser("status")
    sub.add_parser("list")

    rp = sub.add_parser("report")
    rp.add_argument("--wave", required=True)
    rp.add_argument("--plan", required=True)
    rp.add_argument("--event", required=True)
    for k in ("branch", "head", "pr", "merge-sha", "phase", "reason", "task"):
        rp.add_argument(f"--{k}", default=None)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "report":
            _cmd_report(args)
    except WaveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(0)


if __name__ == "__main__":
    sys.exit(main())
