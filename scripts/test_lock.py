#!/usr/bin/env python3
"""test_lock.py — protect the feedback loop from the agent it constrains (BRO-2542).

Why this exists
---------------
The bug-fix loop in
https://academy.claude.com/courses/ai-native-sdlc-playbook/give-claude-a-feedback-loop
is: reproduce the bug as a test, run it, confirm it fails for the reason you
expect, commit that test — and only then make it pass WITHOUT editing the test.
Its stated rule: "an agent fixing code must not be able to weaken the check on
that code." A reproduction test the fixer may rewrite is not a check; the
cheapest route to green is always to change the assertion.

Mechanism: locks are git commit trailers
----------------------------------------
Not a side file (an agent could delete a side file). A trailer lives in history,
is content-addressed with the commit that carries it, and shows up in PR review.

    Test-Lock: <repo-relative path>      the commit that pins the test
    Test-Unlock: <repo-relative path>    a LATER commit that releases it

A release is a human decision, visible in review like any other trailer. A lock
on a directory covers everything under it; an unlock releases every lock at or
below its path.

Range: commits in <base>..HEAD, where base is `--base REF`, else env
BSTACK_TEST_LOCK_BASE, else merge-base(HEAD, origin/HEAD), else
merge-base(HEAD, origin/main), else (no remote) every commit reachable from
HEAD, capped at --max-commits (default 500). Commits are read oldest-first in
topological order, so "later" means later in that order. A lock whose commit
sits before the base is inactive: it belongs to history that already merged.

Invariant
---------
A lock exists so the final test is the original. `verify` exits 1 when a locked
path's content at the end of its lock differs from its content at the lock
commit — modified, deleted or renamed away. The end of a lock is HEAD while it
is active, the state just before its Test-Unlock commit once released (so a
release must precede the change, or ride on that same commit; a release added
after the fact does not launder it), or the re-lock commit when the path is
locked again. A path edited and later restored to the locked content is
reported as `touched_and_restored` with the commits involved — a warning, exit
0: intermediate commits are review material, not a violation.

Not guarded: history rewrites. An amend, rebase or reset that drops the lock
commit removes the lock with it; review sees the rewritten range, and nothing
here can.

Subcommands
-----------
    commit <path>... -m MSG   git add the paths, commit ONLY them with one
                              Test-Lock trailer per path (--allow-empty when
                              nothing changed: the lock pins the content as is)
    list                      active locks: path, lock sha, subject
    verify [--worktree]       the backstop: fails CLOSED on any breach;
                              --worktree also checks the index and worktree
    check-path <path>         exit 2 if an active lock covers the path
    hook                      Claude Code PreToolUse hook (JSON on stdin)

The hook FAILS OPEN. An internal error exits 0 with a one-line stderr warning:
a guard that crashed closed would block every edit in every repository, and
`verify` (run before merge) is the check that fails closed. Its Bash branch is
best effort, and it blocks on the TARGET of a write, not on the co-occurrence
of a locked path and a write construct: it resolves the targets of
redirections, tee, sed -i, perl -i, mv, cp, rm, truncate, dd of=, find -delete,
git checkout/restore/rm/mv (also inside `sh -c` and after `cd`). So
`pytest <locked> 2>&1 | tee log` and `cat <locked>` pass while
`sed -i ... <locked>` blocks. Restoring a locked file from HEAD or the index
passes — that is the recovery `verify --worktree` prescribes; restoring it from
any other source blocks. A command that does not tokenize falls back to the
co-occurrence rule. A git command that commits a Test-Unlock trailer is blocked
while a lock is active: the block message names the release route, and the
agent it constrains must not be the one to take it. A write the parser cannot
see (an interpreter one-liner, a patch file) is what `verify` exists for.

Git hygiene: every git call runs with `-c core.fsmonitor=false`, diff-like
calls with `--no-ext-diff --no-textconv`, logs with `--no-show-signature`, and
a filtered environment (PATH, HOME, LANG, LC_ALL, TMPDIR, GIT_*): a repository's
config can otherwise execute code, and a hook must not hand it the session's
secrets. Only `commit` adds SSH_AUTH_SOCK, GNUPGHOME and GPG_TTY, so a signed
commit still signs; hook, verify, list and check-path never pass them. The
`--worktree` comparison hashes file bytes in-process instead of asking git,
because git would run the repository's clean filters to do it — so a locked
path under LFS or autocrlf can differ falsely from its committed blob.

Exit codes
----------
    commit      0 committed · 1 git failed · 2 usage (missing path, not a repo)
    list        0 · 2 usage/environment (not a repo, bad --base)
    verify      0 clean · 1 violation · 2 usage/environment
    check-path  0 not locked · 2 locked · 1 internal error
    hook        0 allow (including every internal error) · 2 block
"""

from __future__ import annotations

import argparse
import fnmatch
import functools
import hashlib
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Callable, Sequence

LOCK_KEY = "Test-Lock"
UNLOCK_KEY = "Test-Unlock"
BASE_ENV = "BSTACK_TEST_LOCK_BASE"
DEFAULT_MAX_COMMITS = 500
GIT_TIMEOUT = 20
SHORT = 7

