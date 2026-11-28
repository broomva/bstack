#!/usr/bin/env python3
"""
leverage-fleet-sensor.py — the FLEET view of the leverage loop (BRO-3955).

leverage-sensor.py reads the Claude transcripts of the ONE checkout it runs in.
Two of its questions cannot be answered from there:

  m6  "what share of editing sessions touched only meta repos?" — product work
      happens in OTHER repos (and other checkouts), so a sensor standing in the
      meta repo sees almost only meta sessions and saturates at 1.0 by construction.
      Measured 2026-11-19 in one workspace: 1.00 at checkout scope, 0.17 (7 days)
      and 0.44 (30 days) across the host.
  w*  "how long do agents wait on the human?" — a property of every interactive
      session on the host, not of one directory.

This sensor reads EVERY Claude Code and Codex transcript on the host within the
window and writes the workspace's leverage-fleet-state.json (via broomva_paths).
The main sensor merges it (merge_fleet). Because the scan is host-wide, every
checkout that runs it computes the same values: the verdict no longer depends on
where it is run from.

Classification is by the GIT REPO an edit lands in, never by path regex: repos are
named in setpoints `fleet.product_repos` / `fleet.meta_repos`. A session that
touched any product repo is product; one that touched only meta repos is meta.
`fleet.external_repos` names real work that is outside this workspace's loop (a
client's or employer's repo with its own governance): those sessions are counted
and reported, but sit on neither side of m6. Anything else is UNCLASSIFIED and
reported, never folded into either side. With no
repo lists configured, m6 is null (fail closed) and the main sensor keeps its
checkout-scope value.

Edits counted: Edit/Write/MultiEdit/NotebookEdit file paths (Claude), apply_patch
file headers (Codex), and shell writes (redirect / tee targets resolved to a path;
`sed -i` attributed to the command's working directory). A path whose directory no
longer exists (a removed worktree) is resolved through its nearest existing
ancestor, and is unclassified when that is not a git repo.

Wait time (one main session = one transcript; subagent/exec sessions excluded):
  turn          human prompt -> last agent event before the next prompt
  human wait    end of a turn -> next human prompt in that session
  interactive   >= 2 human prompts
  w1  idle share = human wait / (wait + active), each wait capped at
      fleet.wait_cap_minutes (default 30, the cutoff Anthropic's 2026 employment
      report uses for agent runtime) — a longer gap is the human being away
  w2  median minutes from the end of a turn to the human's reply
  w3  mean number of sessions waiting on the human at once, over the time at
      least one is waiting (capped waits only)
Codex replays a resumed thread's history stamped with the replay time, so Codex turns
come from task_started/task_complete's own epoch fields, deduplicated by turn_id.
Claude lines are deduplicated by uuid, newest file first (a resumed copy is the
superset); Codex turns by turn_id, oldest file first (a subagent replays its parent).

No transcript content is written anywhere: only timestamps, paths' repos and counts.

Usage:
  leverage-fleet-sensor.py [--workspace DIR] [--window N] [--json] [--no-store]
                           [--throttle SEC] [--claude-root DIR] [--codex-root DIR]
                           [--config-json JSON] [--now EPOCH]
"""
import argparse
import functools
import glob
import json
import math
import os
import re
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

STATE_NAME = "leverage-fleet-state.json"
DEFAULT_CAP_MIN = 30.0
CACHE_SCHEMA_VERSION = 3
MAX_WINDOW_DAYS = 3650
MAX_FUTURE_SKEW_SEC = 300
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
PATCH_FILE_RE = re.compile(r"\*\*\* (?:Update|Add|Delete) File: ([^\n\\\"]+)")
_PURE_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")
_FILENAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_. -]*$")
PATHLIKE_RE = re.compile(r"/|\.[A-Za-z][A-Za-z0-9]{0,7}$")  # `> 0.5` is a comparison
_VAR_EXP_RE = re.compile(r"[$`]")
_HEREDOC_RE = re.compile(r"""<<(-?)[ \t]*((?:[^ \t\n;|&<>()"'\\]|\\[^\n]|"[^"\n]*"|'[^'\n]*')+)""")


def _safe_window_days(val, default=7):
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            f = float(val)
        except OverflowError:
            return default
        if math.isfinite(f) and 0 < f <= MAX_WINDOW_DAYS:
            return f if isinstance(val, float) else int(val)
    return default


def _safe_pos_float(val, default):
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            f = float(val)
        except OverflowError:
            return float(default)
        if math.isfinite(f) and f > 0:
            return f
    return float(default)


def _norm_str_list(vals):
    if not isinstance(vals, (list, tuple)):
        return []
    return sorted({str(v).strip().lower() for v in vals
                   if isinstance(v, (str, int, float)) and not isinstance(v, bool) and str(v).strip()})


def _norm_prefixes(raw_val):
    if not isinstance(raw_val, (list, tuple)):
        return []
    out = set()
    for x in raw_val:
        if isinstance(x, (str, int, float)) and not isinstance(x, bool):
            s = str(x).strip().rstrip("/")
            if s:
                out.add(s)
    return sorted(out)


