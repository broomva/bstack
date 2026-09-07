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
import time
from pathlib import Path

# Beyond this, origin/main is too old for "matches origin/main" to mean anything.
DEFAULT_STALE_DAYS = 30.0


def _git(repo: Path, *args: str, raw: bool = False) -> "str | None":
    """Run git in `repo`, returning stdout or None on any failure.

    `raw=True` skips the strip(), and every -z call must use it: a path may
    legitimately begin or end with a space, and stripping the whole stdout would
    silently rewrite the first and last records.
    """
    try:
        p = subprocess.run(
            ("git", "-C", str(repo), *args),
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    return p.stdout if raw else p.stdout.strip()


def _z(out: "str | None") -> "list[str]":
    """Split NUL-delimited git output, dropping the trailing empty record."""
    if not out:
        return []
    return [r for r in out.split("\0") if r]


def _ref_age_days(root: Path) -> "float | None":
    """Days since this repo last FETCHED, from FETCH_HEAD's mtime. No network.

    FETCH_HEAD only, deliberately. An earlier version also accepted `packed-refs`
    and the loose `refs/remotes/origin/main`, which is wrong in the dangerous
    direction: `git gc` runs `pack-refs`, which REWRITES packed-refs without any
    fetch having happened. Measured — a clone whose refs were aged 60 days, then
    `git gc`:

        before gc:  packed-refs  60d
        after  gc:  packed-refs   0d      (no fetch occurred)

    so a repo that had not fetched in two months reported as fetched today, and
    the staleness gate this function exists to feed silently reopened. Only
    FETCH_HEAD's mtime means "a fetch happened here".

    Returns None when FETCH_HEAD is absent — a clone that has never fetched. That
    is genuinely unknowable, not fresh: its origin/main is frozen at clone time
    and nothing on disk says whether that was an hour or a year ago.
    """
    gd = _git(root, "rev-parse", "--absolute-git-dir")
    if not gd:
        return None
    try:
        return max(0.0, (time.time() - (Path(gd) / "FETCH_HEAD").stat().st_mtime)
                   / 86400.0)
    except OSError:
        return None


def _toplevel(path: Path, known: "list[Path]") -> "Path | None":
    """Repo root for `path`, reusing already-discovered roots where possible.

    Spawning `git rev-parse` per skill dominated the runtime — 523 of them on
    the real roots. Skills cluster into a handful of repos, so a path already
    under a known root is attributed by walking up and looking for a nearer
    `.git`: filesystem stats instead of a subprocess. A nested repo still wins,
    because the walk finds its `.git` before reaching the outer root.
    """
    for root in known:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        cur = path
        while cur != root:
            if (cur / ".git").exists():
                break            # a nearer repo — fall through to git
            cur = cur.parent
        else:
            return root

    # Most installed skills are plain directories with no repo anywhere above
    # them (408 of 523 here). Spawning git only to be told so was the bulk of the
    # runtime, and the question is answerable with stats: no `.git` between the
    # path and the filesystem root means no repo, no subprocess.
    cur = path
    while True:
        if (cur / ".git").exists():
            break
        if cur.parent == cur:
            return None
        cur = cur.parent

    out = _git(path, "rev-parse", "--show-toplevel")
    return Path(out) if out else None


class RepoState:
    """Per-repo facts, computed once and shared by every skill resolving into it.

    The comparison is `git diff --name-only origin/main` — the WORKING TREE
    against the upstream ref. Commit topology was the first design and it was
    wrong in both directions: it called a checkout current while its files were
    modified on disk, and it called every skill in a repo drifted because one
    commit touched README.md. What runs is the working tree, so that is what is
    compared, and one diff per repo answers it for every skill in that repo.
    """

    __slots__ = ("root", "branch", "head", "ref", "changed", "opaque", "reason",
                 "ref_age")

    def __init__(self, root: Path, stale_days: float = DEFAULT_STALE_DAYS):
        self.root = root
        self.reason: "str | None" = None
        self.ref_age: "float | None" = None
        self.branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD") or "?"
        self.head = _git(root, "rev-parse", "--short", "HEAD") or "?"
        self.changed: "set[str] | None" = None
        self.opaque: "set[str]" = set()

        # origin/main only. A fallback to origin/master could compare against a
        # stale ref and then report "current with origin/main" — inventing a
        # clean answer out of a missing one, which is the failure this check
        # exists to catch.
        self.ref = "origin/main"
        if not _git(root, "rev-parse", "--verify", "--quiet",
                    "refs/remotes/origin/main"):
            self.reason = "no origin/main ref (never fetched, or no remote)"
            return

        # A ref that exists but was never refreshed is not a comparison, it is a
        # comparison against a fiction — and the module's own rule is that what
        # cannot be verified is never reported as current. Measured on this
        # machine: a clone with no FETCH_HEAD whose packed-refs was last written
        # 61 days earlier had an origin/main 180 commits behind upstream, and its
        # 23 skills were being counted as matching origin/main. Dated from
        # mtimes; still no network.
        self.ref_age = _ref_age_days(root)
        if self.ref_age is None:
            # REACHABLE, and common: `git clone` never writes FETCH_HEAD, so any
            # clone that has not since fetched lands here. Its origin/main is a
            # snapshot from clone time and nothing on disk dates it, so it is
            # UNKNOWN rather than current — which is also the honest answer for
            # the real case that motivated this: 23 skills in a clone with no
            # FETCH_HEAD whose origin/main was 180 commits behind upstream.
            self.reason = "cannot date origin/main (no FETCH_HEAD — never fetched since clone)"
            return
        if self.ref_age > stale_days:
            self.reason = (f"origin/main last fetched {self.ref_age:.0f}d ago "
                           f"(> {stale_days:.0f}d) — too stale to compare")
            return

        # fsmonitor force-disabled: a dead daemon makes git report a clean tree
        # while files are modified, which would understate drift silently.
        # --no-renames is load-bearing, not tidiness. git detects renames by
        # default (diff.renames=true), and for a rename `--name-only` prints only
        # the DESTINATION. Move skills/alpha/SKILL.md to README.md and the diff
        # says "README.md", nothing under skills/alpha/ — so drifted_paths()
        # finds nothing and the skill whose only file just left reports as
        # matching origin/main. That is not merely an UNKNOWN gone wrong; it is a
        # positive clean verdict on a drifted skill, the exact failure this
        # module exists to prevent. --no-renames lists both sides.
        # -z is not cosmetic. WITHOUT it git renders any path containing a byte
        # >= 0x80, a quote, a backslash or a control char as a C-quoted string
        # wrapped in literal double quotes:
        #     "skills/alpha/NARI\303\221O.txt"
        # _under()'s startswith(rel + "/") then never matches, the path is
        # dropped, and the skill reports as matching origin/main. That is a
        # positive clean verdict on a diverging skill — the failure this module
        # exists to prevent — and it is LIVE on this machine: the tracked file
        # skills/knowledge/colombia-conflict/.../CEV_TERRITORIAL_NARINO_*.txt.gz
        # carries an N-tilde. -z emits raw bytes and no quoting.
        out = _git(root, "-c", "core.fsmonitor=false",
                   "diff", "-z", "--no-renames", "--name-only", self.ref, raw=True)
        if out is None:
            self.reason = f"could not diff working tree against {self.ref}"
            return
        untracked = _git(root, "-c", "core.fsmonitor=false", "ls-files",
                         "--others", "--exclude-standard", "-z", raw=True)
        if untracked is None:
            self.reason = "could not list untracked files"
            return
        self.changed = set(_z(out)) | set(_z(untracked))

        # `assume-unchanged` and `skip-worktree` exist to make a modified file
        # invisible to git — so `diff` reports nothing while the file on disk
        # differs, and the skill runs code no comparison can see. Verified: a
        # file edited under assume-unchanged read as "matches origin/main".
        # These paths are not clean and not drifted; they are UNVERIFIABLE, and
        # the rule is that what cannot be verified is never reported as current.
        # -z here too: an assume-unchanged path with a non-ASCII byte is quoted
        # exactly the same way, so the UNVERIFIABLE detection had the identical
        # blind spot. The <tag><space><path> record shape is unchanged by -z.
        flags = _git(root, "ls-files", "-v", "-z", raw=True)
        self.opaque: "set[str]" = set()
        if flags is None:
            self.reason = "could not read index flags (ls-files -v)"
            return
        for line in _z(flags):
            if len(line) < 3 or line[1] != " ":
                continue
            tag, path_ = line[0], line[2:]
            # lowercase => assume-unchanged; S => skip-worktree
            if tag.islower() or tag == "S":
                self.opaque.add(path_)

    @property
    def known(self) -> bool:
        return self.reason is None and self.changed is not None

    def _under(self, paths, rel: str) -> "list[str]":
        pre = "" if rel in ("", ".") else rel.rstrip("/") + "/"
        return sorted(c for c in paths if c.startswith(pre))

    def drifted_paths(self, rel: str) -> "list[str]":
        """Changed paths inside `rel` (a repo-relative skill directory)."""
        if not self.known:
            return []
        return self._under(self.changed or (), rel)

    def opaque_paths(self, rel: str) -> "list[str]":
        """Paths inside `rel` git has been told not to look at."""
        if not self.known:
            return []
        return self._under(self.opaque, rel)


def scan(skill_dirs: "list[Path]", stale_days: float = DEFAULT_STALE_DAYS) -> dict:
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
                # A plain FILE in the skills root (CLAUDE.md and friends) is not
                # a skill and is silently skipped, correctly. A SYMLINK that
                # resolves to a file is different: it has the shape of an
                # installed skill and is the one entry this loop dropped with no
                # counter and no line, so a root containing only that printed
                # "0 git-tracked skill(s) match origin/main" — a clean-sounding
                # verdict over nothing.
                if entry.is_symlink():
                    unknown.append((f"{base.name}/{entry.name}",
                                    "resolves to a file, not a skill directory"))
                continue
            rp = str(real)
            if rp in seen:
                continue
            seen[rp] = entry.name
            scanned += 1
            top = _toplevel(real, [r.root for r in repos.values()])
            if top is None:
                # An installed copy rather than a live link into a checkout.
                # Expected for third-party skills, and P7 already reports
                # first-party real-dir copies, so this is a count, not a list.
                no_git.append(entry.name)
                continue
            key = str(top)
            if key not in repos:
                repos[key] = RepoState(top, stale_days)
            try:
                rel = str(real.relative_to(top))
            except ValueError:
                rel = ""
            by_repo.setdefault(key, []).append((entry.name, rel))

    return {"repos": repos, "by_repo": by_repo, "unknown": unknown,
            "no_git": no_git, "scanned": scanned}


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skills-dir", action="append", default=[],
                    help="a directory of installed skills; repeatable")
    ap.add_argument("--home", default=os.path.expanduser("~"))
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--stale-days", type=float, default=DEFAULT_STALE_DAYS,
                    help="origin/main older than this many days is UNKNOWN, not "
                         "a basis for comparison (default: %(default)s)")
    args = ap.parse_args(argv)

    dirs = [Path(d) for d in args.skills_dir] or [
        Path(args.home) / ".claude" / "skills",
        Path(args.home) / ".agents" / "skills",
    ]
    r = scan(dirs, args.stale_days)
    repos: "dict[str, RepoState]" = r["repos"]

    # Per skill, not per repo: a commit touching only README.md changes nothing
    # a skill executes, and reporting every skill in the repo as drifted would
    # make the advisory noise and get it ignored.
    drifted: "dict[str, list[tuple[str, int, int]]]" = {}
    clean = 0
    for key, entries in r["by_repo"].items():
        st = repos[key]
        if not st.known:
            continue
        hits = [(name, len(st.drifted_paths(rel)), len(st.opaque_paths(rel)))
                for name, rel in entries]
        d = [(n, c, o) for n, c, o in hits if c or o]
        if d:
            drifted[key] = d
        clean += len(hits) - len(d)

    unknown_repos = {k: v for k, v in repos.items() if not v.known}

    if args.json:
        print(json.dumps({
            "scanned": r["scanned"],
            "drifted": [
                {"repo": k, "branch": repos[k].branch, "head": repos[k].head,
                 "ref": repos[k].ref, "ref_age_days": repos[k].ref_age,
                 "skills": [{"skill": n, "changed_files": c, "opaque_files": o}
                            for n, c, o in v]}
                for k, v in drifted.items()
            ],
            "unknown_repos": [
                {"repo": k, "reason": v.reason,
                 "skills": [n for n, _ in r["by_repo"][k]]}
                for k, v in unknown_repos.items()
            ],
            "unknown_skills": [{"skill": n, "reason": why} for n, why in r["unknown"]],
            "no_git": r["no_git"],
            "clean": clean,
        }, indent=2))
        return 0

    if not drifted and not unknown_repos and not r["unknown"]:
        # [info], not [ok]: in doctor's output `ok()` both prints [ok] AND
        # increments PASSES. This line is printed by python and counted by
        # nothing, so [ok] would render a check that does not exist — and
        # counting [ok] lines is a real way people diff two doctor runs. Every
        # advisory section this one follows (§4b, §4c, §12) and §28 beside it
        # use [info] only.
        ages = [st.ref_age for st in repos.values()
                if st.known and st.ref_age is not None]
        qual = f" (refs fetched <= {max(ages):.0f}d ago)" if ages else ""
        print(f"  [info] {clean} git-tracked skill(s) match origin/main{qual}")
        if r["no_git"]:
            print(f"        {len(r['no_git'])} installed copy/copies carry no git "
                  f"provenance — drift not evaluable (see P7 skill-source check)")
        return 0

    for key, v in sorted(drifted.items(), key=lambda kv: -len(kv[1])):
        st = repos[key]
        age = f", fetched {st.ref_age:.0f}d ago" if st.ref_age is not None else ""
        print(f"  [info] {len(v)} skill(s) differ from {st.ref} in {key}")
        print(f"         {st.branch} @ {st.head}{age}")
        for name, cnt, opq in v[:6]:
            if cnt:
                print(f"           {name} — {cnt} file(s) differ from {st.ref}")
            if opq:
                print(f"           {name} — {opq} file(s) hidden by "
                      f"assume-unchanged/skip-worktree: UNVERIFIABLE")
        if len(v) > 6:
            print(f"           +{len(v) - 6} more")
        if any(c for _, c, _ in v):
            print("         → what runs here is NOT what merged")
        if any(o for _, _, o in v):
            print("         → hidden paths cannot be compared at all — "
                  "`git update-index --no-assume-unchanged` to make them visible")

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
