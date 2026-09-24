#!/usr/bin/env python3
"""plan_drift.py — does the merged diff still match the committed plan? (BRO-2542)

Source: the AI-native SDLC playbook, plan-mode lesson
(https://academy.claude.com/courses/ai-native-sdlc-playbook/plan-mode), verbatim:

    "Commit the approved plan as plan.md. The plan joins the audit trail, and the
    PR review play (Stage 5: Deploy) checks the eventual diff against it."

    "When implementation departs from the plan, update plan.md in the same commit.
    Consider using a hook to enforce synchronization between the two."

    Lagging indicator: "how often the merged diff still matches the committed plan.md."

This is that check. It reads the plan's `## Files that change` section, diffs
`base...HEAD`, and reports three things a reviewer would otherwise eyeball:

    unplanned        changed files that no plan entry covers
    untouched        literal plan entries the diff never changed
    sync_violations  commits that touched an unplanned file without touching the
                     plan in that same commit (the course's same-commit rule)

INVARIANT: a status of `match` means every changed file (minus the plan itself and
the ignore globs) is covered by a plan entry, every literal entry was changed, and
no commit in range departed from the plan without updating it. Anything less is
`drift`. The check is advisory (exit 0) unless --strict.

Plan discovery, first hit wins:
    1. --plan PATH (repo-relative, or absolute)
    2. the single file changed in range matching --plans-glob
       (default: **/plan.md and docs/plans/*.md). Several candidates is
       `ambiguous_plan` — unless a `Plan: <path>` line in --pr-body names one of them.
    3. a `Plan: <path>` line in --pr-body FILE
    4. nothing -> `no_plan`, exit 0 (even under --strict: no plan is not drift)

Parsing rules for `## Files that change` (level 2 or 3 heading, case-insensitive,
up to the next heading outside a code fence):
    - a line with backticked spans contributes ONLY its backticked tokens, so a
      bullet's prose description never becomes an entry;
    - a line without backticks contributes its comma/whitespace-separated tokens
      that look like paths (contain `/`, `.`, or a glob character). A bare word
      such as `Makefile` is prose unless backticked.
    - globs: `*` and `?` stay inside one path segment, `**` crosses segments
      (`**/x` also matches `x`), a trailing `/` means everything under it.
    - `untouched` is reported for literal entries only; a glob promises no file.

Same-commit rule, precisely: each non-merge commit in range is judged against the
plan AS OF THAT COMMIT (its own tree), falling back to the final plan when the plan
did not exist yet. Judging every commit against the final plan would let a later
fix-up commit launder an earlier departure — which is the exact failure the rule
exists to catch.

Output: human report on stdout; `--json` prints JSON instead; `--json PATH` writes
JSON to PATH and still prints the human report (for a CI artifact + step summary).

Exit codes:
    0  advisory result (match, drift, no_plan, ambiguous_plan)
    1  --strict and the status is drift or ambiguous_plan
    2  usage or input error: not a git repo, base unresolvable, plan unreadable,
       --json PATH unwritable
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_PLANS_GLOBS = ("**/plan.md", "docs/plans/*.md")
DEFAULT_IGNORE = ("CHANGELOG.md",)
BASE_FALLBACKS = ("origin/HEAD", "origin/main")

# Only these reach a git child process. Everything else in the operator's
# environment (tokens, API keys) is none of git's business, and an agent-run
# git with a hostile config is a known exfiltration path.
_ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
# Applied to every invocation: fsmonitor can report a stale tree, and external
# diff / textconv drivers execute repo-configured programs.
_GIT_PREFIX = ("git", "-c", "core.fsmonitor=false")
_DIFF_SAFETY = ("--no-ext-diff", "--no-textconv")

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_SECTION_TITLE_RE = re.compile(r"^files\s+that\s+change\b", re.I)
_FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_TICK_RE = re.compile(r"`([^`]+)`")
_PR_PLAN_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:\*\*|__)?Plan(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*`?([^`\s]+)`?",
    re.M,
)
_GLOB_CHARS = "*?["
_PROSE_TOKENS = {"e.g", "i.e", "etc", "vs"}


class DriftError(Exception):
    """An input problem the caller must fix; maps to exit 2."""


# --- globbing ---------------------------------------------------------------

def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a path glob to an anchored regex.

    fnmatch is not used because its `*` crosses `/`, which would make
    `src/*.py` silently cover `src/deep/nested.py` and hide real drift.
    """
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern.startswith("**/", i):
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pattern.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
            else:
                body = pattern[i + 1:j]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = j + 1
                continue
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def is_glob(entry: str) -> bool:
    return any(ch in entry for ch in _GLOB_CHARS)