# Claude Code tools that write a file, and the tool_input key naming it.
FILE_TOOLS = {
    "Edit": ("file_path",),
    "Write": ("file_path",),
    "MultiEdit": ("file_path",),
    "NotebookEdit": ("notebook_path", "file_path"),
}

_ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
_SIGNING_ENV = ("SSH_AUTH_SOCK", "GNUPGHOME", "GPG_TTY")  # `commit` only
_LOG_FORMAT = (
    "%x1e%H%x1f%s%x1f"
    f"%(trailers:key={LOCK_KEY},valueonly,unfold)%x1f"
    f"%(trailers:key={UNLOCK_KEY},valueonly,unfold)%x1f"
)


class LockError(Exception):
    """A usage or environment failure: exit 2 from list/verify/commit."""


class GitError(LockError):
    pass


# --------------------------------------------------------------------------- #
# git plumbing
# --------------------------------------------------------------------------- #
def git_env(signing: bool = False) -> dict[str, str]:
    """The only environment a git subprocess sees. A repository's hooks and
    config-driven commands run inside it, so nothing else crosses over. The
    signing agent sockets cross only for the `commit` subcommand."""
    keep = _ENV_KEEP + (_SIGNING_ENV if signing else ())
    return {k: v for k, v in os.environ.items() if k in keep or k.startswith("GIT_")}


def _git(args: Sequence[str], cwd: str, *, stdin: bytes | None = None,
         literal: bool = False, signing: bool = False) -> subprocess.CompletedProcess:
    cmd = ["git", "-c", "core.fsmonitor=false", "--no-pager"]
    if literal:
        cmd.append("--literal-pathspecs")
    cmd.extend(args)
    kw: dict = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
    return subprocess.run(cmd, cwd=cwd, env=git_env(signing), capture_output=True,
                          timeout=GIT_TIMEOUT, **kw)


def _text(p: subprocess.CompletedProcess) -> str:
    return p.stdout.decode("utf-8", "surrogateescape")


def _git_ok(args: Sequence[str], cwd: str, **kw) -> str:
    p = _git(args, cwd, **kw)
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GitError(f"git {args[0]} failed: {err[-1] if err else 'exit %d' % p.returncode}")
    return _text(p)


def repo_root(start: str) -> str | None:
    """Top level of the work tree containing `start` (a file or directory that
    need not exist yet), or None outside a repository."""
    d = os.path.abspath(start)
    while not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    p = _git(["rev-parse", "--show-toplevel"], d)
    top = _text(p).strip()
    return top if p.returncode == 0 and top else None


def _commit_sha(root: str, ref: str) -> str | None:
    p = _git(["rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"], root)
    sha = _text(p).strip()
    return sha if p.returncode == 0 and sha else None


@functools.lru_cache(maxsize=None)
def _ignorecase(root: str) -> bool:
    p = _git(["config", "--bool", "--get", "core.ignorecase"], root)
    return _text(p).strip() == "true"


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #
def norm_lock_path(value: str) -> str | None:
    """A trailer value as a canonical repo-relative POSIX path, or None when it
    cannot name a path inside the repository (absolute, escaping, empty)."""
    v = value.strip()
    if not v or v.startswith("/") or "\0" in v:
        return None
    n = posixpath.normpath(v)
    if n in (".", "..") or n.startswith("../"):
        return None
    return n


def covers(lock: str, path: str) -> bool:
    """True when `path` is the locked path or lies under a locked directory."""
    return path == lock or path.startswith(lock + "/")


def _rel_to_root(full: str, root_real: str) -> str | None:
    rel = os.path.relpath(full, root_real)
    if rel == os.curdir:
        return ""
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return None
    return rel.replace(os.sep, "/")


def candidates(path: str, cwd: str, root: str) -> list[str]:
    """Every repo-relative reading of `path`.

    Absolute paths have one reading. A relative path is read against the cwd AND
    against the repository root, and a guard checks both: `tests/x.py` typed in a
    subdirectory is ambiguous, and the ambiguity must not be an exemption. Each
    reading is taken twice — with only the parent directory resolved (so a lock on
    a symlink still matches the symlink) and fully resolved (so an alias that
    points at a locked file matches the file). "" is the repository root.
    """
    root_real = os.path.realpath(root)
    bases = [""] if os.path.isabs(path) else [cwd, root]
    out: list[str] = []
    for b in bases:
        full = os.path.normpath(os.path.join(b, path) if b else path)
        parent_resolved = os.path.join(os.path.realpath(os.path.dirname(full)), os.path.basename(full))
        for form in (parent_resolved, os.path.realpath(full)):
            rel = _rel_to_root(form, root_real)
            if rel is not None and rel not in out:
                out.append(rel)
    return out


# --------------------------------------------------------------------------- #
# history
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Lock:
    path: str
    sha: str
    subject: str

    def as_json(self) -> dict:
        return {"path": self.path, "sha": self.sha, "short": self.sha[:SHORT], "subject": self.subject}


@dataclass
class Commit:
    sha: str
    subject: str
    locks: list[str] = field(default_factory=list)
    unlocks: list[str] = field(default_factory=list)
    changes: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)


@dataclass
class Scan:
    root: str
    base: str | None
    base_source: str
    truncated: bool
    commits: list[Commit]
    warnings: list[str]