def _is_write_target(tgt):
    if not tgt or tgt.startswith(("/dev/", "&", "-")):
        return False
    if _PURE_NUMBER_RE.match(tgt):
        return False
    if _VAR_EXP_RE.search(tgt):
        return True
    return bool(PATHLIKE_RE.search(tgt) or _FILENAME_RE.match(tgt))


def _split_shell_segments(cmd):
    """Split commands with subshell markers; skip heredoc data, not the command's redirects."""
    segs = []
    buf = []
    sep = ""
    i = 0
    n = len(cmd)
    quote = None
    heredocs = []
    while i < n:
        ch = cmd[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < n:
                i += 1
                buf.append(cmd[i])
            elif ch == quote:
                quote = None
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            buf.append(ch)
            i += 1
            buf.append(cmd[i])
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if cmd.startswith("<<<", i):
            buf.append("<<<")
            i += 3
            continue
        if cmd.startswith("<<", i):
            match = _HEREDOC_RE.match(cmd, i)
            if match:
                word = match[2]
                delimiter = shlex.split(word)[0]
                heredocs.append((delimiter, bool(match[1]), word != delimiter))
                buf.append(match[0])
                i = match.end()
                continue
        if cmd.startswith(">|", i):
            buf.append(">|")
            i += 2
            continue
        if cmd[i:i + 2] in ("&&", "||"):
            segs.append((sep, "".join(buf)))
            buf = []
            sep = cmd[i:i + 2]
            i += 2
            continue
        if ch in "()":
            if buf:
                segs.append((sep, "".join(buf)))
                buf = []
            segs.append((ch, ""))
            sep = ""
            i += 1
            continue
        if ch in (";", "|", "\n"):
            segs.append((sep, "".join(buf)))
            buf = []
            sep = ch
            i += 1
            if ch == "\n" and heredocs:
                for delimiter, strip_tabs, quoted in heredocs:
                    while i < n:
                        end = cmd.find("\n", i)
                        end = n if end < 0 else end
                        line = cmd[i:end]
                        i = min(n, end + 1)
                        if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                            break
                        if not quoted and ("$(" in line or "\x60" in line):
                            segs.append(("?", ""))
                heredocs.clear()
            continue
        buf.append(ch)
        i += 1
    if buf:
        segs.append((sep, "".join(buf)))
    return segs


def _tokenize_shell_segment(seg):
    """Tokenize one command segment into (words, redirect_targets).
    Handles unspaced redirects (`echo hi>/product/README`), fd 1 redirects (`1>`),
    clobber, file descriptor prefixes and quoted filenames. Ignores descriptor duplication."""
    words = []
    redirs = []
    i = 0
    n = len(seg)

    def read_word(start):
        j = start
        chars = []
        had_quote = False
        q = None
        while j < n:
            c = seg[j]
            if q:
                if c == "\\" and q == '"' and j + 1 < n:
                    j += 1
                    chars.append(seg[j])
                elif c == q:
                    q = None
                else:
                    chars.append(c)
                j += 1
                continue
            if c.isspace() or c in (">", "<", "(", ")"):
                break
            if c == "\\" and j + 1 < n:
                j += 1
                chars.append(seg[j])
                j += 1
                continue
            if c in ("'", '"'):
                q = c
                had_quote = True
                j += 1
                continue
            chars.append(c)
            j += 1
        return "".join(chars), j, had_quote

    while i < n:
        c = seg[i]
        if c.isspace() or c in ("(", ")"):
            i += 1
            continue
        # Output redirections: >, >>, >|, 1>, 1>>, 1>|, 2>, &>, >&
        if c == ">":
            prev = seg[i - 1] if i > 0 else ""
            if prev in ("-", "=", "<"):
                i += 1
                continue
            # Check for >& (fd dup) vs > / >> / >|
            j = i + 1
            if j < n and seg[j] == "&":
                # skip fd dup target
                j += 1
                while j < n and seg[j].isspace():
                    j += 1
                _, i, _ = read_word(j)
                continue
            if j < n and seg[j] in (">", "|"):
                j += 1
            while j < n and seg[j].isspace():
                j += 1
            tgt, i, _ = read_word(j)
            if tgt:
                redirs.append(tgt)
            continue
        if c == "<":
            j = i + 1
            if j < n and seg[j] in ("<", ">", "&"):
                operator = seg[j]
                j += 1
                if operator == "<" and j < n and seg[j] in ("-", "<"):
                    j += 1
            while j < n and seg[j].isspace():
                j += 1
            _, i, _ = read_word(j)
            continue
        w, j, had_q = read_word(i)
        # Check if this bare word is a file descriptor prefix right before `>` (e.g. `1>` or `2>` or `&>`)
        if not had_q and j < n and seg[j] == ">" and ((w.isascii() and w.isdigit()) or w == "&"):
            k = j + 1
            is_dup = k < n and seg[k] == "&"
            if k < n and seg[k] in (">", "|", "&"):
                k += 1
            while k < n and seg[k].isspace():
                k += 1
            tgt, i, _ = read_word(k)
            if not is_dup and tgt:
                redirs.append(tgt)
            continue
        if w or had_q:
            words.append((w, had_q))
        i = j
    return words, redirs


def _extract_cmd_write_targets(words):
    """Extract ALL file targets written by `tee` or `sed -i` in a tokenized segment."""
    if not words:
        return []
    # Skip leading env assignments (FOO=bar) and wrappers (env, command, sudo)
    idx = 0
    while idx < len(words):
        w, hq = words[idx]
        if not hq and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w):
            idx += 1
            continue
        base = os.path.basename(w)
        if base in ("env", "command", "sudo"):
            idx += 1
            while idx < len(words) and words[idx][0].startswith("-"):
                idx += 1
            continue
        break
    if idx >= len(words):
        return []
    cmd_name = os.path.basename(words[idx][0])
    args = words[idx + 1:]
    if cmd_name == "tee":
        targets = []
        after_dd = False
        for w, hq in args:
            if not after_dd and not hq and w == "--":
                after_dd = True
                continue
            if not after_dd and not hq and w.startswith("-") and w != "-":
                continue
            if w and w != "-":
                targets.append(w)
        return targets
    if cmd_name == "sed":
        in_place = False
        has_script = False
        pos = []
        after_dd = False
        k = 0
        while k < len(args):
            w, hq = args[k]
            if not after_dd and not hq and w == "--":
                after_dd = True
                k += 1
                continue
            if not after_dd and not hq and w.startswith("-") and w != "-":
                if w == "-i":
                    in_place = True
                    # macOS BSD `sed -i ''` passes an explicit empty suffix token
                    if k + 1 < len(args) and args[k + 1][0] == "" and args[k + 1][1]:
                        k += 2
                        continue
                elif w.startswith("-i") or w == "--in-place" or w.startswith("--in-place="):
                    in_place = True
                elif w in ("-e", "-f", "--expression", "--file"):
                    has_script = True
                    k += 2
                    continue
                elif w.startswith(("-e", "-f", "--expression=", "--file=")):
                    has_script = True
                k += 1
                continue
            pos.append(w)
            k += 1
        if not in_place:
            return []
        files = pos if has_script else pos[1:]
        return [f for f in files if f]
    return []


