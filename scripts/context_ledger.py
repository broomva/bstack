#!/usr/bin/env python3
"""
context_ledger.py — the context-ledger metric group of leverage-sensor.py.

What the context we inject costs, and whether anything uses it. Three kinds of text
reach a Claude Code session besides the user's prompt and the tools' results:

  hook      hook output the model sees: SessionStart and UserPromptSubmit stdout, and
            JSON `additionalContext` on any event (role-x intake, the ctx-core board
            brief, the self-improvement-loop brief, the auth pre-flight, ...)
  memory    the instruction files: CLAUDE.md, the auto-memory index MEMORY.md, and the
            nested CLAUDE.md files a Read pulls in
  harness   the listings that grow with what is installed (skills, MCP servers, agents)

Per session and per turn this measures the bytes each source injected, the pointers
those bytes carried (KG entity paths, docs/specs paths, ctx board session ids,
memory-index links), whether a LATER tool call in the same session followed one, and
which retrieval reflexes (KG, specs, memory, deep research) each session showed, split
by whether an injection pointed there.

Same rule as the sensor it extends (h ⟂ U): every number comes from transcript
STRUCTURE -- the attachment records the harness writes and the structured fields of
tool_use inputs. No assistant text or thinking block is read, and neither is a
tool input's free-prose field (an Agent prompt, a Write body, a SendMessage). The
suite replaces all of that prose and asserts the ledger does not move.

The ledger is SHADOW: measured, never graded. It cannot become `worst` and it emits no
actuator. Its one hard claim is its own liveness: a ledger that read sessions and found
no injected byte is reported `blind`, and doctor §29 fails on it (BRO-1696: an all-null
sensor must fail as a gap, never pass as a quiet reading).

leverage-sensor.py loads this file by PATH, not by import: the hooks start the sensor
with `python3 -I`, which keeps this directory off sys.path (BRO-2652). Stdlib only.
"""
import json
import re

SCHEMA = 1

# A hook's PLAIN stdout reaches the model on these two events only (the Claude Code hook
# contract). Stop and PreToolUse stdout is recorded in the transcript but never shown to
# the model: the Stop run of leverage-sensor.py writes ~1KB of summary there, and those
# records carry no `rendered` field, which every record the model did see carries.
# Counting them would bill the model for text it never read.
PLAIN_STDOUT_EVENTS = frozenset({"SessionStart", "UserPromptSubmit"})

# Header → source tag, checked against the START of each line, first match wins. The
# fallback is the hook's script name, then its hookName.
KNOWN_HEADERS = (
    ("[role-x intake", "role-x-intake"),
    ("Shared board facts", "ctx-core-board"),
    ("[self-improvement loop]", "self-improvement-loop"),
    ("[auth pre-flight]", "auth-preflight"),
)

# Harness listings: attachment type → the field(s) holding the text the model reads.
# Not bstack's text, but it grows with what the owner installed, so it belongs on the
# same bill. Everything else the harness attaches (dates, env, token counters) is small
# and is not what an owner can act on.
HARNESS_FIELDS = {
    "skill_listing": "content",
    "mcp_instructions_delta": "addedBlocks",
    "agent_listing_delta": "addedLines",
}

POINTER_KINDS = ("kg", "specs", "ctx", "memory")
REFLEX_KINDS = ("kg", "specs", "memory", "research")

# --- pointers in injected text (hook/harness output, never model prose) -------------
KG_POINTER_RE = re.compile(r"research/entities/([\w./-]+?)\.md\b")
SPECS_POINTER_RE = re.compile(r"\bdocs/specs/[\w./-]*[\w-]")
MEMORY_LINK_RE = re.compile(r"\]\(([\w.-]+\.md)\)")
# The board's own "This session: <id>" line names the reader, so only the rows about
# OTHER sessions ("- session <id>, Paseo agent <id>, ...") are pointers.
CTX_ROW_RE = re.compile(r"^- session ([0-9a-f]{8})\b(?:, Paseo agent ([0-9a-f]{8})\b)?", re.MULTILINE)
RESEARCH_POINTER_RE = re.compile(r"\bdeep[- ]research\b|\bWebSearch\b|\bWebFetch\b", re.IGNORECASE)

