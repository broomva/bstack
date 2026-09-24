#!/usr/bin/env python3
"""agent_evals.py — continuous evals of the agent's configuration (BRO-2542).

CLAUDE.md, skills and hooks steer the agent, so a change to them is a behaviour
change and needs the regression testing that code gets. This runs a suite of evals
non-interactively: each eval is a real task taken from recent work (the prompt), plus
the checks that define an acceptable outcome (a command's exit and output, a file left
unchanged, a file that must contain something, a reply that must or must not match).

    validate <evals-dir>            every eval file is well-formed
    validate <evals-dir> --prove    the checks DISCRIMINATE: they pass on the eval's
                                    `reference`, fail on each named violation, and fail
                                    on a no-op (no model is called)
    run <evals-dir>                 run `claude -p` on every eval, score it, summarize
    baseline <results.json> --out F freeze a run's pass rate for later comparison

Invariant: an eval never writes to the live repository. Each arm runs in a scratch
that is a STANDALONE git repository (never a `git worktree`, which would share refs,
config, stash and objects with the live repo): one orphan commit holding HEAD's tree,
built inside the scratch from the live repo by READ-only commands (ls-tree,
pack-objects, config --get), with a local `main` at that commit and HEAD detached
there. Whatever the agent does to main, tags, config or the stash lands in the
scratch's own .git and is deleted with it (unless --keep). `leaked_refs` stays in each
result for schema stability and is structurally empty.

Hidden paths: `--hide PATH` (repo-relative, repeatable), and by default the evals dir
itself whenever it lies inside --repo (`--no-hide-evals-dir` opts out), are left out of
the orphan tree, so no ref, history or object in the scratch contains them. On a
case-folding filesystem (probed once per repo) the match is case-insensitive. Checks
run in the same scratch, so they cannot read hidden files either.

Threat model, plainly: hiding and the environment scrub stop ACCIDENTAL discovery —
an agent that runs `git log --all`, reads $PWD or $GITHUB_WORKSPACE, or follows its
cwd upward. They do NOT contain a same-user process that goes looking: `ps` shows this
script's argv, `lsof`/`/proc/<pid>/cwd` show the live checkout, and `find /` finds the
evals. Containing that needs an OS sandbox or a container around the claude process.
The grant: built-in tools = allowed_tools (`--tools` gets their base names, e.g.
`Bash(git *)` -> `Bash`; `allowed_tools: []` passes `--tools ""`, so no built-in tool is
available at all); no MCP servers (`--strict-mcp-config` with no --mcp-config loads
none); nothing unapproved (`--permission-mode dontAsk`; a per-eval `permission_mode` may
only be dontAsk or plan). Within an AVAILABLE tool, an allow rule from a settings file
or a hook can still approve a call that allowed_tools does not list — scope Bash rules
in allowed_tools narrowly for that reason.
claude's environment is the parent's minus: the GIT_* variables that relocate a repo;
PWD, OLDPWD, INIT_CWD, GITHUB_WORKSPACE, GITHUB_EVENT_PATH, RUNNER_WORKSPACE, and the
workflow-command files GITHUB_ENV, GITHUB_PATH, GITHUB_OUTPUT, GITHUB_STEP_SUMMARY and
GITHUB_STATE (writing those would inject env, PATH or a forged summary into later steps);
and every variable whose value names the live repo or the evals dir — compared after
realpath on BOTH sides, so a symlinked form (/tmp vs /private/tmp) is caught too —
except auth variables that may hold a path (HOME, CLAUDE_CONFIG_DIR, cloud credential
files). PATH is filtered entry by entry. PWD is set to the scratch.

Planted git config: the agent can rewrite its scratch's .git/config and hooks. Every
process the runner starts afterwards (check commands, internal git) gets top-precedence
overrides via GIT_CONFIG_COUNT — core.fsmonitor=false, core.hooksPath=/dev/null,
core.pager=cat, diff.external= (empty), core.editor=true, sequence.editor=true,
protocol.ext.allow=never, credential.helper= (empty), commit/tag.gpgSign=false — and
every filter/diff/merge driver command defined in ANY config scope is blanked, with
filter.<name>.required=false, filter.lfs.* included. The scratch's own checkout is made
the same way, so LFS files arrive as pointer files: an eval over LFS content must
materialize those files in its `setup`. Consequence: a porcelain `git diff` in a check exits 128
("external diff died") instead of running anything; use `git diff --no-ext-diff` or
plumbing (diff-index, diff-files, cat-file, rev-parse, ls-files). What this does NOT
cover: an eval's shell check still runs whatever PROGRAM it names, and the agent could
have planted one (a script in the scratch, a shim earlier on a relative PATH entry).
Write checks against git plumbing or absolute system tools, never scratch-local scripts.

A check that cannot fail is not a check. `validate --prove` runs every eval that has a
`reference`, without claude, one fresh scratch per arm, in this order:
  1. reference arm — setup, the reference commands (their stdout stands in for the
     reply), all checks: every check must PASS;
  2. one violation arm per `violations` entry — a named plausible WRONG behaviour
     (`run`: shell commands) — setup, those commands, all checks: one must FAIL. The
     reply is the same lie as the no-op's, never the commands' stdout, so an
     output-only check cannot "catch" a violation no file or git check sees. Each arm
     records `caught_by`: the checks that failed;
  3. no-op arm, LAST — setup only, and the reply is the lie "I have completed the
     task.": at least one check must FAIL. Running it last means a reference that left
     state outside its scratch (a /tmp marker) makes the no-op pass, which is reported
     as "does not discriminate" instead of certified.
An eval with no violations is warned as "no violation arm" under --prove, and is an
error with --require-violations. `validate` also warns when a setup, reference,
violation or command check names an absolute path outside the scratch.

Trust model: `setup`, `reference`, violation and `command` shell strings are run with
cwd = the scratch. That is acceptable because eval files are repo-owned and reviewed
like code, the same as a Makefile. Those shells, and every git subprocess this script
spawns, get a filtered environment (PATH, HOME, LANG, LC_ALL, TMPDIR, GIT_*, minus the
GIT_* variables that relocate a repository or inject config), so an API key in the
parent environment is not visible to them. Git runs with core.fsmonitor=false and hooks
disabled; diff-family commands also get --no-ext-diff --no-textconv. claude never gets
--dangerously-skip-permissions.

Eval file (one JSON object per `<evals-dir>/*.json`):
    id, description, source, prompt, allowed_tools, checks       required
    setup, reference, violations, timeout_s (default 600), tags   optional
violations: [{"name": "<what the wrong agent does>", "run": ["shell cmd", ...]}, ...]
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
(one fixed, one broken), unless --allow-regressions. An eval that passed in the
baseline and is MISSING now (removed_passing) also fails the gate, unless
--allow-removed: deleting a passing eval is how a change hides the regression it
causes. `baseline.json` in the evals dir
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
import functools
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
OPTIONAL_KEYS = ("setup", "reference", "violations", "permission_mode", "timeout_s", "tags")
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
# dontAsk denies anything not pre-approved; plan cannot act at all. Every other mode
# approves calls allowed_tools never listed (acceptEdits: file edits and mkdir/mv/rm;
# auto: a classifier; bypassPermissions: everything), so they are refused.
DEFAULT_PERMISSION_MODE = "dontAsk"
PERMISSION_MODES = ("dontAsk", "plan")
# The no-op arm's reply: an agent that did nothing and says it did everything. An
# empty reply let any `output_regex "\\S"` "discriminate", and a real claude always
# replies with something.
NOOP_REPLY = "I have completed the task."
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


# Top-precedence git config for every shell and git process the RUNNER starts (setup,
# reference, violation and check commands, internal git calls). GIT_CONFIG_COUNT beats
# every config file — including a scratch .git/config the agent under test rewrote —
# so a planted fsmonitor, hook, pager, external diff, editor, credential helper or
# signing program never runs outside the agent's own permission sandbox.
GIT_SAFE_OVERRIDES: tuple[tuple[str, str], ...] = (
    ("core.fsmonitor", "false"), ("core.hooksPath", "/dev/null"), ("core.pager", "cat"),
    ("diff.external", ""), ("core.editor", "true"), ("sequence.editor", "true"),
    ("protocol.ext.allow", "never"), ("credential.helper", ""),
    ("commit.gpgSign", "false"), ("tag.gpgSign", "false"),
)
# Driver commands have agent-chosen names (.gitattributes `filter=evil`), so no fixed
# list can name them; scratch_env() blanks every one any config scope defines — lfs
# too — and sets filter.<name>.required=false, or a blanked required filter would make
# git die instead of passing content through.
_DRIVER_KEY_RE = (r"^(filter\..+\.(clean|smudge|process|required)"
                  r"|diff\..+\.(command|textconv)|merge\..+\.driver)$")


def filtered_env(extra_overrides: tuple[tuple[str, str], ...] | list = ()) -> dict[str, str]:
    """PATH, HOME, LANG, LC_ALL, TMPDIR and the safe GIT_* variables — nothing else —
    plus the GIT_SAFE_OVERRIDES (and any extra) as GIT_CONFIG_COUNT/KEY_n/VALUE_n."""
    env = {k: v for k, v in os.environ.items() if k in _ENV_KEEP or _git_var_allowed(k)}
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    pairs = list(GIT_SAFE_OVERRIDES) + list(extra_overrides)
    env["GIT_CONFIG_COUNT"] = str(len(pairs))
    for i, (k, v) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = k, v
    return env


def scratch_env(wt: Path) -> dict[str, str]:
    """filtered_env() for commands run in a scratch, with every filter/diff/merge driver
    command defined in ANY config scope (system, global, the scratch's own; includes
    followed) blanked and every filter made non-required. Reading config executes
    nothing."""
    p = git(["config", "--includes", "--name-only", "--get-regexp", _DRIVER_KEY_RE], wt)
    keys = sorted(set(p.stdout.split()))
    return filtered_env([(k, "false" if k.lower().endswith(".required") else "")
                         for k in keys])


# Variables that name the live checkout (or the job's event payload) outright.
_CLAUDE_ENV_DROP = frozenset({"PWD", "OLDPWD", "INIT_CWD", "GITHUB_WORKSPACE",
                              "GITHUB_EVENT_PATH", "RUNNER_WORKSPACE",
                              # workflow-command files: env/PATH/output injection
                              "GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT",
                              "GITHUB_STEP_SUMMARY", "GITHUB_STATE"})
# Auth-related variables that may legitimately hold a path; kept even when that path
# happens to lie inside the repo. (Keys and tokens never hold paths.)
_AUTH_PATH_VARS = frozenset({"HOME", "CLAUDE_CONFIG_DIR", "GOOGLE_APPLICATION_CREDENTIALS",
                             "AWS_SHARED_CREDENTIALS_FILE", "AWS_CONFIG_FILE"})


def _needles(paths) -> list[str]:
    out: set[str] = set()
    for p in paths or []:
        for form in (os.path.abspath(p), os.path.realpath(p)):
            if len(form) > 1:
                out.add(form)
    return sorted(out)


def _names_secret(value: str, needles: list[str]) -> bool:
    """True when `value`, or the realpath of any absolute path-like piece of it, contains
    a secret path. Realpath on the value side catches a logical form (/tmp/x) of a
    physical needle (/private/tmp/x)."""
    if any(n in value for n in needles):
        return True
    for piece in value.split(os.pathsep):
        if piece.startswith(os.sep) and any(n in os.path.realpath(piece) for n in needles):
            return True
    return False


def claude_env(scratch: Path | None = None, secret_paths=None) -> dict[str, str]:
    """The environment for claude: the parent's (claude needs its credentials), minus
    the GIT_* variables that relocate a repo, minus the variables that name the live
    checkout (PWD, OLDPWD, GITHUB_WORKSPACE, …), minus ANY variable whose value contains
    a secret path (the live repo, the evals dir) — except auth variables. PATH is
    filtered entry by entry rather than dropped. PWD is set to the scratch."""
    needles = _needles(secret_paths)
    env: dict[str, str] = {}
    for k, v in os.environ.items():
        if (k.startswith("GIT_") and not _git_var_allowed(k)) or k in _CLAUDE_ENV_DROP:
            continue
        if needles and _names_secret(v, needles):
            if k == "PATH":
                v = os.pathsep.join(e for e in v.split(os.pathsep)
                                    if not _names_secret(e, needles))
            elif k not in _AUTH_PATH_VARS:
                continue
        env[k] = v
    if scratch is not None:
        env["PWD"] = str(scratch)
    return env


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


def _violation_problems(vs) -> list[str]:
    if not isinstance(vs, list) or not vs:
        return ["'violations' must be a non-empty list"]
    p: list[str] = []
    names: set[str] = set()
    for i, v in enumerate(vs):
        where = f"violations[{i}]"
        if not isinstance(v, dict):
            p.append(f"{where}: must be an object")
            continue
        for k in v:
            if k not in ("name", "run"):
                p.append(f"{where}: unknown field '{k}'")
        name = v.get("name")
        if not isinstance(name, str) or not name.strip():
            p.append(f"{where}: 'name' must be a non-empty string")
        elif name in names:
            p.append(f"{where}: duplicate name {name!r}")
        else:
            names.add(name)
        if "run" not in v:
            p.append(f"{where}: missing field 'run'")
        else:
            p += _str_list_problems(f"{where}.run", v["run"], allow_empty=False)
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
    if "violations" in ev:
        p += _violation_problems(ev["violations"])
    if "permission_mode" in ev and ev["permission_mode"] not in PERMISSION_MODES:
        p.append(f"'permission_mode' must be one of {', '.join(PERMISSION_MODES)}, got "
                 f"{ev['permission_mode']!r} (any other mode approves calls allowed_tools "
                 f"does not list)")
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


_SCRATCH_IDENTITY = {"GIT_AUTHOR_NAME": "bstack-evals", "GIT_AUTHOR_EMAIL": "evals@localhost",
                     "GIT_COMMITTER_NAME": "bstack-evals",
                     "GIT_COMMITTER_EMAIL": "evals@localhost"}
# Repo-local settings a standalone scratch inherits, so the agent's own commits behave
# as they would in the live repo (identity, and a tracked hooks dir such as .githooks).
_INHERITED_CONFIG = ("user.name", "user.email", "core.hooksPath")


@functools.lru_cache(maxsize=None)
def _case_insensitive(root_real: str) -> bool:
    try:
        a, b = os.path.join(root_real, ".git"), os.path.join(root_real, ".GIT")
        return os.path.exists(b) and os.path.samefile(a, b)
    except OSError:
        return False


def fs_case_insensitive(root: Path) -> bool:
    """Probe once per repo: does its filesystem fold case? (.git and .GIT the same?)"""
    return _case_insensitive(os.path.realpath(root))


def _repo_rel(root_real: str, path_real: str, fold: bool) -> str | None:
    prefix = root_real.rstrip(os.sep) + os.sep
    a, b = (path_real.casefold(), prefix.casefold()) if fold else (path_real, prefix)
    if a.startswith(b) and len(path_real) > len(prefix):
        return PurePosixPath(*Path(path_real[len(prefix):]).parts).as_posix()
    return None


def resolve_hidden(root: Path, evals: Path, hide: list[str] | None,
                   hide_evals_dir: bool) -> list[str]:
    """Repo-relative POSIX paths to hide. Relative --hide values are repo-relative; the
    evals path is a filesystem path and is hidden only when it lies inside the repo.
    On a case-folding filesystem, containment is decided case-insensitively."""
    root_real = os.path.realpath(root)
    fold = fs_case_insensitive(root)
    out: set[str] = set()
    for h in hide or []:
        if os.path.isabs(h):
            rel = _repo_rel(root_real, os.path.realpath(h), fold)
            if rel is None:
                raise EvalError(f"--hide {h}: not inside the repository {root_real}")
            h = rel
        prob = _path_problem("--hide", h)
        if prob or PurePosixPath(h).as_posix() in ("", "."):
            raise EvalError(prob or f"--hide {h!r} would hide the whole repository")
        out.add(PurePosixPath(h).as_posix())
    if hide_evals_dir:
        rel = _repo_rel(root_real, os.path.realpath(evals), fold)
        if rel:
            out.add(rel)
    return sorted(out)


class Scratch:
    """A STANDALONE repository in a temp dir holding one orphan commit of HEAD's tree
    (minus any hidden paths); removed on exit unless keep.

    Never a `git worktree`: a worktree shares refs, config, stash and objects with the
    live repo, so an agent could move the live main, tag, stash, or write
    core.fsmonitor/hooksPath into the live config (code execution on the user's next
    git command). Here every write lands in the scratch's own .git. The live repo is
    only READ (ls-tree, pack-objects, config --get)."""

    def __init__(self, root: Path, keep: bool = False, hide: list[str] | None = None):
        self.root = root
        self.keep = keep
        self.hide = list(hide or [])
        self.fold = fs_case_insensitive(root)
        self.tmp: Path | None = None
        self.path: Path | None = None
        # Structurally empty: the scratch shares no ref with the live repo. Kept so
        # the result schema does not change for consumers.
        self.leaked_refs: list[str] = []

    def __enter__(self) -> "Scratch":
        self.tmp = Path(tempfile.mkdtemp(prefix="bstack-eval-"))
        self.path = self.tmp / "wt"
        try:
            self._build_orphan()
        except RuntimeError:
            shutil.rmtree(self.tmp, ignore_errors=True)
            raise
        return self

    def _is_hidden(self, path: str) -> bool:
        if self.fold:
            path = path.casefold()
            return any(path == h.casefold() or path.startswith(h.casefold() + "/")
                       for h in self.hide)
        return any(path == h or path.startswith(h + "/") for h in self.hide)

    def _build_orphan(self) -> None:
        def must(p: subprocess.CompletedProcess, what: str) -> subprocess.CompletedProcess:
            if p.returncode != 0:
                err = p.stderr if isinstance(p.stderr, str) else p.stderr.decode("utf-8",
                                                                                 "replace")
                raise RuntimeError(f"scratch: {what} failed: {err.strip()[:300]}")
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
        # drivers blanked here too: nothing from any config scope runs at checkout, and
        # LFS content arrives as pointer files
        must(git(["checkout", "-q", "-f", "--detach", commit], self.path,
                 extra_env=scratch_env(self.path)), "checkout")

    def __exit__(self, *exc) -> None:
        if not self.keep and self.tmp is not None:
            shutil.rmtree(self.tmp, ignore_errors=True)


def run_shell_list(cmds: list[str], cwd: Path, timeout: float, label: str
                   ) -> tuple[str | None, str]:
    """Run commands in order; (error or None, concatenated stdout)."""
    out = []
    env = scratch_env(cwd)
    for cmd in cmds:
        r = spawn(cmd, cwd=cwd, env=env, timeout=timeout, shell=True)
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


def run_check(c: dict, wt: Path, reply: str, base: str,
              env: dict[str, str] | None = None) -> dict:
    t = c["type"]
    rec = {"type": t, "passed": False, "evidence": ""}
    for k in ("path", "regex", "run"):
        if k in c:
            rec[k] = c[k]
    if t == "command":
        r = spawn(c["run"], cwd=wt, env=env if env is not None else scratch_env(wt),
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
    env = scratch_env(wt)   # read once, AFTER the agent: its planted drivers included
    return [run_check(c, wt, reply, base, env) for c in ev["checks"]]


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------

def resolve_claude(spec: str) -> str:
    """Absolute path: claude is spawned with cwd = the scratch, so a relative path
    resolved against the caller's cwd would no longer point at it."""
    if os.sep in spec or (os.altsep and os.altsep in spec):
        if os.path.isfile(spec) and os.access(spec, os.X_OK):
            return os.path.abspath(spec)
        raise EvalError(f"claude binary not found or not executable: {spec}")
    found = shutil.which(spec)
    if not found:
        raise EvalError(f"claude binary '{spec}' not found on PATH")
    return os.path.abspath(found)