def _extract_cd_target(words):
    """If segment is `cd [flags] <dir>`, return `<dir>`, else None."""
    if not words or words[0][0] != "cd":
        return None
    after_dd = False
    for w, hq in words[1:]:
        if not after_dd and not hq and w == "--":
            after_dd = True
            continue
        if not after_dd and not hq and w.startswith("-") and w != "-":
            continue
        return w
    return None


def resolve_workspace(arg=None):
    if arg:
        return os.path.abspath(arg)
    env = os.environ.get("BSTACK_WORKSPACE") or os.environ.get("BROOMVA_WORKSPACE")
    if env:
        return os.path.abspath(env)
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        if top:
            return top
    except Exception:
        pass
    return os.getcwd()


def _import_yaml():
    """PyYAML without letting the caller's directories in (BRO-2652); see
    leverage-ship-sensor.py for the reasoning. The user site is APPENDED only."""
    try:
        import yaml
        return yaml
    except ImportError:
        import site
        user_site = site.getusersitepackages()
        if isinstance(user_site, str) and os.path.isdir(user_site) and user_site not in sys.path:
            sys.path.append(user_site)
        import yaml
        return yaml


_PATHS_MOD = None


def _broomva_paths():
    global _PATHS_MOD
    if _PATHS_MOD is None:
        import importlib.util
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "broomva_paths.py")
        spec = importlib.util.spec_from_file_location("broomva_paths", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _PATHS_MOD = mod
    return _PATHS_MOD


def _fleet_state_path(workspace):
    return str(_broomva_paths().repo_path("leverage_fleet_state", workspace))


def load_config(workspace):
    path = str(_broomva_paths().repo_path("leverage_setpoints", workspace))
    try:
        with open(path) as f:
            sp = _import_yaml().safe_load(f) or {}
    except Exception as e:
        print(f"[fleet-sensor] WARN could not load setpoints ({path}): {e}", file=sys.stderr)
        return {}, 7
    cfg = sp.get("fleet") if isinstance(sp, dict) else None
    window = _safe_window_days(sp.get("window_days") if isinstance(sp, dict) else None)
    return (cfg if isinstance(cfg, dict) else {}), window


def _ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _ancestor_tracks_missing(anc, first_missing):
    """True only when `anc` tracks non-submodule files under `first_missing` in git.
    Prevents a vanished nested clone inside a meta workspace from falling through
    to the parent meta repo. Uses --literal-pathspecs so directory names with glob
    characters like `[ab]` cannot match sibling tracked files."""
    try:
        rel = os.path.relpath(first_missing, anc)
        if not rel or rel.startswith(".."):
            return False
        r = subprocess.run(["git", "--literal-pathspecs", "-C", anc, "ls-files", "-s", "--", rel],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return False
        for line in r.stdout.splitlines():
            parts = line.split()
            if parts and parts[0] != "160000":
                return True
        return False
    except Exception:
        return False


@functools.lru_cache(maxsize=None)
def repo_of_dir(d):
    """owner/name of the GitHub repo containing directory d, else None. When `d`
    no longer exists on disk, only attribute to an existing ancestor repo if that
    ancestor actually tracks non-submodule files under the missing path — otherwise
    return None so a vanished nested product clone never resolves as meta."""
    if not d:
        return None
    d = os.path.abspath(d)
    first_missing = None
    cur = d
    while cur and cur != "/" and not os.path.isdir(cur):
        first_missing = cur
        cur = os.path.dirname(cur)
    if not cur or cur == "/":
        return None
    if first_missing is not None and not _ancestor_tracks_missing(cur, first_missing):
        return None
    try:
        r = subprocess.run(["git", "-C", cur, "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    m = re.search(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?/?$", r.stdout.strip())
    return m.group(1).lower() if m else None


def repo_of_path(p, cwd=None):
    if not p:
        return None
    p = os.path.expanduser(str(p).strip().strip("'\""))
    if not os.path.isabs(p):
        if not cwd:
            return None
        p = os.path.join(cwd, p)
    if p.startswith("/dev/"):
        return None
    return repo_of_dir(os.path.dirname(os.path.normpath(p)))


def edit_repo(p, cwd=None):
    """The repo a TOOL or SHELL edit landed in, or "?<abs path>" when it cannot be resolved.
    An unresolved edit is kept, not dropped: it may be product work in a clone or
    worktree that no longer exists, so it must not let a session read as meta-only.
    measure() discards the ones under scratch or harness folders."""
    r = repo_of_path(p, cwd)
    if r:
        return r
    if not p:
        return None
    p = os.path.expanduser(str(p).strip().strip("'\""))
    if not p or p.startswith("/dev/"):
        return None
    if not os.path.isabs(p):
        if not cwd:
            # A relative path with no working directory to anchor it: where it
            # landed is unknown, which is exactly an unresolved edit.
            return "?" + p
        p = os.path.join(cwd, p)
    return "?" + os.path.normpath(p)


def shell_write_repos(cmd, cwd):
    """Repos a shell command writes into: redirections (`>`, `>>`, `>|`, `1>`, `1>>`,
    even without leading space), ALL targets of `tee` (including `--`), and ALL file
    targets of `sed -i` (GNU & macOS BSD `-i ''`), with per-segment `cd` tracking.
    Targets containing shell variables (`$TARGET`, `$DIR/file`) or unresolvable paths
    are emitted as `?<path>` so a mixed session is never misclassified as meta-only."""
    if not isinstance(cmd, str) or not cmd.strip():
        return set()
    out = set()
    cur_cwd = cwd
    scopes = []
    segments = _split_shell_segments(cmd)
    for i, (sep, seg) in enumerate(segments):
        if sep == "(":
            scopes.append(cur_cwd)
            continue
        if sep == ")":
            cur_cwd = scopes.pop() if scopes else None
            continue
        if sep == "?":
            out.add("?heredoc expansion")
            continue
        words, redirs = _tokenize_shell_segment(seg)
        # Bash opens redirects before executing the command, including cd.
        for tgt in redirs + _extract_cmd_write_targets(words):
            if not _is_write_target(tgt):
                continue
            if _VAR_EXP_RE.search(tgt):
                out.add("?" + tgt)
                continue
            r = edit_repo(tgt, cur_cwd)
            if r:
                out.add(r)
        following = segments[i + 1][0] if i + 1 < len(segments) else ""
        cd_tgt = _extract_cd_target(words)
        if cd_tgt is None or sep == "|" or following in ("|", "||"):
            continue
        if _VAR_EXP_RE.search(cd_tgt) or cd_tgt == "-":
            cur_cwd = None
            continue
        expanded = os.path.expanduser(cd_tgt)
        if os.path.isabs(expanded):
            cur_cwd = os.path.normpath(expanded)
        elif cur_cwd:
            cur_cwd = os.path.normpath(os.path.join(cur_cwd, expanded))
    return out


# ── transcript readers ─────────────────────────────────────────────────────────

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_claude(path, seen_uuids):
    """events: [(t, kind)] kind in human|machine|agent; edits: [(t, "tool"|"shell",
    repo|None)] each stamped with its own line's time, so the window applies to the
    edit and not to the session; meta: cwd/entry."""
    ev, edits = [], []
    cwd = edit_cwd = entry = None
    try:
        fh = open(path, errors="ignore")
    except OSError:
        return None
    with fh:
        for line in fh:
            try:
                o = json.loads(line)
            except Exception:
                continue
            if not isinstance(o, dict) or o.get("isSidechain"):
                continue
            u = o.get("uuid")
            if u:
                if u in seen_uuids:
                    continue
                seen_uuids.add(u)
            row_cwd = o.get("cwd")
            if isinstance(row_cwd, str) and os.path.isabs(row_cwd):
                edit_cwd = row_cwd
                cwd = cwd or row_cwd
            entry = entry or o.get("entrypoint")
            t = _ts(o.get("timestamp"))
            typ = o.get("type")
            msg = o.get("message") if isinstance(o.get("message"), dict) else {}
            content = msg.get("content")
            if typ == "user" and t is not None:
                items = content if isinstance(content, list) else []
                is_tool = any(isinstance(i, dict) and i.get("type") == "tool_result" for i in items)
                is_text = isinstance(content, str) or any(
                    isinstance(i, dict) and i.get("type") == "text" for i in items)
                lead = content.lstrip() if isinstance(content, str) else ""
                if is_tool:
                    ev.append((t, "agent"))
                elif (is_text and not o.get("isMeta") and not o.get("isCompactSummary")
                      and not lead.startswith(("<local-command-stdout", "<local-command-caveat"))):
                    machine = (o.get("turnOrigin") == "task_notification"
                               or lead.startswith("<task-notification"))
                    ev.append((t, "machine" if machine else "human"))
            elif typ == "assistant":
                if t is not None:
                    ev.append((t, "agent"))
                for it in content if isinstance(content, list) else []:
                    if not isinstance(it, dict) or it.get("type") != "tool_use" or t is None:
                        continue
                    inp = it.get("input") if isinstance(it.get("input"), dict) else {}
                    name = it.get("name")
                    if name in EDIT_TOOLS:
                        edits.append((t, "tool", edit_repo(inp.get("file_path") or inp.get("notebook_path"), edit_cwd)))
                    elif name == "Bash":
                        edits += [(t, "shell", r) for r in shell_write_repos(inp.get("command"), edit_cwd)]
    return {"events": ev, "edits": edits, "cwd": cwd, "entry": entry}


def read_codex(path, seen_turns):
    """Codex replays a resumed or forked thread's history -- the parent's turns and
    tool calls -- into the new file, stamped with the replay time. So every tool
    call is attributed to the turn it ran in (the last task_started before it), and
    only turns this file is first to claim count, timed by their own epoch
    started_at. A subagent file therefore keeps its own turns and none of its
    parent's edits. The FIRST session_meta is the file's own; later ones belong
    to replayed history."""
    cwd = src = None
    meta_seen = False
    starts, ends, aborted = {}, {}, {}
    machine_turns = set()
    turn_edits = {}
    cur = None
    try:
        fh = open(path, errors="ignore")
    except OSError:
        return None
    with fh:
        for line in fh:
            try:
                o = json.loads(line)
            except Exception:
                continue
            if not isinstance(o, dict):
                continue
            p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
            if o.get("type") == "session_meta":
                if not meta_seen:
                    cwd, src, meta_seen = p.get("cwd"), p.get("source"), True
                continue
            pt, tid = p.get("type"), p.get("turn_id")
            if pt == "task_started" and tid:
                cur = tid
                st = _num(p.get("started_at"))
                if st is not None:
                    starts[tid] = st
                # A turn another agent or `codex exec` started is not a human
                # reply: its wait is not the human's latency.
                ta = p.get("turn_attribution") if isinstance(p.get("turn_attribution"), dict) else {}
                if ta.get("turn_trigger") or ta.get("parent_turn_id") or ta.get("initiating_agent_path"):
                    machine_turns.add(tid)
            elif pt == "task_complete" and tid:
                en = _num(p.get("completed_at"))
                if en is not None:
                    ends[tid] = en
                    st = _num(p.get("started_at"))
                    starts.setdefault(tid, st if st is not None else en)
            elif pt == "turn_aborted" and tid:
                aborted[tid] = _ts(o.get("timestamp"))
            elif pt in ("custom_tool_call", "function_call") and cur is not None:
                raw_args = p.get("input") if p.get("input") is not None else p.get("arguments")
                s = raw_args if isinstance(raw_args, str) else json.dumps(raw_args or "")
                files = PATCH_FILE_RE.findall(s.replace("\\n", "\n"))
                bucket = turn_edits.setdefault(cur, [])
                lt = _ts(o.get("timestamp"))
                # The call's own working directory, when it names one, decides where
                # relative paths land -- for a patch header as much as a shell write.
                wd, cmd = cwd, s
                try:
                    a = json.loads(s) if isinstance(s, str) and s.startswith("{") else None
                    if isinstance(a, dict):
                        wd = a.get("workdir") or a.get("cwd") or cwd
                        c = a.get("cmd") if a.get("cmd") is not None else a.get("command")
                        cmd = " ".join(c) if isinstance(c, list) else str(c or "")
                except Exception:
                    pass
                if files:
                    bucket += [(lt, "tool", edit_repo(f, wd)) for f in files]
                else:
                    bucket += [(lt, "shell", r) for r in shell_write_repos(cmd, wd)]
    ev, edits = [], []
    for tid, st in starts.items():
        if tid in seen_turns:
            continue
        seen_turns.add(tid)
        en = ends.get(tid) or aborted.get(tid)
        ev.append((st, "machine" if tid in machine_turns else "human"))
        if en and en >= st:
            ev.append((en, "agent"))
        # An edit keeps its own line time, so a turn that began before the window and
        # edited inside it counts. A replayed line carries the replay time, which
        # falls outside its turn's span: clamp such a time to the turn's start.
        for lt, k, r in turn_edits.get(tid, ()):
            ok = lt is not None and lt >= st and (en is None or lt <= en)
            edits.append((lt if ok else st, k, r))
    entry = src if isinstance(src, str) else ("subagent" if src else None)
    return {"events": ev, "edits": edits, "cwd": cwd, "entry": entry}


def turns(events):
    """[(start, end, opener, gap_before)] — opener human|machine."""
    out, cur, last_agent = [], None, None
    for t, k in sorted(events):
        if k in ("human", "machine"):
            if cur is not None:
                out.append((cur[0], last_agent if last_agent is not None else cur[0], cur[1]))
            cur, last_agent = (t, k), None
        elif cur is not None:
            last_agent = t
    if cur is not None:
        out.append((cur[0], last_agent if last_agent is not None else cur[0], cur[1]))
    res = []
    for i, (s, e, k) in enumerate(out):
        gap = None if i == 0 else max(0.0, s - out[i - 1][1])
        res.append((s, max(e, s), k, gap))
    return res


def _pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))]