# --- uses in tool calls (structural fields only) --------------------------------------
KG_PATH_RE = re.compile(r"research/entities/([\w./-]+?)\.md\b")
SPECS_PATH_RE = re.compile(r"\bdocs/specs/[\w./-]*[\w-]")
SPECS_READ_RE = re.compile(r"(?:^|/)(?:docs/)?specs/")
MEMORY_PATH_RE = re.compile(r"\.claude/(?:projects/[^/]+/)?memory/([\w.-]+\.md)\b")
MEMORY_READ_RE = re.compile(r"\.claude/(?:projects/[^/]+/)?memory(?:/|$)")
HEX8_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{8})")
KG_LOAD_RE = re.compile(r"\bkg(?:\.py)?['\"]?\s+load\b([^\n;&|]*)")
CTX_BOARD_RE = re.compile(r"\bctx(?:\.py)?['\"]?\s+board\b", re.IGNORECASE)
HEREDOC_RE = re.compile(r"<<-?\s*['\"]?\w+['\"]?[\s\S]*", re.MULTILINE)
KG_SKILLS = frozenset({"kg", "checkit"})
RESEARCH_TOOLS = frozenset({"WebFetch", "WebSearch"})
RESEARCH_SKILLS = frozenset({
    "deep-research", "technical-research", "alpha-research", "literature-review",
    "last30days", "autoresearch", "financial-deep-research",
    "deep-dive-research-orchestrator", "interceptor-research", "checkit",
})

HEADLINE_KEYS = (
    "cl1_injected_bytes_per_turn_p50",
    "cl2_kg_pointer_follow_through_rate",
    "cl3_retrieval_reflex_session_rate",
)
TOKEN_ESTIMATE_NOTE = "bytes/4 — an estimate for English/markdown, not a tokenizer count"


def _nbytes(text):
    return len(text.encode("utf-8", errors="replace"))


