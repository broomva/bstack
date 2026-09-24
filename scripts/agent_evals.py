#!/usr/bin/env python3
"""agent_evals.py — continuous evals of the agent's configuration (BRO-2542).

CLAUDE.md, skills and hooks steer the agent, so a change to them is a behaviour
change and needs the regression testing that code gets. This runs a suite of evals
non-interactively: each eval is a real task taken from recent work (the prompt), plus
the checks that define an acceptable outcome (a command's exit and output, a file left
unchanged, a file that must contain something, a reply that must or must not match).

    validate <evals-dir>            every eval file is well-formed
    validate <evals-dir> --prove    the checks DISCRIMINATE: they fail when nothing is
                                    done and pass on the eval's `reference` commands
                                    (no model is called)
    run <evals-dir>                 run `claude -p` on every eval, score it, summarize
    baseline <results.json> --out F freeze a run's pass rate for later comparison

Invariant: an eval never touches the live checkout. Each one runs in a scratch
`git worktree add --detach <tmp> HEAD` that is removed afterwards (unless --keep). The
live tree's status, HEAD and working files are byte-identical after a run. Branches an
eval checks out inside its scratch are deleted with it (they are identified from the
scratch's own HEAD reflog, so a branch another session creates meanwhile is never
touched); any other new branch is reported as `leaked_refs`, never deleted.

Hidden paths: the agent under test must not be able to read the answers. A worktree
shares the live repo's refs and objects, so `git show HEAD~1:evals/…` or `git log --all`
would reveal a deleted evals dir. When any path is hidden (`--hide PATH`, and by default
the evals dir itself whenever it lies inside --repo; `--no-hide-evals-dir` opts out),
the scratch is instead a STANDALONE repository holding one orphan commit: HEAD's tree
minus the hidden paths, built inside the scratch (ls-tree, pack only the kept blobs,
update-index, write-tree, commit-tree, all in the filtered env). No ref, history or
object in it contains the hidden paths, and the live object store is not written to.
It gets a local `main` at that commit, with HEAD detached there, as in a worktree.
Relative --hide paths are repo-relative. Checks run in the same scratch, so they cannot
read hidden files either.

A check that cannot fail is not a check. `validate --prove` runs every eval that has a
`reference` twice, in two fresh scratch checkouts, without claude: a no-op arm (setup
only, empty reply) where at least one check must FAIL, and a reference arm (setup, then
the reference commands, whose stdout stands in for the reply) where every check must PASS.

Trust model: `setup`, `reference` and `command` checks are shell strings run with
cwd = the scratch checkout. That is acceptable because eval files are repo-owned and
reviewed like code, the same as a Makefile. Those shells, and every git subprocess this
script spawns, get a filtered environment (PATH, HOME, LANG, LC_ALL, TMPDIR, GIT_*,
minus the GIT_* variables that relocate a repository or inject config), so an API key
in the parent environment is not visible to them. Git runs with core.fsmonitor=false
and hooks disabled; diff-family commands also get --no-ext-diff --no-textconv. Only the
claude process inherits the full environment, because it needs its credentials; it
never gets --dangerously-skip-permissions.

Eval file (one JSON object per `<evals-dir>/*.json`):
    id, description, source, prompt, allowed_tools, checks       required
    setup, reference, timeout_s (default 600), tags               optional
Check types: command {run, expect_exit=0, expect_stdout_regex?, timeout_s?},
file_unchanged {path}, file_contains {path, regex}, file_absent {path},
output_regex {regex}, output_not_regex {regex}. Regexes use re.search with no implicit
flags (write (?m) or (?i) inline). file_unchanged compares bytes against the scratch's
HEAD taken after setup, so an agent that commits its change cannot launder it.

Scoring: status is passed (every check passed), failed (a check failed), or errored
(setup failed, claude exited non-zero, or claude timed out). pass_rate = passed / ran;
errored counts as not passed.

Baseline comparison runs only over the evals present in BOTH runs, so adding a new
(possibly failing) eval or retiring an old one never reads as a drop. The summary
reports baseline_pass_rate and current_pass_rate_on_common over that intersection,
plus added_ids and removed_ids. A regression is an eval that passed in the baseline
and now fails or errors; `--gate` fails on any regression, even when the rate is flat
(one fixed, one broken), unless --allow-regressions. `baseline.json` in the evals dir
is the baseline file, never an eval, and is skipped by discovery.

Nested sessions: measured 2026-09-23, a nested `claude -p` with CLAUDECODE=1 and
CLAUDE_CODE_ENTRYPOINT=sdk-cli set ran normally on Claude Code 2.1.280 (is_error false),
so claude's environment keeps those variables.

Exit codes:
    0  run completed (advisory by default, even with failures); validate found nothing
    1  run --gate: pass rate below --min-pass-rate; pass rate on the common evals below
       baseline − tolerance; a regression (unless --allow-regressions); or no eval in
       common with the baseline. validate: a problem was found (schema, or --prove)
    2  schema error in the evals passed to `run`, no claude binary, not a git repo,
       unreadable baseline/results, or a usage error (e.g. --gate with no threshold)

CI shape — on a schedule, and on any change to CLAUDE.md, skills or hooks:
    bstack-evals validate evals/ --prove
    bstack-evals run evals/ --baseline evals/baseline.json --tolerance 0.05 \
        --min-pass-rate 0.8 --gate --json eval-results.json

Source: Anthropic Academy, "AI-native SDLC playbook", continuous evals in CI.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath

REQUIRED_KEYS = ("id", "description", "source", "prompt", "allowed_tools", "checks")
OPTIONAL_KEYS = ("setup", "reference", "timeout_s", "tags")
# type -> (required fields, optional fields), "type" itself excluded.
CHECK_TYPES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "command": (("run",), ("expect_exit", "expect_stdout_regex", "timeout_s")),
    "file_unchanged": (("path",), ()),
    "file_contains": (("path", "regex"), ()),
    "file_absent": (("path",), ()),
    "output_regex": (("regex",), ()),
    "output_not_regex": (("regex",), ()),
}
REGEX_FIELDS = ("regex", "expect_stdout_regex")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
BASELINE_NAME = "baseline.json"
DEFAULT_TIMEOUT_S = 600
CHECK_TIMEOUT_S = 120
EXCERPT = 4000
# Float slack for rate comparisons: 0.8 - 0.1 is 0.7000000000000001, and a run at
# exactly 0.7 must not fail a gate it meets.
EPS = 1e-9

_ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
# GIT_* passes through, except variables that point git at ANOTHER repository (run
# from inside a hook, GIT_DIR would aim every scratch command at the live repo) or
# inject configuration (a filter or diff driver is an exec vector).
_GIT_ENV_DENY = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
    "GIT_PREFIX", "GIT_EXTERNAL_DIFF", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
})
_GIT_ENV_DENY_PREFIX = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


class EvalError(Exception):
    """A usage or environment error: exit 2."""


# --------------------------------------------------------------------------
# environment + subprocesses
# --------------------------------------------------------------------------

def _git_var_allowed(k: str) -> bool:
    return (k.startswith("GIT_") and k not in _GIT_ENV_DENY
            and not k.startswith(_GIT_ENV_DENY_PREFIX))


def filtered_env() -> dict[str, str]:
    """PATH, HOME, LANG, LC_ALL, TMPDIR and the safe GIT_* variables — nothing else."""
    env = {k: v for k, v in os.environ.items() if k in _ENV_KEEP or _git_var_allowed(k)}
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return env


def claude_env() -> dict[str, str]:
    """The full environment (claude needs its credentials), minus the variables that
    would relocate its git operations onto another repository."""
    return {k: v for k, v in os.environ.items()
            if not (k.startswith("GIT_") and not _git_var_allowed(k))}


def git(args: list[str], cwd: str | Path, *, binary: bool = False, timeout: float = 120,
        input: bytes | None = None,
        extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    cmd = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", *args]
    env = filtered_env()
    env.update(extra_env or {})
    io_kw: dict = {"input": input} if input is not None else {"stdin": subprocess.DEVNULL}
    return subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True,
                          text=not binary, timeout=timeout, **io_kw)


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, AttributeError):
        try:
            proc.kill()
        except OSError:
            pass


def spawn(cmd: str | list[str], *, cwd: str | Path, env: dict[str, str],
          timeout: float, shell: bool = False) -> dict:
    """Run to completion or timeout. The child gets its own process group so a timeout
    kills the whole tree, not just the shell that started it."""
    start = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, shell=shell,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
    except OSError as e:
        return {"rc": None, "stdout": "", "stderr": str(e), "timed_out": False,
                "spawn_error": str(e), "duration_s": 0.0}
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc)
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out, err = b"", b""
    return {
        "rc": proc.returncode,
        "stdout": out.decode("utf-8", "replace"),
        "stderr": err.decode("utf-8", "replace"),
        "timed_out": timed_out,
        "spawn_error": None,
        "duration_s": round(time.monotonic() - start, 3),
    }


def _tail(s: str, n: int = 500) -> str:
    s = s or ""
    return s if len(s) <= n else "…" + s[-n:]


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z")


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------

def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _path_problem(where: str, path) -> str | None:
    if not isinstance(path, str) or not path.strip():
        return f"{where}: 'path' must be a non-empty string"
    pp = PurePosixPath(path)
    if pp.is_absolute() or os.path.isabs(path) or re.match(r"^[A-Za-z]:", path):
        return f"{where}: path {path!r} must be relative to the checkout"
    if ".." in pp.parts:
        return f"{where}: path {path!r} must not contain '..'"
    if pp.parts and pp.parts[0] == ".git":
        return f"{where}: path {path!r} is inside .git"
    return None


def _str_list_problems(where: str, v, *, allow_empty: bool = True,
                       no_commas: bool = False) -> list[str]:
    if not isinstance(v, list):
        return [f"{where} must be a list of strings"]
    out = []
    if not v and not allow_empty:
        out.append(f"{where} must not be empty")
    for i, s in enumerate(v):
        if not isinstance(s, str) or not s.strip():
            out.append(f"{where}[{i}] must be a non-empty string")
        elif no_commas and "," in s:
            out.append(f"{where}[{i}] {s!r} contains a comma; entries are joined with ','")
    return out


def _check_problems(i: int, c) -> list[str]:
    where = f"checks[{i}]"
    if not isinstance(c, dict):
        return [f"{where}: must be an object"]
    t = c.get("type")
    if t not in CHECK_TYPES:
        return [f"{where}: unknown check type {t!r} (known: {', '.join(sorted(CHECK_TYPES))})"]
    req, opt = CHECK_TYPES[t]
    p = []
    for k in req:
        if k not in c:
            p.append(f"{where} ({t}): missing field '{k}'")
    for k in c:
        if k != "type" and k not in req and k not in opt:
            p.append(f"{where} ({t}): unknown field '{k}'")
    if "path" in c:
        pr = _path_problem(f"{where} ({t})", c["path"])
        if pr:
            p.append(pr)
    for k in REGEX_FIELDS:
        if k in c:
            if not isinstance(c[k], str):
                p.append(f"{where} ({t}): '{k}' must be a string")
                continue
            try:
                re.compile(c[k])
            except re.error as e:
                p.append(f"{where} ({t}): '{k}' does not compile: {e}")
    if "run" in c and (not isinstance(c["run"], str) or not c["run"].strip()):
        p.append(f"{where} ({t}): 'run' must be a non-empty string")
    if "expect_exit" in c and (not isinstance(c["expect_exit"], int)
                               or isinstance(c["expect_exit"], bool)):
        p.append(f"{where} ({t}): 'expect_exit' must be an integer")
    if "timeout_s" in c and (not _is_num(c["timeout_s"]) or c["timeout_s"] <= 0):
        p.append(f"{where} ({t}): 'timeout_s' must be a positive number")
    return p


def validate_eval(ev) -> list[str]:
    """Problems with one eval object. Empty list means well-formed."""
    if not isinstance(ev, dict):
        return ["top level must be a JSON object"]
    p = []
    for k in REQUIRED_KEYS:
        if k not in ev:
            p.append(f"missing required key '{k}'")
    for k in ev:
        if k not in REQUIRED_KEYS and k not in OPTIONAL_KEYS:
            p.append(f"unknown key '{k}' (a misspelt optional key would be silently ignored)")
    if "id" in ev and (not isinstance(ev["id"], str) or not ID_RE.match(ev["id"])):
        p.append(f"'id' must match {ID_RE.pattern}, got {ev.get('id')!r}")
    for k in ("description", "source"):
        if k in ev and (not isinstance(ev[k], str) or not ev[k].strip()):
            p.append(f"'{k}' must be a non-empty string")
    if "prompt" in ev:
        pr = ev["prompt"]
        if not isinstance(pr, str) or not pr.strip():
            p.append("'prompt' must be a non-empty string")
        elif pr.lstrip().startswith("-"):
            p.append("'prompt' must not start with '-' (claude would parse it as a flag)")
    if "allowed_tools" in ev:
        p += _str_list_problems("'allowed_tools'", ev["allowed_tools"], no_commas=True)
    if "setup" in ev:
        p += _str_list_problems("'setup'", ev["setup"])
    if "reference" in ev:
        p += _str_list_problems("'reference'", ev["reference"], allow_empty=False)
    if "tags" in ev:
        p += _str_list_problems("'tags'", ev["tags"])
    if "timeout_s" in ev and (not _is_num(ev["timeout_s"]) or ev["timeout_s"] <= 0):
        p.append("'timeout_s' must be a positive number")
    if "checks" in ev:
        checks = ev["checks"]
        if not isinstance(checks, list):
            p.append("'checks' must be a list")
        elif not checks:
            p.append("'checks' is empty — an eval with no checks passes vacuously")
        else:
            for i, c in enumerate(checks):
                p += _check_problems(i, c)
    return p


def discover(path: Path) -> list[Path]:
    """A directory yields its *.json (sorted, non-recursive), except the baseline file
    kept beside the evals; a file yields itself."""
    if path.is_dir():
        return sorted(p for p in path.glob("*.json")
                      if p.is_file() and p.name != BASELINE_NAME)
    return [path]


def load_evals(path: Path) -> tuple[list[tuple[Path, dict]], list[str]]:
    """(well-formed evals, problems). Every problem reads `file: problem`."""
    if not path.exists():
        raise EvalError(f"no such file or directory: {path}")
    files = discover(path)
    problems: list[str] = []
    if not files:
        problems.append(f"{path}: no *.json eval files — an empty suite passes vacuously")
    good: list[tuple[Path, dict]] = []
    seen: dict[str, Path] = {}
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as e:
            problems.append(f"{f}: unreadable: {e}")
            continue
        except json.JSONDecodeError as e:
            problems.append(f"{f}: invalid JSON: {e}")
            continue
        ps = validate_eval(data)
        problems += [f"{f}: {x}" for x in ps]
        eid = data.get("id") if isinstance(data, dict) else None
        if isinstance(eid, str):
            if eid in seen:
                problems.append(f"{f}: duplicate id '{eid}' (also in {seen[eid]})")
                continue
            seen[eid] = f
        if not ps:
            good.append((f, data))
    return good, problems


def select(evals: list[tuple[Path, dict]], only: list[str] | None) -> list[tuple[Path, dict]]:
    if not only:
        return evals
    known = {e["id"] for _, e in evals}
    unknown = [i for i in only if i not in known]
    if unknown:
        raise EvalError(f"--only names unknown eval id(s): {', '.join(unknown)}")
    return [(f, e) for f, e in evals if e["id"] in only]


# --------------------------------------------------------------------------
# scratch checkout
# --------------------------------------------------------------------------

def repo_root(repo: Path) -> Path:
    p = git(["rev-parse", "--show-toplevel"], repo)
    if p.returncode != 0:
        raise EvalError(f"{repo}: not a git repository ({p.stderr.strip()})")
    root = Path(p.stdout.strip())
    if git(["rev-parse", "--verify", "-q", "HEAD"], root).returncode != 0:
        raise EvalError(f"{root}: HEAD has no commit to check out")
    return root


def _branch_refs(root: Path) -> dict[str, str]:
    p = git(["for-each-ref", "--format=%(refname) %(objectname)", "refs/heads"], root)
    out = {}
    for line in p.stdout.splitlines():
        if " " in line:
            ref, sha = line.split(" ", 1)
            out[ref] = sha
    return out


_CHECKOUT_RE = re.compile(r"^checkout: moving from (\S+) to (\S+)$")


_SCRATCH_IDENTITY = {"GIT_AUTHOR_NAME": "bstack-evals", "GIT_AUTHOR_EMAIL": "evals@localhost",
                     "GIT_COMMITTER_NAME": "bstack-evals",
                     "GIT_COMMITTER_EMAIL": "evals@localhost"}
# Repo-local settings a standalone scratch inherits, so the agent's own commits behave
# as they would in the live repo (identity, and a tracked hooks dir such as .githooks).
_INHERITED_CONFIG = ("user.name", "user.email", "core.hooksPath")


def resolve_hidden(root: Path, evals: Path, hide: list[str] | None,
                   hide_evals_dir: bool) -> list[str]:
    """Repo-relative POSIX paths to hide. Relative --hide values are repo-relative; the
    evals path is a filesystem path and is hidden only when it lies inside the repo."""
    root_real = Path(os.path.realpath(root))
    out: set[str] = set()
    for h in hide or []:
        if os.path.isabs(h):
            real = Path(os.path.realpath(h))
            if root_real not in real.parents:
                raise EvalError(f"--hide {h}: not inside the repository {root_real}")
            h = real.relative_to(root_real).as_posix()
        prob = _path_problem("--hide", h)
        if prob or PurePosixPath(h).as_posix() in ("", "."):
            raise EvalError(prob or f"--hide {h!r} would hide the whole repository")
        out.add(PurePosixPath(h).as_posix())
    if hide_evals_dir:
        real = Path(os.path.realpath(evals))
        if root_real in real.parents:
            out.add(real.relative_to(root_real).as_posix())
    return sorted(out)


class Scratch:
    """A scratch checkout of HEAD in a temp dir, removed on exit unless keep: a detached
    worktree, or — when paths are hidden — a standalone repo with one orphan commit."""

    def __init__(self, root: Path, keep: bool = False, hide: list[str] | None = None):
        self.root = root
        self.keep = keep
        self.hide = list(hide or [])
        self.tmp: Path | None = None
        self.path: Path | None = None
        self.cleaned_refs: list[str] = []
        self.leaked_refs: list[str] = []

    def __enter__(self) -> "Scratch":
        self.tmp = Path(tempfile.mkdtemp(prefix="bstack-eval-"))
        self.path = self.tmp / "wt"
        if self.hide:
            try:
                self._build_orphan()
            except RuntimeError:
                shutil.rmtree(self.tmp, ignore_errors=True)
                raise
            return self
        self.refs_before = _branch_refs(self.root)
        p = git(["worktree", "add", "--detach", str(self.path), "HEAD"], self.root)
        if p.returncode != 0:
            shutil.rmtree(self.tmp, ignore_errors=True)
            raise RuntimeError(f"git worktree add failed: {p.stderr.strip()}")
        return self

    def _is_hidden(self, path: str) -> bool:
        return any(path == h or path.startswith(h + "/") for h in self.hide)

    def _build_orphan(self) -> None:
        def must(p: subprocess.CompletedProcess, what: str) -> subprocess.CompletedProcess:
            if p.returncode != 0:
                err = p.stderr if isinstance(p.stderr, str) else p.stderr.decode("utf-8",
                                                                                 "replace")
                raise RuntimeError(f"hidden scratch: {what} failed: {err.strip()[:300]}")
            return p

        ls = must(git(["ls-tree", "-r", "-z", "--full-tree", "HEAD"], self.root, binary=True),
                  "ls-tree")
        keep: list[bytes] = []
        blobs: set[bytes] = set()
        for rec in filter(None, ls.stdout.split(b"\0")):
            meta, _, raw_path = rec.partition(b"\t")
            if self._is_hidden(raw_path.decode("utf-8", "surrogateescape")):
                continue
            keep.append(rec)
            _mode, typ, sha = meta.split(b" ")
            if typ == b"blob":
                blobs.add(sha)
        must(git(["init", "-q", "--template=", "-b", "main", str(self.path)], self.tmp), "init")
        for key in _INHERITED_CONFIG:
            v = git(["config", "--local", "--get", key], self.root)
            if v.returncode == 0 and v.stdout.strip():
                must(git(["config", key, v.stdout.strip()], self.path), f"config {key}")
        if blobs:
            pack = must(git(["pack-objects", "--stdout"], self.root, binary=True,
                            input=b"\n".join(sorted(blobs)) + b"\n"), "pack-objects")
            must(git(["index-pack", "--stdin"], self.path, binary=True, input=pack.stdout),
                 "index-pack")
        if keep:
            must(git(["update-index", "-z", "--index-info"], self.path, binary=True,
                     input=b"\0".join(keep) + b"\0"), "update-index")
        tree = must(git(["write-tree"], self.path), "write-tree").stdout.strip()
        commit = must(git(["commit-tree", tree, "-m", "scratch"], self.path,
                          extra_env=_SCRATCH_IDENTITY), "commit-tree").stdout.strip()
        must(git(["update-ref", "refs/heads/main", commit], self.path), "update-ref")
        must(git(["checkout", "-q", "-f", "--detach", commit], self.path), "checkout")

    def _own_branches(self) -> set[str]:
        """Branch names this scratch checked out, read from its own HEAD reflog (a
        per-worktree log), plus whatever it has checked out now."""
        names: set[str] = set()
        p = git(["reflog", "show", "--format=%gs", "HEAD"], self.path)
        for line in p.stdout.splitlines():
            m = _CHECKOUT_RE.match(line.strip())
            if m:
                names.update(m.groups())
        p = git(["symbolic-ref", "-q", "--short", "HEAD"], self.path)
        if p.returncode == 0 and p.stdout.strip():
            names.add(p.stdout.strip())
        return names

    def __exit__(self, *exc) -> None:
        if self.keep or self.path is None:
            return
        if self.hide:
            # standalone: nothing is registered in the live repo, nothing to unregister
            shutil.rmtree(self.tmp, ignore_errors=True)
            return
        own = self._own_branches()
        p = git(["worktree", "remove", "--force", "--force", str(self.path)], self.root)
        if p.returncode != 0:
            shutil.rmtree(self.path, ignore_errors=True)
            git(["worktree", "prune"], self.root)
        shutil.rmtree(self.tmp, ignore_errors=True)
        after = _branch_refs(self.root)
        for ref, sha in sorted(after.items()):
            if ref in self.refs_before:
                continue
            if ref.removeprefix("refs/heads/") in own:
                # compare-and-delete: only if it still points where we saw it
                if git(["update-ref", "-d", ref, sha], self.root).returncode == 0:
                    self.cleaned_refs.append(ref)
                    continue
            self.leaked_refs.append(ref)


def run_shell_list(cmds: list[str], cwd: Path, timeout: float, label: str
                   ) -> tuple[str | None, str]:
    """Run commands in order; (error or None, concatenated stdout)."""
    out = []
    for cmd in cmds:
        r = spawn(cmd, cwd=cwd, env=filtered_env(), timeout=timeout, shell=True)
        out.append(r["stdout"])
        if r["timed_out"]:
            return f"{label} command timed out after {timeout}s: {cmd}", "".join(out)
        if r["rc"] != 0:
            return (f"{label} command exited {r['rc']}: {cmd}"
                    f"{' — ' + _tail(r['stderr'].strip(), 300) if r['stderr'].strip() else ''}",
                    "".join(out))
    return None, "".join(out)


def head_sha(wt: Path) -> str:
    p = git(["rev-parse", "HEAD"], wt)
    return p.stdout.strip()


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def _inside(wt: Path, rel: str) -> Path | None:
    full = wt / rel
    real = Path(os.path.realpath(full))
    root = Path(os.path.realpath(wt))
    if real == root or root in real.parents:
        return full
    return None


def _match_excerpt(m: re.Match | None) -> str:
    return repr(m.group(0)[:200]) if m else ""


def run_check(c: dict, wt: Path, reply: str, base: str) -> dict:
    t = c["type"]
    rec = {"type": t, "passed": False, "evidence": ""}
    for k in ("path", "regex", "run"):
        if k in c:
            rec[k] = c[k]
    if t == "command":
        r = spawn(c["run"], cwd=wt, env=filtered_env(),
                  timeout=c.get("timeout_s", CHECK_TIMEOUT_S), shell=True)
        want = c.get("expect_exit", 0)
        ev = [f"exit {r['rc']} (want {want})"]
        ok = not r["timed_out"] and r["rc"] == want
        if r["timed_out"]:
            ev.append("timed out")
        if "expect_stdout_regex" in c:
            m = re.search(c["expect_stdout_regex"], r["stdout"])
            ev.append(f"stdout /{c['expect_stdout_regex']}/ "
                      + (f"matched {_match_excerpt(m)}" if m else "did not match"))
            ok = ok and m is not None
        ev.append(f"stdout: {_tail(r['stdout'].strip(), 300)!r}")
        if r["stderr"].strip():
            ev.append(f"stderr: {_tail(r['stderr'].strip(), 300)!r}")
        rec["passed"], rec["evidence"] = ok, "; ".join(ev)
        return rec

    if t in ("output_regex", "output_not_regex"):
        m = re.search(c["regex"], reply)
        if t == "output_regex":
            rec["passed"] = m is not None
            rec["evidence"] = f"matched {_match_excerpt(m)}" if m else "no match in reply"
        else:
            rec["passed"] = m is None
            rec["evidence"] = f"forbidden match {_match_excerpt(m)}" if m else "absent from reply"
        return rec

    rel = c["path"]
    full = wt / rel
    if t == "file_absent":
        present = os.path.lexists(full)
        rec["passed"] = not present
        rec["evidence"] = "present" if present else "absent"
        return rec

    safe = _inside(wt, rel)
    if t == "file_contains":
        if not os.path.lexists(full):
            rec["evidence"] = "file does not exist"
        elif safe is None:
            rec["evidence"] = "path resolves outside the scratch checkout"
        elif not full.is_file():
            rec["evidence"] = "not a regular file"
        else:
            text = full.read_text(encoding="utf-8", errors="replace")
            m = re.search(c["regex"], text)
            rec["passed"] = m is not None
            rec["evidence"] = (f"matched {_match_excerpt(m)}" if m
                               else f"no match in {len(text)} chars")
        return rec

    # file_unchanged — byte comparison against the blob at `base`. No filters run.
    at_base = git(["cat-file", "-e", f"{base}:{rel}"], wt).returncode == 0
    exists = os.path.lexists(full)
    if not at_base:
        rec["passed"] = not exists
        rec["evidence"] = ("absent at base and now present" if exists
                           else "absent at base and still absent")
        return rec
    if not exists:
        rec["evidence"] = "deleted"
        return rec
    blob = git(["show", "--no-ext-diff", "--no-textconv", f"{base}:{rel}"], wt, binary=True)
    if blob.returncode != 0:
        rec["evidence"] = "could not read the base blob: " + blob.stderr.decode(
            "utf-8", "replace").strip()[:200]
        return rec
    if full.is_symlink():
        now = os.readlink(full).encode()
    elif safe is None or not full.is_file():
        rec["evidence"] = "no longer a regular file inside the checkout"
        return rec
    else:
        now = full.read_bytes()
    rec["passed"] = now == blob.stdout
    rec["evidence"] = ("identical to base" if rec["passed"]
                       else f"differs from base ({len(blob.stdout)} -> {len(now)} bytes)")
    return rec


def run_checks(ev: dict, wt: Path, reply: str, base: str) -> list[dict]:
    return [run_check(c, wt, reply, base) for c in ev["checks"]]


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------

def resolve_claude(spec: str) -> str:
    if os.sep in spec or (os.altsep and os.altsep in spec):
        if os.path.isfile(spec) and os.access(spec, os.X_OK):
            return spec
        raise EvalError(f"claude binary not found or not executable: {spec}")
    found = shutil.which(spec)
    if not found:
        raise EvalError(f"claude binary '{spec}' not found on PATH")
    return found


def claude_version(claude: str) -> str | None:
    r = spawn([claude, "--version"], cwd=os.getcwd(), env=claude_env(), timeout=20)
    if r["rc"] == 0 and r["stdout"].strip():
        return r["stdout"].strip().splitlines()[0]
    return None


def claude_argv(claude: str, ev: dict) -> list[str]:
    argv = [claude, "-p", ev["prompt"]]
    if ev["allowed_tools"]:
        argv += ["--allowedTools", ",".join(ev["allowed_tools"])]
    return argv + ["--output-format", "json"]


def parse_reply(stdout: str) -> tuple[str, dict, str | None]:
    """(reply text, the parsed object or {}, note or None)."""
    text = stdout.strip()
    candidates = [text] + ([text.splitlines()[-1]] if "\n" in text else [])
    for cand in candidates:
        try:
            data = json.loads(cand)
        except ValueError:
            continue
        if isinstance(data, dict) and "result" in data:
            res = data["result"]
            return ("" if res is None else res if isinstance(res, str) else json.dumps(res),
                    data, None)
        return stdout, {}, "stdout was JSON without a 'result' field; checks ran on the raw text"
    return stdout, {}, "stdout was not JSON; checks ran against the raw text"


def run_eval(f: Path, ev: dict, root: Path, claude: str, keep: bool,
             hide: list[str] | None = None) -> dict:
    res: dict = {"id": ev["id"], "file": str(f), "status": "errored", "checks": [],
                 "notes": [], "error": None, "claude_exit": None, "duration_s": 0.0}
    start = time.monotonic()
    timeout = ev.get("timeout_s", DEFAULT_TIMEOUT_S)
    sc = Scratch(root, keep=keep, hide=hide)
    try:
        with sc:
            if keep:
                res["scratch"] = str(sc.path)
            err, _ = run_shell_list(ev.get("setup", []), sc.path, timeout, "setup")
            if err:
                res["error"] = err
                return res
            base = head_sha(sc.path)
            r = spawn(claude_argv(claude, ev), cwd=sc.path, env=claude_env(), timeout=timeout)
            res["claude_exit"] = r["rc"]
            if r["spawn_error"]:
                res["error"] = f"could not start claude: {r['spawn_error']}"
                return res
            if r["timed_out"]:
                res["error"] = f"claude timed out after {timeout}s"
                return res
            reply, data, note = parse_reply(r["stdout"])
            if note:
                res["notes"].append(note)
            for k in ("num_turns", "total_cost_usd", "is_error", "subtype"):
                if k in data:
                    res[k] = data[k]
            res["reply_excerpt"] = reply[:EXCERPT]
            if r["rc"] != 0:
                res["error"] = f"claude exited {r['rc']}"
                if r["stderr"].strip():
                    res["error"] += f": {_tail(r['stderr'].strip(), 300)}"
                return res
            res["checks"] = run_checks(ev, sc.path, reply, base)
            res["status"] = "passed" if all(c["passed"] for c in res["checks"]) else "failed"
            return res
    except RuntimeError as e:
        res["error"] = str(e)
        return res
    finally:
        res["duration_s"] = round(time.monotonic() - start, 2)
        if sc.cleaned_refs:
            res["cleaned_refs"] = sc.cleaned_refs
        if sc.leaked_refs:
            res["leaked_refs"] = sc.leaked_refs
            res["notes"].append("new branch refs left behind: " + ", ".join(sc.leaked_refs))


def load_baseline(path: Path) -> dict[str, str]:
    """{id: status} from a `baseline` file ({per_id}) or a raw `run` summary ({results}).

    The comparison is per eval, over the ids both runs share, so a baseline with no
    per-id record cannot be compared at all and is refused rather than read as empty."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise EvalError(f"unreadable baseline {path}: {e}")
    if not isinstance(data, dict):
        raise EvalError(f"{path}: baseline must be a JSON object")
    per_id = data.get("per_id")
    if per_id is None:
        per_id = {r.get("id"): r.get("status") for r in data.get("results") or []
                  if isinstance(r, dict)}
    if not isinstance(per_id, dict) or not per_id or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in per_id.items()):
        raise EvalError(f"{path}: baseline needs a non-empty per_id {{id: status}}")
    return per_id


