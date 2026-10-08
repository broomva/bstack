#!/usr/bin/env python3
"""session-loops-hook.py — record a session's crons on the loops/session stream (BRO-2932).

Loop layer P3 (spec: broomva/workspace docs/specs/2026-10-07-unified-loop-layer.html
§5). A Claude Code session cron lives only in the session's memory: when the
session dies the cron dies with it, and nothing on disk ever said it existed
(10-06: the coordinator's cron 42152ef6 vanished on a restart and ~30 h passed
before anyone noticed). This hook writes what the harness forgets to
<BROOMVA_HOME>/ledger/loops/session.jsonl, through the BRO-2909 writer, so
Maestro can judge ORPHANED and EXPIRING against it. The writer is
scripts/broomva_home.py, a byte-identical copy of broomva/workspace's (the
source commit and its sha256 are pinned in tests/session-loops-hook.test.sh):
importing the workspace checkout's copy would make every write depend on
which branch that checkout happens to be on.

Events (measured on Claude Code 2.1.280; payloads in tests/fixtures/session-loops/):

  PostToolUse CronCreate  tool_response.id         -> loop.session.cron_created (via CronCreate)
  PostToolUse CronDelete  tool_input.id            -> loop.session.cron_deleted (via CronDelete)
  Stop                    session_crons, every turn -> on change only:
                            a cron the cache lacks  -> loop.session.cron_created (via snapshot)
                            a cron gone from it     -> loop.session.cron_deleted (via snapshot:
                                                       expired, a one-shot that fired, or
                                                       deleted where no PostToolUse saw it)
                            then                    -> loop.session.snapshot
  UserPromptSubmit        prompt == a cron's prompt -> loop.run.started (a cron fire carries
                                                       no origin field; equality is the
                                                       correlation, §5)
  SessionEnd              reason                    -> loop.session.ended (only for a session
                                                       that ever had a cron)

The per-session cache (<BROOMVA_HOME>/cache/session-loops/<sid>.json) holds the
last snapshot's digest and the crons it listed. Most turns compare one digest
and write nothing: a session that never had a cron never creates a cache file.

Never a secret: a prompt is recorded as prompt_sha256 plus prompt_head (its
first 120 characters, "[redacted]" whole if the full prompt matches one of the
writer's secret patterns). Under a Stimulus / SRI checkout the record says scope: sri
and prompt_head is omitted.

Never blocks: every path exits 0, and an error is one line on stderr.

Run as `python3 -I`: the hook's cwd is the session's repository, and a json.py
committed there must not replace the standard library (BRO-2652).
"""

import json
import os
import re
import sys
import time

# hashlib and subprocess are imported where used: most calls (a Stop in a session
# that never had a cron) need neither, and they are a third of the import time.

STREAM = "loops/session"
CRON_LIFE_S = 7 * 24 * 3600  # CronCreate: "Recurring tasks auto-expire after 7 days"
PRUNE_AFTER_S = CRON_LIFE_S + 24 * 3600
PROMPT_HEAD = 120
# The hook's own deadline, under hooks.json's 5 s timeout. The writer's flock has
# no timeout and every session shares one stream lock, so a writer stuck holding
# it would otherwise hold every turn on the machine for the full 5 s.
DEADLINE_S = 3
# Session and cron ids become a file name and part of an envelope subject.
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
AGENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@+=-]{0,127}")
# A Stimulus/SRI checkout (the linear-routing-gate rule, BRO-2089): relative to
# HOME, plus <workspace>/work/stimulus.
SRI_HOME_ROOTS = ("conductor/workspaces/sri", ".superconductor/worktrees/sri")
# git reads these from the environment ahead of -C.
GIT_ENV = (
    "GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
)


def home_dir():
    """BROOMVA_HOME if set, else ~/.broomva: the writer's own rule.

    None for a relative BROOMVA_HOME, which the writer refuses: resolved here it
    would land the cache inside the session's repository (the hook's cwd).
    """
    raw = os.environ.get("BROOMVA_HOME", "")
    if not raw:
        return os.path.join(os.path.expanduser("~"), ".broomva")
    path = os.path.expanduser(raw)
    return path if os.path.isabs(path) else None


def cache_path(sid):
    return os.path.join(home_dir(), "cache", "session-loops", sid + ".json")