def _as_text(value):
    """A string, or the newline-joined strings of a list; anything else is empty."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(v for v in value if isinstance(v, str))
    return ""


def _dist(values):
    """median (interpolated) and p90 (nearest rank) of a list of numbers."""
    if not values:
        return {"n": 0, "median": None, "p90": None}
    s = sorted(values)
    n = len(s)
    mid = n // 2
    median = s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2
    p90 = s[max(0, -(-9 * n // 10) - 1)]
    return {"n": n, "median": round(median, 1), "p90": p90}


def _rate(num, den):
    return round(num / den, 4) if den else None


def source_tag(text, command=None):
    """Which hook produced this text: a known header, else the script it ran.

    A hook_additional_context record names its event but not its command, so text
    with no known header from such a record is `unattributed` -- said, not guessed."""
    for line in text.splitlines():
        s = line.lstrip()
        for header, tag in KNOWN_HEADERS:
            if s.startswith(header):
                return tag
    scripts = re.findall(r"[\w.-]+\.(?:sh|py|js|mjs|ts|rb)\b", command or "")
    return scripts[-1] if scripts else "unattributed"


def hook_text(att):
    """The text ONE hook_success record put into context, and where it was found.

    The record carries plain output twice, in `content` and again in `stdout`, so the
    two are never added: `content` wins, `stdout` is read only when `content` is empty.
    JSON output leaves `content` empty and puts the text in
    `hookSpecificOutput.additionalContext`. Returns (text, origin) with origin one of
    "content", "stdout", "json" -- or ("", None) when the hook injected nothing."""
    content = att.get("content")
    if isinstance(content, str) and content.strip():
        return content, "content"
    stdout = att.get("stdout")
    if not isinstance(stdout, str) or not stdout.strip():
        return "", None
    if stdout.lstrip().startswith("{"):
        try:
            payload = json.loads(stdout)
        except ValueError:
            return "", None
        hso = payload.get("hookSpecificOutput") if isinstance(payload, dict) else None
        ctx = hso.get("additionalContext") if isinstance(hso, dict) else None
        if isinstance(ctx, str) and ctx.strip():
            return ctx, "json"
        # `{}` and `{"systemMessage": ...}` reach the user's screen, not the model.
        return "", None
    return stdout, "stdout"


def _human_prompt(obj):
    """A user record that is a prompt (text, not a tool_result, not harness meta)."""
    if obj.get("isMeta") or obj.get("isCompactSummary"):
        return False
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    kinds = {it.get("type") for it in content if isinstance(it, dict)}
    return "text" in kinds and "tool_result" not in kinds


class _Session:
    """One transcript file's ledger. Records are fed in order; `idx` is the order."""

    def __init__(self, kg_read_re, read_targets):
        self.kg_read_re = kg_read_re
        self.read_targets = read_targets
        self.own_records = 0
        self.fork_copies = 0
        self.hook_records = 0
        self.turns = 0
        self._prompt_id = None
        self.pre_turn = {}          # source → bytes injected before the first prompt
        self.turn_bytes = []        # per turn: source → bytes
        self.src = {}               # source → {"events", "bytes", "sizes"}
        self.not_injected = {}      # source → {"events", "bytes"}
        # JSON additionalContext is written TWICE on current Claude Code: inside the
        # hook_success stdout and again as a hook_additional_context record (the one
        # the model sees; the two share no id). Unmatched halves wait here so each
        # injection is counted once, whichever half arrives first.
        self._pending = {"json": {}, "hac": {}}
        self.pointers = {k: {} for k in POINTER_KINDS}   # kind → key → {first, sources}
        self.uses = {k: {} for k in POINTER_KINDS}       # kind → key → [first, last]
        self.kg_load_tokens = []    # (idx, tokens) from `kg.py load ...` / Skill(kg)
        self.board_cmds = []        # idx of each `ctx board` command
        self.reflexes = set()
        self.pointed = set()

    # -- turns ---------------------------------------------------------------------
    def on_user(self, obj):
        """A turn is one Claude Code promptId; before promptId existed, one prompt."""
        pid = obj.get("promptId")
        if pid:
            if pid != self._prompt_id:
                self._prompt_id = pid
                self.turn_bytes.append({})
                self.turns += 1
        elif _human_prompt(obj):
            self.turn_bytes.append({})
            self.turns += 1

    # -- injections ----------------------------------------------------------------
    def inject(self, source, text, idx):
        n = _nbytes(text)
        if not n:
            return
        s = self.src.setdefault(source, {"events": 0, "bytes": 0, "sizes": []})
        s["events"] += 1
        s["bytes"] += n
        s["sizes"].append(n)
        bucket = self.turn_bytes[-1] if self.turn_bytes else self.pre_turn
        bucket[source] = bucket.get(source, 0) + n
        # A harness listing is a catalog, not a pointer: the skill listing names
        # `deep-research` in every session, which would mark every session "pointed".
        if not source.startswith("harness:"):
            self._extract_pointers(source, text, idx)

    def _paired(self, half, event, text):
        """True when the other half of this JSON injection was already counted."""
        other = "hac" if half == "json" else "json"
        key = (event, text.strip())
        if self._pending[other].get(key, 0) > 0:
            self._pending[other][key] -= 1
            return True
        self._pending[half][key] = self._pending[half].get(key, 0) + 1
        return False

    def on_hook_success(self, att, idx):
        self.hook_records += 1
        text, origin = hook_text(att)
        if not text:
            return
        event = str(att.get("hookEvent") or "unknown")
        source = f"hook:{event}:{source_tag(text, att.get('command'))}"
        if origin == "json":
            if not self._paired("json", event, text):
                self.inject(source, text, idx)
        elif event in PLAIN_STDOUT_EVENTS:
            self.inject(source, text, idx)
        else:
            s = self.not_injected.setdefault(source, {"events": 0, "bytes": 0})
            s["events"] += 1
            s["bytes"] += _nbytes(text)

    def on_hook_additional_context(self, att, idx):
        self.hook_records += 1
        text = _as_text(att.get("content"))
        if not text.strip():
            return
        event = str(att.get("hookEvent") or "unknown")
        if not self._paired("hac", event, text):
            self.inject(f"hook:{event}:{source_tag(text)}", text, idx)

    def on_instructions(self, att, idx):
        for f in att.get("files") or []:
            if isinstance(f, dict) and isinstance(f.get("content"), str):
                self.inject(f"memory:{f.get('type') or 'unknown'}", f["content"], idx)

    def on_nested_memory(self, att, idx):
        body = att.get("content")
        if isinstance(body, dict) and isinstance(body.get("content"), str):
            self.inject(f"memory:nested-{body.get('type') or 'unknown'}", body["content"], idx)

    def _extract_pointers(self, source, text, idx):
        found = {k: set() for k in POINTER_KINDS}
        found["kg"].update(m.lower() for m in KG_POINTER_RE.findall(text))
        found["specs"].update(m.lower() for m in SPECS_POINTER_RE.findall(text))
        if source.endswith(":ctx-core-board"):
            for sid, agent in CTX_ROW_RE.findall(text):
                found["ctx"].update(x for x in (sid, agent) if x)
        if source == "memory:AutoMem":
            found["memory"].update(m.lower() for m in MEMORY_LINK_RE.findall(text))
        for kind, keys in found.items():
            if keys:
                self.pointed.add(kind)
            for key in keys:
                p = self.pointers[kind].setdefault(key, {"first": idx, "sources": set()})
                p["sources"].add(source)
        if RESEARCH_POINTER_RE.search(text):
            self.pointed.add("research")

    # -- uses ----------------------------------------------------------------------
    def _use(self, kind, key, idx):
        u = self.uses[kind].get(key)
        if u is None:
            self.uses[kind][key] = [idx, idx]
        else:
            u[1] = idx

    def _read_path(self, path, idx, opened=True):
        """One path a tool READ (or searched, for Glob: opened=False)."""
        p = path.lower()
        if not p:
            return
        if self.kg_read_re.search(p):
            self.reflexes.add("kg")
        if SPECS_READ_RE.search(p):
            self.reflexes.add("specs")
        if MEMORY_READ_RE.search(p):
            self.reflexes.add("memory")
        if not opened:
            return
        for key in KG_PATH_RE.findall(p):
            self._use("kg", key, idx)
        for key in SPECS_PATH_RE.findall(p):
            self._use("specs", key, idx)
        for key in MEMORY_PATH_RE.findall(p):
            self._use("memory", key, idx)

    def _ids(self, text, idx):
        for key in HEX8_RE.findall(text.lower()):
            self._use("ctx", key, idx)

    def _load_tokens(self, text, idx):
        toks = set()
        for w in text.split():
            w = w.strip("'\"").lower()
            if w.endswith(".md"):
                w = w[:-3]
            if w and not w.startswith("-"):
                toks.add(w)
                toks.add(w.rsplit("/", 1)[-1])
        if toks:
            self.kg_load_tokens.append((idx, toks))
            self.reflexes.add("kg")

    def on_tool_use(self, name, inp, idx):
        """Structural fields only. Free prose in a tool input (Agent.prompt,
        Write.content, SendMessage.message, WebFetch.prompt) is never read."""
        if name == "Read":
            self._read_path(str(inp.get("file_path") or ""), idx)
            self._ids(str(inp.get("file_path") or ""), idx)
        elif name == "Grep":
            self._read_path(str(inp.get("path") or ""), idx)
            self._ids(str(inp.get("path") or "") + " " + str(inp.get("pattern") or ""), idx)
        elif name == "Glob":
            self._read_path(str(inp.get("pattern") or "") + " " + str(inp.get("path") or ""), idx,
                            opened=False)
        elif name == "Bash":
            cmd = str(inp.get("command") or "")
            # A heredoc body is authored text, not an action on what it names.
            acted = HEREDOC_RE.sub(" ", cmd)
            for target in self.read_targets(acted.lower()):
                self._read_path(target, idx)
            for m in KG_LOAD_RE.finditer(acted):
                self._load_tokens(m.group(1), idx)
            if CTX_BOARD_RE.search(acted):
                self.board_cmds.append(idx)
            self._ids(acted, idx)
        elif name == "Skill":
            skill = str(inp.get("skill") or "").lower()
            if skill in KG_SKILLS:
                self.reflexes.add("kg")
                self._load_tokens(str(inp.get("args") or ""), idx)
            if skill in RESEARCH_SKILLS:
                self.reflexes.add("research")
        elif name in RESEARCH_TOOLS:
            self.reflexes.add("research")
        elif name.startswith("mcp__"):
            # An MCP call names a session or agent through its id fields (agentId,
            # workspaceId, id), never through its prose ones (prompt, initialPrompt).
            for k, v in inp.items():
                if isinstance(v, str) and k.lower().endswith("id"):
                    self._ids(v, idx)

    # -- follow-through ------------------------------------------------------------
    def followed(self, kind, key, first):
        """Whether a tool call AFTER the pointer's first injection used it."""
        u = self.uses[kind].get(key)
        if u and u[1] > first:
            return True
        if kind == "kg":
            slug = key.rsplit("/", 1)[-1]
            if any(i > first and (key in t or slug in t) for i, t in self.kg_load_tokens):
                return True
        if kind == "ctx" and any(i > first for i in self.board_cmds):
            return True
        return False

    def turn_series(self):
        """Per-turn source→bytes, with pre-prompt injections folded into turn one."""
        turns = [dict(t) for t in self.turn_bytes]
        if turns and self.pre_turn:
            for s, n in self.pre_turn.items():
                turns[0][s] = turns[0].get(s, 0) + n
        return turns