def gate_failures(summary: dict, min_rate: float | None, tolerance: float,
                  allow_regressions: bool = False) -> list[str]:
    fails = []
    rate = summary["pass_rate"]
    if min_rate is not None and rate + EPS < min_rate:
        fails.append(f"pass rate {rate:.3f} < --min-pass-rate {min_rate:.3f}")
    if not summary.get("baseline_used"):
        return fails
    base = summary["baseline_pass_rate"]
    if base is None:
        fails.append("no eval in common with the baseline; the comparison checked nothing")
        return fails
    cur = summary["current_pass_rate_on_common"]
    if cur + EPS < base - tolerance:
        fails.append(f"pass rate on {summary['common']} common eval(s) {cur:.3f} < "
                     f"baseline {base:.3f} − tolerance {tolerance:.3f}")
    if summary["regressions"] and not allow_regressions:
        fails.append("regressed since the baseline: " + ", ".join(summary["regressions"]))
    return fails


def summarize(results: list[dict], started_at: str, version: str | None,
              baseline: dict[str, str] | None) -> dict:
    ran = len(results)
    passed = sum(r["status"] == "passed" for r in results)
    failed = sum(r["status"] == "failed" for r in results)
    errored = sum(r["status"] == "errored" for r in results)
    rate = round(passed / ran, 6) if ran else 0.0
    s = {"ran": ran, "passed": passed, "failed": failed, "errored": errored,
         "pass_rate": rate, "results": results, "baseline_used": baseline is not None,
         "baseline_pass_rate": None, "current_pass_rate_on_common": None, "delta": None,
         "common": 0, "added_ids": [], "removed_ids": [], "regressions": [],
         "started_at": started_at, "finished_at": now_iso(), "claude_version": version}
    if baseline is not None:
        now = {r["id"]: r["status"] for r in results}
        common = sorted(set(now) & set(baseline))
        s["common"] = len(common)
        s["added_ids"] = sorted(set(now) - set(baseline))
        s["removed_ids"] = sorted(set(baseline) - set(now))
        s["regressions"] = [i for i in common
                            if baseline[i] == "passed" and now[i] != "passed"]
        if common:
            brate = round(sum(baseline[i] == "passed" for i in common) / len(common), 6)
            crate = round(sum(now[i] == "passed" for i in common) / len(common), 6)
            s["baseline_pass_rate"] = brate
            s["current_pass_rate_on_common"] = crate
            s["delta"] = round(crate - brate, 6)
    return s