# ── measurement ───────────────────────────────────────────────────────────────

def _collect_jsonl(root, recursive):
    """Collect *.jsonl files under `root` and count unreadable directories/entries.
    Missing root (FileNotFoundError) is benign (0 files, 0 errors); permission or
    other OSErrors count as unreadable so m6 fails closed. Follows readable
    directory symlinks with an inode cycle guard so symlinked session roots work."""
    if not root:
        return [], 0
    root = os.path.normpath(root)
    files, errors = [], 0
    if recursive:
        try:
            st0 = os.stat(root)
        except FileNotFoundError:
            return [], int(os.path.islink(root))
        except OSError:
            return [], 1
        visited = {(st0.st_dev, st0.st_ino)}
        stack = [root]
        while stack:
            cur = stack.pop()
            try:
                with os.scandir(cur) as it:
                    entries = list(it)
            except OSError:
                errors += 1
                continue
            for e in entries:
                try:
                    st = os.stat(e.path)  # follows symlinks; raises on dangling/unsearchable target
                except OSError:
                    if e.name.endswith(".jsonl") or e.is_symlink():
                        errors += 1
                    continue
                if os.path.isdir(e.path):
                    key = (st.st_dev, st.st_ino)
                    if key not in visited:
                        visited.add(key)
                        stack.append(e.path)
                elif e.name.endswith(".jsonl"):
                    files.append(e.path)
        return files, errors
    try:
        entries = list(os.scandir(root))
    except FileNotFoundError:
        return [], int(os.path.islink(root))
    except OSError:
        return [], 1
    for e in entries:
        try:
            if e.is_symlink():
                os.stat(e.path)  # count dangling project directories as unreadable
            if not e.is_dir():
                continue
            for sub in os.scandir(e.path):
                try:
                    if sub.name.endswith(".jsonl"):
                        os.stat(sub.path)  # fail closed on unreadable symlink targets
                        if sub.is_file():
                            files.append(sub.path)
                except OSError:
                    errors += 1
        except OSError:
            errors += 1
    return files, errors