def _group(source):
    return source.split(":", 1)[0]


def read_session(path, iter_records, kg_read_re, read_targets):
    s = _Session(kg_read_re, read_targets)
    for idx, obj in enumerate(iter_records(path)):
        if not isinstance(obj, dict):
            continue
        # A forked session starts with a COPY of its parent's history, each record
        # marked `forkedFrom`. Those injections happened in the parent; counting them
        # again bills one injection twice (630 of 1,508 role-x firings on 2026-09-29).
        if obj.get("forkedFrom"):
            s.fork_copies += 1
            continue
        s.own_records += 1
        t = obj.get("type")
        if t == "user":
            s.on_user(obj)
        elif t == "attachment":
            att = obj.get("attachment")
            if not isinstance(att, dict):
                continue
            at = att.get("type")
            if at == "hook_success":
                s.on_hook_success(att, idx)
            elif at == "hook_additional_context":
                s.on_hook_additional_context(att, idx)
            elif at == "instructions":
                s.on_instructions(att, idx)
            elif at == "nested_memory":
                s.on_nested_memory(att, idx)
            elif at in HARNESS_FIELDS:
                s.inject(f"harness:{at}", _as_text(att.get(HARNESS_FIELDS[at])), idx)
        elif t == "assistant":
            content = (obj.get("message") or {}).get("content")
            for it in content if isinstance(content, list) else []:
                # tool_use blocks only: `text` and `thinking` are the model's prose.
                if isinstance(it, dict) and it.get("type") == "tool_use":
                    inp = it.get("input") if isinstance(it.get("input"), dict) else {}
                    s.on_tool_use(str(it.get("name") or ""), inp, idx)
    return s