def _first_failure(r: dict) -> str:
    if r.get("error"):
        return r["error"]
    for c in r["checks"]:
        if not c["passed"]:
            what = c.get("path") or c.get("regex") or c.get("run") or ""
            return f"{c['type']} {what}: {c['evidence']}"[:160]
    return "; ".join(r.get("notes") or [])[:160]


def render_table(s: dict) -> str:
    lines = [f"{'STATUS':<8} {'ID':<32} {'CHECKS':>6} {'TIME':>7}  NOTE"]
    for r in s["results"]:
        n = len(r["checks"])
        ok = sum(c["passed"] for c in r["checks"])
        chk = f"{ok}/{n}" if n else "-"
        lines.append(f"{r['status']:<8} {r['id'][:32]:<32} {chk:>6} {r['duration_s']:>6.1f}s"
                     f"  {_first_failure(r)}")
    line = (f"agent-evals: ran {s['ran']}, passed {s['passed']}, failed {s['failed']}, "
            f"errored {s['errored']}, pass rate {s['pass_rate'] * 100:.1f}%")
    lines.append(line)
    if s["baseline_used"]:
        if s["baseline_pass_rate"] is None:
            lines.append("baseline: no eval in common with this run")
        else:
            lines.append(f"baseline: {s['current_pass_rate_on_common'] * 100:.1f}% now vs "
                         f"{s['baseline_pass_rate'] * 100:.1f}% on {s['common']} common "
                         f"eval(s), delta {s['delta'] * 100:+.1f}pp")
        if s["added_ids"]:
            lines.append("added since baseline (not compared): " + ", ".join(s["added_ids"]))
        if s["removed_ids"]:
            lines.append("removed since baseline: " + ", ".join(s["removed_ids"]))
    if s["regressions"]:
        lines.append("regressions (passed in baseline, not now): " + ", ".join(s["regressions"]))
    return "\n".join(lines)