def resolve_base(root: str, explicit: str | None) -> tuple[str | None, str]:
    """(base sha or None, where it came from). An explicit base that does not
    resolve is an error, never a silent fall-through to a wider range."""
    for label, ref in (("--base", explicit), (BASE_ENV, os.environ.get(BASE_ENV) or None)):
        if ref:
            sha = _commit_sha(root, ref)
            if sha is None:
                raise LockError(f"{label} {ref!r} does not name a commit")
            return sha, f"{label} {ref}"
    for ref in ("origin/HEAD", "origin/main"):
        p = _git(["merge-base", "HEAD", ref], root)
        sha = _text(p).strip()
        if p.returncode == 0 and sha:
            return sha, f"merge-base(HEAD, {ref})"
    return None, "none (no remote): every reachable commit"


def _split_values(raw: str) -> list[str]:
    return [line.strip() for line in raw.split("\n") if line.strip()]


def parse_log(raw: bytes) -> list[Commit]:
    """Parse `git log -z --format=_LOG_FORMAT [--name-status]` output."""
    commits: list[Commit] = []
    for rec in raw.decode("utf-8", "surrogateescape").split("\x1e")[1:]:
        parts = rec.split("\x1f", 4)
        if len(parts) < 5:
            continue
        sha, subject, locks_raw, unlocks_raw, rest = parts
        c = Commit(sha=sha.strip(), subject=subject,
                   locks=_split_values(locks_raw), unlocks=_split_values(unlocks_raw))
        toks = rest.split("\0")
        j = 0
        while j < len(toks):
            status = toks[j].strip("\n")
            if not status:
                j += 1
                continue
            n = 2 if status[0] in "RC" else 1
            paths = tuple(toks[j + 1:j + 1 + n])
            j += 1 + n
            if len(paths) == n:
                c.changes.append((status[0], paths))
        commits.append(c)
    return commits


def scan(root: str, base_arg: str | None = None, max_commits: int | None = None,
         with_changes: bool = False) -> Scan:
    base, source = resolve_base(root, base_arg)
    warnings: list[str] = []
    if _commit_sha(root, "HEAD") is None:  # unborn branch: no history, no locks
        return Scan(root, base, source, False, [], warnings)
    cap = max_commits if max_commits is not None else (None if base else DEFAULT_MAX_COMMITS)
    args = ["log", "-z", "--topo-order", "--reverse", "--no-color", "--no-show-signature",
            f"--format={_LOG_FORMAT}"]
    if cap:
        args.append(f"--max-count={cap}")
    if with_changes:
        args += ["-M", "--name-status", "--no-ext-diff", "--no-textconv"]
    args += [f"{base}..HEAD" if base else "HEAD", "--"]
    p = _git(args, root)
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GitError(f"git log failed: {err[-1] if err else p.returncode}")
    commits = parse_log(p.stdout)
    for c in commits:
        for key, values in ((LOCK_KEY, c.locks), (UNLOCK_KEY, c.unlocks)):
            good = []
            for v in values:
                n = norm_lock_path(v)
                if n is None:
                    warnings.append(f"{c.sha[:SHORT]}: ignored malformed '{key}: {v}' "
                                    "(needs a repo-relative path)")
                else:
                    good.append(n)
            values[:] = good
    truncated = bool(cap) and len(commits) >= cap
    if truncated:
        warnings.append(f"walk capped at {cap} commits: a lock older than that is not seen "
                        f"(pass --base or set {BASE_ENV})")
    return Scan(root, base, source, truncated, commits, warnings)


def _release(locked: dict[str, Lock], unlock: str) -> list[Lock]:
    freed = [lk for p, lk in locked.items() if covers(unlock, p)]
    for lk in freed:
        del locked[lk.path]
    return freed


def active_locks(commits: Sequence[Commit]) -> dict[str, Lock]:
    """Locks set in range and not released by a later in-range commit."""
    locked: dict[str, Lock] = {}
    for c in commits:
        for u in c.unlocks:
            _release(locked, u)
        for p in c.locks:
            locked[p] = Lock(p, c.sha, c.subject)
    return locked


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #
@dataclass
class Violation:
    sha: str
    path: str
    kind: str
    lock_path: str
    lock_sha: str
    detail: str
    release_route: str
    commits: list[str] = field(default_factory=list)

    def as_json(self) -> dict:
        d = asdict(self)
        d["short"] = self.sha[:SHORT] if len(self.sha) >= 40 else self.sha
        return d


def _committed_route(lock_path: str, lock_sha: str) -> str:
    return (f"restore {lock_path} to its content at {lock_sha[:SHORT]} in a new commit, or, if "
            f"the test itself had to change, a human puts 'Test-Unlock: {lock_path}' on or "
            f"before the change (a decision visible in review)")


def _describe(status: str, paths: tuple[str, ...], lock_path: str) -> tuple[str, str, str] | None:
    """(path, kind, detail) when this change touches the lock, else None."""
    if status in "RC" and len(paths) == 2:
        old, new = paths
        if status == "R" and covers(lock_path, old):
            return old, "renamed", f"{old} -> {new}"
        if covers(lock_path, new):
            return new, "overwritten", f"{old} -> {new} lands on the locked path"
        return None
    path = paths[0]
    if not covers(lock_path, path):
        return None
    kind = {"M": "modified", "D": "deleted", "A": "re-added", "T": "type-changed"}.get(status, "changed")
    return path, kind, kind