def analyze_context(files, kg_read_re, read_targets, iter_records):
    """The context_ledger block for one window of transcript files."""
    sessions = [read_session(p, iter_records, kg_read_re, read_targets) for p in files]
    live_sessions = [s for s in sessions if s.own_records]

    per_session_total, per_turn_total = [], []
    src = {}
    not_injected = {}
    turns = 0
    for s in live_sessions:
        turns += s.turns
        per_session_total.append(sum(v["bytes"] for v in s.src.values()))
        series = s.turn_series()
        per_turn_total.extend(sum(t.values()) for t in series)
        for name, v in s.src.items():
            a = src.setdefault(name, {"events": 0, "bytes": 0, "sessions": 0,
                                      "sizes": [], "per_session": [], "per_turn": []})
            a["events"] += v["events"]
            a["bytes"] += v["bytes"]
            a["sessions"] += 1
            a["sizes"].extend(v["sizes"])
            a["per_session"].append(v["bytes"])
            a["per_turn"].extend(t.get(name, 0) for t in series)
        for name, v in s.not_injected.items():
            a = not_injected.setdefault(name, {"events": 0, "bytes": 0})
            a["events"] += v["events"]
            a["bytes"] += v["bytes"]

    sources = {}
    for name, a in sorted(src.items(), key=lambda kv: -kv[1]["bytes"]):
        sources[name] = {
            "group": _group(name), "events": a["events"], "sessions": a["sessions"],
            "bytes": a["bytes"], "est_tokens": a["bytes"] // 4,
            "per_event": _dist(a["sizes"]),
            "per_session": _dist(a["per_session"]),
            "per_turn": _dist(a["per_turn"]),
        }
    total_bytes = sum(a["bytes"] for a in src.values())
    by_group = {}
    for name, a in src.items():
        by_group[_group(name)] = by_group.get(_group(name), 0) + a["bytes"]

    follow = _follow_through(live_sessions)
    reflexes = _reflexes(live_sessions)

    if not sessions:
        status, reason = "no_data", "no session file in the window"
    elif not live_sessions:
        status, reason = "no_data", "every record in the window was a fork copy"
    elif total_bytes == 0:
        status = "blind"
        reason = (f"read {len(live_sessions)} session(s), {sum(s.own_records for s in live_sessions)} "
                  f"records, {sum(s.hook_records for s in live_sessions)} hook records, "
                  "and extracted 0 injected bytes — the attachment schema no longer matches")
    else:
        status, reason = "live", None

    turn_dist = _dist(per_turn_total)
    headline = dict.fromkeys(HEADLINE_KEYS)
    if status == "live":
        headline = {
            HEADLINE_KEYS[0]: turn_dist["median"],
            HEADLINE_KEYS[1]: follow["by_kind"]["kg"]["rate"],
            HEADLINE_KEYS[2]: reflexes["any"]["rate"],
        }
    return {
        "schema": SCHEMA,
        "status": status,
        "status_reason": reason,
        "headline": headline,
        "files": len(files),
        "sessions": len(live_sessions),
        "turns": turns,
        "turn_unit": "one Claude Code promptId (pre-promptId transcripts: one human prompt)",
        "fork_copied_records_skipped": sum(s.fork_copies for s in sessions),
        "token_estimate": TOKEN_ESTIMATE_NOTE,
        # CLAUDE.md and MEMORY.md reach the transcript as `instructions` and
        # `nested_memory` attachments carrying their full text. When neither appears,
        # their size cannot be read from here, and that is said rather than zeroed.
        "memory_observable": "memory" in by_group,
        "totals": {
            "bytes": total_bytes, "est_tokens": total_bytes // 4,
            "by_group": {g: {"bytes": b, "est_tokens": b // 4} for g, b in sorted(by_group.items())},
            "per_session": _dist(per_session_total),
            "per_turn": turn_dist,
        },
        "sources": sources,
        "not_injected": dict(sorted(not_injected.items(), key=lambda kv: -kv[1]["bytes"])),
        "follow_through": follow,
        "reflexes": reflexes,
    }


def _follow_through(sessions):
    """Unit: one (session, pointer) pair, however often the pointer was re-injected."""
    by_kind = {k: {"injected": 0, "followed": 0} for k in POINTER_KINDS}
    by_source = {}
    opened = {k: {"opened": 0, "opened_never_injected": 0, "opened_before_injection_only": 0}
              for k in ("kg", "specs", "memory")}
    for s in sessions:
        for kind in POINTER_KINDS:
            for key, p in s.pointers[kind].items():
                hit = s.followed(kind, key, p["first"])
                by_kind[kind]["injected"] += 1
                by_kind[kind]["followed"] += hit
                for source in p["sources"]:
                    b = by_source.setdefault(source, {}).setdefault(kind, {"injected": 0, "followed": 0})
                    b["injected"] += 1
                    b["followed"] += hit
        for kind in opened:
            for key, (first_use, last_use) in s.uses[kind].items():
                o = opened[kind]
                o["opened"] += 1
                p = s.pointers[kind].get(key)
                if p is None:
                    o["opened_never_injected"] += 1
                elif last_use < p["first"]:
                    o["opened_before_injection_only"] += 1
    for v in by_kind.values():
        v["rate"] = _rate(v["followed"], v["injected"])
    for kinds in by_source.values():
        for v in kinds.values():
            v["rate"] = _rate(v["followed"], v["injected"])
    for o in opened.values():
        # Self-directed retrieval: the share of what the agent opened that nothing
        # injected into this session had pointed at.
        o["self_directed_share"] = _rate(o["opened_never_injected"], o["opened"])
    injected = sum(v["injected"] for v in by_kind.values())
    followed = sum(v["followed"] for v in by_kind.values())
    return {
        "unit": "(session, pointer) pair; followed = a later tool call used it",
        "overall": {"injected": injected, "followed": followed, "rate": _rate(followed, injected)},
        "by_kind": by_kind,
        "by_source": dict(sorted(by_source.items())),
        "opened": opened,
    }


def _reflexes(sessions):
    out = {}
    for kind in REFLEX_KINDS:
        pointed = [s for s in sessions if kind in s.pointed]
        unpointed = [s for s in sessions if kind not in s.pointed]
        out[kind] = {
            "pointed": {"sessions": len(pointed),
                        "with_reflex": sum(kind in s.reflexes for s in pointed)},
            "unpointed": {"sessions": len(unpointed),
                          "with_reflex": sum(kind in s.reflexes for s in unpointed)},
        }
        for side in ("pointed", "unpointed"):
            d = out[kind][side]
            d["rate"] = _rate(d["with_reflex"], d["sessions"])
    anyr = sum(bool(s.reflexes) for s in sessions)
    out["any"] = {"sessions": len(sessions), "with_reflex": anyr, "rate": _rate(anyr, len(sessions))}
    return out
