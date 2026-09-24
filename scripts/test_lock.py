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

    Test-Lock: <repo-relative path> sha256=<hex>   the commit that pins the test
    Test-Unlock: <repo-relative path>              a LATER commit that releases it

`commit` writes the sha256 of the locked content (a file's bytes; for a
directory, a manifest of its files). A lock on a directory covers everything
under it; an unlock releases every lock at or below its path.

Trailers are parsed HERE, not by git: commits are enumerated with `git
rev-list`, their raw messages read with `git cat-file`, and the trailer block —
the last paragraph, never the title — split into `Key: value` lines with keys
compared case-insensitively. No trailer.*, core.commentChar or color.* setting
can rename, drop or hide a lock.

Range: commits in <base>..HEAD, where base is `--base REF`, else env
BSTACK_TEST_LOCK_BASE, else merge-base(HEAD, origin/HEAD), else
merge-base(HEAD, origin/main), else (no remote) every commit reachable from
HEAD, capped at --max-commits (default 500). Commits are read oldest-first in
topological order, so "later" means later in that order. A lock whose commit
sits before the base is inactive: it belongs to history that already merged.
The base comes from local refs, so the gate is `verify --base origin/<target>`
in a fresh CI checkout.

What it guarantees, and what it does not
----------------------------------------
It is tamper-EVIDENT against an agent taking the shortcut of weakening the test
it was asked to make pass. `verify` exits 1 when a locked path's content at the
end of its lock differs from its content at the lock commit (modified, deleted
or renamed away); when a Test-Lock trailer carries no sha256 ("lock without
content hash"); and when the content at a lock commit no longer matches the
sha256 its trailer recorded — which is what catches `commit --amend -a` into
the lock commit. It exits 3 on any Test-Unlock trailer in range until a human
accepts that commit with `--accept-unlock SHA`; 1 outranks 3, 3 outranks 0.
The end of a lock is HEAD while it is active, the state just before its
Test-Unlock commit once released (so a release must precede the change or ride
on it), or the re-lock commit when the path is locked again. An edit later
restored to the locked content is `touched_and_restored`: a warning, exit 0.

It is NOT a security boundary. An agent that forges git objects with your
credentials (`commit-tree` with a recomputed trailer, say) or drops the lock
commit from the branch leaves history `verify` cannot tell from an honest one.
Both are visible only in review of the lock commit and the range.

Any git error or unparseable git output during a `verify` scan exits 2 — never
"0 locks". A broken or hostile configuration fails the gate closed.

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

The hook is the course's play, "a hook that blocks edits to test files during a
fix task", and nothing more: Edit/Write/MultiEdit/NotebookEdit on a locked
path, and a Bash command whose write TARGET is a locked path (redirections,
tee, sed -i, perl -i, mv, cp, rm, truncate, dd of=, find -delete, git
checkout/restore/rm/mv; also inside `sh -c` and after `cd`). `cat <locked>` and
`pytest <locked> 2>&1 | tee log` pass. Restoring a locked file from HEAD, the
index or its lock commit passes: those are the recoveries `verify` prints. A
command over 64 KB or 200 segments is not analysed: it blocks if a locked path
appears in its text, else passes. The hook does not police history rewrites or
commit messages; `verify` is the gate for both. It FAILS OPEN — an internal
error exits 0 with a one-line warning — because a guard that crashed closed
would block every edit in every repository, while `verify` fails closed.

Git hygiene: every git call runs with `core.fsmonitor=false` and
`--no-replace-objects`, diff-like calls with `--no-ext-diff --no-textconv`, and
a filtered environment (PATH, HOME, LANG, LC_ALL, TMPDIR, GIT_*): a
repository's config can otherwise execute code, and a hook must not hand it the
session's secrets. Only `commit` adds SSH_AUTH_SOCK, GNUPGHOME and GPG_TTY, so a
signed commit still signs. `commit` writes its trailers into the message itself
(no `--trailer`, so no trailer.*.cmd runs and no trailer.*.ifmissing drops one)
and re-reads the commit it made: a lock whose trailer did not land exits 1. The
`--worktree` comparison hashes file bytes in-process instead of asking git,
because git would run the repository's clean filters to do it — so a locked
path under LFS or autocrlf can differ falsely from its committed blob.

Exit codes
----------
    commit      0 committed · 1 git failed, or the lock trailer did not land ·
                2 usage (missing path, not a repo)
    list        0 · 2 usage/environment (not a repo, bad --base)
    verify      0 clean · 1 violation (content drift, a missing hash, or a
                lock commit that no longer matches its hash) · 2 usage, or any
                git error during the scan · 3 a release in range needs a human
                (--accept-unlock SHA); 1 outranks 3, 3 outranks 0
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
# Pinned on every git call: fsmonitor runs a program on index reads, and a
# `git replace` ref would swap the lock commit for another. Trailers are parsed
# in-process (see message_trailers), so no trailer/comment/log setting matters.
_PINNED = ("-c", "core.fsmonitor=false", "--no-replace-objects", "--no-pager")
_DIGEST_KEY = "sha256="
_HEX64 = re.compile(r"[0-9a-f]{64}")
_TRAILER_LINE = re.compile(r"([A-Za-z0-9][A-Za-z0-9-]*)[ \t]*:(.*)")
MAX_COMMAND = 64 * 1024
MAX_SEGMENTS = 200


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
    cmd = ["git", *_PINNED]
    if literal:
        cmd.append("--literal-pathspecs")
    cmd.extend(args)
    kw: dict = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
    return subprocess.run(cmd, cwd=cwd, env=git_env(signing), capture_output=True,
                          timeout=GIT_TIMEOUT, **kw)


def _text(p: subprocess.CompletedProcess) -> str:
    return p.stdout.decode("utf-8", "surrogateescape")


def _git_raw(args: Sequence[str], cwd: str, **kw) -> bytes:
    p = _git(args, cwd, **kw)
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GitError(f"git {args[0]} failed: {err[-1] if err else 'exit %d' % p.returncode}")
    return p.stdout


def _git_ok(args: Sequence[str], cwd: str, **kw) -> str:
    return _git_raw(args, cwd, **kw).decode("utf-8", "surrogateescape")


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
    """The commit `ref` names; None only when it names nothing (an unborn HEAD,
    an unknown ref). Any other failure — git said something on stderr, a
    config it cannot parse — raises, so a scan fails closed instead of
    mistaking a broken repository for an empty one."""
    p = _git(["rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"], root)
    sha = _text(p).strip()
    if p.returncode == 0 and sha:
        return sha
    err = p.stderr.decode("utf-8", "replace").strip()
    if err or p.returncode not in (0, 1):
        raise GitError(f"git rev-parse {ref} failed: {err.splitlines()[-1] if err else p.returncode}")
    return None


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
    root_real = _realdir(root)
    bases = [""] if os.path.isabs(path) else [cwd, root]
    out: list[str] = []
    for b in bases:
        full = os.path.normpath(os.path.join(b, path) if b else path)
        parent_resolved = os.path.join(_realdir(os.path.dirname(full)), os.path.basename(full))
        # realpath(full) is parent_resolved unless the last component is itself a
        # link: one lstat, not a full walk, keeps a 64 KB command inside budget.
        fully = os.path.realpath(parent_resolved) if os.path.islink(parent_resolved) else parent_resolved
        for form in (parent_resolved, fully):
            rel = _rel_to_root(form, root_real)
            if rel is not None and rel not in out:
                out.append(rel)
    return out


@functools.lru_cache(maxsize=4096)
def _realdir(d: str) -> str:
    return os.path.realpath(d)


# --------------------------------------------------------------------------- #
# history
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Lock:
    path: str
    sha: str
    subject: str
    digest: str | None = None  # sha256 the trailer recorded; None is itself a violation

    def as_json(self) -> dict:
        return {"path": self.path, "sha": self.sha, "short": self.sha[:SHORT],
                "subject": self.subject, "sha256": self.digest}


@dataclass
class Commit:
    sha: str
    subject: str
    locks: list[str] = field(default_factory=list)
    unlocks: list[str] = field(default_factory=list)
    changes: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    digests: dict[str, str] = field(default_factory=dict)
    unlock_raw: list[str] = field(default_factory=list)  # every Test-Unlock value, even malformed


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


def split_digest(value: str) -> tuple[str, str | None]:
    """`<path> sha256=<hex>` -> (path, hex); without the suffix the hash is
    None, which `verify` reports as a violation."""
    head, sep, tail = value.strip().rpartition(" " + _DIGEST_KEY)
    if sep and _HEX64.fullmatch(tail.strip().lower()):
        return head.strip(), tail.strip().lower()
    return value.strip(), None


def message_trailers(message: str) -> list[tuple[str, str]]:
    """(key, value) pairs from a commit message's trailer block, parsed here
    rather than by git so no trailer.*, core.commentChar or color.* setting can
    rename, drop or hide one. The block is the last paragraph when there is more
    than one (the title paragraph is never a trailer block, as in git); every
    `Key: value` line in it counts, and a line starting with whitespace continues
    the previous value."""
    paras: list[list[str]] = []
    cur: list[str] = []
    for line in message.replace("\r\n", "\n").split("\n"):
        if line.strip():
            cur.append(line)
        elif cur:
            paras.append(cur)
            cur = []
    if cur:
        paras.append(cur)
    if len(paras) < 2:
        return []
    out: list[tuple[str, str]] = []
    for line in paras[-1]:
        if line[:1] in (" ", "\t"):
            if out:
                out[-1] = (out[-1][0], f"{out[-1][1]} {line.strip()}".strip())
            continue
        m = _TRAILER_LINE.fullmatch(line.rstrip())
        if m:
            out.append((m.group(1), m.group(2).strip()))
    return out


def _messages(root: str, shas: Sequence[str]) -> dict[str, str]:
    """Raw message of each commit, read in one `cat-file --batch` pass. Output
    that does not parse as the commits asked for raises (exit 2 in verify)."""
    if not shas:
        return {}
    raw = _git_raw(["cat-file", "--batch"], root, stdin=("\n".join(shas) + "\n").encode())
    out: dict[str, str] = {}
    pos = 0
    for sha in shas:
        nl = raw.find(b"\n", pos)
        header = raw[pos:nl].split() if nl >= 0 else []
        if len(header) != 3 or header[0].decode() != sha or header[1] != b"commit":
            raise GitError(f"unparseable cat-file output for {sha[:SHORT]}")
        size = int(header[2])
        body = raw[nl + 1:nl + 1 + size]
        pos = nl + 1 + size + 1
        _, sep, message = body.partition(b"\n\n")
        if not sep and body:
            message = b""
        out[sha] = message.decode("utf-8", "surrogateescape")
    return out


def _changes(root: str, shas: Sequence[str]) -> dict[str, list[tuple[str, tuple[str, ...]]]]:
    """Per-commit name-status (renames detected), one `diff-tree --stdin` pass.
    A merge shows nothing; the content comparison in `verify` covers merges."""
    known = set(shas)
    out: dict[str, list[tuple[str, tuple[str, ...]]]] = {sha: [] for sha in shas}
    if not shas:
        return out
    raw = _git_ok(["diff-tree", "--stdin", "-r", "-z", "-M", "--name-status", "--root",
                   "--no-ext-diff", "--no-textconv"], root,
                  stdin=("\n".join(shas) + "\n").encode())
    toks = raw.split("\0")
    cur: str | None = None
    j = 0
    while j < len(toks):
        t = toks[j].strip("\n")
        if not t:
            j += 1
            continue
        if t in known:
            cur = t
            j += 1
            continue
        n = 2 if t[0] in "RC" else 1
        paths = tuple(toks[j + 1:j + 1 + n])
        if cur is None or len(paths) != n or not t[0].isalpha():
            raise GitError("unparseable diff-tree output")
        out[cur].append((t[0], paths))
        j += 1 + n
    return out


def scan(root: str, base_arg: str | None = None, max_commits: int | None = None,
         with_changes: bool = False) -> Scan:
    """Every in-range commit with its trailers (and, for verify, its changes).
    Any git failure raises GitError: a scan never reports "no locks" because
    git could not answer."""
    base, source = resolve_base(root, base_arg)
    warnings: list[str] = []
    if _commit_sha(root, "HEAD") is None:  # unborn branch: no history, no locks
        return Scan(root, base, source, False, [], warnings)
    cap = max_commits if max_commits is not None else (None if base else DEFAULT_MAX_COMMITS)
    args = ["rev-list", "--topo-order", "--reverse"]
    if cap:
        args.append(f"--max-count={cap}")
    args += [f"{base}..HEAD" if base else "HEAD", "--"]
    shas = _git_ok(args, root).split()
    if any(not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", x) for x in shas):
        raise GitError("unparseable rev-list output")
    messages = _messages(root, shas)
    changes = _changes(root, shas) if with_changes else {}
    commits: list[Commit] = []
    for sha in shas:
        msg = messages[sha]
        trailers = message_trailers(msg)
        c = Commit(sha=sha, subject=msg.split("\n", 1)[0].strip(),
                   locks=[v for k, v in trailers if k.lower() == LOCK_KEY.lower()],
                   unlocks=[v for k, v in trailers if k.lower() == UNLOCK_KEY.lower()],
                   changes=changes.get(sha, []))
        commits.append(c)
    for c in commits:
        c.unlock_raw = list(c.unlocks)
        for key, values in ((LOCK_KEY, c.locks), (UNLOCK_KEY, c.unlocks)):
            good = []
            for v in values:
                v, digest = split_digest(v)
                n = norm_lock_path(v)
                if n is not None and digest and key == LOCK_KEY:
                    c.digests[n] = digest
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
            locked[p] = Lock(p, c.sha, c.subject, c.digests.get(p))
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


def _blobs(root: str, oids: Sequence[str]) -> dict[str, bytes]:
    """Raw content of each blob, read in one `cat-file --batch` pass."""
    want = list(dict.fromkeys(oids))
    if not want:
        return {}
    raw = _git_raw(["cat-file", "--batch"], root, stdin=("\n".join(want) + "\n").encode())
    out: dict[str, bytes] = {}
    pos = 0
    for oid in want:
        nl = raw.find(b"\n", pos)
        if nl < 0:
            raise GitError("unparseable cat-file output")
        header = raw[pos:nl].split()
        pos = nl + 1
        if len(header) == 3 and header[1] == b"blob":
            size = int(header[2])
            out[oid] = raw[pos:pos + size]
            pos += size + 1
    return out


def content_digest(root: str, entries: Sequence[tuple[str, str, str]], path: str) -> str | None:
    """sha256 of a locked path's content from (path, mode, oid) entries: the
    file's bytes for a file lock, a manifest of `mode sha256 path` lines for a
    directory lock. Independent of git's object format."""
    rows = sorted(e for e in entries if covers(path, e[0]))
    if not rows:
        return None
    blobs = _blobs(root, [oid for _, mode, oid in rows if mode != "160000"])
    if len(rows) == 1 and rows[0][0] == path:
        return hashlib.sha256(blobs.get(rows[0][2], rows[0][2].encode())).hexdigest()
    h = hashlib.sha256()
    for p, mode, oid in rows:
        inner = hashlib.sha256(blobs[oid]).hexdigest() if oid in blobs else oid
        h.update(f"{mode} {inner} {p}\n".encode("utf-8", "surrogateescape"))
    return h.hexdigest()


def _tree_rows(root: str, rev: str, path: str) -> list[tuple[str, str, str]]:
    raw = _git_ok(["ls-tree", "-r", "-z", rev, "--", path], root, literal=True)
    return [(p, mode, oid) for p, (mode, oid) in _entries(raw, 2).items()]


def _index_rows(root: str, path: str) -> list[tuple[str, str, str]]:
    raw = _git_ok(["ls-files", "-s", "-z", "--", path], root, literal=True)
    return [(p, mode, oid) for p, (mode, oid) in _entries(raw, 1).items()]


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
            locked[p] = Lock(p, c.sha, c.subject, c.digests.get(p))
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
        # The trailer's hash binds the lock to the content it was taken on. An
        # amend, fixup or rebase that rewrites the lock commit keeps the trailer
        # but not the content — this is the check that sees it.
        if lk.digest is None:
            violations.append(Violation(
                lk.sha, lk.path, "lock-without-hash", lk.path, lk.sha,
                f"lock without content hash: its {LOCK_KEY} trailer carries no sha256, so a "
                "rewrite of the lock commit would go unseen",
                "a human re-locks the test with `bstack test-lock commit`, which records the "
                "hash", [lk.sha]))
            continue
        if content_digest(s.root, _tree_rows(s.root, lk.sha, lk.path), lk.path) != lk.digest:
            violations.append(Violation(
                lk.sha, lk.path, "lock-rewritten", lk.path, lk.sha,
                f"lock commit rewritten: the content at {lk.sha[:SHORT]} no longer matches the "
                f"sha256 its {LOCK_KEY} trailer recorded",
                "restore the original lock commit (the reflog has it), or a human re-locks the "
                "test in a commit visible in review", [lk.sha]))
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
# Bash analysis (best effort): write targets only
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
_HEAD_SOURCES = {"HEAD", "@"}
_GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"}
_SEGMENT_SPLIT = re.compile(r"[;&|\n]+")


@dataclass
class _Target:
    path: str
    cwd: str
    source: str | None = None  # a git restore's explicit source; None for other writes


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


def _git_targets(args: list[str], cwd: str) -> list[_Target]:
    """Paths a git invocation may overwrite in the worktree. Restoring from
    HEAD or the index returns a locked file to its committed content — the
    recovery `verify --worktree` prescribes — so only an explicit other source
    is a target (and the hook still lets the lock commit itself through)."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] in _GIT_VALUE_OPTS and i + 1 < len(args):
            if args[i] == "-C":
                cwd = os.path.normpath(os.path.join(cwd, args[i + 1]))
            i += 2
            continue
        i += 1
    if i >= len(args):
        return []
    sub, rest = args[i], args[i + 1:]
    if sub in ("rm", "mv"):
        return [_Target(p, cwd) for p in _operands(rest)]
    if sub not in ("checkout", "restore"):
        return []
    source: str | None = None
    words: list[str] = []
    before_dd: list[str] | None = None
    j = 0
    while j < len(rest):
        a = rest[j]
        if a == "--":
            before_dd, words = list(words), []
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
        source, paths = (before_dd[0] if before_dd else None), words
    elif len(words) >= 2 and not os.path.lexists(os.path.join(cwd, words[0])):
        source, paths = words[0], words[1:]
    else:
        paths = words
    if source is None or source in _HEAD_SOURCES:
        return []
    return [_Target(p, cwd, source) for p in paths]


def _copy_targets(cmd: str, args: list[str], cwd: str) -> list[_Target]:
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
    out = [_Target(o, cwd) for o in ops] if cmd == "mv" else []
    if os.path.isdir(os.path.join(cwd, dest)):
        out += [_Target(os.path.join(dest, os.path.basename(os.path.normpath(o))), cwd) for o in ops]
    else:
        out.append(_Target(dest, cwd))
    return out


def _segment(words: list[str], cwd: str, out: list[_Target], depth: int) -> str | None:
    """One simple command's write targets into `out`; a new cwd if it is a `cd`."""
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
        inner = analyze(script, cwd, depth + 1) if script else None
        if inner:
            out += inner
        return None
    if cmd == "git":
        out += _git_targets(args, cwd)
    elif cmd in _ALL_OPERANDS:
        out += [_Target(p, cwd) for p in _operands(args)]
    elif cmd in _COPIERS:
        out += _copy_targets(cmd, args, cwd)
    elif cmd in _INPLACE:
        if any(_INPLACE[cmd].match(a) or a.startswith("--in-place") for a in args):
            out += [_Target(p, cwd) for p in _operands(args)]
    elif cmd == "dd":
        out += [_Target(a[3:], cwd) for a in args if a.startswith("of=")]
    elif cmd == "find" and any(a in ("-delete", "-exec", "-execdir", "-ok") for a in args):
        for a in args:
            if a.startswith("-") or a in ("(", "!"):
                break
            out.append(_Target(a, cwd))
    return None


def analyze(command: str, cwd: str, depth: int = 0) -> list[_Target] | None:
    """Write targets a command visibly makes; None when it does not tokenize
    (the caller falls back to a coarse rule, never to allow). Linear in the
    command's length: tokenizing is one pass, and nothing here backtracks."""
    try:
        tokens = _tokenize(command)
    except ValueError:
        return None
    out: list[_Target] = []
    cur = cwd
    segment: list[str] = []
    redirects: list[str] = []

    def flush() -> None:
        nonlocal cur
        out.extend(_Target(r, cur) for r in redirects)
        new = _segment(segment, cur, out, depth) if segment else None
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


def bash_write_targets(command: str, cwd: str, depth: int = 0) -> list[tuple[str, str]] | None:
    """(path, directory it resolves in) for every write the command visibly makes."""
    a = analyze(command, cwd, depth)
    return None if a is None else [(t.path, t.cwd) for t in a]


def _coarse_write(command: str) -> bool:
    """The co-occurrence rule for a command that does not tokenize. Linear: a
    split and a set lookup per word, no backtracking regex."""
    if ">" in command:
        return True
    for seg in _SEGMENT_SPLIT.split(command):
        words = seg.split()
        names = {os.path.basename(w) for w in words}
        if names & (_ALL_OPERANDS | _COPIERS):
            return True
        if names & set(_INPLACE) and any(w.startswith("-") and "i" in w for w in words):
            return True
        if "git" in names and names & {"checkout", "restore", "rm", "mv"}:
            return True
        if "dd" in names and any(w.startswith("of=") for w in words):
            return True
    return False


def _over_cap(command: str) -> bool:
    """Past this size the command is not analysed (the hook has a 5 s budget)."""
    return len(command) > MAX_COMMAND or len(_SEGMENT_SPLIT.split(command)) > MAX_SEGMENTS


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
    # Bind each lock to the content it pins. The pathspec commit below takes the
    # just-added index content, so the index is what the lock commit will hold.
    digests = {r: content_digest(root, _index_rows(root, r), r) for r in rels}
    missing = [r for r, d in digests.items() if d is None]
    if missing:
        print(f"error: {', '.join(missing)}: nothing tracked there to lock", file=sys.stderr)
        return 2
    # The trailers go into the message here, not through `--trailer`: git's
    # trailer machinery obeys trailer.*.cmd (runs a program) and
    # trailer.*.ifmissing (can drop the line). A trailing Key: value paragraph
    # the caller wrote stays one paragraph with ours.
    wanted = [f"{LOCK_KEY}: {r} {_DIGEST_KEY}{digests[r]}" for r in rels]
    body = args.message.rstrip("\n")
    joins = len(body.split("\n\n")) > 1 and all(
        _TRAILER_LINE.fullmatch(x.rstrip()) for x in body.split("\n\n")[-1].splitlines() if x.strip())
    message = body + ("\n" if joins else "\n\n") + "\n".join(wanted) + "\n"
    cmd = ["commit", "-q", "--cleanup=whitespace", "-m", message]
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
    # Re-read the commit just made and parse its trailers in-process: a commit-msg
    # hook (or anything else) that dropped a lock line leaves a commit that pins
    # nothing, and that must not print "locked".
    landed = {v for k, v in message_trailers(_messages(root, [sha])[sha]) if k.lower() == LOCK_KEY.lower()}
    lost = [w for w in wanted if w.split(": ", 1)[1] not in landed]
    if lost:
        print(f"error: commit {sha[:SHORT]} is missing {', '.join(lost)} — something rewrote the "
              "message (a commit-msg hook?). Nothing is locked; undo that commit and lock again.",
              file=sys.stderr)
        return 1
    changed = [r for r in rels if content_digest(root, _tree_rows(root, sha, r), r) != digests[r]]
    if changed:
        print(f"error: {', '.join(changed)} changed while committing (a commit hook rewrote it?): "
              f"lock {sha[:SHORT]} would fail verify. Undo it with `git reset --soft HEAD~1` and "
              "lock again.", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"sha": sha, "paths": rels, "empty": empty, "sha256": digests}, indent=2))
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
    accepted: set[str] = set()
    for ref in args.accept_unlock or []:
        sha = _commit_sha(root, ref)
        if sha is None:
            raise LockError(f"--accept-unlock {ref!r} does not name a commit")
        accepted.add(sha)
    locked, violations, releases, restored = committed_violations(s)
    if args.worktree:
        violations += worktree_violations(root, locked)
    # A release is fail-CLOSED. No guard over command text can enumerate every way
    # to spell a Test-Unlock (`--trailer K=V`, -F files, trailer aliases, split
    # literals), so any Test-Unlock trailer git parses in range needs a human:
    # exit 3 until each one is accepted by sha. A content violation outranks it.
    unlocks = [{"sha": c.sha, "short": c.sha[:SHORT], "path": raw, "subject": c.subject,
                "accepted": c.sha in accepted}
               for c in s.commits for raw in c.unlock_raw]
    pending = [u for u in unlocks if not u["accepted"]]
    code = 1 if violations else (3 if pending else 0)
    if args.json:
        print(json.dumps({"ok": code == 0, "exit": code, "root": root, "base": s.base,
                          "base_source": s.base_source,
                          "truncated": s.truncated, "worktree": bool(args.worktree),
                          "locks": [lk.as_json() for lk in sorted(locked.values(), key=lambda x: x.path)],
                          "releases": releases,
                          "unlocks": unlocks,
                          "touched_and_restored": restored,
                          "violations": [v.as_json() for v in violations],
                          "warnings": s.warnings}, indent=2))
        return code
    for w in s.warnings:
        print(f"warning: {w}", file=sys.stderr)
    for t in restored:
        print(f"warning: touched_and_restored: {t['path']} was changed by "
              f"{', '.join(x[:SHORT] for x in t['commits'])} and is back to its content at lock "
              f"{t['lock_sha'][:SHORT]} (review material, not a violation)")
    for u in unlocks:
        if u["accepted"]:
            print(f"release accepted: {u['short']} {u['path']}")
        else:
            print(f"release requires a human: {u['short']} {u['path']}")
    if violations:
        print(f"test-lock: {len(violations)} violation(s) in {_range_label(s)}")
        for v in violations:
            where = v.sha[:SHORT] if len(v.sha) >= 40 else v.sha
            print(f"  {where}  {v.path}  {v.detail} ({LOCK_KEY}: {v.lock_path} at {v.lock_sha[:SHORT]})")
            print(f"      release route: {v.release_route}")
    elif pending:
        print(f"test-lock: {len(pending)} release(s) need a human — review each, then pass "
              "--accept-unlock <sha> (exit 3 until then)")
    else:
        print(f"test-lock: clean — {len(locked)} active lock(s) in {_range_label(s)}")
    return code


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
    if _over_cap(command):
        root = repo_root(cwd)
        if root is None:
            return 0
        f = _fold(_ignorecase(root))
        text = f(command)
        for lk in sorted(active_locks(scan(root).commits).values(), key=lambda x: x.path):
            if f(lk.path) in text:
                return _block(lk.path, lk.sha)
        return 0
    targets = analyze(command, cwd)
    if targets == []:
        return 0  # fast path: nothing written, no git spawned
    root = repo_root(cwd)
    if root is None:
        return 0
    locked = active_locks(scan(root).commits)
    if not locked:
        return 0
    if targets is None:  # untokenizable: fall back to the coarse rule, never to allow
        if _coarse_write(command):
            f = _fold(_ignorecase(root))
            for lk in sorted(locked.values(), key=lambda x: x.path):
                if f(lk.path) in f(command):
                    return _block(lk.path, lk.sha)
        return 0
    sources: dict[str, str | None] = {}
    for t in targets:
        lk = locking(locked, candidates(t.path, t.cwd, root), root, match=_target_hits)
        if lk is None:
            continue
        # Restoring from the lock commit itself is the recovery `verify` prints.
        if t.source is not None:
            if t.source not in sources:
                sources[t.source] = _commit_sha(root, t.source)
            if sources[t.source] == lk.sha:
                continue
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
    v.add_argument("--accept-unlock", action="append", metavar="SHA",
                   help="accept the Test-Unlock on this commit (repeatable); without it any "
                        "release in range exits 3")
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