def load_cache(sid):
    try:
        with open(cache_path(sid), encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(cache, dict) or not isinstance(cache.get("crons"), dict):
        return None
    return cache


def save_cache(sid, cache):
    path = cache_path(sid)
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cache, f, sort_keys=True, separators=(",", ":"))
    os.replace(tmp, path)


def prune_caches(now):
    """Drop caches untouched for longer than a cron can live."""
    directory = os.path.dirname(cache_path("x"))
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        path = os.path.join(directory, name)
        try:
            if now - os.stat(path).st_mtime > PRUNE_AFTER_S:
                os.unlink(path)
        except OSError:
            continue


def sha256(text):
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def workspace():
    """$BROOMVA_WORKSPACE, else the `workspace:` key of ~/.bstack/config.yaml (SRI roots only)."""
    ws = os.environ.get("BROOMVA_WORKSPACE", "")
    if ws:
        return ws
    state = os.environ.get("BSTACK_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".bstack")
    try:
        with open(os.path.join(state, "config.yaml"), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    found = None
    for line in lines:  # bstack-config get: the last `workspace:` line wins
        if line.startswith("workspace:"):
            found = line[len("workspace:"):].strip().strip("'\"") or None
    return found


def load_writer():
    """The bundled BRO-2909 writer, imported by absolute path (never via sys.path), or None."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "scripts", "broomva_home.py")
    if not os.path.isfile(path):
        return None
    import importlib.util

    spec = importlib.util.spec_from_file_location("broomva_home", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def in_sri(cwd, ws):
    """(whether cwd is a Stimulus/SRI checkout, whether that answer may be cached).

    Fails closed: when git cannot answer (missing, timed out), the cwd counts as
    SRI for this call only, so a prompt head is withheld rather than leaked.
    """
    if not cwd:
        return False, True
    home = os.path.expanduser("~")
    roots = [os.path.join(home, r) for r in SRI_HOME_ROOTS]
    if ws:
        roots.append(os.path.join(ws, "work", "stimulus"))
    roots += [os.path.realpath(r) for r in roots]

    def under(path):
        return any(path == r or path.startswith(r + os.sep) for r in roots)

    if under(cwd) or under(os.path.realpath(cwd)):
        return True, True
    # A worktree can live anywhere; its common dir points back at the main checkout.
    if not os.path.isdir(cwd):
        return False, True
    import subprocess

    try:
        p = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=2,
            env={k: v for k, v in os.environ.items() if k not in GIT_ENV},
        )
    except Exception:
        return True, False
    g = p.stdout.strip() if p.returncode == 0 else ""
    if not g:
        return False, True  # not a git checkout, so not a worktree of one
    if not os.path.isabs(g):
        g = os.path.join(cwd, g)
    return under(os.path.dirname(os.path.realpath(g))), True


def cron_view(cron):
    """The recorded shape of one session_crons entry, or None if it is malformed."""
    if not isinstance(cron, dict):
        return None
    cid = cron.get("id")
    if not isinstance(cid, str) or not ID_RE.fullmatch(cid):
        return None
    prompt = cron.get("prompt") if isinstance(cron.get("prompt"), str) else ""
    schedule = cron.get("schedule") if isinstance(cron.get("schedule"), str) else ""
    return {
        "id": cid,
        "schedule": schedule,
        "recurring": bool(cron.get("recurring")),
        "prompt_sha256": sha256(prompt),
        "_prompt": prompt,  # in memory only: never cached, never written whole
    }


def digest(views):
    rows = sorted((v["id"], v["schedule"], v["recurring"], v["prompt_sha256"]) for v in views)
    return sha256(json.dumps(rows, separators=(",", ":")))


class Session:
    """One hook call: the payload, the session's cache, and a lazy writer."""

    def __init__(self, payload, sid, cache):
        self.payload = payload
        self.sid = sid
        self.cache = cache
        self._writer = None
        self._sri = None
        agent = os.environ.get("PASEO_AGENT_ID", "")
        self.agent = agent if AGENT_RE.fullmatch(agent) else None
        try:
            self.pid = int(os.environ.get("CLAUDE_PID", ""))
        except ValueError:
            self.pid = None
        cwd = payload.get("cwd")
        self.cwd = cwd if isinstance(cwd, str) else None

    def start_cache(self):
        if self.cache is None:
            self.cache = {"crons": {}, "digest": None}
        return self.cache

    def writer(self):
        if self._writer is None:
            self._writer = load_writer() or False
        return self._writer or None

    def sri(self):
        # Kept per cwd in the cache: the git lookup costs ~10 ms, and a session's
        # cwd rarely changes.
        if self._sri is None:
            known = (self.cache or {}).get("sri") or {}
            if self.cwd in known:
                self._sri = bool(known[self.cwd])
            else:
                self._sri, cacheable = in_sri(self.cwd, workspace())
                if cacheable and self.cache is not None and self.cwd:
                    self.cache["sri"] = {self.cwd: self._sri}
        return self._sri

    def cron_data(self, view, prompt):
        data = {
            "cron": view.get("schedule", ""),
            "recurring": bool(view.get("recurring")),
            "prompt_sha256": view["prompt_sha256"],
        }
        if self.sri():
            data["scope"] = "sri"
        elif self.writer().secret_match(prompt):
            # Matched on the whole prompt, not the head: a secret cut at the
            # 120th character no longer matches its own pattern.
            data["prompt_head"] = "[redacted]"
        else:
            data["prompt_head"] = prompt[:PROMPT_HEAD]
        return data

    def emit(self, type_, subject, data, cause=None):
        writer = self.writer()
        data = dict(data)
        data["pid"] = self.pid
        data["cwd"] = self.cwd
        refs = {"session": "session:" + self.sid}
        if self.agent:
            refs["agent"] = "agent:" + self.agent
        actor = "agent:" + self.agent if self.agent else "hook:session-loops"
        return writer.append(STREAM, type_, subject, data, actor=actor, refs=refs, cause=cause)["id"]

    def subject(self, cid):
        return "loop:cc/%s/%s" % (self.sid, cid)


def on_stop(s):
    crons = s.payload.get("session_crons")
    if not isinstance(crons, list):
        return  # not a harness that reports crons
    views = [v for v in (cron_view(c) for c in crons) if v]
    if s.cache is None and not views:
        return  # the common case: no crons, never had any
    new_digest = digest(views)
    if s.cache is not None and s.cache.get("digest") == new_digest:
        return
    if s.writer() is None:
        return
    # No snapshot of this session was ever recorded (no cache, or only a
    # PostToolUse's): a cron found now may predate the hook.
    first = s.cache is None or s.cache.get("digest") is None
    cache = s.start_cache()
    known = cache["crons"]
    live = {v["id"]: v for v in views}
    now = time.time()
    for cid, view in sorted(live.items()):
        if cid in known:
            continue
        if not view["recurring"]:
            # A one-shot (ScheduleWakeup's, every turn of a self-paced /loop):
            # the snapshot lists it and its fire is still correlated, but its
            # own created/deleted pair would be three fsync'd writes per turn
            # for a loop ORPHANED and EXPIRING never judge.
            known[cid] = {k: view[k] for k in ("schedule", "recurring", "prompt_sha256")}
            known[cid].update(created_event=None, seen_at=now)
            continue
        data = s.cron_data(view, view["_prompt"])
        data["via"] = "snapshot"
        if first:
            # No earlier turn of this session was seen: the cron may predate the
            # hook, so the time of this record is only a bound (§4 EXPIRING).
            data["created_at_lower_bound"] = True
        event = s.emit("loop.session.cron_created", s.subject(cid), data)
        known[cid] = {k: view[k] for k in ("schedule", "recurring", "prompt_sha256")}
        known[cid].update(created_event=event, seen_at=now)
    for cid in sorted(set(known) - set(live)):
        gone = known[cid]
        if not gone.get("recurring") and not gone.get("created_event"):
            del known[cid]  # a one-shot that fired: recorded by the snapshot alone
            continue
        s.emit("loop.session.cron_deleted", s.subject(cid),
               {"via": "snapshot", "recurring": bool(gone.get("recurring")),
                "prompt_sha256": gone.get("prompt_sha256", "")},
               cause=gone.get("created_event"))
        del known[cid]
    for cid, view in live.items():  # a schedule or prompt edited in place
        known[cid].update(schedule=view["schedule"], recurring=view["recurring"],
                          prompt_sha256=view["prompt_sha256"])
    s.emit("loop.session.snapshot", "session:" + s.sid, {
        "crons": [dict(s.cron_data(v, v["_prompt"]), id=v["id"]) for _, v in sorted(live.items())],
        "count": len(live),
    })
    cache["digest"] = new_digest


def on_tool(s):
    tool = s.payload.get("tool_name")
    ti = s.payload.get("tool_input") if isinstance(s.payload.get("tool_input"), dict) else {}
    tr = s.payload.get("tool_response") if isinstance(s.payload.get("tool_response"), dict) else {}
    if tool == "CronCreate":
        view = cron_view({"id": tr.get("id"), "schedule": ti.get("cron"),
                          "recurring": tr.get("recurring", ti.get("recurring")),
                          "prompt": ti.get("prompt")})
        if view is None or s.writer() is None:
            return
        cache = s.start_cache()
        if view["id"] in cache["crons"]:
            return
        data = s.cron_data(view, view["_prompt"])
        data["via"] = "CronCreate"
        event = s.emit("loop.session.cron_created", s.subject(view["id"]), data)
        cache["crons"][view["id"]] = {"schedule": view["schedule"], "recurring": view["recurring"],
                                      "prompt_sha256": view["prompt_sha256"],
                                      "created_event": event, "seen_at": time.time()}
        return
    if tool == "CronDelete":
        cid = ti.get("id") or tr.get("id")
        if not isinstance(cid, str) or not ID_RE.fullmatch(cid) or s.writer() is None:
            return
        # A cron the hook never saw (it predates the hook) is still worth the record.
        cache = s.start_cache()
        known = cache["crons"].get(cid, {})
        s.emit("loop.session.cron_deleted", s.subject(cid),
               {"via": "CronDelete", "recurring": bool(known.get("recurring")),
                "prompt_sha256": known.get("prompt_sha256", "")},
               cause=known.get("created_event"))
        cache["crons"].pop(cid, None)


def on_prompt(s):
    cache = s.cache
    if not cache or not cache["crons"]:
        return
    prompt = s.payload.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return
    digest_ = sha256(prompt)
    hits = sorted(cid for cid, c in cache["crons"].items() if c.get("prompt_sha256") == digest_)
    if not hits or s.writer() is None:
        return
    cid = hits[0]
    known = cache["crons"][cid]
    data = s.cron_data(dict(known), prompt)
    if len(hits) > 1:
        data["candidates"] = hits  # two crons share the prompt: the fire is one of them
    s.emit("loop.run.started", s.subject(cid), data, cause=known.get("created_event"))


def on_end(s):
    # main() only calls this when the cache file exists; an unreadable one still
    # means the session had a cron, so the end is recorded with crons unknown.
    if s.writer() is None:
        return
    reason = s.payload.get("reason")
    s.emit("loop.session.ended", "session:" + s.sid, {
        "reason": reason if isinstance(reason, str) else None,
        "crons": sorted(s.cache["crons"]) if s.cache else None,
    })
    s.cache = None
    try:
        os.unlink(cache_path(s.sid))
    except OSError:
        pass
    prune_caches(time.time())


HANDLERS = {"Stop": on_stop, "PostToolUse": on_tool, "UserPromptSubmit": on_prompt, "SessionEnd": on_end}


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    handler = HANDLERS.get(payload.get("hook_event_name"))
    sid = payload.get("session_id")
    if handler is None or not isinstance(sid, str) or not ID_RE.fullmatch(sid):
        return
    if home_dir() is None:
        return
    # The cheapest exit first: a prompt or a tool call in a session with no cache.
    if handler is on_prompt or handler is on_end:
        if not os.path.exists(cache_path(sid)):
            return
    s = Session(payload, sid, load_cache(sid))
    before = json.dumps(s.cache, sort_keys=True)
    try:
        handler(s)
    finally:
        # Handlers record each written event in s.cache as they go, so saving it
        # even after a later write failed keeps the next turn from writing the
        # same event twice.
        if s.cache is not None and json.dumps(s.cache, sort_keys=True) != before:
            save_cache(sid, s.cache)


def _deadline(signum, frame):
    sys.stderr.write("session-loops-hook: gave up after %d s (stream lock held?)\n" % DEADLINE_S)
    os._exit(0)


if __name__ == "__main__":
    try:
        import signal

        signal.signal(signal.SIGALRM, _deadline)
        signal.alarm(DEADLINE_S)
        main()
    except BaseException as exc:  # never break a session, whatever happened
        if not isinstance(exc, SystemExit) or exc.code not in (0, None):
            try:
                sys.stderr.write("session-loops-hook: %s: %s\n" % (type(exc).__name__, exc))
            except Exception:
                pass
    os._exit(0)