def _objects(root: str, specs: Sequence[str]) -> list[str | None]:
    """Object ids for `rev:path` specs; None where the path does not exist."""
    if not specs:
        return []
    out = _git_ok(["cat-file", "--batch-check=%(objectname)"], root,
                  stdin=("\n".join(specs) + "\n").encode("utf-8", "surrogateescape"))
    ids: list[str | None] = []
    for line in out.splitlines():
        ids.append(None if line.endswith(" missing") or " " in line.strip() else line.strip())
    return ids


@dataclass
class _Span:
    """One lock's lifetime in the walk, and every commit that touched it."""
    lock: Lock
    end: str = "HEAD"          # rev whose content is the lock's final state
    end_sha: str | None = None
    touches: list[tuple[str, str, str]] = field(default_factory=list)  # (sha, path, kind)


def committed_violations(s: Scan) -> tuple[dict[str, Lock], list[Violation], list[dict], list[dict]]:
    """(active locks, violations, releases, touched_and_restored).

    The verdict is the CONTENT at the end of each lock against the content at
    the lock commit. The per-commit walk only attributes: it names the commits
    that touched the path, which become the violation's evidence or, when the
    content came back, the `touched_and_restored` review note. A merge carries
    no change list, so a side-branch edit shows up as a difference with no
    touching commit (kind content-drift)."""
    locked: dict[str, Lock] = {}
    live: dict[str, _Span] = {}
    spans: list[_Span] = []
    releases: list[dict] = []
    for c in s.commits:
        for u in c.unlocks:
            for lk in _release(locked, u):
                sp = live.pop(lk.path)
                sp.end, sp.end_sha = f"{c.sha}^", c.sha  # the state just before the release
                releases.append({"path": lk.path, "lock_sha": lk.sha, "sha": c.sha,
                                 "short": c.sha[:SHORT], "subject": c.subject})
        for status, paths in c.changes:
            for sp in live.values():
                hit = _describe(status, paths, sp.lock.path)
                if hit:
                    sp.touches.append((c.sha, hit[0], hit[1]))
        for p in c.locks:
            if p in live:  # re-locked: the old lock ends at what the new one pins
                old = live.pop(p)
                old.end, old.end_sha = c.sha, c.sha
            locked[p] = Lock(p, c.sha, c.subject)
            live[p] = _Span(locked[p])
            spans.append(live[p])

    head = _commit_sha(s.root, "HEAD") or "HEAD"
    specs: list[str] = []
    for sp in spans:
        specs += [f"{sp.lock.sha}:{sp.lock.path}", f"{sp.end}:{sp.lock.path}"]
    ids = _objects(s.root, specs) if all("\n" not in x for x in specs) else []
    violations: list[Violation] = []
    restored: list[dict] = []
    for i, sp in enumerate(spans):
        if len(ids) < 2 * i + 2:
            break
        lk = sp.lock
        at_lock, at_end = ids[2 * i], ids[2 * i + 1]
        commits = list(dict.fromkeys(t[0] for t in sp.touches))
        if at_lock is None:
            s.warnings.append(f"{lk.sha[:SHORT]}: 'Test-Lock: {lk.path}' names a path absent "
                              "at its own commit — the lock pins nothing")
            continue
        if at_end != at_lock:
            kinds = [t[2] for t in sp.touches]
            if at_end is None:
                kind = "renamed" if "renamed" in kinds else "deleted"
            else:
                kind = "modified" if sp.touches else "content-drift"
            sha = commits[-1] if commits else (sp.end_sha or head)
            where = "HEAD" if sp.end == "HEAD" else f"the end of the lock ({sp.end_sha[:SHORT]})"
            detail = (f"{kind}: content at {where} differs from lock {lk.sha[:SHORT]}"
                      + (f"; touched by {', '.join(x[:SHORT] for x in commits)}" if commits
                         else " with no walked commit changing it (a merge brought the change in)"))
            violations.append(Violation(sha, lk.path, kind, lk.path, lk.sha, detail,
                                        _committed_route(lk.path, lk.sha), commits))
        elif sp.touches:
            restored.append({"path": lk.path, "lock_sha": lk.sha, "commits": commits,
                             "paths": sorted({t[1] for t in sp.touches})})
    return locked, violations, releases, restored


def _blob_id(data: bytes, hexlen: int) -> str:
    h = hashlib.sha1() if hexlen == 40 else hashlib.sha256()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def _entries(raw: str, sha_field: int) -> dict[str, tuple[str, str]]:
    """`ls-tree -z` / `ls-files -s -z` rows as {path: (mode, object id)}."""
    out: dict[str, tuple[str, str]] = {}
    for row in raw.split("\0"):
        if "\t" not in row:
            continue
        meta, path = row.split("\t", 1)
        cols = meta.split()
        if len(cols) > sha_field:
            out[path] = (cols[0], cols[sha_field])
    return out


