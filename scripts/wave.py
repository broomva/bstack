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
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

try:  # `from scripts.wave import main` (tests) — package-relative
    from scripts import peer
except ImportError:  # `python3 scripts/wave.py` — scripts/ is sys.path[0]
    import peer  # type: ignore[no-redef]

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
    session_id: str | None = None      # `claude --bg` short id (peer.parse_session_id)
    session_name: str | None = None    # `<worktree>-<ticket>-<slug>` (peer.compose_name)


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


def read_wave_state(wd: Path) -> dict[str, dict]:
    """Return {slug: last_event_dict}. Slugs with no JSONL get {'event': 'pending'}."""
    m = read_manifest(wd)
    out: dict[str, dict] = {}
    for plan in m.plans:
        jl = wd / f"{plan.slug}.status.jsonl"
        if not jl.exists():
            out[plan.slug] = {"event": "pending"}
            continue
        last = None
        for line in jl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                continue
        out[plan.slug] = last or {"event": "pending"}
    return out


def _pr_number(pr_url: str) -> str:
    url = pr_url or ""
    # Prefer explicit /pull/<n> pattern (GitHub URLs)
    m = re.search(r"/pull/(\d+)", url)
    if m:
        return f"#{m.group(1)}"
    # Fall back to trailing numeric path segment (e.g. https://x/1218)
    m = re.search(r"/(\d+)$", url)
    if m:
        return f"#{m.group(1)}"
    return url or "—"


def render_status_table(wd: Path, agents: list[dict] | None = None) -> str:
    """Render the human-readable status table including reflexive suggestions.

    `agents` is the parsed `claude agents --json --all` listing (or None when
    it could not be read). Liveness is joined per plan by session id, then by
    session name; a plan with nothing to join on renders as `unknown`, never
    as live."""
    m = read_manifest(wd)
    state = read_wave_state(wd)
    rows: list[tuple[str, str, str, str, str, str, str]] = []
    open_prs: list[str] = []
    attention: list[str] = []
    all_merged = True
    any_event = False
    for plan in m.plans:
        s = state.get(plan.slug, {"event": "pending"})
        ev = s.get("event", "pending")
        if ev != "pending":
            any_event = True
        if ev != "pr_merged":
            all_merged = False
        pr = _pr_number(s.get("pr", ""))
        live, entry = peer.liveness(agents, session_id=plan.session_id,
                                    name=plan.session_name)
        rows.append((plan.slug, plan.branch, plan.linear or "—", ev, pr,
                     plan.session_name or "—", live))
        if ev == "pr_opened" and s.get("pr"):
            open_prs.append(s["pr"])
        sid = plan.session_id or (entry or {}).get("id") or "<id>"
        if live == peer.IDLE_START:
            attention.append(
                f"  • {plan.session_name or plan.slug} is waiting for a prompt — "
                f"SendMessage it its plan, or: claude attach {sid}")
        elif live == peer.GONE and ev not in ("pr_merged", "failed"):
            attention.append(
                f"  • {plan.session_name or plan.slug} is gone (no pid) after "
                f"'{ev}' — inspect: claude logs {sid}; then re-dispatch")
    lines = [f"{m.wave_id} ({m.name or '—'}) — created {m.created_at}", ""]
    lines.append(f"  {'SLUG':<28} {'BRANCH':<28} {'LINEAR':<10} {'LAST EVENT':<16} "
                 f"{'PR':<8} {'SESSION':<40} LIVE")
    for slug, branch, linear, ev, pr, sname, live in rows:
        lines.append(f"  {slug:<28} {branch:<28} {linear:<10} {ev:<16} {pr:<8} "
                     f"{sname:<40} {live}")
    lines.append("")
    if agents is None:
        lines.append("(liveness unavailable: `claude agents --json --all` could not be read)")
    if open_prs or attention:
        lines.append("Suggestions:")
        for pr in open_prs:
            num = _pr_number(pr).lstrip("#")
            lines.append(f"  • {pr} is open — run: p9 watch {num} --background")
        lines.extend(attention)
    if all_merged and any_event:
        if not (open_prs or attention):
            lines.append("Suggestions:")
        lines.append("  • All slugs merged — run: make janitor && "
                     "python3 skills/bookkeeping/scripts/bookkeeping.py run")
    return "\n".join(lines)


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