def claude_version(claude: str) -> str | None:
    r = spawn([claude, "--version"], cwd=os.getcwd(), env=claude_env(), timeout=20)
    if r["rc"] == 0 and r["stdout"].strip():
        return r["stdout"].strip().splitlines()[0]
    return None


def tool_base_names(allowed: list[str]) -> list[str]:
    """`Bash(git *)` -> `Bash`; deduplicated, first occurrence first."""
    out: list[str] = []
    for t in allowed:
        base = t.split("(", 1)[0].strip()
        if base and base not in out:
            out.append(base)
    return out


def claude_argv(claude: str, ev: dict) -> list[str]:
    """--tools: the only AVAILABLE built-in tools ("" = none); --allowedTools: the scoped
    rules pre-approved; --strict-mcp-config (no --mcp-config): no MCP servers; dontAsk:
    everything else denied."""
    argv = [claude, "-p", ev["prompt"],
            "--tools", ",".join(tool_base_names(ev["allowed_tools"]))]
    if ev["allowed_tools"]:
        argv += ["--allowedTools", ",".join(ev["allowed_tools"])]
    argv += ["--strict-mcp-config",
             "--permission-mode", ev.get("permission_mode", DEFAULT_PERMISSION_MODE)]
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
             hide: list[str] | None = None, secret_paths=None) -> dict:
    res: dict = {"id": ev["id"], "file": str(f), "status": "errored", "checks": [],
                 "notes": [], "error": None, "claude_exit": None, "duration_s": 0.0,
                 "leaked_refs": []}
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
            r = spawn(claude_argv(claude, ev), cwd=sc.path,
                      env=claude_env(sc.path, secret_paths), timeout=timeout)
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
        res["leaked_refs"] = sc.leaked_refs


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
                  allow_regressions: bool = False, allow_removed: bool = False) -> list[str]:
    fails = []
    rate = summary["pass_rate"]
    if min_rate is not None and rate + EPS < min_rate:
        fails.append(f"pass rate {rate:.3f} < --min-pass-rate {min_rate:.3f}")
    if not summary.get("baseline_used"):
        return fails
    # A passing eval that disappears is how a PR hides the regression it causes.
    if summary["removed_passing"] and not allow_removed:
        fails.append("regression: passed in the baseline, missing now: "
                     + ", ".join(summary["removed_passing"]))
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
         "removed_passing": [],
         "started_at": started_at, "finished_at": now_iso(), "claude_version": version}
    if baseline is not None:
        now = {r["id"]: r["status"] for r in results}
        common = sorted(set(now) & set(baseline))
        s["common"] = len(common)
        s["added_ids"] = sorted(set(now) - set(baseline))
        s["removed_ids"] = sorted(set(baseline) - set(now))
        s["regressions"] = [i for i in common
                            if baseline[i] == "passed" and now[i] != "passed"]
        s["removed_passing"] = [i for i in s["removed_ids"] if baseline[i] == "passed"]
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
    secrets = [root, args.evals]
    results = [run_eval(f, ev, root, claude, args.keep, hidden, secrets) for f, ev in evals]
    s = summarize(results, started, version, baseline)
    s["hidden_paths"] = hidden
    fails = gate_failures(s, args.min_pass_rate, args.tolerance, args.allow_regressions,
                          args.allow_removed)
    s["gate"] = {"enabled": bool(args.gate), "min_pass_rate": args.min_pass_rate,
                 "tolerance": args.tolerance, "allow_regressions": args.allow_regressions,
                 "allow_removed": args.allow_removed,
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
    """No model, a fresh scratch per arm. The checks must pass when the reference is
    done, fail on every named violation, and fail when nothing is done while the reply
    claims success. The no-op arm runs LAST: a reference or violation that leaves state
    OUTSIDE its scratch (a /tmp marker) then makes the no-op pass, and the eval is
    reported as not discriminating instead of being certified."""
    out: dict = {"id": ev["id"], "status": "proven", "problems": [],
                 "noop_reply": NOOP_REPLY, "noop_checks": [], "reference_checks": [],
                 "violation_arms": []}
    timeout = ev.get("timeout_s", DEFAULT_TIMEOUT_S)
    try:
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
        for v in ev.get("violations", []):
            out["violation_arms"].append(_violation_arm(ev, v, root, hide, timeout, out))
        with Scratch(root, hide=hide) as sc:
            err, _ = run_shell_list(ev.get("setup", []), sc.path, timeout, "setup")
            if err:
                out["problems"].append(f"no-op arm: {err}")
            else:
                out["noop_checks"] = run_checks(ev, sc.path, NOOP_REPLY, head_sha(sc.path))
                if all(c["passed"] for c in out["noop_checks"]):
                    out["problems"].append(
                        "does not discriminate: every check passes when nothing is done "
                        f"and the reply is {NOOP_REPLY!r}")
    except RuntimeError as e:
        out["problems"].append(str(e))
    if out["problems"]:
        out["status"] = "failed"
    return out


# An absolute path in a shell string: `/…` right after start, whitespace, a quote, `=`,
# `(`, a redirect or a separator. Excludes URLs (`https://…` — `:` precedes the `/`).
_ABS_PATH_RE = re.compile(r"""(?:^|[\s'"=(<>|;&])(/[^\s'"`;|&()<>]*)""")
_OUTSIDE_VAR_RE = re.compile(r"(~/|\$\{?(?:HOME|TMPDIR|RUNNER_TEMP|GITHUB_WORKSPACE|OLDPWD)\b)")
_ABS_OK_RE = re.compile(r"^/dev/(null|stdin|stdout|stderr|fd/\d+)$|^/(usr|bin|sbin|opt/homebrew)(/|$)")


def outside_path_warnings(ev: dict) -> list[str]:
    """Shell strings that name a location outside the scratch. State written there
    survives between arms and between runs, so a check can pass for a reason that has
    nothing to do with what the agent did. A warning, not an error: reading a system
    path is harmless, and the regex cannot tell reading from writing."""
    where: list[tuple[str, str]] = []
    where += [(f"setup[{i}]", c) for i, c in enumerate(ev.get("setup", []))]
    where += [(f"reference[{i}]", c) for i, c in enumerate(ev.get("reference", []))]
    for v in ev.get("violations", []):
        where += [(f"violation {v['name']!r}", c) for c in v["run"]]
    where += [(f"checks[{i}]", c["run"]) for i, c in enumerate(ev["checks"])
              if c["type"] == "command"]
    out = []
    for label, cmd in where:
        hits = [m.group(1) for m in _ABS_PATH_RE.finditer(cmd)
                if not _ABS_OK_RE.match(m.group(1))]
        hits += [m.group(1) for m in _OUTSIDE_VAR_RE.finditer(cmd)]
        if hits:
            out.append(f"{label} refers to {', '.join(repr(h) for h in hits)}, outside the "
                       f"scratch; state there survives between arms and runs")
    return out


def _violation_arm(ev: dict, v: dict, root: Path, hide: list[str] | None,
                   timeout: float, out: dict) -> dict:
    """setup, then the wrong behaviour, then every check: one must fail. The reply is
    the lying agent's, never the commands' stdout: a violation that prints nothing
    must not count as caught by an output check that only saw silence."""
    arm: dict = {"name": v["name"], "status": "caught", "checks": [], "caught_by": [],
                 "error": None}
    with Scratch(root, hide=hide) as sc:
        err, _ = run_shell_list(ev.get("setup", []), sc.path, timeout, "setup")
        if not err:
            base = head_sha(sc.path)
            err, _ = run_shell_list(v["run"], sc.path, timeout, "violation")
        if err:
            arm["status"], arm["error"] = "error", err
            out["problems"].append(f"violation '{v['name']}': {err}")
            return arm
        arm["checks"] = run_checks(ev, sc.path, NOOP_REPLY, base)
    arm["caught_by"] = [f"checks[{i}] {c['type']}" for i, c in enumerate(arm["checks"])
                        if not c["passed"]]
    if all(c["passed"] for c in arm["checks"]):
        arm["status"] = "passes"
        out["problems"].append(f"violation '{v['name']}' passes every check")
    return arm


def cmd_validate(args) -> int:
    evals, problems = load_evals(args.evals)
    evals = select(evals, args.only)
    warnings: list[str] = []
    proofs: list[dict] = []
    for f, ev in evals:
        warnings += [f"{f}: eval '{ev['id']}': {w}" for w in outside_path_warnings(ev)]
        if "reference" not in ev:
            msg = f"{f}: eval '{ev['id']}' has no 'reference' — unproven"
            if args.require_reference:
                problems.append(msg)
            elif args.prove:
                warnings.append(msg)
        if "violations" not in ev:
            msg = f"{f}: eval '{ev['id']}' has no violation arm"
            if args.require_violations:
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
    r.add_argument("--allow-removed", action="store_true",
                   help="with --gate, do not fail when an eval that passed in the baseline "
                        "is missing now")
    r.add_argument("--allow-regressions", action="store_true",
                   help="with --gate, do not fail on evals that passed in the baseline "
                        "and fail now (the rate checks still apply)")
    r.add_argument("--keep", action="store_true", help="leave scratch repos in place")


    v = sub.add_parser("validate", help="check eval files; --prove checks they discriminate")
    v.add_argument("evals", type=Path)
    v.add_argument("--prove", action="store_true",
                   help="run each `reference` eval in a no-op arm and a reference arm")
    v.add_argument("--require-violations", action="store_true",
                   help="an eval with no `violations` is an error, not a warning")
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