def worktree_violations(root: str, locked: dict[str, Lock]) -> list[Violation]:
    """Index and worktree against HEAD for every active lock, without letting
    git run clean filters: file bytes are hashed here, the way git would store
    them unfiltered."""
    violations: list[Violation] = []
    for lk in locked.values():
        route = f"restore it: git checkout HEAD -- {lk.path}"
        head = _entries(_git_ok(["ls-tree", "-r", "-z", "HEAD", "--", lk.path], root, literal=True), 2)
        index = _entries(_git_ok(["ls-files", "-s", "-z", "--", lk.path], root, literal=True), 1)
        for path in sorted(set(head) | set(index)):
            h, ix = head.get(path), index.get(path)
            if h != ix:
                violations.append(Violation("index", path, "staged", lk.path, lk.sha,
                                            "the index differs from HEAD for a locked path", route))
            if h is None or h[0] == "160000":  # gone from HEAD, or a submodule
                continue
            full = os.path.join(root, path)
            if not os.path.lexists(full):
                violations.append(Violation("worktree", path, "deleted", lk.path, lk.sha,
                                            "deleted in the worktree", route))
                continue
            try:
                data = (os.readlink(full).encode("utf-8", "surrogateescape")
                        if h[0] == "120000" else open(full, "rb").read())
            except OSError as exc:
                violations.append(Violation("worktree", path, "unreadable", lk.path, lk.sha,
                                            f"cannot read the worktree copy: {exc}", route))
                continue
            if _blob_id(data, len(h[1])) != h[1]:
                violations.append(Violation("worktree", path, "modified", lk.path, lk.sha,
                                            "modified in the worktree", route))
        extra = _git_ok(["ls-files", "-o", "--exclude-standard", "-z", "--", lk.path], root, literal=True)
        for path in filter(None, extra.split("\0")):
            violations.append(Violation("worktree", path, "untracked", lk.path, lk.sha,
                                        "an untracked file under a locked path",
                                        f"remove it, or have a human release {lk.path}"))
    return violations


# --------------------------------------------------------------------------- #
# Bash write-target extraction (best effort)
# --------------------------------------------------------------------------- #
_PUNCT = set("();<>|&\n")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPERS = {"sudo", "doas", "command", "builtin", "exec", "nohup", "time", "nice", "env", "xargs"}
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
_ALL_OPERANDS = {"rm", "unlink", "shred", "truncate", "tee"}
_COPIERS = {"cp", "mv", "install", "ln", "rsync"}
# In-place switches, with only the no-argument flags allowed to precede `i` in a
# cluster: `-pi`, `-ni`, `-Ei`, `-i.bak` are in place; `perl -Mstrict` is not.
_INPLACE = {"sed": re.compile(r"^-[nErszu]*i"), "gsed": re.compile(r"^-[nErszu]*i"),
            "perl": re.compile(r"^-[aFlnpsTtuUwWX0-9]*i")}
_UNLOCK_RE = re.compile(r"test-unlock\s*:", re.I)
_GIT_COMMITISH_RE = re.compile(r"\bgit\b.*\b(commit|commit-tree|interpret-trailers|rebase|merge|notes)\b", re.S)
_COARSE_WRITE = re.compile(
    r">|\btee\b|\bsed\b[^|;&]*\s-\S*i|\bperl\b[^|;&]*\s-\S*i|\bmv\b|\bcp\b|\brm\b"
    r"|\bgit\b[^|;&]*\b(checkout|restore)\b|\btruncate\b|\bdd\b[^|;&]*\bof=")
_HEAD_SOURCES = {"HEAD", "@"}


def _tokenize(command: str) -> list[str]:
    lex = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    return list(lex)


def _is_punct(tok: str) -> bool:
    return bool(tok) and all(ch in _PUNCT for ch in tok)


def _operands(args: Sequence[str]) -> list[str]:
    out, after_dd = [], False
    for a in args:
        if after_dd or not a.startswith("-") or a == "-":
            out.append(a)
        elif a == "--":
            after_dd = True
    return out


