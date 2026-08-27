#!/usr/bin/env python3
"""Report installed skills whose executed code diverges from origin/main.

A skill directory under ~/.claude/skills or ~/.agents/skills is usually a
symlink into a checkout. Which checkout, and which branch that checkout happens
to be parked on, decides what actually runs — merging to main does not.

This reports that difference. It is advisory: drift is a deployment fact, not a
contract violation, and the caller must never turn it into a gate.

The one rule that matters here: a skill this cannot evaluate is reported as
UNKNOWN, never as current. This check exists precisely because the failure mode
is a silent pass, and a checker that resolves ambiguity toward "fine" would
reproduce the bug it was written to catch.

No network. `origin/main` is read as whatever the last fetch left; when that ref
is absent the repo is UNKNOWN, not clean.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _git(repo: Path, *args: str) -> "str | None":
    """Run git in `repo`, returning stripped stdout or None on any failure."""
    try:
        p = subprocess.run(
            ("git", "-C", str(repo), *args),
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    return p.stdout.strip()


def _toplevel(path: Path) -> "Path | None":
    out = _git(path, "rev-parse", "--show-toplevel")
    return Path(out) if out else None


class RepoState:
    """Per-repo facts, computed once and shared by every skill resolving into it."""

    __slots__ = ("root", "branch", "head", "behind", "ref", "dirty", "reason")

    def __init__(self, root: Path):
        self.root = root
        self.reason: "str | None" = None
        self.branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD") or "?"
        self.head = _git(root, "rev-parse", "--short", "HEAD") or "?"
        self.behind: "int | None" = None
        self.dirty: "int | None" = None

        # Prefer origin/main, fall back to origin/master. Read only — never fetch:
        # a doctor run must stay fast and work offline. A stale ref understates
        # drift, which is why absence is reported rather than treated as clean.
        self.ref: "str | None" = None
        for cand in ("refs/remotes/origin/main", "refs/remotes/origin/master"):
            if _git(root, "rev-parse", "--verify", "--quiet", cand):
                self.ref = cand.rsplit("refs/remotes/", 1)[-1]
                break
        if self.ref is None:
            self.reason = "no origin/main ref (never fetched, or no remote)"
            return

        counts = _git(root, "rev-list", "--count", f"HEAD..{self.ref}")
        if counts is None or not counts.isdigit():
            self.reason = f"could not count HEAD..{self.ref}"
            return
        self.behind = int(counts)

        porcelain = _git(root, "-c", "core.fsmonitor=false", "status", "--porcelain")
        # fsmonitor is force-disabled: a dead daemon makes `status` report a clean
        # tree while files are modified, which would silently understate drift.
        self.dirty = len(porcelain.splitlines()) if porcelain is not None else None

    @property
    def known(self) -> bool:
        return self.reason is None and self.behind is not None

    @property
    def drifted(self) -> bool:
        return self.known and (self.behind or 0) > 0


def scan(skill_dirs: "list[Path]") -> dict:
    repos: "dict[str, RepoState]" = {}
    # repo key -> skill names; and the names this could not evaluate at all
    by_repo: "dict[str, list[str]]" = {}
    unknown: "list[tuple[str, str]]" = []
    no_git: "list[str]" = []
    scanned = 0

    # One skill reached through two roots (~/.claude/skills/x -> ~/.agents/skills/x)
    # is ONE skill. Keying on the resolved path is what stops the same directory
    # being counted, and reported, twice.
    seen: "dict[str, str]" = {}

    for base in skill_dirs:
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if entry.name.startswith("."):
                continue
            try:
                real = entry.resolve(strict=True)
            except (OSError, RuntimeError):
                # A dangling symlink still prints a path under readlink, so
                # resolve(strict=True) is the only reliable tell it is dead.
                unknown.append((f"{base.name}/{entry.name}", "unresolvable path"))
                continue
            if not real.is_dir():
                continue          # CLAUDE.md and friends are not skills
            rp = str(real)
            if rp in seen:
                continue
            seen[rp] = entry.name
            scanned += 1
            top = _toplevel(real)
            if top is None:
                # An installed copy rather than a live link into a checkout.
                # Expected for third-party skills, and P7 already reports
                # first-party real-dir copies, so this is a count, not a list.
                no_git.append(entry.name)
                continue
            key = str(top)
            if key not in repos:
                repos[key] = RepoState(top)
            by_repo.setdefault(key, []).append(entry.name)

    return {"repos": repos, "by_repo": by_repo, "unknown": unknown,
            "no_git": no_git, "scanned": scanned}


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skills-dir", action="append", default=[],
                    help="a directory of installed skills; repeatable")
    ap.add_argument("--home", default=os.path.expanduser("~"))
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    dirs = [Path(d) for d in args.skills_dir] or [
        Path(args.home) / ".claude" / "skills",
        Path(args.home) / ".agents" / "skills",
    ]
    r = scan(dirs)
    repos: "dict[str, RepoState]" = r["repos"]

    drifted = {k: v for k, v in repos.items() if v.drifted}
    unknown_repos = {k: v for k, v in repos.items() if not v.known}

    if args.json:
        print(json.dumps({
            "scanned": r["scanned"],
            "drifted": [
                {"repo": k, "branch": v.branch, "head": v.head, "behind": v.behind,
                 "dirty": v.dirty, "ref": v.ref, "skills": r["by_repo"][k]}
                for k, v in drifted.items()
            ],
            "unknown_repos": [
                {"repo": k, "reason": v.reason, "skills": r["by_repo"][k]}
                for k, v in unknown_repos.items()
            ],
            "unknown_skills": [{"skill": n, "reason": why} for n, why in r["unknown"]],
            "no_git": r["no_git"],
        }, indent=2))
        return 0

    tracked = r["scanned"] - len(r["no_git"])
    if not drifted and not unknown_repos and not r["unknown"]:
        print(f"  [ok] {tracked} git-tracked skill(s) are current with origin/main")
        if r["no_git"]:
            print(f"        {len(r['no_git'])} installed copy/copies carry no git "
                  f"provenance — drift not evaluable (see P7 skill-source check)")
        return 0

    for key, st in sorted(drifted.items(), key=lambda kv: -len(r["by_repo"][kv[0]])):
        names = r["by_repo"][key]
        dirty = f", {st.dirty} uncommitted" if st.dirty else ""
        print(f"  [info] {len(names)} skill(s) run from {key}")
        print(f"         {st.branch} @ {st.head} — {st.behind} commit(s) behind "
              f"{st.ref}{dirty}")
        shown = ", ".join(names[:6]) + (f", +{len(names) - 6} more" if len(names) > 6 else "")
        print(f"         {shown}")
        print("         → merged changes to those commits are NOT what runs here")

    for key, st in sorted(unknown_repos.items()):
        print(f"  [info] {len(r['by_repo'][key])} skill(s) at {key}: "
              f"drift UNKNOWN — {st.reason}")

    if r["unknown"]:
        print(f"  [info] {len(r['unknown'])} skill dir(s) could not be resolved:")
        for name, why in r["unknown"][:5]:
            print(f"         {name} — {why}")
        if len(r["unknown"]) > 5:
            print(f"         +{len(r['unknown']) - 5} more")

    if r["no_git"]:
        print(f"  [info] {len(r['no_git'])} installed copy/copies carry no git "
              f"provenance — drift not evaluable")

    return 0


if __name__ == "__main__":
    sys.exit(main())
