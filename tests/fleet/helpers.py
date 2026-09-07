"""Shared fixtures for the `bstack fleet` suite.

The stub binary is written by the tests (as `tests/wave/test_dispatch.py` does)
rather than shipped as a fixture file, so the contract it answers is visible in
the same file as the assertions about it.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

# Every fleet-relevant env var the suite must not inherit from the operator's
# shell (or leak into the next test).
FLEET_ENV = (
    "BSTACK_FLEET_STATE_DIR", "BSTACK_FLEET_BASE", "BSTACK_FLEET_PEER_CONTRACT",
    "BSTACK_FLEET_ALLOWED_TOOLS", "BSTACK_FLEET_TICKET_PATTERN",
    "BSTACK_FLEET_MCP", "BSTACK_FLEET_CLAUDE_BIN", "BSTACK_WAVE_CLAUDE_BIN",
    "BSTACK_STATE_DIR", "BSTACK_PEER_MCP",
)


@contextlib.contextmanager
def sandbox():
    """A temp dir plus a clean, restored environment.

    `BSTACK_STATE_DIR` is pointed at the temp dir so `~/.bstack/config.yaml` is
    never read (or written) by a test, and `BSTACK_FLEET_STATE_DIR` so no test
    can reach the operator's real `~/.cache/bstack/fleet`.
    """
    saved = {k: os.environ.get(k) for k in FLEET_ENV}
    with tempfile.TemporaryDirectory() as td:
        for k in FLEET_ENV:
            os.environ.pop(k, None)
        os.environ["BSTACK_STATE_DIR"] = td + "/bstack-state"
        os.environ["BSTACK_FLEET_STATE_DIR"] = td + "/fleet-state"
        try:
            yield Path(td)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def write_stub(td: Path) -> Path:
    """A stand-in for `claude` that answers like the real binary.

    Behaviour is steered by marker files in the same directory, so one stub
    serves every case:

      agents.json      — printed by `agents --json --all` (default `[]`)
      agents.fail      — `agents` exits 1 (an unreadable listing)
      spawn.fail       — a spawn exits 1 with a login-expired message
      fail-rm-<id>     — `rm <id>` exits 1 with a transient error
      gone-rm-<id>     — `rm <id>` exits 1 saying the session is not found

    It records, per spawn: the exact argv (NUL-separated, because the prompt is
    multi-line), the working directory it was launched in (`cwd-<n>.log`, so a
    "drop the spawn cwd" mutant cannot survive) and — the assertion that a
    "write state after spawning" mutant cannot survive — whether a `fleet.json`
    already existed when it was called. `stop`/`rm` calls append to `calls.log`.
    """
    d = str(td)
    stub = td / "fake-claude.sh"
    stub.write_text(
        "#!/bin/sh\n"
        f"D='{d}'\n"
        "if [ \"$1\" = agents ]; then\n"
        "  if [ -f \"$D/agents.fail\" ]; then echo 'agents: unreadable' >&2; exit 1; fi\n"
        "  if [ -f \"$D/agents.json\" ]; then cat \"$D/agents.json\"; else echo '[]'; fi\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = stop ] || [ \"$1\" = rm ]; then\n"
        "  echo \"$1 $2\" >> \"$D/calls.log\"\n"
        "  if [ -f \"$D/gone-$1-$2\" ]; then echo \"$1: session $2 not found\" >&2; exit 1; fi\n"
        "  if [ -f \"$D/fail-$1-$2\" ]; then echo \"$1: transient failure\" >&2; exit 1; fi\n"
        "  exit 0\n"
        "fi\n"
        "n=$(ls \"$D\"/argv-*.log 2>/dev/null | wc -l | tr -d ' ')\n"
        "printf '%s\\0' \"$@\" > \"$D/argv-$n.log\"\n"
        "pwd -P > \"$D/cwd-$n.log\"\n"
        "if ls \"$BSTACK_FLEET_STATE_DIR\"/fleet_*/fleet.json >/dev/null 2>&1; then\n"
        "  echo yes > \"$D/state-at-spawn-$n\"\n"
        "else\n"
        "  echo no > \"$D/state-at-spawn-$n\"\n"
        "fi\n"
        "if [ -f \"$D/spawn.fail\" ]; then\n"
        "  echo 'Login expired · Please run /login' >&2; exit 1\n"
        "fi\n"
        "printf '\\033[1mbackgrounded\\033[0m · \\033[36mabc12%s\\033[0m\\n' \"$n\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    os.environ["BSTACK_FLEET_CLAUDE_BIN"] = str(stub)
    return stub


def plain_worktree(td: Path, name: str = "wt") -> Path:
    """A directory that is not a git repo.

    Most cases never need one: `up` only uses the worktree as the spawn cwd,
    and git is consulted solely to infer a ticket from a branch name. Paying
    for a fixture repo everywhere would cost seconds per test and prove
    nothing — `init_repo` is for the tests that actually read a branch.
    """
    wt = td / name
    wt.mkdir(parents=True, exist_ok=True)
    return wt


def init_repo(td: Path, name: str = "repo", branch: str = "main") -> Path:
    repo = td / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README").write_text("hi\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    # --no-verify: a workspace-global pre-commit hook (gitleaks) would run on
    # every fixture repo, costing seconds per test and proving nothing here.
    subprocess.run(["git", "-C", str(repo), "commit", "--no-verify", "-qm", "init"],
                   check=True)
    return repo


def write_roster(td: Path, entries: list[dict], name: str = "roster.jsonl") -> Path:
    p = td / name
    p.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return p


def argv_logs(td: Path) -> list[list[str]]:
    """Every recorded spawn argv, in call order."""
    out = []
    for log in sorted(td.glob("argv-*.log")):
        out.append(log.read_text(encoding="utf-8").split("\0")[:-1])
    return out


def cwd_logs(td: Path) -> list[str]:
    """The working directory each spawn was launched in, in call order."""
    return [log.read_text(encoding="utf-8").strip()
            for log in sorted(td.glob("cwd-*.log"))]


def only_fleet_dir(td: Path) -> Path:
    root = Path(os.environ["BSTACK_FLEET_STATE_DIR"])
    dirs = [d for d in root.iterdir() if d.name.startswith("fleet_")]
    assert len(dirs) == 1, f"expected exactly one fleet dir, got {dirs}"
    return dirs[0]


def read_state_json(fd: Path) -> dict:
    return json.loads((fd / "fleet.json").read_text(encoding="utf-8"))
