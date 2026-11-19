#!/usr/bin/env python3
"""
leverage-fleet-sensor.py — the FLEET view of the leverage loop (BRO-3955).

leverage-sensor.py reads the Claude transcripts of the ONE checkout it runs in.
Two of its questions cannot be answered from there:

  m6  "what share of editing sessions touched only meta repos?" — product work
      happens in OTHER repos (and other checkouts), so a sensor standing in the
      meta repo sees almost only meta sessions and saturates at 1.0 by construction.
      Measured 2026-11-19 in one workspace: 1.00 at checkout scope, 0.14 (7 days)
      and 0.54 (30 days) across the host.
  w*  "how long do agents wait on the human?" — a property of every interactive
      session on the host, not of one directory.

This sensor reads EVERY Claude Code and Codex transcript on the host within the
window and writes <workspace>/.control/leverage-fleet-state.json. The main sensor
merges it (merge_fleet). Because the scan is host-wide, every checkout that runs it
computes the same values: the verdict no longer depends on where it is run from.

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
Claude lines are deduplicated by uuid, oldest file first.

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
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

STATE_NAME = "leverage-fleet-state.json"
DEFAULT_CAP_MIN = 30.0
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
PATCH_FILE_RE = re.compile(r"\*\*\* (?:Update|Add|Delete) File: ([^\n\\\"]+)")
# A shell redirect target: `> f`, `>> f` after whitespace or a command separator
# (so `->`, `=>` and `a>b` in heredoc bodies do not match), never `2>&1` or `>&2`.
# Only path-like targets count (a `/` or a file extension), so `x > 0` in an inline
# script is not a write.
REDIRECT_RE = re.compile(r"(?:^|[\s;&|])>>?\s*([^\s;&|<>()'\"]+)")
TEE_RE = re.compile(r"\btee\s+(?:-a\s+)?([^\s;&|<>()'\"]+)")
PATHLIKE_RE = re.compile(r"/|\.[A-Za-z0-9]{1,8}$")
# `sed -i[SUFFIX] [-e] SCRIPT FILE` — the FILE is the write target.
SED_INPLACE_RE = re.compile(
    r"\bsed\s+(?:-[A-Za-z]+\s+)*-i\S*\s+(?:-e\s+)?(?:'[^']*'|\"[^\"]*\"|\S+)\s+([^\s;&|<>()'\"]+)")


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


def load_config(workspace):
    path = os.path.join(workspace, ".control", "leverage-setpoints.yaml")
    try:
        with open(path) as f:
            sp = _import_yaml().safe_load(f) or {}
    except Exception as e:
        print(f"[fleet-sensor] WARN could not load setpoints ({path}): {e}", file=sys.stderr)
        return {}, 7
    cfg = sp.get("fleet") if isinstance(sp, dict) else None
    window = sp.get("window_days", 7) if isinstance(sp, dict) else 7
    return (cfg if isinstance(cfg, dict) else {}), window


def _ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


@functools.lru_cache(maxsize=None)
def repo_of_dir(d):
    """owner/name of the GitHub repo containing directory d, else None. A missing
    directory resolves through its nearest existing ancestor."""
    if not d:
        return None
    d = os.path.abspath(d)
    while d and d != "/" and not os.path.isdir(d):
        d = os.path.dirname(d)
    if not d or d == "/":
        return None
    try:
        r = subprocess.run(["git", "-C", d, "remote", "get-url", "origin"],
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


def shell_write_repos(cmd, cwd):
    """Repos a shell command writes into: the targets of redirects, `tee` and
    `sed -i`, resolved as paths. A target that starts with `$` cannot be resolved
    without running the shell (it is usually a temp dir) and is skipped rather than
    guessed onto the working directory. Heuristic by design; the number of sessions
    it decides is reported separately in raw."""
    out = set()
    for tgt in REDIRECT_RE.findall(cmd) + TEE_RE.findall(cmd) + SED_INPLACE_RE.findall(cmd):
        if tgt.startswith(("/dev/", "&", "$")) or not PATHLIKE_RE.search(tgt):
            continue
        r = repo_of_path(tgt, cwd)
        if r:
            out.add(r)
    return out


# ── transcript readers ─────────────────────────────────────────────────────────

def read_claude(path, seen_uuids):
    """events: [(t, kind)] kind in human|machine|agent; edits: set of repos
    (tool edits), shell: set of repos (shell writes); meta: cwd/entry."""
    ev, tool_repos, shell_repos = [], set(), set()
    cwd = entry = None
    tool_edit_seen = False
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
            cwd = cwd or o.get("cwd")
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
                if is_tool:
                    ev.append((t, "agent"))
                elif (is_text and not o.get("isMeta") and not o.get("isCompactSummary")
                      and not (isinstance(content, str) and content.lstrip().startswith(
                          ("<local-command-stdout", "<local-command-caveat")))):
                    ev.append((t, "machine" if o.get("turnOrigin") == "task_notification" else "human"))
            elif typ == "assistant":
                if t is not None:
                    ev.append((t, "agent"))
                for it in content if isinstance(content, list) else []:
                    if not isinstance(it, dict) or it.get("type") != "tool_use":
                        continue
                    inp = it.get("input") if isinstance(it.get("input"), dict) else {}
                    name = it.get("name")
                    if name in EDIT_TOOLS:
                        tool_edit_seen = True
                        r = repo_of_path(inp.get("file_path") or inp.get("notebook_path"), cwd)
                        if r:
                            tool_repos.add(r)
                    elif name == "Bash":
                        shell_repos |= shell_write_repos(str(inp.get("command", "")), cwd)
    return {"events": ev, "tool_repos": tool_repos, "shell_repos": shell_repos,
            "edited": tool_edit_seen or bool(shell_repos), "cwd": cwd, "entry": entry}


def read_codex(path, seen_turns):
    cwd = src = None
    starts, ends, aborted = {}, {}, {}
    tool_repos, shell_repos = set(), set()
    edited = False
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
                cwd, src = p.get("cwd"), p.get("source")
                continue
            pt, tid = p.get("type"), p.get("turn_id")
            if pt == "task_started" and tid and p.get("started_at"):
                starts[tid] = float(p["started_at"])
            elif pt == "task_complete" and tid and p.get("completed_at"):
                ends[tid] = float(p["completed_at"])
                starts.setdefault(tid, float(p.get("started_at") or p["completed_at"]))
            elif pt == "turn_aborted" and tid:
                aborted[tid] = _ts(o.get("timestamp"))
            elif pt in ("custom_tool_call", "function_call"):
                raw_args = p.get("input") if p.get("input") is not None else p.get("arguments")
                s = raw_args if isinstance(raw_args, str) else json.dumps(raw_args or "")
                files = PATCH_FILE_RE.findall(s.replace("\\n", "\n"))
                if files:
                    edited = True
                    for f in files:
                        r = repo_of_path(f, cwd)
                        if r:
                            tool_repos.add(r)
                else:
                    wd = cwd
                    cmd = s
                    try:
                        a = json.loads(s) if isinstance(s, str) and s.startswith("{") else None
                        if isinstance(a, dict):
                            wd = a.get("workdir") or a.get("cwd") or cwd
                            c = a.get("cmd") if a.get("cmd") is not None else a.get("command")
                            cmd = " ".join(c) if isinstance(c, list) else str(c or "")
                    except Exception:
                        pass
                    sr = shell_write_repos(cmd, wd)
                    if sr:
                        edited = True
                        shell_repos |= sr
    ev = []
    for tid, st in starts.items():
        if tid in seen_turns:
            continue
        seen_turns.add(tid)
        en = ends.get(tid) or aborted.get(tid)
        ev.append((st, "human"))
        if en and en >= st:
            ev.append((en, "agent"))
    entry = src if isinstance(src, str) else ("subagent" if src else None)
    return {"events": ev, "tool_repos": tool_repos, "shell_repos": shell_repos,
            "edited": edited, "cwd": cwd, "entry": entry}


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

def measure(cfg, window_days, claude_root, codex_root, now=None):
    now = now or time.time()
    since = now - float(window_days) * 86400
    cap = float(cfg.get("wait_cap_minutes") or DEFAULT_CAP_MIN) * 60
    product = {str(r).lower() for r in (cfg.get("product_repos") or [])}
    meta = {str(r).lower() for r in (cfg.get("meta_repos") or [])}
    external = {str(r).lower() for r in (cfg.get("external_repos") or [])}
    # Sessions started in a scratch directory are throwaway runs (scripted evals,
    # dogfood fixtures), not work: excluded from both m6 and the wait metrics.
    scratch_prefixes = tuple(cfg.get("scratch_prefixes", ["/tmp", "/private/tmp"]) or ())

    files = []
    if claude_root:
        files += [("claude", f) for f in glob.glob(os.path.join(claude_root, "*", "*.jsonl"))]
    if codex_root:
        files += [("codex", f) for f in glob.glob(os.path.join(codex_root, "**", "*.jsonl"), recursive=True)]
    # Oldest first: a resumed session copies earlier lines, and the original must
    # claim them, or the copy steals the history and the original loses its gaps.
    files = [(h, f) for h, f in files if _mtime(f) >= since]
    files.sort(key=lambda hf: _mtime(hf[1]))

    seen_uuids, seen_turns = set(), set()
    read = {"claude": 0, "codex": 0}
    editing = meta_s = product_s = external_s = unclassified = shell_attributed = 0
    tool_meta = tool_product = 0  # the same rule on tool edits alone (sensitivity)
    unclassified_repos = {}
    interactive = []
    for h, f in files:
        s = read_claude(f, seen_uuids) if h == "claude" else read_codex(f, seen_turns)
        if s is None:
            continue
        read[h] += 1
        tt = [x for x in turns(s["events"]) if x[0] >= since]
        cwd = s["cwd"] or ""
        scratch = bool(scratch_prefixes) and cwd.startswith(scratch_prefixes)
        # m6 — repo classification of edits
        if s["edited"] and tt and not scratch:
            editing += 1
            repos = s["tool_repos"] | s["shell_repos"]
            if s["shell_repos"] - s["tool_repos"]:
                shell_attributed += 1
            if s["tool_repos"] & product:
                tool_product += 1
            elif s["tool_repos"] & meta:
                tool_meta += 1
            if repos & product:
                product_s += 1
            elif repos & meta:
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
    m6 = round(meta_s / working, 4) if (configured and working) else None
    w1 = round(wait_cap / (wait_cap + active), 4) if (gaps and active > 0) else None
    p50 = _pct(gaps, 0.5)
    w2 = round(p50 / 60, 2) if p50 is not None else None
    w3 = round(weighted / waiting, 3) if waiting > 0 else None
    notes = []
    if not configured:
        notes.append("fleet.product_repos / fleet.meta_repos not both configured — m6 not measured at fleet scope")
    return {
        "metrics": {
            "m6_meta_work_session_ratio": m6,
            "w1_agent_idle_share": w1,
            "w2_reply_latency_p50_min": w2,
            "w3_waiting_sessions_mean": w3,
        },
        "raw": {
            "transcripts_read": read,
            "editing_sessions": editing, "meta_sessions": meta_s, "product_sessions": product_s,
            "external_sessions": external_s,
            "unclassified_sessions": unclassified,
            "unclassified_repos": dict(sorted(unclassified_repos.items(), key=lambda kv: -kv[1])[:10]),
            "shell_write_attributed_sessions": shell_attributed,
            # m6 if shell writes were ignored. The shell heuristic decides many
            # sessions; when the two disagree on which side of the target m6 sits,
            # the reading is not robust and should not drive anything.
            "m6_tool_edits_only": (round(tool_meta / (tool_meta + tool_product), 4)
                                   if configured and (tool_meta + tool_product) else None),
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


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def write_state(record, workspace):
    state_file = os.path.join(workspace, ".control", STATE_NAME)
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
    if args.throttle:
        try:
            with open(os.path.join(workspace, ".control", STATE_NAME)) as f:
                st = json.load(f)
            if time.time() - datetime.fromisoformat(st["measured_at"]).timestamp() < args.throttle:
                return
        except Exception:
            pass
    if args.config_json:
        cfg, window = json.loads(args.config_json), 7
    else:
        cfg, window = load_config(workspace)
    if args.window is not None:
        window = args.window

    started = time.time()
    result = measure(cfg, window, args.claude_root, args.codex_root, now=args.now)
    record = {
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_days": window,
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
