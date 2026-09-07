#!/usr/bin/env python3
"""bstack peer — the peer-session spawn contract shared by `bstack wave` and
`bstack fleet` (Fanout, P5).

A peer is a background Claude Code session another session can address. The
contract this module makes executable, and that the tests pin:

  1. Every peer is NAMED at launch, `<worktree>-<ticket>-<slug>` (lowercase,
     hyphens only, typeahead-safe), because the agent-side `ListAgents`
     listing has no working-directory column — the name is the only join from
     a session to its worktree, branch and ticket, and a running session cannot
     rename itself. Measured 2026-09-06 (Claude Code 2.1.258): a session spawned
     without `--name` is displayed under its prompt text.
  2. Every unattended spawn carries `--strict-mcp-config` by default (a fresh
     session in a project with unapproved `.mcp.json` servers otherwise stalls
     on the trust dialog with nobody at the keyboard) and
     `--settings '{"crossSessionInbound":"accept"}'` (otherwise the peer cannot
     take a `SendMessage`). Strict mode also means the peer has NO MCP tools;
     a workspace whose project servers are already approved may opt into
     `mcp="inherit"` per plan/roster or via `BSTACK_PEER_MCP=inherit`.
  3. The prompt is passed positionally and LAST. Measured 2.1.258: it runs as
     the first turn. Older notes recorded an idle start on an earlier build, so
     the liveness read below DETECTS that case (`needs` set on the agent
     entry) instead of assuming either way.
  4. `claude --bg` prints `backgrounded · <id>` (ANSI-coloured on a TTY). The
     id is captured with the colour codes stripped — an un-stripped regex
     reports every spawn as failed and a later teardown then removes nothing.
  5. Liveness comes from `claude agents --json --all`, keyed on `pid`. A dead
     session can keep listing as `blocked`; only a pid is a live process.

Stdlib-only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

STRICT_MCP_FLAG = "--strict-mcp-config"
INBOUND_ACCEPT_SETTINGS = json.dumps(
    {"crossSessionInbound": "accept"}, separators=(",", ":"))
NAME_MAX = 64
MCP_MODES = ("strict", "inherit")

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_BACKGROUNDED_RE = re.compile(r"backgrounded\W*([0-9a-f]{6,})", re.IGNORECASE)
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


class PeerError(Exception):
    """User-facing failure of the spawn contract. Always carries a message."""


# --------------------------------------------------------------------------- #
# Name grammar
# --------------------------------------------------------------------------- #
def slugify(value: str | None) -> str:
    """Lowercase; every run of non-[a-z0-9] becomes one hyphen; no edge hyphens."""
    if not value:
        return ""
    return _NON_SLUG_RE.sub("-", str(value).lower()).strip("-")


def compose_name(worktree: str | os.PathLike | None, ticket: str | None,
                 slug: str) -> str:
    """`<worktree-basename>-<ticket>-<slug>`; the ticket part is omitted when
    absent (a ticketless arc is `<worktree>-<slug>`). Deterministic and
    boring on purpose: a peer must be able to recompute it from the same
    inputs and get the same address."""
    wt = slugify(Path(worktree).name) if worktree else ""
    parts = [p for p in (wt, slugify(ticket), slugify(slug)) if p]
    name = "-".join(parts)[:NAME_MAX].rstrip("-")
    if not name:
        raise PeerError("cannot compose a session name from empty parts")
    return name


# --------------------------------------------------------------------------- #
# Spawn argv
# --------------------------------------------------------------------------- #
def mcp_mode(explicit: str | None = None) -> str:
    """Resolve the MCP mode: explicit value, else $BSTACK_PEER_MCP, else strict."""
    mode = (explicit or os.environ.get("BSTACK_PEER_MCP") or "strict").strip().lower()
    if mode not in MCP_MODES:
        raise PeerError(f"mcp mode must be one of {MCP_MODES}, got {mode!r}")
    return mode


def build_spawn_argv(name: str, prompt: str | None, *, binary: str = "claude",
                     mcp: str = "strict", agent: str | None = None,
                     model: str | None = None, allowed_tools: str | None = None,
                     extra: Sequence[str] = ()) -> list[str]:
    """The exact argv for one peer. Flag order is part of the contract the
    tests assert: `--bg --name <name> [--strict-mcp-config] --settings <accept>
    [--agent A] [--model M] [--allowedTools T] [extra...] [prompt]`."""
    if not name:
        raise PeerError("a peer must be named")
    if mcp not in MCP_MODES:
        raise PeerError(f"mcp mode must be one of {MCP_MODES}, got {mcp!r}")
    argv: list[str] = [binary, "--bg", "--name", name]
    if mcp == "strict":
        argv.append(STRICT_MCP_FLAG)
    argv += ["--settings", INBOUND_ACCEPT_SETTINGS]
    if agent:
        argv += ["--agent", str(agent)]
    if model:
        argv += ["--model", str(model)]
    if allowed_tools:
        argv += ["--allowedTools", str(allowed_tools)]
    argv += [str(x) for x in extra]
    if prompt:
        argv.append(prompt)
    return argv


# --------------------------------------------------------------------------- #
# Spawn + id capture
# --------------------------------------------------------------------------- #
def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def parse_session_id(text: str) -> str | None:
    """Recover the short session id from `claude --bg` output, colour or not."""
    m = _BACKGROUNDED_RE.search(strip_ansi(text))
    return m.group(1).lower() if m else None


@dataclass
class SpawnResult:
    name: str
    session_id: str | None
    returncode: int | None
    output: str          # ANSI-stripped, truncated; for the operator's eyes
    ok: bool             # process exited 0 AND an id was recovered

    @property
    def summary(self) -> str:
        if self.ok:
            return self.session_id or ""
        tail = (self.output or "").strip().splitlines()
        return "ERROR: " + (tail[-1][:120] if tail else f"exit {self.returncode}")


def spawn(argv: Sequence[str], *, cwd: str | os.PathLike | None = None,
          timeout: float = 60.0) -> SpawnResult:
    """Run `claude --bg ...` synchronously. `--bg` returns as soon as the
    session is registered, so blocking here is cheap and is the only way to
    read the id it prints. stdin is /dev/null: a peer must never inherit the
    launcher's terminal."""
    name = ""
    if "--name" in argv:
        i = list(argv).index("--name")
        if i + 1 < len(argv):
            name = str(argv[i + 1])
    try:
        proc = subprocess.run(
            list(argv), cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return SpawnResult(name=name, session_id=None, returncode=None,
                           output=f"timeout after {timeout:.0f}s", ok=False)
    except OSError as exc:
        return SpawnResult(name=name, session_id=None, returncode=None,
                           output=str(exc), ok=False)
    out = strip_ansi((proc.stdout or "") + (proc.stderr or ""))[:4000]
    sid = parse_session_id(out)
    return SpawnResult(name=name, session_id=sid, returncode=proc.returncode,
                       output=out, ok=(proc.returncode == 0 and sid is not None))


# --------------------------------------------------------------------------- #
# Liveness
# --------------------------------------------------------------------------- #
LIVE = "live"              # a pid is present: a real process
IDLE_START = "idle-start"  # the entry says it needs a prompt: dispatch by message
DONE = "done"              # finished cleanly
GONE = "gone"              # listed without a pid, or not listed at all
UNKNOWN = "unknown"        # nothing to join on (no id recorded / agents unreadable)


def list_agents(binary: str = "claude", timeout: float = 20.0) -> list[dict] | None:
    """`claude agents --json --all`, or None when it cannot be read (binary
    missing, old client, stub). None is reported as UNKNOWN, never as clean."""
    try:
        proc = subprocess.run(
            [binary, "agents", "--json", "--all"], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(strip_ansi(proc.stdout or "") or "null")
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        for key in ("agents", "sessions", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    return data if isinstance(data, list) else None


def find_agent(agents: Iterable[dict] | None, *, session_id: str | None = None,
               name: str | None = None) -> dict | None:
    """Join by id first (stable), then by name (the address)."""
    if not agents:
        return None
    if session_id:
        for a in agents:
            if str(a.get("id", "")).lower() == session_id.lower():
                return a
    if name:
        for a in agents:
            if a.get("name") == name:
                return a
    return None


def classify(entry: dict | None) -> str:
    """One word per agent entry. `needs` wins over `pid` because it is the
    actionable one; a missing pid is GONE even if the state says otherwise."""
    if entry is None:
        return GONE
    if entry.get("needs"):
        return IDLE_START
    if entry.get("pid"):
        return LIVE
    if str(entry.get("state", "")).lower() == "done":
        return DONE
    return GONE


def liveness(agents: list[dict] | None, *, session_id: str | None,
             name: str | None) -> tuple[str, dict | None]:
    """(classification, entry). UNKNOWN when there is nothing to join on or
    the listing could not be read — a check that cannot tell must say so."""
    if agents is None or not (session_id or name):
        return UNKNOWN, None
    entry = find_agent(agents, session_id=session_id, name=name)
    return classify(entry), entry
