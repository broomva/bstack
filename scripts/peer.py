#!/usr/bin/env python3
"""bstack peer — the peer-session spawn contract shared by `bstack wave` and
`bstack fleet` (Fanout, P5).

A peer is a background Claude Code session another session can address. The
contract this module makes executable, and that the tests pin:

  1. Every peer is NAMED at launch, `<worktree>-<ticket>-<slug>` (lowercase,
     hyphens only, typeahead-safe). The CLI listing carries `cwd`, but the
     agent-side `ListAgents` tool a peer sees from inside a session does not —
     there the name is the only join from a session to its worktree, branch
     and ticket — and a running session cannot rename itself. Measured 2026-09-06 (Claude Code 2.1.258): a session spawned
     without `--name` is displayed under its prompt text.
  2. Every unattended spawn carries `--strict-mcp-config` by default (a fresh
     session in a project with unapproved `.mcp.json` servers otherwise stalls
     on the trust dialog with nobody at the keyboard) and
     `--settings '{"crossSessionInbound":"accept"}'` (otherwise the peer cannot
     take a `SendMessage`). Strict mode also means the peer has NO MCP tools;
     a workspace whose project servers are already approved may opt into
     `mcp="inherit"` per plan/roster or via `BSTACK_PEER_MCP=inherit`.
  3. The prompt is passed positionally and LAST. Measured 2.1.258: it runs as
     the first turn. Older notes recorded an idle start on an earlier build;
     the liveness read below does not assume either way — it reports what the
     listing shows (see clause 5), and a peer needing the operator surfaces as
     WAITING there.
  4. `claude --bg` prints `backgrounded · <id>` (ANSI-coloured on a TTY). The
     id is captured with the colour codes stripped — an un-stripped regex
     reports every spawn as failed and a later teardown then removes nothing.
  5. Liveness comes from `claude agents --json --all`. Measured schema on
     2.1.258 (fixture `tests/wave/fixtures/claude-agents-2.1.258.json`, 32
     entries): every entry has `kind` (background|interactive), `sessionId`,
     `name`, `cwd`, `startedAt`; background entries also carry the short `id`
     (= sessionId[:8]) and a `state` (working|blocked|done|failed|stopped);
     a live process carries `pid` and a `status` (idle|busy|waiting) with
     `waitingFor` when waiting ("dialog open", "permission prompt", ...).
     There is NO `needs` field — an earlier draft of this module keyed on one
     and was caught by review against the real payload. A dead session can
     keep a `state` of `blocked`; only a pid is a process.

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

# CSI sequences (colour/cursor) and OSC sequences (titles, OSC-8 hyperlinks).
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# The id is on the same line as the word; never let the scan cross a newline.
_BACKGROUNDED_RE = re.compile(r"backgrounded[^\S\n]*[^\w\n]*([0-9a-f]{6,})", re.IGNORECASE)
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


def spawn_timeout(default: float = 60.0) -> float:
    """$BSTACK_PEER_SPAWN_TIMEOUT seconds, else the default. `claude --bg`
    returns as soon as the session is registered, so this only bounds a
    launcher that blocks (login/trust prompt) — and the spawn is serial, so a
    10-plan wave with a stuck launcher costs 10× this."""
    try:
        return float(os.environ.get("BSTACK_PEER_SPAWN_TIMEOUT") or default)
    except ValueError:
        return default


def spawn(argv: Sequence[str], *, cwd: str | os.PathLike | None = None,
          timeout: float | None = None) -> SpawnResult:
    """Run `claude --bg ...` synchronously. `--bg` returns as soon as the
    session is registered, so blocking here is cheap and is the only way to
    read the id it prints. stdin is /dev/null: a peer must never inherit the
    launcher's terminal."""
    name = ""
    if "--name" in argv:
        i = list(argv).index("--name")
        if i + 1 < len(argv):
            name = str(argv[i + 1])
    if timeout is None:
        timeout = spawn_timeout()
    # stdout/stderr go to a temp FILE, not a pipe: `subprocess.run` with a pipe
    # blocks until EOF, and if `claude --bg`'s background child keeps the stdout
    # pipe open the launcher's own exit is not enough — every spawn would cost
    # the full timeout. A file returns as soon as the direct child exits,
    # whatever the grandchild holds. The file is managed by hand (not `with`)
    # so the timeout handler below can still read the id the launcher printed
    # before it stalled. stdin is /dev/null: a peer never inherits the terminal.
    import tempfile

    def _read(buf) -> str:
        try:
            buf.seek(0)
            return buf.read()
        except Exception:
            return ""

    buf = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    try:
        try:
            proc = subprocess.run(
                list(argv), cwd=str(cwd) if cwd else None,
                stdin=subprocess.DEVNULL, stdout=buf, stderr=subprocess.STDOUT,
                text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            # The launcher was killed, but the session it registered may be
            # alive: the temp file holds whatever it printed. Keep the id so the
            # manifest can still join the peer instead of orphaning it.
            partial = strip_ansi(_read(buf))
            sid = parse_session_id(partial)
            return SpawnResult(name=name, session_id=sid, returncode=None,
                               output=(f"timeout after {timeout:.0f}s; " +
                                       (f"session {sid} was registered before the launcher stalled — "
                                        f"check `claude agents`" if sid else "no session id seen")),
                               ok=False)
        except OSError as exc:
            return SpawnResult(name=name, session_id=None, returncode=None,
                               output=str(exc), ok=False)
        captured = _read(buf)
    finally:
        buf.close()
    proc = subprocess.CompletedProcess(proc.args, proc.returncode, captured, "")
    out = strip_ansi((proc.stdout or "") + (proc.stderr or ""))[:4000]
    sid = parse_session_id(out)
    return SpawnResult(name=name, session_id=sid, returncode=proc.returncode,
                       output=out, ok=(proc.returncode == 0 and sid is not None))


# --------------------------------------------------------------------------- #
# Liveness
# --------------------------------------------------------------------------- #
LIVE = "live"        # a pid, and the process is idle or busy
WAITING = "waiting"  # a pid, `status: waiting` — blocked on a dialog / permission / input
IDLE_START = WAITING  # older name; the idle-start hypothesis is one `waitingFor` value
DONE = "done"        # `state: done` — the turn finished (the process may still be up)
GONE = "gone"        # `state: failed|stopped`, no pid, or not listed at all
UNKNOWN = "unknown"  # nothing to join on (no id recorded / agents unreadable)


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
               name: str | None = None, cwd: str | os.PathLike | None = None) -> dict | None:
    """Join by short id (background entries carry `id`; every entry carries a
    `sessionId` the short id prefixes), then by name (the address), then by
    `cwd` when the caller knows the worktree and nothing else matched."""
    if not agents:
        return None
    agents = list(agents)
    if session_id:
        # The id is the stable key. A recorded id that is absent from a listing
        # that includes completed sessions means gone — do NOT fall through to
        # the name, because compose_name is deterministic and a re-dispatch of
        # the same plan produces the same name (an old wave would then adopt the
        # new wave's peer).
        sid = session_id.lower()
        for a in agents:
            if str(a.get("id", "")).lower() == sid or str(a.get("sessionId", "")).lower().startswith(sid):
                return a
        return None
    if name:
        for a in agents:
            if a.get("name") == name:
                return a
    if cwd:
        # Legacy manifests carry no id/name; the worktree is the only join. Only
        # a background peer lives in a wave/fleet worktree — an interactive
        # `claude` the operator opened there to look must not be adopted as the
        # peer and reported live.
        want = os.path.realpath(str(cwd))
        hits = [a for a in agents
                if a.get("kind") == "background" and a.get("cwd")
                and os.path.realpath(str(a["cwd"])) == want]
        if len(hits) == 1:
            return hits[0]
    return None