def cmd_run(args) -> int:
    if not (0 <= args.tolerance <= 1):
        raise EvalError("--tolerance must be in [0, 1]")
    if args.min_pass_rate is not None and not (0 <= args.min_pass_rate <= 1):
        raise EvalError("--min-pass-rate must be in [0, 1]")
    if args.gate and args.min_pass_rate is None and args.baseline is None:
        raise EvalError("--gate needs --min-pass-rate and/or --baseline; "
                        "a gate with no threshold checks nothing")
    evals, problems = load_evals(args.evals)
    if problems:
        for p in problems:
            print(f"  ERROR {p}", file=sys.stderr)
        print(f"agent-evals: {len(problems)} schema problem(s); nothing ran", file=sys.stderr)
        return 2
    evals = select(evals, args.only)
    claude = resolve_claude(args.claude)
    root = repo_root(args.repo)
    baseline = load_baseline(args.baseline) if args.baseline else None
    started = now_iso()
    version = claude_version(claude)
    hidden = resolve_hidden(root, args.evals, args.hide, not args.no_hide_evals_dir)
    results = [run_eval(f, ev, root, claude, args.keep, hidden) for f, ev in evals]
    s = summarize(results, started, version, baseline)
    s["hidden_paths"] = hidden
    fails = gate_failures(s, args.min_pass_rate, args.tolerance, args.allow_regressions)
    s["gate"] = {"enabled": bool(args.gate), "min_pass_rate": args.min_pass_rate,
                 "tolerance": args.tolerance, "allow_regressions": args.allow_regressions,
                 "failures": fails}
    table = render_table(s)
    if fails:
        table += "\n" + ("gate: FAIL — " if args.gate else "advisory: ") + "; ".join(fails)
    elif args.gate:
        table += "\ngate: pass"
    if args.json == "-":
        print(table, file=sys.stderr)
        print(json.dumps(s, indent=2))
    else:
        print(table)
        if args.json:
            Path(args.json).write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
    return 1 if (args.gate and fails) else 0