def measure(cfg, window_days, claude_root, codex_root, now=None):
    cfg = cfg if isinstance(cfg, dict) else {}
    now = now or time.time()
    window_days = _safe_window_days(window_days)
    since = now - float(window_days) * 86400
    cap = _safe_pos_float(cfg.get("wait_cap_minutes"), DEFAULT_CAP_MIN) * 60
    product = set(_norm_str_list(cfg.get("product_repos")))
    meta = set(_norm_str_list(cfg.get("meta_repos")))
    external = set(_norm_str_list(cfg.get("external_repos")))
    # Sessions started in a scratch directory are throwaway runs (scripted evals,
    # dogfood fixtures), not work: excluded from both m6 and the wait metrics.
    if "scratch_prefixes" in cfg:
        scratch_prefixes = tuple(_norm_prefixes(cfg.get("scratch_prefixes")))
    else:
        scratch_prefixes = tuple(_norm_prefixes(["/tmp", "/private/tmp"]))
    # Edits outside any repo that are not work toward one: scratch, and the agent
    # harnesses' own homes (memory, settings). Anything else unresolved is kept.
    home = os.path.expanduser("~")
    ignored_unrepo = scratch_prefixes + tuple(
        os.path.join(home, d) for d in (".claude", ".codex", ".agents", ".config", ".cache"))
    under = lambda path, prefixes: any(path == x or path.startswith(x + "/") for x in prefixes)

    unreadable = {"claude": 0, "codex": 0}
    files = []
    if claude_root:
        cf, cerr = _collect_jsonl(claude_root, recursive=False)
        files += [("claude", f) for f in cf]
        unreadable["claude"] += cerr
    if codex_root:
        xf, xerr = _collect_jsonl(codex_root, recursive=True)
        files += [("codex", f) for f in xf]
        unreadable["codex"] += xerr
    # Which file claims a shared line or turn (the two harnesses dedupe separately,
    # so their orders are independent):
    #   Claude newest first -- a resumed copy repeats the earlier lines and continues
    #     them, so it is the superset; oldest-first would split one session in two
    #     and lose the wait where it resumed.
    #   Codex by CREATION time, oldest first -- a subagent's file replays its
    #     PARENT's turns, and the parent must claim them. Modification time is the
    #     wrong clock here: a parent records the end of its turn after the child
    #     returns, so the parent's file is usually the NEWER one.
    kept_files = []
    for h, f in files:
        try:
            mt = os.path.getmtime(f)
        except OSError:
            unreadable[h] += 1
            continue
        if mt >= since:
            kept_files.append((h, f))
    files = kept_files
    files.sort(key=lambda hf: _codex_created(hf[1]) if hf[0] == "codex" else -_mtime(hf[1]))

    seen_uuids, seen_turns = set(), set()
    read = {"claude": 0, "codex": 0}
    editing = meta_s = product_s = external_s = unclassified = shell_attributed = 0
    tool_meta = tool_product = 0  # the same rule on tool edits alone (sensitivity)
    unclassified_repos = {}
    interactive = []
    for h, f in files:
        s = read_claude(f, seen_uuids) if h == "claude" else read_codex(f, seen_turns)
        if s is None:
            unreadable[h] += 1
            continue
        read[h] += 1
        tt = [x for x in turns(s["events"]) if x[0] >= since]
        if tt:
            # The first turn in the window has no in-window predecessor: its gap
            # would reach back to a turn before the window (days, for a resumed
            # session), and the depth sweep would index the wrong turn.
            tt[0] = tt[0][:3] + (None,)
        cwd = (s["cwd"] or "").rstrip("/")
        scratch = any(cwd == p or cwd.startswith(p + "/") for p in scratch_prefixes)
        # m6 — repo classification of the edits made INSIDE the window
        ed = [e for e in s["edits"] if e[0] >= since]
        tool_repos = {r for _, k, r in ed if k == "tool" and r
                      and not (r.startswith("?") and under(r[1:], ignored_unrepo))}
        # every unresolved edit that survives the filter reads as one unknown repo
        tool_repos = {("<unresolved>" if r.startswith("?") else r) for r in tool_repos}
        shell_repos = {r for _, k, r in ed if k == "shell" and r
                       and not (r.startswith("?") and under(r[1:], ignored_unrepo))}
        shell_repos = {("<unresolved>" if r.startswith("?") else r) for r in shell_repos}
        edited = any(k == "tool" for _, k, _ in ed) or bool(shell_repos)
        if edited and not scratch:
            editing += 1
            repos = tool_repos | shell_repos
            if shell_repos - tool_repos:
                shell_attributed += 1
            # Product wins on any product edit. Meta means ONLY meta repos: a session
            # that also edited an external or unclassified repo is not meta-only.
            if tool_repos & product:
                tool_product += 1
            elif tool_repos and tool_repos <= meta:
                tool_meta += 1
            if repos & product:
                product_s += 1
            elif repos and repos <= meta:
                meta_s += 1
            elif repos & external:
                external_s += 1
            else:
                unclassified += 1
                for r in repos or {"<no repo>"}:
                    unclassified_repos[r] = unclassified_repos.get(r, 0) + 1
        # wait — interactive main sessions only
        n_human = sum(1 for x in tt if x[2] == "human")
        if n_human >= 2 and s["entry"] not in ("exec", "subagent", "sdk-cli") and not scratch:
            interactive.append((h, tt))

    active = wait_cap = wait_raw = 0.0
    gaps, away, marks = [], 0, []
    for _, tt in interactive:
        for i, (st, en, k, gap) in enumerate(tt):
            active += en - st
            if gap is None or k != "human":
                continue
            gaps.append(gap)
            wait_raw += gap
            wait_cap += min(gap, cap)
            away += gap > cap
            if gap > 0:
                w0 = tt[i - 1][1]
                marks += [(w0, 1), (min(st, w0 + cap), -1)]
    marks.sort()
    depth, last, weighted, waiting = 0, None, 0.0, 0.0
    for t, d in marks:
        if last is not None and depth > 0:
            weighted += depth * (t - last)
            waiting += t - last
        depth += d
        last = t

    working = meta_s + product_s
    configured = bool(product and meta)
    unreadable_total = unreadable["claude"] + unreadable["codex"]
    # An unreadable transcript could hold product edits: fail closed on m6 rather
    # than inflating meta share to 1.0 on partial transcript coverage.
    m6 = round(meta_s / working, 4) if (configured and working and unreadable_total == 0) else None
    w1 = round(wait_cap / (wait_cap + active), 4) if (gaps and active > 0) else None
    p50 = statistics.median(gaps) if gaps else None
    w2 = round(p50 / 60, 2) if p50 is not None else None
    w3 = round(weighted / waiting, 3) if waiting > 0 else None
    notes = []
    if not configured:
        notes.append("fleet.product_repos / fleet.meta_repos not both configured — m6 not measured at fleet scope")
    if unreadable_total > 0:
        notes.append(f"{unreadable_total} transcript(s) unreadable — m6 failed closed to None")
    return {
        "metrics": {
            "m6_meta_work_session_ratio": m6,
            "w1_agent_idle_share": w1,
            "w2_reply_latency_p50_min": w2,
            "w3_waiting_sessions_mean": w3,
        },
        "raw": {
            "transcripts_read": read,
            "unreadable_transcripts": unreadable,
            "editing_sessions": editing, "meta_sessions": meta_s, "product_sessions": product_s,
            "external_sessions": external_s,
            "unclassified_sessions": unclassified,
            "unclassified_repos": dict(sorted(unclassified_repos.items(), key=lambda kv: -kv[1])[:10]),
            "shell_write_attributed_sessions": shell_attributed,
            # m6 if shell writes were ignored. The shell heuristic decides many
            # sessions; when the two disagree on which side of the target m6 sits,
            # the reading is not robust and should not drive anything.
            "m6_tool_edits_only": (round(tool_meta / (tool_meta + tool_product), 4)
                                   if configured and (tool_meta + tool_product) and unreadable_total == 0 else None),
            "interactive_sessions": len(interactive), "human_replies": len(gaps),
            "active_hours": round(active / 3600, 2),
            "wait_capped_hours": round(wait_cap / 3600, 2),
            "wait_raw_hours": round(wait_raw / 3600, 2),
            "idle_share_uncapped": round(wait_raw / (wait_raw + active), 4) if (gaps and active > 0) else None,
            "reply_latency_p90_min": round(_pct(gaps, 0.9) / 60, 2) if gaps else None,
            "replies_after_cap": away,
            "wait_cap_minutes": cap / 60,
            "notes": notes,
        },
    }