def matches(pattern: str, path: str) -> bool:
    return bool(glob_to_regex(pattern).match(path))


def _as_pattern(entry: str) -> str:
    """A trailing slash names a directory: everything under it."""
    return entry + "**" if entry.endswith("/") else entry


def is_literal(entry: str) -> bool:
    return not is_glob(entry) and not entry.endswith("/")


# --- plan parsing -----------------------------------------------------------

def _clean(token: str) -> str:
    t = token.strip().strip("\"'")
    t = t.lstrip("(<")
    t = t.rstrip(",;:)>")
    if len(t) > 1 and t.endswith(".") and not t.endswith(".."):
        t = t[:-1]
    if t.startswith("./"):
        t = t[2:]
    return t


def _pathlike(token: str) -> bool:
    if not token or "://" in token or token.lower() in _PROSE_TOKENS:
        return False
    if not any(ch.isalnum() for ch in token):
        return False
    return "/" in token or "." in token or is_glob(token)


def _entries_from_line(line: str) -> list[str]:
    body = _BULLET_RE.sub("", line, count=1)
    spans = _TICK_RE.findall(body)
    if spans:
        tokens = [_clean(t) for s in spans for t in re.split(r"[,\s]+", s)]
        return [t for t in tokens if t]
    tokens = [_clean(t) for t in re.split(r"[,\s]+", body)]
    return [t for t in tokens if _pathlike(t)]


def parse_plan(text: str) -> tuple[bool, list[str]]:
    """Return (section_found, entries) for the `Files that change` section."""
    entries: list[str] = []
    found = in_section = in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            h = _HEADING_RE.match(line)
            if h:
                level = len(h.group(1))
                if in_section:
                    in_section = False
                if level in (2, 3) and _SECTION_TITLE_RE.match(h.group(2)) and not found:
                    found = in_section = True
                continue
        if in_section:
            entries.extend(_entries_from_line(line))
    seen: set[str] = set()
    unique = [e for e in entries if not (e in seen or seen.add(e))]
    return found, unique


def plan_ref_from_pr_body(text: str) -> str | None:
    for m in _PR_PLAN_RE.finditer(text):
        cand = _clean(m.group(1))
        if _pathlike(cand):
            return cand
    return None


# --- git --------------------------------------------------------------------

def git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k in _ENV_KEEP or k.startswith("GIT_")}


def _git(root: Path | str, *args: str, check: bool = True) -> tuple[int, str]:
    p = subprocess.run(
        [*_GIT_PREFIX, "-C", str(root), *args],
        capture_output=True, env=git_env(), stdin=subprocess.DEVNULL,
    )
    out = p.stdout.decode("utf-8", "surrogateescape")
    if check and p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip()
        raise DriftError(f"git {' '.join(args)} failed: {err or 'exit ' + str(p.returncode)}")
    return p.returncode, out


def _z(out: str) -> list[str]:
    return [x for x in out.split("\0") if x]


def repo_root(start: Path) -> Path:
    try:
        _, out = _git(start, "rev-parse", "--show-toplevel")
    except DriftError as e:
        raise DriftError(f"{start}: not a git repository ({e})")
    return Path(out.strip())


def _rev(root: Path, ref: str) -> str | None:
    rc, out = _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    return out.strip() if rc == 0 and out.strip() else None


def resolve_base(root: Path, base: str | None) -> tuple[str, str]:
    """(ref label, merge-base sha). `base...HEAD` is `merge-base(base, HEAD)..HEAD`."""
    refs = (base,) if base else BASE_FALLBACKS
    for ref in refs:
        if _rev(root, ref) is None:
            continue
        rc, out = _git(root, "merge-base", ref, "HEAD", check=False)
        if rc != 0 or not out.strip():
            raise DriftError(f"{ref} and HEAD share no history")
        return ref, out.strip()
    if base:
        raise DriftError(f"--base {base!r} does not resolve to a commit")
    raise DriftError("no base: origin/HEAD and origin/main do not resolve; pass --base REF")


def changed_files(root: Path, mb: str) -> list[str]:
    _, out = _git(root, "diff", "--name-only", "--no-renames", *_DIFF_SAFETY, "-z", mb, "HEAD")
    return sorted(set(_z(out)))