def classify(entry: dict | None) -> str:
    """One word per agent entry, in the order the real schema demands:
    a terminal `state` first (failed/stopped → gone, done → done even if the
    process lingers), then no-pid → gone (a dead background session keeps
    listing as `blocked`, so the pid is the liveness test), then anything that
    means "needs the operator". On 2.1.258 "needs the operator"
    surfaces two ways: an interactive session sets `status: waiting` (with
    `waitingFor`), and a background `--bg` peer was observed setting `state:
    blocked`. Both map to WAITING, so the class is reached however a given
    build reports it — the classifier does not depend on which field a peer of
    a particular kind happens to use."""
    if entry is None:
        return GONE
    state = str(entry.get("state") or "").lower()
    status = str(entry.get("status") or "").lower()
    if state in ("failed", "stopped"):
        return GONE
    if state == "done":
        return DONE
    if not entry.get("pid"):
        return GONE
    if status == "waiting" or state == "blocked":
        return WAITING
    return LIVE


def waiting_for(entry: dict | None) -> str:
    """The `waitingFor` text of a waiting entry ("dialog open", "permission
    prompt", ...), or "" — for the operator's suggestion line."""
    return str((entry or {}).get("waitingFor") or "")


def liveness(agents: list[dict] | None, *, session_id: str | None,
             name: str | None, cwd: str | os.PathLike | None = None) -> tuple[str, dict | None]:
    """(classification, entry). UNKNOWN when there is nothing to join on or
    the listing could not be read — a check that cannot tell must say so."""
    if agents is None or not (session_id or name or cwd):
        return UNKNOWN, None
    entry = find_agent(agents, session_id=session_id, name=name, cwd=cwd)
    return classify(entry), entry