def _codex_created(path):
    """When a Codex session file was created: its first line's session_meta
    timestamp, else its modification time."""
    try:
        with open(path, errors="ignore") as f:
            o = json.loads(f.readline())
        t = _ts(o.get("timestamp")) or _ts((o.get("payload") or {}).get("timestamp"))
        if t is not None:
            return t
    except Exception:
        pass
    return _mtime(path)


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def write_state(record, workspace):
    state_file = _fleet_state_path(workspace)
    d = os.path.dirname(state_file)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".fleet-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(record, f, indent=2)
            f.write("\n")
        os.replace(tmp, state_file)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fleet_config_signature(cfg, window):
    """Canonical measurement config recorded alongside cached readings so readers and
    --throttle never reuse a scan produced under a different window, repo list,
    scratch filter, or wait cap."""
    c = cfg if isinstance(cfg, dict) else {}
    if "scratch_prefixes" in c:
        prefixes = _norm_prefixes(c.get("scratch_prefixes"))
    else:
        prefixes = _norm_prefixes(["/tmp", "/private/tmp"])
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "window_days": float(_safe_window_days(window)),
        "product_repos": _norm_str_list(c.get("product_repos")),
        "meta_repos": _norm_str_list(c.get("meta_repos")),
        "external_repos": _norm_str_list(c.get("external_repos")),
        "scratch_prefixes": prefixes,
        "wait_cap_minutes": _safe_pos_float(c.get("wait_cap_minutes"), DEFAULT_CAP_MIN),
    }