def commits_in_range(root: Path, mb: str) -> list[tuple[str, str, list[str]]]:
    _, out = _git(root, "log", "--reverse", "--no-merges", "--format=%H%x00%s%x00", f"{mb}..HEAD")
    parts = out.split("\0")
    commits: list[tuple[str, str, list[str]]] = []
    for i in range(0, len(parts) - 1, 2):
        sha = parts[i].strip()
        if not sha:
            continue
        _, files = _git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root",
                        "--no-renames", *_DIFF_SAFETY, "-z", sha)
        commits.append((sha, parts[i + 1], sorted(set(_z(files)))))
    return commits


def plan_at(root: Path, sha: str, plan_rel: str) -> str | None:
    rc, out = _git(root, "cat-file", "blob", f"{sha}:{plan_rel}", check=False)
    return out if rc == 0 else None


# --- the check --------------------------------------------------------------

def _covered(path: str, entries: list[str]) -> bool:
    return any(matches(_as_pattern(e), path) for e in entries)


def _ignored(path: str, ignore: list[str]) -> bool:
    return any(matches(g, path) for g in ignore)


def _plan_rel(root: Path, plan: str) -> str | None:
    """Repo-relative path of the plan, or None when it lives outside the repo."""
    p = Path(plan)
    if not p.is_absolute():
        return Path(os.path.normpath(plan)).as_posix()
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def analyze(repo: Path, base: str | None = None, plan: str | None = None,
            plans_globs: list[str] | None = None, pr_body: str | None = None,
            ignore: list[str] | None = None) -> dict:
    root = repo_root(repo)
    plans_globs = list(plans_globs) if plans_globs else list(DEFAULT_PLANS_GLOBS)
    ignore = list(DEFAULT_IGNORE) if ignore is None else list(ignore)
    ref, mb = resolve_base(root, base)
    raw_changed = changed_files(root, mb)

    report: dict = {
        "tool": "plan_drift",
        "status": None,
        "plan": None,
        "plan_source": None,
        "candidates": [],
        "base": {"ref": ref, "sha": mb},
        "changed": raw_changed,
    }

    pr_ref = plan_ref_from_pr_body(pr_body) if pr_body else None
    if plan:
        chosen, source = plan, "flag"
    else:
        cands = sorted(f for f in raw_changed
                       if any(matches(g, f) for g in plans_globs) and (root / f).is_file())
        report["candidates"] = cands
        if len(cands) == 1:
            chosen, source = cands[0], "changed-in-range"
        elif len(cands) > 1 and pr_ref in cands:
            chosen, source = pr_ref, "pr-body"
        elif len(cands) > 1:
            report["status"] = "ambiguous_plan"
            return report
        elif pr_ref:
            chosen, source = pr_ref, "pr-body"
        else:
            report["status"] = "no_plan"
            return report

    plan_path = Path(chosen) if Path(chosen).is_absolute() else root / chosen
    try:
        plan_text = plan_path.read_text(encoding="utf-8")
    except OSError as e:
        raise DriftError(f"plan {chosen!r} ({source}) is unreadable: {e.strerror or e}")
    rel = _plan_rel(root, chosen)
    report["plan"] = rel or str(plan_path)
    report["plan_source"] = source

    found, entries = parse_plan(plan_text)
    entries = [e for e in entries if e != rel]
    considered = [f for f in raw_changed if f != rel and not _ignored(f, ignore)]
    matched = [f for f in considered if _covered(f, entries)]
    unplanned = [f for f in considered if f not in matched]
    changed_set = set(raw_changed)
    untouched = [e for e in entries if is_literal(e) and e not in changed_set]

    violations = []
    for sha, subject, files in commits_in_range(root, mb):
        at = plan_at(root, sha, rel) if rel else None
        commit_entries = [e for e in parse_plan(at)[1] if e != rel] if at is not None else entries
        departs = [f for f in files
                   if f != rel and not _ignored(f, ignore) and not _covered(f, commit_entries)]
        if departs and (rel is None or rel not in files):
            violations.append({
                "sha": sha,
                "subject": subject,
                "unplanned": departs,
                "plan_at_commit": "present" if at is not None else "absent",
            })

    reasons = []
    if not found:
        reasons.append("plan has no '## Files that change' section")
    if unplanned:
        reasons.append(f"{len(unplanned)} unplanned file(s)")
    if untouched:
        reasons.append(f"{len(untouched)} untouched plan entr{'y' if len(untouched) == 1 else 'ies'}")
    if violations:
        reasons.append(f"{len(violations)} commit(s) departed from the plan without updating it")

    report.update({
        "status": "drift" if reasons else "match",
        "section_found": found,
        "entries": entries,
        "ignore": ignore,
        "ignored": [f for f in raw_changed if f != rel and _ignored(f, ignore)],
        "matched": matched,
        "unplanned": unplanned,
        "untouched": untouched,
        "sync_violations": violations,
        "match_ratio": round(len(matched) / len(considered), 4) if considered else None,
        "drift_reasons": reasons,
    })
    return report