# --------------------------------------------------------------------------
# validate / prove
# --------------------------------------------------------------------------

def prove_eval(ev: dict, root: Path, hide: list[str] | None = None) -> dict:
    """Two arms, two fresh scratch checkouts, no model. The checks must fail when
    nothing is done and pass when the reference is done."""
    out: dict = {"id": ev["id"], "status": "proven", "problems": [],
                 "noop_checks": [], "reference_checks": []}
    timeout = ev.get("timeout_s", DEFAULT_TIMEOUT_S)
    try:
        with Scratch(root, hide=hide) as sc:
            err, _ = run_shell_list(ev.get("setup", []), sc.path, timeout, "setup")
            if err:
                out["problems"].append(f"no-op arm: {err}")
            else:
                out["noop_checks"] = run_checks(ev, sc.path, "", head_sha(sc.path))
                if all(c["passed"] for c in out["noop_checks"]):
                    out["problems"].append(
                        "does not discriminate: every check passes when nothing is done")
        with Scratch(root, hide=hide) as sc:
            err, _ = run_shell_list(ev.get("setup", []), sc.path, timeout, "setup")
            if err:
                out["problems"].append(f"reference arm: {err}")
            else:
                base = head_sha(sc.path)
                err, stdout = run_shell_list(ev["reference"], sc.path, timeout, "reference")
                if err:
                    out["problems"].append(f"reference arm: {err}")
                else:
                    out["reference_checks"] = run_checks(ev, sc.path, stdout, base)
                    for i, c in enumerate(out["reference_checks"]):
                        if not c["passed"]:
                            what = c.get("path") or c.get("regex") or c.get("run") or ""
                            out["problems"].append(
                                f"reference arm: checks[{i}] {c['type']} {what} failed: "
                                f"{c['evidence']}")
    except RuntimeError as e:
        out["problems"].append(str(e))
    if out["problems"]:
        out["status"] = "failed"
    return out