def main():
    home = os.path.expanduser("~")
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--window", type=float, default=None)
    ap.add_argument("--claude-root", default=os.environ.get("BSTACK_CLAUDE_PROJECTS", os.path.join(home, ".claude", "projects")))
    ap.add_argument("--codex-root", default=os.environ.get("BSTACK_CODEX_SESSIONS", os.path.join(home, ".codex", "sessions")))
    ap.add_argument("--config-json", default=None, help="inline fleet config (hermetic test)")
    ap.add_argument("--now", type=float, default=None, help="epoch seconds (hermetic test)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-store", action="store_true")
    ap.add_argument("--throttle", type=int, default=0,
                    help="skip recompute if the fleet state is younger than N sec (SessionStart wire)")
    args = ap.parse_args()

    workspace = resolve_workspace(args.workspace)
    fleet_state_file = _fleet_state_path(workspace)
    if args.config_json:
        cfg, window = json.loads(args.config_json), 7
    else:
        cfg, window = load_config(workspace)
    cfg = cfg if isinstance(cfg, dict) else {}
    if args.window is not None:
        window = _safe_window_days(args.window)
    else:
        window = _safe_window_days(window)

    config_sig = fleet_config_signature(cfg, window)

    if args.throttle:
        try:
            with open(fleet_state_file) as f:
                st = json.load(f)
            age = time.time() - datetime.fromisoformat(st["measured_at"]).timestamp()
            if (-MAX_FUTURE_SKEW_SEC <= age < args.throttle
                    and st.get("config") == config_sig
                    and st.get("claude_root") == args.claude_root
                    and st.get("codex_root") == args.codex_root):
                return
        except Exception:
            pass

    started = time.time()
    result = measure(cfg, window, args.claude_root, args.codex_root, now=args.now)
    record = {
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_days": window,
        "config": config_sig,
        "scope": "host",
        "host": os.uname().nodename if hasattr(os, "uname") else None,
        "claude_root": args.claude_root, "codex_root": args.codex_root,
        "elapsed_s": round(time.time() - started, 2),
        **result,
    }
    if not args.no_store:
        write_state(record, workspace)
    if args.json:
        print(json.dumps(record, indent=2))
    else:
        m, r = record["metrics"], record["raw"]
        print(f"[fleet] {r['transcripts_read']} transcripts / {window}d on {record['host']} "
              f"({record['elapsed_s']}s)")
        print(f"  m6 meta_work_session_ratio = {m['m6_meta_work_session_ratio']}  "
              f"(meta {r['meta_sessions']} / product {r['product_sessions']}; external {r['external_sessions']}; "
              f"unclassified {r['unclassified_sessions']}: {r['unclassified_repos']})")
        print(f"  w1 agent idle share (waits capped at {r['wait_cap_minutes']:g} min) = {m['w1_agent_idle_share']}  "
              f"(uncapped {r['idle_share_uncapped']})")
        print(f"  w2 reply latency p50 = {m['w2_reply_latency_p50_min']} min  (p90 {r['reply_latency_p90_min']} min, "
              f"{r['replies_after_cap']}/{r['human_replies']} replies after the cap)")
        print(f"  w3 sessions waiting at once = {m['w3_waiting_sessions_mean']}  "
              f"({r['interactive_sessions']} interactive sessions, {r['active_hours']} agent-hours)")
        for n in r["notes"]:
            print(f"  note: {n}")


if __name__ == "__main__":
    main()