def render(report: dict) -> str:
    status = report["status"]
    base = report["base"]
    head = f"plan-drift: {status} (base {base['ref']} @ {base['sha'][:9]})"
    lines = [head]
    if status == "no_plan":
        lines.append("  no plan found: pass --plan, change a plan file in range, or add "
                     "'Plan: <path>' to the PR body")
        return "\n".join(lines)
    if status == "ambiguous_plan":
        lines.append("  several plan files changed in range; pass --plan or name one with "
                     "'Plan: <path>' in the PR body:")
        lines.extend(f"    CANDIDATE  {c}" for c in report["candidates"])
        return "\n".join(lines)
    lines.append(f"  plan: {report['plan']} (via {report['plan_source']})")
    considered = len(report["matched"]) + len(report["unplanned"])
    ratio = report["match_ratio"]
    pct = "n/a" if ratio is None else f"{ratio:.0%}"
    lines.append(f"  match_ratio: {len(report['matched'])}/{considered} ({pct})")
    if not report["section_found"]:
        lines.append("  WARNING    plan has no '## Files that change' section (level 2 or 3)")
    lines.extend(f"  UNPLANNED  {f}" for f in report["unplanned"])
    lines.extend(f"  UNTOUCHED  {e}" for e in report["untouched"])
    for v in report["sync_violations"]:
        lines.append(f"  SYNC       {v['sha'][:9]} {v['subject']!r} touched "
                     f"{', '.join(v['unplanned'])} without updating {report['plan']}")
    return "\n".join(lines)


def emit(report: dict, json_dest: str | None) -> None:
    """Bare --json: JSON on stdout. --json PATH: JSON to the file, human report on
    stdout — the shape a CI step wants (artifact + step summary from one run)."""
    if json_dest == "-":
        print(json.dumps(report, indent=2))
        return
    if json_dest:
        Path(json_dest).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(render(report))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="bstack plan-drift", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--repo", type=Path, default=Path.cwd(), help="repository (default: cwd)")
    ap.add_argument("--base", help="base ref (default: merge-base with origin/HEAD, else origin/main)")
    ap.add_argument("--plan", help="plan file, repo-relative or absolute (skips discovery)")
    ap.add_argument("--plans-glob", action="append", dest="plans_globs", metavar="GLOB",
                    help=f"plan discovery glob, repeatable (default: {', '.join(DEFAULT_PLANS_GLOBS)})")
    ap.add_argument("--pr-body", type=Path, metavar="FILE",
                    help="PR description; a 'Plan: <path>' line names the plan")
    ap.add_argument("--ignore", action="append", metavar="GLOB",
                    help=f"changed paths never counted as drift, repeatable (default: {', '.join(DEFAULT_IGNORE)})")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 on drift or an ambiguous plan (default: advisory, exit 0)")
    ap.add_argument("--json", nargs="?", const="-", metavar="PATH",
                    help="machine-readable report: bare --json prints it to stdout; "
                         "--json PATH writes it there and keeps the human report on stdout")
    args = ap.parse_args(argv)

    try:
        pr_body = None
        if args.pr_body is not None:
            try:
                pr_body = args.pr_body.read_text(encoding="utf-8")
            except OSError as e:
                raise DriftError(f"--pr-body {args.pr_body}: unreadable: {e.strerror or e}")
        report = analyze(args.repo, base=args.base, plan=args.plan,
                         plans_globs=args.plans_globs, pr_body=pr_body, ignore=args.ignore)
    except DriftError as e:
        print(f"plan-drift: error: {e}", file=sys.stderr)
        return 2

    try:
        emit(report, args.json)
    except OSError as e:
        print(f"plan-drift: error: --json {args.json}: {e.strerror or e}", file=sys.stderr)
        return 2
    if args.strict and report["status"] in ("drift", "ambiguous_plan"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