def _find_repo_root(start: Path) -> Path:
    """Walk up from start to find the nearest .git directory. Return its parent."""
    p = Path(start).resolve()
    for candidate in (p, *p.parents):
        if (candidate / ".git").exists():
            return candidate
    raise WaveError(f"no git repo above {start}")


def _git_is_clean(repo: Path, exclude_paths: set[Path] | None = None) -> bool:
    """Return True if repo has no uncommitted changes (ignoring plan files under validation)."""
    r = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
        check=True, capture_output=True, text=True,
    )
    if not r.stdout.strip():
        return True
    if not exclude_paths:
        return False
    repo_resolved = repo.resolve()
    excludes_resolved = {Path(p).resolve() for p in exclude_paths}
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        # porcelain format: "XY path" or "XY path -> path"
        rel = line[3:].strip().split(" -> ")[-1]
        abs_path = (repo_resolved / rel).resolve()
        if abs_path not in excludes_resolved:
            return False
    return True


def validate_plans(plan_paths: list[Path]) -> list[dict]:
    """Atomic pre-launch validation. Returns a list of resolved entries.

    Raises WaveError on first failure. Nothing is created or launched.
    """
    if not plan_paths:
        raise WaveError("no plans provided")

    entries: list[dict] = []
    seen_branches: dict[str, Path] = {}
    seen_worktrees: dict[str, Path] = {}
    seen_repos: set[Path] = set()
    resolved_plan_paths: set[Path] = set()

    for pp in plan_paths:
        pp = Path(pp).resolve()
        resolved_plan_paths.add(pp)
        fm = parse_plan_frontmatter(pp)
        repo_root = _find_repo_root(pp.parent)
        worktree_abs = (repo_root / fm["worktree"]).resolve()
        branch = fm["branch"]
        if branch in seen_branches:
            raise WaveError(
                f"duplicate branch {branch!r} in {pp} and {seen_branches[branch]}")
        seen_branches[branch] = pp
        if str(worktree_abs) in seen_worktrees:
            raise WaveError(
                f"duplicate worktree {worktree_abs} in {pp} and "
                f"{seen_worktrees[str(worktree_abs)]}")
        seen_worktrees[str(worktree_abs)] = pp
        seen_repos.add(repo_root)
        entries.append({
            "plan_path": str(pp),
            "repo_root": str(repo_root),
            "worktree": str(worktree_abs),
            "branch": branch,
            "base": fm.get("base", "main"),
            "slug": fm.get("slug") or derive_slug(pp),
            "linear": fm.get("linear"),
            "mcp": fm.get("mcp"),
        })
        # Fail before anything is created: a bad mcp value or an uncomposable
        # name is a validation error, not a launch-time surprise.
        peer.mcp_mode(fm.get("mcp"))
        peer.compose_name(worktree_abs, fm.get("linear"), entries[-1]["slug"])

    for repo in seen_repos:
        if not _git_is_clean(repo, exclude_paths=resolved_plan_paths):
            raise WaveError(f"source repo {repo} is dirty; refuse to start wave")
    return entries


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


def _cmd_status(args) -> int:
    wd = wave_dir(args.wave_id)
    if not wd.exists():
        raise WaveError(f"wave {args.wave_id} not found at {wd}")
    print(render_status_table(wd, agents=peer.list_agents(_claude_binary())))
    return 0