def _git_targets(args: list[str], cwd: str) -> tuple[list[tuple[str, str]], str]:
    """Paths a git invocation may overwrite, and the directory they resolve in."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] in ("-C", "-c") and i + 1 < len(args):
            if args[i] == "-C":
                cwd = os.path.normpath(os.path.join(cwd, args[i + 1]))
            i += 2
            continue
        i += 1
    if i >= len(args):
        return [], cwd
    sub, rest = args[i], args[i + 1:]
    if sub in ("rm", "mv"):
        return [(p, cwd) for p in _operands(rest)], cwd
    if sub not in ("checkout", "restore"):
        return [], cwd
    # Restoring from HEAD or the index returns a locked file to its committed
    # content — the recovery `verify --worktree` prescribes. Only an explicit
    # older source can weaken it.
    source: str | None = None
    words: list[str] = []
    before_dd: list[str] | None = None
    j = 0
    while j < len(rest):
        a = rest[j]
        if a == "--":
            before_dd = list(words)
            words = []
        elif sub == "restore" and a in ("-s", "--source") and j + 1 < len(rest):
            source = rest[j + 1]
            j += 1
        elif sub == "restore" and a.startswith("--source="):
            source = a.split("=", 1)[1]
        elif sub == "restore" and a.startswith("-s") and len(a) > 2:
            source = a[2:]
        elif sub == "checkout" and a in ("-b", "-B", "--orphan") and j + 1 < len(rest):
            j += 1
        elif not a.startswith("-"):
            words.append(a)
        j += 1
    if sub == "restore":
        paths = (before_dd or []) + words
    elif before_dd is not None:
        source = before_dd[0] if before_dd else None
        paths = words
    elif len(words) >= 2 and not os.path.lexists(os.path.join(cwd, words[0])):
        source, paths = words[0], words[1:]
    else:
        paths = words
    if source is None or source in _HEAD_SOURCES:
        return [], cwd
    return [(p, cwd) for p in paths], cwd


def _copy_targets(cmd: str, args: list[str], cwd: str) -> list[tuple[str, str]]:
    """cp/mv/install/ln/rsync: the destination is written — and when it is an
    existing directory, what is written is dest/<basename of each source>, not
    the directory itself. `mv` also removes its sources."""
    ops, dest = _operands(args), None
    for k, a in enumerate(args):
        if a in ("-t", "--target-directory") and k + 1 < len(args):
            dest = args[k + 1]
            ops = [o for o in ops if o != dest]
        elif a.startswith("--target-directory="):
            dest = a.split("=", 1)[1]
    if dest is None:
        if len(ops) < 2:
            return []
        dest, ops = ops[-1], ops[:-1]
    out = [(o, cwd) for o in ops] if cmd == "mv" else []
    if os.path.isdir(os.path.join(cwd, dest)):
        out += [(os.path.join(dest, os.path.basename(os.path.normpath(o))), cwd) for o in ops]
    else:
        out.append((dest, cwd))
    return out


def _segment_targets(words: list[str], cwd: str, out: list[tuple[str, str]], depth: int) -> str | None:
    """Collect one simple command's write targets into `out`; return a new cwd
    when the command is a `cd`."""
    i = 0
    while i < len(words):
        w = words[i]
        if _ASSIGN.match(w):
            i += 1
            continue
        if os.path.basename(w) in _WRAPPERS:
            i += 1
            while i < len(words) and (words[i].startswith("-") or _ASSIGN.match(words[i])):
                i += 1
            continue
        break
    argv = words[i:]
    if not argv:
        return None
    cmd, args = os.path.basename(argv[0]), argv[1:]
    if cmd in ("cd", "pushd"):
        dest = next((a for a in args if not a.startswith("-")), None)
        if dest and "$" not in dest and not dest.startswith("~"):
            return os.path.normpath(os.path.join(cwd, dest))
        return None
    if cmd in _SHELLS or cmd == "eval":
        if depth >= 3:
            return None
        script = None
        if cmd == "eval":
            script = " ".join(args)
        else:
            for k, a in enumerate(args):
                if a.startswith("-") and not a.startswith("--") and "c" in a[1:] and k + 1 < len(args):
                    script = args[k + 1]
                    break
        if script:
            out.extend(bash_write_targets(script, cwd, depth + 1) or [])
        return None
    if cmd == "git":
        found, _ = _git_targets(args, cwd)
        out.extend(found)
        return None
    if cmd in _ALL_OPERANDS:
        out.extend((p, cwd) for p in _operands(args))
    elif cmd in _COPIERS:
        out.extend(_copy_targets(cmd, args, cwd))
    elif cmd in _INPLACE:
        if any(_INPLACE[cmd].match(a) or a.startswith("--in-place") for a in args):
            out.extend((p, cwd) for p in _operands(args))
    elif cmd == "dd":
        out.extend((a[3:], cwd) for a in args if a.startswith("of="))
    elif cmd == "find" and any(a in ("-delete", "-exec", "-execdir", "-ok") for a in args):
        for a in args:
            if a.startswith("-") or a in ("(", "!"):
                break
            out.append((a, cwd))
    return None


def bash_write_targets(command: str, cwd: str, depth: int = 0) -> list[tuple[str, str]] | None:
    """(path, directory it resolves in) for every write the command visibly
    makes; None when the command does not tokenize (the caller falls back to a
    coarse rule, never to allow)."""
    try:
        tokens = _tokenize(command)
    except ValueError:
        return None
    out: list[tuple[str, str]] = []
    cur = cwd
    segment: list[str] = []
    redirects: list[str] = []

    def flush() -> None:
        nonlocal cur
        out.extend((r, cur) for r in redirects)
        new = _segment_targets(segment, cur, out, depth) if segment else None
        if new:
            cur = new
        segment.clear()
        redirects.clear()

    i = 0
    while i < len(tokens):
        t = tokens[i]
        if not _is_punct(t):
            segment.append(t)
            i += 1
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if ">" in t or "<" in t:
            if segment and segment[-1].isdigit():
                segment.pop()  # the fd number in `2>`
            if nxt is not None and not _is_punct(nxt):
                is_dup = t.endswith("&") and (nxt.isdigit() or nxt == "-")
                if ">" in t and not is_dup:
                    redirects.append(nxt)
                i += 2
            else:
                i += 1
            continue
        flush()
        i += 1
    flush()
    return out


def _target_hits(lock: str, target: str) -> bool:
    """A write to `target` can change `lock`: same path, a path under a locked
    directory, a directory above the lock, or a glob matching either."""
    if target == "" or covers(lock, target) or lock.startswith(target + "/"):
        return True
    if any(ch in target for ch in "*?["):
        parts = lock.split("/")
        width = target.count("/") + 1
        for k in range(1, len(parts) + 1):
            if k == width and fnmatch.fnmatchcase("/".join(parts[:k]), target):
                return True
    return False


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def _need_root() -> str:
    root = repo_root(os.getcwd())
    if root is None:
        raise LockError("not inside a git work tree")
    return root


def _fold(ignorecase: bool) -> Callable[[str], str]:
    return str.casefold if ignorecase else (lambda s: s)


def locking(locked: dict[str, Lock], cands: Sequence[str], root: str,
            match: Callable[[str, str], bool] = covers) -> Lock | None:
    """The active lock that `match`es any candidate reading, else None."""
    if not locked or not cands:
        return None
    f = _fold(_ignorecase(root))
    for lk in sorted(locked.values(), key=lambda x: x.path):
        if any(match(f(lk.path), f(c)) for c in cands):
            return lk
    return None


def cmd_commit(args: argparse.Namespace) -> int:
    root = _need_root()
    cwd = os.getcwd()
    rels: list[str] = []
    for p in args.paths:
        full = p if os.path.isabs(p) else os.path.join(cwd, p)
        if not os.path.lexists(full):
            print(f"error: {p}: no such file or directory — lock the test you just wrote", file=sys.stderr)
            return 2
        full = os.path.normpath(full)
        rel = _rel_to_root(os.path.join(os.path.realpath(os.path.dirname(full)), os.path.basename(full)),
                           os.path.realpath(root))
        if not rel:
            print(f"error: {p}: {'is the repository root' if rel == '' else 'is outside the repository'}",
                  file=sys.stderr)
            return 2
        if rel not in rels:
            rels.append(rel)
    _git_ok(["add", "--", *rels], root, literal=True)
    staged = _git_ok(["diff", "--cached", "--name-only", "-z", "--no-ext-diff", "--no-textconv",
                      "--", *rels], root, literal=True)
    empty = not staged.strip("\0")
    cmd = ["commit", "-q", "-m", args.message]
    for r in rels:
        cmd += ["--trailer", f"{LOCK_KEY}: {r}"]
    if empty:
        cmd.append("--allow-empty")
    # The pathspec commits ONLY the locked paths: anything else already staged
    # stays staged, so the lock commit is exactly the test it pins.
    cmd += ["--", *rels]
    p = _git(cmd, root, literal=True, signing=True)
    if p.returncode != 0:
        sys.stderr.write(p.stderr.decode("utf-8", "replace"))
        print("error: git commit failed; nothing was locked", file=sys.stderr)
        return 1
    sha = _git_ok(["rev-parse", "HEAD"], root).strip()
    if args.json:
        print(json.dumps({"sha": sha, "paths": rels, "empty": empty}, indent=2))
    else:
        for r in rels:
            print(f"locked {r} in {sha[:SHORT]}{' (empty commit: content pinned as is)' if empty else ''}")
    return 0


def _range_label(s: Scan) -> str:
    return f"{s.base[:SHORT]}..HEAD ({s.base_source})" if s.base else f"HEAD ({s.base_source})"


def cmd_list(args: argparse.Namespace) -> int:
    root = _need_root()
    s = scan(root, args.base, args.max_commits)
    locked = active_locks(s.commits)
    rows = sorted(locked.values(), key=lambda x: x.path)
    if args.json:
        print(json.dumps({"root": root, "base": s.base, "base_source": s.base_source,
                          "truncated": s.truncated, "locks": [lk.as_json() for lk in rows],
                          "warnings": s.warnings}, indent=2))
        return 0
    for w in s.warnings:
        print(f"warning: {w}", file=sys.stderr)
    if not rows:
        print(f"no active test locks in {_range_label(s)}")
    for lk in rows:
        print(f"{lk.sha[:SHORT]}  {lk.path}  {lk.subject}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    root = _need_root()
    s = scan(root, args.base, args.max_commits, with_changes=True)
    locked, violations, releases, restored = committed_violations(s)
    if args.worktree:
        violations += worktree_violations(root, locked)
    ok = not violations
    if args.json:
        print(json.dumps({"ok": ok, "root": root, "base": s.base, "base_source": s.base_source,
                          "truncated": s.truncated, "worktree": bool(args.worktree),
                          "locks": [lk.as_json() for lk in sorted(locked.values(), key=lambda x: x.path)],
                          "releases": releases,
                          "touched_and_restored": restored,
                          "violations": [v.as_json() for v in violations],
                          "warnings": s.warnings}, indent=2))
        return 0 if ok else 1
    for w in s.warnings:
        print(f"warning: {w}", file=sys.stderr)
    for r in releases:
        print(f"released: {r['path']} by {r['short']} ({r['subject']})")
    for t in restored:
        print(f"warning: touched_and_restored: {t['path']} was changed by "
              f"{', '.join(x[:SHORT] for x in t['commits'])} and is back to its content at lock "
              f"{t['lock_sha'][:SHORT]} (review material, not a violation)")
    if ok:
        print(f"test-lock: clean — {len(locked)} active lock(s) in {_range_label(s)}")
        return 0
    print(f"test-lock: {len(violations)} violation(s) in {_range_label(s)}")
    for v in violations:
        where = v.sha[:SHORT] if len(v.sha) >= 40 else v.sha
        print(f"  {where}  {v.path}  {v.detail} ({LOCK_KEY}: {v.lock_path} at {v.lock_sha[:SHORT]})")
        print(f"      release route: {v.release_route}")
    return 1


def cmd_check_path(args: argparse.Namespace) -> int:
    cwd = os.getcwd()
    root = repo_root(args.path if os.path.isabs(args.path) else cwd)
    if root is None:
        return 0
    locked = active_locks(scan(root, args.base, args.max_commits).commits)
    lk = locking(locked, candidates(args.path, cwd, root), root)
    if lk is None:
        return 0
    print(f"{args.path}: locked by {lk.sha[:SHORT]} ({LOCK_KEY}: {lk.path})")
    return 2


def _block(path: str, sha: str) -> int:
    print(f"BLOCKED (test-lock): {path} is locked by {sha[:SHORT]} ({LOCK_KEY} trailer). "
          f"Fix the code, not the test. Releasing it takes a commit with "
          f"'{UNLOCK_KEY}: {path}' — a human decision visible in review.", file=sys.stderr)
    return 2


def _hook(payload: dict) -> int:
    tool = payload.get("tool_name")
    tin = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) and payload.get("cwd") else os.getcwd()
    if tool in FILE_TOOLS:
        path = next((tin[k] for k in FILE_TOOLS[tool] if isinstance(tin.get(k), str) and tin[k]), None)
        if not path:
            return 0
        full = path if os.path.isabs(path) else os.path.join(cwd, path)
        root = repo_root(os.path.dirname(full))
        if root is None:
            return 0
        locked = active_locks(scan(root).commits)
        lk = locking(locked, candidates(full, cwd, root), root)
        return _block(lk.path, lk.sha) if lk else 0
    if tool != "Bash":
        return 0
    command = tin.get("command")
    if not isinstance(command, str) or not command.strip():
        return 0
    targets = bash_write_targets(command, cwd)
    unlocking = bool(_UNLOCK_RE.search(command) and _GIT_COMMITISH_RE.search(command))
    if targets == [] and not unlocking:
        return 0  # fast path: nothing written, no git spawned
    root = repo_root(cwd)
    if root is None:
        return 0
    locked = active_locks(scan(root).commits)
    if not locked:
        return 0
    if unlocking:
        lk = sorted(locked.values(), key=lambda x: x.path)[0]
        print(f"BLOCKED (test-lock): this commits a {UNLOCK_KEY} trailer while {lk.path} is locked "
              f"by {lk.sha[:SHORT]}. Releasing a lock is a human decision visible in review, not a "
              "step for the agent the lock constrains. Fix the code, or ask the human.", file=sys.stderr)
        return 2
    if targets is None:  # untokenizable: fall back to the coarse rule, never to allow
        if _COARSE_WRITE.search(command):
            f = _fold(_ignorecase(root))
            for lk in sorted(locked.values(), key=lambda x: x.path):
                if f(lk.path) in f(command):
                    return _block(lk.path, lk.sha)
        return 0
    for target, base in targets:
        lk = locking(locked, candidates(target, base, root), root, match=_target_hits)
        if lk:
            return _block(lk.path, lk.sha)
    return 0


def cmd_hook(_args: argparse.Namespace) -> int:
    try:
        payload = json.loads(sys.stdin.read() or "null")
        if not isinstance(payload, dict):
            raise ValueError("hook input is not a JSON object")
        return _hook(payload)
    except Exception as exc:  # fail OPEN: `verify` is the backstop, and it fails closed
        print(f"test-lock: hook error, allowing ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bstack test-lock",
        description="Lock a reproduction test with a git trailer so the fix cannot weaken it.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def ranged(p: argparse.ArgumentParser) -> None:
        p.add_argument("--base", default=None,
                       help=f"range base (default: ${BASE_ENV}, else merge-base with origin)")
        p.add_argument("--max-commits", type=int, default=None,
                       help=f"cap the walk (default {DEFAULT_MAX_COMMITS} when no base resolves)")

    c = sub.add_parser("commit", help="commit paths with one Test-Lock trailer each")
    c.add_argument("paths", nargs="+")
    c.add_argument("-m", "--message", required=True)
    c.add_argument("--json", action="store_true")

    ls = sub.add_parser("list", help="active locks in range")
    ranged(ls)
    ls.add_argument("--json", action="store_true")

    v = sub.add_parser("verify", help="exit 1 if a locked path changed after its lock")
    ranged(v)
    v.add_argument("--worktree", action="store_true",
                   help="also fail on index/worktree differences from HEAD for a locked path")
    v.add_argument("--json", action="store_true")

    cp = sub.add_parser("check-path", help="exit 2 if an active lock covers PATH")
    cp.add_argument("path")
    ranged(cp)

    sub.add_parser("hook", help="PreToolUse hook: JSON on stdin, exit 2 blocks")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "hook":
        return cmd_hook(args)
    handlers = {"commit": cmd_commit, "list": cmd_list, "verify": cmd_verify,
                "check-path": cmd_check_path}
    try:
        return handlers[args.cmd](args)
    except (LockError, OSError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        # check-path owns exit 2 for "locked", so its errors must not reuse it;
        # verify owns exit 1 for "violation", so its errors must not reuse that.
        if args.cmd == "check-path" or (args.cmd == "commit" and isinstance(exc, GitError)):
            return 1
        return 2


if __name__ == "__main__":
    sys.exit(main())