def cmd_validate(args) -> int:
    evals, problems = load_evals(args.evals)
    evals = select(evals, args.only)
    warnings: list[str] = []
    proofs: list[dict] = []
    for f, ev in evals:
        if "reference" not in ev:
            msg = f"{f}: eval '{ev['id']}' has no 'reference' — unproven"
            if args.require_reference:
                problems.append(msg)
            elif args.prove:
                warnings.append(msg)
    hidden: list[str] = []
    if args.prove:
        root = repo_root(args.repo)
        hidden = resolve_hidden(root, args.evals, args.hide, not args.no_hide_evals_dir)
        for f, ev in evals:
            if "reference" not in ev:
                continue
            pr = prove_eval(ev, root, hidden)
            proofs.append(pr)
            problems += [f"{f}: eval '{ev['id']}': {p}" for p in pr["problems"]]
    if args.json:
        print(json.dumps({"evals": len(evals), "problems": problems, "warnings": warnings,
                          "proofs": proofs, "hidden_paths": hidden}, indent=2))
    else:
        for p in problems:
            print(f"  ERROR {p}")
        for w in warnings:
            print(f"  WARN  {w}")
        proven = sum(p["status"] == "proven" for p in proofs)
        tail = f", {proven}/{len(proofs)} proven" if args.prove else ""
        print(f"agent-evals: {len(evals)} eval(s), {len(problems)} problem(s), "
              f"{len(warnings)} warning(s){tail}")
    return 1 if problems else 0