def list_waves() -> list[dict]:
    """Return list of {wave_id, name, created_at, plans, summary} for each wave dir."""
    root = cache_dir()
    if not root.exists():
        return []
    out = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or not entry.name.startswith("wave_"):
            continue
        try:
            m = read_manifest(entry)
        except WaveError:
            continue
        state = read_wave_state(entry)
        summary = {"total": len(m.plans), "merged": 0, "open_pr": 0,
                   "in_progress": 0, "failed": 0}
        for plan in m.plans:
            ev = state.get(plan.slug, {}).get("event", "pending")
            if ev == "pr_merged":
                summary["merged"] += 1
            elif ev == "pr_opened":
                summary["open_pr"] += 1
            elif ev == "failed":
                summary["failed"] += 1
            else:
                summary["in_progress"] += 1
        out.append({
            "wave_id": m.wave_id,
            "name": m.name,
            "created_at": m.created_at,
            "plans": len(m.plans),
            "summary": summary,
        })
    return out


def _cmd_list(args) -> int:
    waves = list_waves()
    if not waves:
        print("(no waves)")
        return 0
    for w in waves:
        s = w["summary"]
        bits = []
        if s["merged"]: bits.append(f"{s['merged']} merged")
        if s["open_pr"]: bits.append(f"{s['open_pr']} open PR")
        if s["in_progress"]: bits.append(f"{s['in_progress']} in-progress")
        if s["failed"]: bits.append(f"{s['failed']} failed")
        state = " · ".join(bits) if bits else "no events"
        name = w["name"] or "—"
        print(f"{w['wave_id']:<28} {name:<24} {w['created_at']:<22} "
              f"{w['plans']} plan(s) · {state}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bstack wave")
    sub = parser.add_subparsers(dest="cmd", required=True)
    dp = sub.add_parser("dispatch")
    dp.add_argument("plans", nargs="+", type=Path)
    dp.add_argument("--name", default=None)
    dp.add_argument("--dry-run", action="store_true")
    sp = sub.add_parser("status")
    sp.add_argument("wave_id")
    sub.add_parser("list")

    rp = sub.add_parser("report")
    rp.add_argument("--wave", required=True)
    rp.add_argument("--plan", required=True)
    rp.add_argument("--event", required=True)
    for k in ("branch", "head", "pr", "merge-sha", "phase", "reason", "task"):
        rp.add_argument(f"--{k}", default=None)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "dispatch":
            rc = _cmd_dispatch(args)
            raise SystemExit(rc)
        if args.cmd == "report":
            _cmd_report(args)
        if args.cmd == "status":
            return _cmd_status(args)
        if args.cmd == "list":
            return _cmd_list(args)
    except WaveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(0)


def _claude_binary() -> str:
    return os.environ.get("BSTACK_WAVE_CLAUDE_BIN") or "claude"


def _ensure_claude_on_path() -> None:
    bin_name = _claude_binary()
    if os.path.isabs(bin_name):
        if not os.access(bin_name, os.X_OK):
            raise WaveError(f"{bin_name} is not executable")
        return
    for d in os.environ.get("PATH", "").split(os.pathsep):
        cand = Path(d) / bin_name
        if cand.exists() and os.access(cand, os.X_OK):
            return
    raise WaveError(
        f"{bin_name!r} not found on PATH. Install Claude Code or set "
        f"BSTACK_WAVE_CLAUDE_BIN to a stub for testing.")


def _create_worktree(repo: Path, worktree: Path, branch: str, base: str) -> None:
    if worktree.exists():
        head = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "--abbrev-ref", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        if head != branch:
            raise WaveError(
                f"worktree {worktree} exists but on {head}, expected {branch}")
        if not _git_is_clean(worktree):
            raise WaveError(f"worktree {worktree} exists but is dirty")
        return
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", str(worktree),
         "-b", branch, base],
        check=True, capture_output=True, text=True,
    )