def cmd_baseline(args) -> int:
    try:
        data = json.loads(args.results.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise EvalError(f"unreadable results {args.results}: {e}")
    if not isinstance(data, dict) or not _is_num(data.get("pass_rate")) \
            or not isinstance(data.get("results"), list):
        raise EvalError(f"{args.results}: not a `run` summary (needs pass_rate and results)")
    if not data["results"]:
        raise EvalError(f"{args.results}: zero evals ran — refusing to freeze an empty baseline")
    out = {"pass_rate": data["pass_rate"],
           "per_id": {r["id"]: r["status"] for r in data["results"]},
           "recorded_at": now_iso()}
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"baseline: pass rate {out['pass_rate']:.3f} over {len(out['per_id'])} eval(s) "
          f"-> {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bstack-evals", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run every eval through `claude -p` and score it")
    r.add_argument("evals", type=Path, help="directory of *.json evals (or one file)")
    r.add_argument("--repo", type=Path, default=Path("."), help="repo to check out (default cwd)")
    r.add_argument("--claude", default="claude", help="claude binary (default: claude on PATH)")
    r.add_argument("--only", action="append", metavar="ID", help="run only this id (repeatable)")
    r.add_argument("--json", metavar="OUT", help="write the summary JSON here ('-' = stdout)")
    r.add_argument("--baseline", type=Path, help="baseline file (or earlier run summary)")
    r.add_argument("--min-pass-rate", type=float, metavar="F")
    r.add_argument("--tolerance", type=float, default=0.0, metavar="F",
                   help="allowed drop below the baseline pass rate (default 0)")
    r.add_argument("--gate", action="store_true", help="exit 1 when a threshold is missed")
    r.add_argument("--allow-regressions", action="store_true",
                   help="with --gate, do not fail on evals that passed in the baseline "
                        "and fail now (the rate checks still apply)")
    r.add_argument("--keep", action="store_true", help="leave scratch worktrees in place")


    v = sub.add_parser("validate", help="check eval files; --prove checks they discriminate")
    v.add_argument("evals", type=Path)
    v.add_argument("--prove", action="store_true",
                   help="run each `reference` eval in a no-op arm and a reference arm")
    v.add_argument("--require-reference", action="store_true",
                   help="an eval with no `reference` is an error, not a warning")
    v.add_argument("--only", action="append", metavar="ID")
    v.add_argument("--repo", type=Path, default=Path("."), help="repo for --prove (default cwd)")
    v.add_argument("--json", action="store_true", help="machine-readable output")

    b = sub.add_parser("baseline", help="freeze a run summary into a baseline file")
    b.add_argument("results", type=Path)
    b.add_argument("--out", required=True)

    for sp in (r, v):
        sp.add_argument("--hide", action="append", metavar="PATH",
                        help="keep PATH (repo-relative) out of the scratch entirely: "
                             "no file, ref, history or object (repeatable)")
        sp.add_argument("--no-hide-evals-dir", action="store_true",
                        help="do not hide the evals dir when it lies inside the repo")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "run":
            return cmd_run(args)
        if args.cmd == "validate":
            return cmd_validate(args)
        return cmd_baseline(args)
    except EvalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