def _build_prompt(wave_id: str, slug: str, plan_path: Path, worktree: Path,
                  session_name: str) -> str:
    return (
        f"Session: {session_name}\n"
        f"Peers address you by that name (Fanout P5); keep it in every report and "
        f"in the handoff.\n"
        f"Worktree: {worktree}\n"
        f"You are already inside this worktree; do not re-enter or recreate it.\n"
        f"Plan: {plan_path}\n"
        f"Wave: {wave_id}\n"
        f"Plan-slug: {slug}\n\n"
        f"Use the superpowers:subagent-driven-development skill to execute the plan "
        f"task-by-task.\n\n"
        f"Report lifecycle events by shelling out:\n"
        f"  bstack wave report --wave {wave_id} --plan {slug} --event started\n"
        f"  bstack wave report --wave {wave_id} --plan {slug} --event branch_pushed --branch <name> --head <sha>\n"
        f"  bstack wave report --wave {wave_id} --plan {slug} --event pr_opened --pr <url>\n"
        f"  bstack wave report --wave {wave_id} --plan {slug} --event pr_merged --merge-sha <sha>\n"
        f"  bstack wave report --wave {wave_id} --plan {slug} --event failed --phase <where> --reason <why>\n"
    )


def _cmd_dispatch(args) -> int:
    entries = validate_plans(args.plans)  # atomic; raises on first failure
    binary = _claude_binary()
    for e in entries:
        e["session_name"] = peer.compose_name(e["worktree"], e.get("linear"), e["slug"])
    if args.dry_run:
        print(f"would dispatch wave with {len(entries)} plan(s):")
        for e in entries:
            print(f"  {e['slug']:<28} {e['branch']:<28} {e['worktree']}")
            argv = peer.build_spawn_argv(
                e["session_name"], "<prompt>", binary=binary,
                mcp=peer.mcp_mode(e.get("mcp")))
            print(f"    spawn: {' '.join(_shell_quote(a) for a in argv)}")
        return 0
    _ensure_claude_on_path()

    wave_id = mint_wave_id()
    wd = wave_dir(wave_id)
    wd.mkdir(parents=True, exist_ok=True)

    # The manifest is written BEFORE any peer launches: a peer's very first
    # act is `bstack wave report --event started`, which validates its slug
    # against this file. Session ids are filled in as each spawn returns.
    repo_root = entries[0]["repo_root"]
    plan_entries: list[PlanEntry] = [
        PlanEntry(
            slug=e["slug"], plan_path=e["plan_path"], worktree=e["worktree"],
            branch=e["branch"], base=e["base"], linear=e.get("linear"),
            agent_pid=None, launched_at=None, session_id=None,
            session_name=e["session_name"],
        )
        for e in entries
    ]
    manifest = Manifest(wave_id=wave_id, name=args.name, created_at=_utc_now_iso(),
                        repo_root=repo_root, plans=plan_entries)
    write_manifest(wd, manifest)

    failures = 0
    for e, pe in zip(entries, plan_entries):
        wt = Path(e["worktree"])
        _create_worktree(Path(e["repo_root"]), wt, e["branch"], e["base"])
        prompt = _build_prompt(wave_id, e["slug"], Path(e["plan_path"]), wt,
                               pe.session_name or "")
        argv = peer.build_spawn_argv(pe.session_name or "", prompt, binary=binary,
                                     mcp=peer.mcp_mode(e.get("mcp")))
        res = peer.spawn(argv, cwd=wt)
        pe.launched_at = _utc_now_iso()
        pe.session_id = res.session_id
        write_manifest(wd, manifest)  # persist each id as soon as it is known
        if not res.ok:
            failures += 1
        print(f"  {pe.slug:<28} {pe.session_name:<40} {res.summary}")

    print(f"Wave {wave_id} launched ({len(plan_entries) - failures}/{len(plan_entries)} agents).")
    print(f"Watch dashboard:  claude agents")
    print(f"Forensic state:   bstack wave status {wave_id}")
    return 1 if failures else 0


def _shell_quote(arg: str) -> str:
    import shlex
    return shlex.quote(str(arg))


if __name__ == "__main__":
    sys.exit(main())
