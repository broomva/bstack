#!/usr/bin/env python3
"""
context_ledger.py — the context-ledger metric group of leverage-sensor.py.

What the context we inject costs, and whether anything uses it. Three kinds of text
reach a Claude Code session besides the user's prompt and the tools' results:

  hook      hook output the model sees: SessionStart and UserPromptSubmit stdout, JSON
            `additionalContext` on any event, and a hook's block `reason` (role-x
            intake, the ctx-core board brief, the self-improvement-loop brief, ...)
  memory    the instruction files: CLAUDE.md, the auto-memory index MEMORY.md, and the
            nested CLAUDE.md files a Read pulls in
  harness   the listings that grow with what is installed (skills, MCP instructions,
            agents, deferred tools)

Per session and per turn this measures the bytes each source injected, the pointers
those bytes carried (KG entity paths, docs/specs paths, ctx board session ids,
memory-index links), whether a LATER tool call in the same session followed one, and
which retrieval reflexes (KG, specs, memory, deep research) each session showed, split
by whether an injection pointed there. Subagent transcripts are measured as their own
block, and a parent's pointer that only its subagent opened is counted separately.

Same rule as the sensor it extends (h ⟂ U): every number comes from transcript
STRUCTURE -- the attachment records the harness writes and the structured fields of
tool_use inputs. No assistant text or thinking block is read, and neither is prose
that a structured field carries: an Agent prompt, a Write body, a Grep pattern, a
commit message inside a Bash command. A shell command is parsed into the program each
segment runs; only a program's own arguments are read, and only for `kg load`,
`ctx board` and the read verbs the sensor already allowlists.

The ledger is SHADOW: measured, never graded. It cannot become `worst` and it emits no
actuator. Its hard claim is its own liveness. A ledger that read sessions and billed
no byte is `blind`; one that saw a record the model was shown (Claude Code's
`rendered` field) and billed nothing from it is `partial`. Doctor §29 fails on both
(BRO-1696: a sensor that goes dark must fail as a gap, never pass as a quiet reading).

leverage-sensor.py loads this file by PATH, not by import: the hooks start the sensor
with `python3 -I`, which keeps this directory off sys.path (BRO-2652). Stdlib only.
"""
import json
import os
import re
import shlex

SCHEMA = 2

# A hook's PLAIN stdout reaches the model on these two events only; that is the Claude
# Code hook contract, and it decides billing. Stop and PreToolUse stdout is recorded in
# the transcript but not shown to the model (the Stop run of leverage-sensor.py writes
# ~1KB there). JSON `additionalContext` and a block `reason` reach it on any event.
PLAIN_STDOUT_EVENTS = frozenset({"SessionStart", "UserPromptSubmit"})

# Header → source tag, checked against the START of each line, first match wins. The
# fallback is the hook's script name.
KNOWN_HEADERS = (
    ("[role-x intake", "role-x-intake"),
    ("Shared board facts", "ctx-core-board"),
    ("[self-improvement loop]", "self-improvement-loop"),
    ("[auth pre-flight]", "auth-preflight"),
)

# Harness listings: attachment type → the field holding the listing. They are not
# bstack's text, but they grow with what the owner installed, so they belong on the same
# bill. A listing record that carries `rendered` is billed from it instead: a delta also
# tells the model which MCP servers need auth or disconnected, and that notice is in no
# listing field (10 such records in the 2026-09-29 window). Every other model-visible
# attachment is reported, unattributed, under `unattributed_visible` rather than dropped.
HARNESS_FIELDS = {
    "skill_listing": "content",
    "mcp_instructions_delta": "addedBlocks",
    "agent_listing_delta": "addedLines",
    "deferred_tools_delta": "addedLines",
}
BILLED_TYPES = frozenset({"hook_success", "hook_additional_context", "instructions",
                          "nested_memory", *HARNESS_FIELDS})

POINTER_KINDS = ("kg", "specs", "ctx", "memory")
REFLEX_KINDS = ("kg", "specs", "memory", "research")

# Paths, matched the same way in injected text (a pointer) and in a tool's path (a use).
KG_PATH_RE = re.compile(r"research/entities/([\w./-]+?)\.md\b")
SPECS_PATH_RE = re.compile(r"\bdocs/specs/[\w./-]*[\w-]")
SPECS_READ_RE = re.compile(r"(?:^|/)(?:docs/)?specs/")
MEMORY_LINK_RE = re.compile(r"\]\(([\w.-]+\.md)\)")
MEMORY_PATH_RE = re.compile(r"\.claude/(?:projects/[^/]+/)?memory/([\w.-]+\.md)\b")
MEMORY_READ_RE = re.compile(r"\.claude/(?:projects/[^/]+/)?memory(?:/|$)")
MEMORY_INDEX = "memory.md"
# The board's own "This session: <id>" line names the reader, so only the rows about
# OTHER sessions ("- session <id>, Paseo agent <id>, ...") are pointers.
CTX_ROW_RE = re.compile(r"^- session ([0-9a-f]{8})\b(?:, Paseo agent ([0-9a-f]{8})\b)?", re.MULTILINE)
HEX8_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{8})")
RESEARCH_POINTER_RE = re.compile(r"\bdeep[- ]research\b|\bWebSearch\b|\bWebFetch\b", re.IGNORECASE)

KG_SKILLS = frozenset({"kg", "checkit"})
RESEARCH_TOOLS = frozenset({"WebFetch", "WebSearch"})
RESEARCH_SKILLS = frozenset({
    "deep-research", "technical-research", "alpha-research", "literature-review",
    "last30days", "autoresearch", "financial-deep-research",
    "deep-dive-research-orchestrator", "interceptor-research", "checkit",
})
# Programs a segment may be wrapped in before the one it runs.
_WRAPPERS = frozenset({"sudo", "env", "timeout", "nice", "nohup", "time", "command", "exec"})
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PYTHON_RE = re.compile(r"^python(?:\d+(?:\.\d+)?)?$")
_KG_LOAD_VALUE_OPTS = frozenset({"--n", "-n", "--type", "--terms", "--limit"})

HEADLINE_KEYS = (
    "cl1_injected_bytes_per_session_p50",
    "cl2_kg_pointer_follow_through_rate",
    "cl3_retrieval_reflex_session_rate",
)
TOKEN_ESTIMATE_NOTE = "bytes/4 — an estimate for English/markdown, not a tokenizer count"
REFLEX_NOTE = ("observational: sessions no injection pointed at differ in other ways "
               "(many are idle), so pointed-vs-unpointed is not an effect size; the "
               "Layer-2 ablation measures that")


def _nbytes(text):
    return len(text.encode("utf-8", errors="replace"))


def _as_text(value):
    """A string, or the newline-joined strings of a list; anything else is empty."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(v for v in value if isinstance(v, str))
    return ""


def rendered_text(rendered):
    """Claude Code's `rendered` field (2.1.280+): the text the model was shown for a
    record, wrapper included. Empty when the field is absent or not the known shape."""
    parts = []
    for item in rendered if isinstance(rendered, list) else []:
        body = item.get("content") if isinstance(item, dict) else item
        if isinstance(body, str):
            parts.append(body)
        elif isinstance(body, list):
            parts.extend(b["text"] for b in body
                         if isinstance(b, dict) and isinstance(b.get("text"), str))
    return "\n".join(parts)


def _dist(values):
    """median (interpolated) and p90 (nearest rank) of a list of numbers."""
    if not values:
        return {"n": 0, "median": None, "p90": None}
    s = sorted(values)
    n = len(s)
    mid = n // 2
    median = s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2
    return {"n": n, "median": round(median, 1), "p90": s[max(0, -(-9 * n // 10) - 1)]}


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
    A JSON object's text is its `hookSpecificOutput.additionalContext`, or the `reason`
    of a `"decision": "block"`. Output that starts with `{` but is not a JSON object is
    plain text, as Claude Code treats it. Returns (text, origin), origin one of
    "content", "stdout", "json" -- or ("", None) when the hook injected nothing."""
    content = att.get("content")
    if isinstance(content, str) and content.strip():
        return content, "content"
    stdout = att.get("stdout")
    if not isinstance(stdout, str) or not stdout.strip():
        return "", None
    try:
        payload = json.loads(stdout) if stdout.lstrip().startswith("{") else None
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return stdout, "stdout"
    hso = payload.get("hookSpecificOutput")
    ctx = hso.get("additionalContext") if isinstance(hso, dict) else None
    if isinstance(ctx, str) and ctx.strip():
        return ctx, "json"
    reason = payload.get("reason")
    if payload.get("decision") == "block" and isinstance(reason, str) and reason.strip():
        return reason, "json"
    # `{}` and `{"systemMessage": ...}` reach the user's screen, not the model.
    return "", None


def attachment_texts(att, shown=""):
    """(source, event, text, origin) for each piece of text one attachment carries.
    `shown` is the record's rendered text, used for harness listings.

    Pure: the same call serves a session's own records (billed) and a fork's copies of
    its parent's (read for pointers only)."""
    at = att.get("type")
    if at == "hook_success":
        text, origin = hook_text(att)
        if not text:
            return []
        event = att.get("hookEvent") if isinstance(att.get("hookEvent"), str) else None
        return [(f"hook:{event or 'unknown'}:{source_tag(text, att.get('command'))}",
                 event, text, origin)]
    if at == "hook_additional_context":
        event = att.get("hookEvent") if isinstance(att.get("hookEvent"), str) else None
        body = att.get("content")
        # One entry per hook: a record that batches two hooks' text is two injections,
        # each paired with its own JSON twin.
        entries = body if isinstance(body, list) else [body]
        return [(f"hook:{event or 'unknown'}:{source_tag(e)}", event, e, "hac")
                for e in entries if isinstance(e, str) and e.strip()]
    if at == "instructions":
        return [(f"memory:{f.get('type') or 'unknown'}", None, f["content"], "file")
                for f in att.get("files") or []
                if isinstance(f, dict) and isinstance(f.get("content"), str)]
    if at == "nested_memory":
        body = att.get("content")
        if isinstance(body, dict) and isinstance(body.get("content"), str):
            return [(f"memory:nested-{body.get('type') or 'unknown'}", None, body["content"], "file")]
        return []
    if at in HARNESS_FIELDS:
        text = shown or _as_text(att.get(HARNESS_FIELDS[at]))
        return [(f"harness:{at}", None, text, "listing")] if text else []
    return []


def text_pointers(source, text):
    """kind → set of pointer keys named by one injected text. Harness catalogs name no
    pointers: the skill listing lists `deep-research` in every session."""
    found = {k: set() for k in POINTER_KINDS}
    if source.startswith("harness:"):
        return found, False
    found["kg"].update(m.lower() for m in KG_PATH_RE.findall(text))
    found["specs"].update(m.lower() for m in SPECS_PATH_RE.findall(text))
    if source.endswith(":ctx-core-board"):
        for sid, agent in CTX_ROW_RE.findall(text):
            found["ctx"].update(x for x in (sid, agent) if x)
    if source == "memory:AutoMem":
        found["memory"].update(m.lower() for m in MEMORY_LINK_RE.findall(text))
        found["memory"].discard(MEMORY_INDEX)
    return found, bool(RESEARCH_POINTER_RE.search(text))


def shell_segments(cmd):
    """The token lists of a shell command's segments, quotes respected.

    A heredoc and everything after it are dropped: the body is authored text, and the
    sensor's own read detection drops it the same way. An unparseable command (an
    unbalanced quote) yields no segments -- an under-count, the safe direction."""
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|()<>\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    segs, seg = [], []
    try:
        for tok in lex:
            if tok.startswith("<<"):
                break
            if tok and all(c in ";&|()\n" for c in tok):
                if seg:
                    segs.append(seg)
                seg = []
                continue
            seg.append(tok)
    except ValueError:
        return []
    if seg:
        segs.append(seg)
    return segs


def segment_program(seg):
    """(program, args) a segment runs: past VAR=value, wrappers such as `timeout 150`,
    and a python interpreter, so `KG_NO_POLICY=1 timeout 150 python3 -I kg.py load x`
    is ("kg.py", ["load", "x"]). `$CTX` is the ctx-core skill's documented alias."""
    i = 0
    while i < len(seg):
        tok = seg[i]
        if _ASSIGN_RE.match(tok):
            i += 1
            continue
        base = os.path.basename(tok)
        if base in _WRAPPERS:
            i += 1
            while i < len(seg) and seg[i].startswith("-"):
                i += 1
            if base == "timeout" and i < len(seg) and re.match(r"^[\d.]+[smhd]?$", seg[i]):
                i += 1
            continue
        break
    rest = seg[i:]
    if not rest:
        return None, []
    name, args = os.path.basename(rest[0]).lower(), rest[1:]
    if _PYTHON_RE.match(name):
        j = 0
        while j < len(args) and args[j].startswith("-"):
            j += 1
        if j >= len(args):
            return name, []
        name, args = os.path.basename(args[j]).lower(), args[j + 1:]
    if name in ("$ctx", "${ctx}"):
        name = "ctx"
    return name, args


def load_query_tokens(args):
    """The query words of `kg load <query...>`, normalized for slug matching."""
    toks, skip = set(), False
    for a in args:
        if skip:
            skip = False
            continue
        if a.startswith("-"):
            skip = a in _KG_LOAD_VALUE_OPTS
            continue
        for w in a.split():
            w = w.strip("'\",.;:()[]").lower()
            if w.endswith(".md"):
                w = w[:-3]
            if w:
                toks.add(w)
    return toks


class _Session:
    """One transcript file. Records are fed in order; `idx` is the order, `ts` the
    record's own timestamp (used only to order a parent against its subagents)."""

    def __init__(self, path, kg_read_re, read_targets):
        self.path = path
        self.kg_read_re = kg_read_re
        self.read_targets = read_targets
        self.own_records = self.fork_copies = self.malformed = 0
        self.turns = 0
        self._prompt_id = None
        self.candidates = []        # injections, billed in finish()
        self.visible = {}           # idx → type, for billed-type records with `rendered`
        self.unattributed_visible = {}
        self.src = {}               # source → {"events", "bytes", "sizes"}
        self.turn_bytes = {}        # turn (-1 = before the first prompt) → source → bytes
        self.not_injected = {}
        self.unknown_event = 0
        self.unbilled_visible = {}
        self.pointers = {k: {} for k in POINTER_KINDS}   # kind → key → {first, ts, sources}
        self.uses = {k: {} for k in POINTER_KINDS}       # kind → key → [first, last, last_ts]
        self.kg_loads = []          # (idx, ts, tokens)
        self.board_cmds = []        # (idx, ts)
        self.reflexes = set()
        self.pointed = set()

    # -- feeding ---------------------------------------------------------------------
    def feed(self, obj, idx):
        ts = obj.get("timestamp") if isinstance(obj.get("timestamp"), str) else ""
        t = obj.get("type")
        att = obj.get("attachment") if t == "attachment" else None
        if obj.get("forkedFrom"):
            # A fork starts with a COPY of its parent's history. Those injections were
            # made, and billed, in the parent -- but the fork's model still sees them,
            # so their pointers count as "pointed" for the reflex split.
            self.fork_copies += 1
            if isinstance(att, dict):
                for source, _, text, _ in attachment_texts(att):
                    self._mark_pointed(source, text)
            return
        self.own_records += 1
        if t == "user":
            self._on_user(obj)
        elif t == "attachment":
            if not isinstance(att, dict):
                self.malformed += 1
                return
            at = att.get("type")
            shown = rendered_text(obj.get("rendered"))
            if at in BILLED_TYPES:
                if shown:
                    self.visible[idx] = at
                for source, event, text, origin in attachment_texts(att, shown):
                    self.candidates.append({"idx": idx, "ts": ts, "turn": self.turns - 1,
                                            "source": source, "event": event,
                                            "text": text, "origin": origin})
            elif shown:
                u = self.unattributed_visible.setdefault(str(at), {"records": 0, "bytes": 0})
                u["records"] += 1
                u["bytes"] += _nbytes(shown)
        elif t == "assistant":
            msg = obj.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if msg is not None and not isinstance(msg, dict):
                self.malformed += 1
            for it in content if isinstance(content, list) else []:
                # tool_use blocks only: `text` and `thinking` are the model's prose.
                if isinstance(it, dict) and it.get("type") == "tool_use":
                    inp = it.get("input") if isinstance(it.get("input"), dict) else {}
                    self._on_tool_use(str(it.get("name") or ""), inp, idx, ts)

    def _on_user(self, obj):
        """A turn is one Claude Code promptId; before promptId existed, one prompt."""
        pid = obj.get("promptId")
        if pid:
            if pid != self._prompt_id:
                self._prompt_id = pid
                self.turns += 1
            return
        msg = obj.get("message")
        if not isinstance(msg, dict):
            self.malformed += msg is not None
            return
        if obj.get("isMeta") or obj.get("isCompactSummary"):
            return
        content = msg.get("content")
        if isinstance(content, str):
            human = bool(content.strip())
        elif isinstance(content, list):
            kinds = {it.get("type") for it in content if isinstance(it, dict)}
            human = "text" in kinds and "tool_result" not in kinds
        else:
            human = False
        if human:
            self.turns += 1

    # -- billing ---------------------------------------------------------------------
    def finish(self):
        """Bill every candidate once. A JSON injection and its hook_additional_context
        twin are paired within ONE turn by (event, text): the twin carries no id, and a
        pair that spanned turns would swallow a later, genuine injection. The pair is
        billed under the JSON half's source, which names the hook's command."""
        halves = {}
        for i, c in enumerate(self.candidates):
            if c["origin"] in ("json", "hac"):
                key = (c["turn"], c["event"], c["text"].strip())
                halves.setdefault(key, {"json": [], "hac": []})[c["origin"]].append(i)
        twin_of = {}
        for h in halves.values():
            for j, k in zip(h["json"], h["hac"]):
                twin_of[j], twin_of[k] = k, j
        billed_idx = set()
        for i, c in enumerate(self.candidates):
            twin = twin_of.get(i)
            if twin is not None and c["origin"] == "hac":
                billed_idx.add(c["idx"])      # billed through its JSON half
                continue
            if c["origin"] in ("content", "stdout"):
                if c["event"] not in PLAIN_STDOUT_EVENTS:
                    if c["event"] is None:
                        self.unknown_event += 1
                    s = self.not_injected.setdefault(c["source"], {"events": 0, "bytes": 0})
                    s["events"] += 1
                    s["bytes"] += _nbytes(c["text"])
                    continue
            first = min(c["idx"], self.candidates[twin]["idx"]) if twin is not None else c["idx"]
            self._bill(c["source"], c["text"], c["turn"], first, c["ts"])
            billed_idx.add(c["idx"])
        # Drift check against Claude Code's own record of what the model was shown.
        for idx, at in self.visible.items():
            if idx not in billed_idx:
                self.unbilled_visible[at] = self.unbilled_visible.get(at, 0) + 1

    def _bill(self, source, text, turn, idx, ts):
        n = _nbytes(text)
        s = self.src.setdefault(source, {"events": 0, "bytes": 0, "sizes": []})
        s["events"] += 1
        s["bytes"] += n
        s["sizes"].append(n)
        bucket = self.turn_bytes.setdefault(turn, {})
        bucket[source] = bucket.get(source, 0) + n
        found, _ = text_pointers(source, text)
        for kind, keys in found.items():
            for key in keys:
                p = self.pointers[kind].setdefault(key, {"first": idx, "ts": ts, "sources": set()})
                p["sources"].add(source)
        self._mark_pointed(source, text)

    def _mark_pointed(self, source, text):
        found, research = text_pointers(source, text)
        self.pointed.update(k for k, keys in found.items() if keys)
        if research:
            self.pointed.add("research")

    # -- uses ------------------------------------------------------------------------
    def _use(self, kind, key, idx, ts):
        u = self.uses[kind].get(key)
        if u is None:
            self.uses[kind][key] = [idx, idx, ts]
        else:
            u[1], u[2] = idx, ts

    def _read_path(self, path, idx, ts, opened=True):
        """One path a tool READ (or, for Glob, searched: opened=False)."""
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
            self._use("kg", key, idx, ts)
        for key in SPECS_PATH_RE.findall(p):
            self._use("specs", key, idx, ts)
        for key in MEMORY_PATH_RE.findall(p):
            if key != MEMORY_INDEX:      # the index is injected, not pointed at
                self._use("memory", key, idx, ts)
        for key in HEX8_RE.findall(p):
            self._use("ctx", key, idx, ts)

    def _kg_load(self, args, idx, ts):
        self.reflexes.add("kg")
        toks = load_query_tokens(args)
        if toks:
            self.kg_loads.append((idx, ts, toks))

    def _on_tool_use(self, name, inp, idx, ts):
        """Structural fields only. Free prose in a tool input (Agent.prompt, Write.content,
        Grep.pattern, SendMessage.message, WebFetch.prompt) is never read."""
        if name == "Read":
            self._read_path(str(inp.get("file_path") or ""), idx, ts)
        elif name == "Grep":
            self._read_path(str(inp.get("path") or ""), idx, ts)
        elif name == "Glob":
            self._read_path(str(inp.get("pattern") or "") + " " + str(inp.get("path") or ""),
                            idx, ts, opened=False)
        elif name == "Bash":
            cmd = str(inp.get("command") or "")
            for target in self.read_targets(cmd.lower()):
                self._read_path(target, idx, ts)
            for seg in shell_segments(cmd):
                prog, args = segment_program(seg)
                if prog in ("kg", "kg.py") and args[:1] == ["load"]:
                    self._kg_load(args[1:], idx, ts)
                elif prog in ("ctx", "ctx.py") and args[:1] == ["board"]:
                    self.board_cmds.append((idx, ts))
        elif name == "Skill":
            skill = str(inp.get("skill") or "").lower()
            if skill in KG_SKILLS:
                self.reflexes.add("kg")
                try:
                    args = shlex.split(str(inp.get("args") or ""))
                except ValueError:
                    args = []
                if args[:1] == ["load"]:
                    self._kg_load(args[1:], idx, ts)
            if skill in RESEARCH_SKILLS:
                self.reflexes.add("research")
        elif name in RESEARCH_TOOLS:
            self.reflexes.add("research")
        elif name.startswith("mcp__"):
            # An MCP call names a session or agent through its id fields (agentId,
            # workspaceId, id), never through its prose ones (prompt, initialPrompt).
            for k, v in inp.items():
                if isinstance(v, str) and k.lower().endswith("id"):
                    for key in HEX8_RE.findall(v.lower()):
                        self._use("ctx", key, idx, ts)

    # -- follow-through --------------------------------------------------------------
    def used(self, kind, key, after_idx=None, after_ts=None):
        """Whether a tool call used a pointer after a point in this session (after_idx)
        or, for a subagent reading its parent's pointer, after a moment (after_ts)."""
        def later(i, t):
            return i > after_idx if after_idx is not None else bool(t) and t > after_ts
        u = self.uses[kind].get(key)
        if u and later(u[1], u[2]):
            return True
        if kind == "kg":
            slug = key.rsplit("/", 1)[-1]
            for i, t, toks in self.kg_loads:
                # A bare query word names an entity only when the slug is multi-word:
                # `kg load "why is memory over its limit"` does not name concept/memory.
                named = key in toks or any(x.endswith("/" + key) for x in toks) or \
                    ("-" in slug and slug in toks)
                if named and later(i, t):
                    return True
        if kind == "ctx":
            return any(later(i, t) for i, t in self.board_cmds)
        return False


def read_session(path, iter_records, kg_read_re, read_targets):
    s = _Session(path, kg_read_re, read_targets)
    for idx, obj in enumerate(iter_records(path)):
        if isinstance(obj, dict):
            s.feed(obj, idx)
        else:
            s.malformed += 1
    s.finish()
    return s


def _group(source):
    return source.split(":", 1)[0]


def _aggregate(files, sessions):
    live = [s for s in sessions if s.own_records]
    per_session_total, per_turn_total = [], []
    src, not_injected, unattributed = {}, {}, {}
    turns = 0
    for s in live:
        turns += s.turns
        per_session_total.append(sum(v["bytes"] for v in s.src.values()))
        # Pre-prompt injections fold into turn one; a session with no prompt has no turn.
        series = []
        for t in range(s.turns):
            b = dict(s.turn_bytes.get(t, {}))
            if t == 0:
                for name, n in s.turn_bytes.get(-1, {}).items():
                    b[name] = b.get(name, 0) + n
            series.append(b)
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
        for bucket, rows in ((not_injected, s.not_injected), (unattributed, s.unattributed_visible)):
            for name, v in rows.items():
                a = bucket.setdefault(name, dict.fromkeys(v, 0))
                for k in v:
                    a[k] += v[k]
    sources = {}
    for name, a in sorted(src.items(), key=lambda kv: -kv[1]["bytes"]):
        sources[name] = {
            "group": _group(name), "events": a["events"], "sessions": a["sessions"],
            "bytes": a["bytes"], "est_tokens": a["bytes"] // 4,
            "per_event": _dist(a["sizes"]), "per_session": _dist(a["per_session"]),
            "per_turn": _dist(a["per_turn"]),
        }
    total = sum(a["bytes"] for a in src.values())
    by_group = {}
    for name, a in src.items():
        by_group[_group(name)] = by_group.get(_group(name), 0) + a["bytes"]
    unbilled = {}
    for s in live:
        for at, n in s.unbilled_visible.items():
            unbilled[at] = unbilled.get(at, 0) + n
    unknown_event = sum(s.unknown_event for s in live)

    if not sessions:
        status, reason = "no_data", "no session file in the window"
    elif not live:
        status, reason = "no_data", "every record in the window was a fork copy"
    elif total == 0:
        status = "blind"
        reason = (f"read {len(live)} session(s), {sum(s.own_records for s in live)} records, "
                  "and billed 0 injected bytes — the attachment schema no longer matches")
    elif unbilled or unknown_event:
        # One source going dark while the others keep the total above zero is the same
        # failure one level down: the ledger would read `live` with role-x missing.
        status = "partial"
        parts = [f"{n} {at} record(s) the model was shown billed 0 bytes"
                 for at, n in sorted(unbilled.items())]
        if unknown_event:
            parts.append(f"{unknown_event} hook output(s) with no hookEvent")
        reason = "; ".join(parts) + " — part of the attachment schema no longer matches"
    else:
        status, reason = "live", None

    return {
        "status": status, "status_reason": reason,
        "files": len(files), "sessions": len(live), "turns": turns,
        "fork_copied_records_skipped": sum(s.fork_copies for s in sessions),
        "malformed_records": sum(s.malformed for s in sessions),
        "memory_observable": "memory" in by_group,
        "totals": {
            "bytes": total, "est_tokens": total // 4,
            "by_group": {g: {"bytes": b, "est_tokens": b // 4} for g, b in sorted(by_group.items())},
            "per_session": _dist(per_session_total), "per_turn": _dist(per_turn_total),
        },
        "sources": sources,
        "not_injected": dict(sorted(not_injected.items(), key=lambda kv: -kv[1]["bytes"])),
        "unbilled_visible": dict(sorted(unbilled.items())),
        "unattributed_visible": dict(sorted(unattributed.items(), key=lambda kv: -kv[1]["bytes"])),
        "follow_through": _follow_through(live),
        "reflexes": _reflexes(live),
    }


def _follow_through(sessions):
    """Unit: one (session, pointer) pair, however often the pointer was re-injected."""
    by_kind = {k: {"injected": 0, "followed": 0} for k in POINTER_KINDS}
    by_source, kg_by_type = {}, {}
    opened = {k: {"opened": 0, "opened_never_injected": 0, "opened_before_injection_only": 0}
              for k in ("kg", "specs", "memory")}
    for s in sessions:
        for kind in POINTER_KINDS:
            for key, p in s.pointers[kind].items():
                hit = s.used(kind, key, after_idx=p["first"])
                by_kind[kind]["injected"] += 1
                by_kind[kind]["followed"] += hit
                if kind == "kg":
                    t = kg_by_type.setdefault(key.split("/", 1)[0] if "/" in key else "(flat)",
                                              {"injected": 0, "followed": 0})
                    t["injected"] += 1
                    t["followed"] += hit
                for source in p["sources"]:
                    b = by_source.setdefault(source, {}).setdefault(kind, {"injected": 0, "followed": 0})
                    b["injected"] += 1
                    b["followed"] += hit
        for kind, o in opened.items():
            for key, (_, last_use, _) in s.uses[kind].items():
                o["opened"] += 1
                p = s.pointers[kind].get(key)
                if p is None:
                    o["opened_never_injected"] += 1
                elif last_use < p["first"]:
                    o["opened_before_injection_only"] += 1
    for rows in [by_kind, kg_by_type] + list(by_source.values()):
        for v in rows.values():
            v["rate"] = _rate(v["followed"], v["injected"])
    for o in opened.values():
        # Self-directed retrieval: the share of what the agent opened that nothing
        # injected into this session had pointed at.
        o["self_directed_share"] = _rate(o["opened_never_injected"], o["opened"])
    injected = sum(v["injected"] for v in by_kind.values())
    followed = sum(v["followed"] for v in by_kind.values())
    return {
        "unit": "(session, pointer) pair; followed = a later tool call in the same session opened it",
        "overall": {"injected": injected, "followed": followed, "rate": _rate(followed, injected)},
        "by_kind": by_kind,
        # persona/* entries carry their claim inline, so an unopened one may still be used.
        "kg_by_type": dict(sorted(kg_by_type.items())),
        "by_source": dict(sorted(by_source.items())),
        "opened": opened,
    }


def _reflexes(sessions):
    out = {}
    for kind in REFLEX_KINDS:
        pointed = [s for s in sessions if kind in s.pointed]
        unpointed = [s for s in sessions if kind not in s.pointed]
        out[kind] = {}
        for side, group in (("pointed", pointed), ("unpointed", unpointed)):
            n = sum(kind in s.reflexes for s in group)
            out[kind][side] = {"sessions": len(group), "with_reflex": n, "rate": _rate(n, len(group))}
    anyr = sum(bool(s.reflexes) for s in sessions)
    out["any"] = {"sessions": len(sessions), "with_reflex": anyr, "rate": _rate(anyr, len(sessions))}
    out["note"] = REFLEX_NOTE
    return out


def _via_subagents(block, main, subs):
    """Parent pointers the parent never opened but one of its own subagents did, after
    the pointer was injected. A subagent lives at <parent-id>/subagents/*.jsonl."""
    kids = {}
    for s in subs:
        parent = os.path.basename(os.path.dirname(os.path.dirname(s.path)))
        kids.setdefault(parent, []).append(s)
    for kind in POINTER_KINDS:
        n = 0
        for s in main:
            children = kids.get(os.path.splitext(os.path.basename(s.path))[0], [])
            if not children:
                continue
            for key, p in s.pointers[kind].items():
                if not s.used(kind, key, after_idx=p["first"]) and p["ts"] and any(
                        c.used(kind, key, after_ts=p["ts"]) for c in children):
                    n += 1
        block["follow_through"]["by_kind"][kind]["followed_only_by_own_subagents"] = n


def analyze_context(files, kg_read_re, read_targets, iter_records, subagent_files=()):
    """The context_ledger block for one window of transcript files (and their subagents)."""
    main = [read_session(p, iter_records, kg_read_re, read_targets) for p in files]
    subs = [read_session(p, iter_records, kg_read_re, read_targets) for p in subagent_files]
    block = _aggregate(files, main)
    sub = _aggregate(subagent_files, subs)
    _via_subagents(block, [s for s in main if s.own_records], [s for s in subs if s.own_records])
    headline = dict.fromkeys(HEADLINE_KEYS)
    if block["status"] in ("live", "partial"):
        headline = {
            HEADLINE_KEYS[0]: block["totals"]["per_session"]["median"],
            HEADLINE_KEYS[1]: block["follow_through"]["by_kind"]["kg"]["rate"],
            HEADLINE_KEYS[2]: block["reflexes"]["any"]["rate"],
        }
    block["totals"]["bytes_including_subagents"] = block["totals"]["bytes"] + sub["totals"]["bytes"]
    subagents = {k: sub[k] for k in ("status", "status_reason", "files", "sessions", "turns",
                                     "totals", "sources", "unbilled_visible")}
    subagents["follow_through"] = {"by_kind": sub["follow_through"]["by_kind"]}
    subagents["reflexes"] = {"any": sub["reflexes"]["any"]}
    return {
        "schema": SCHEMA, **block, "headline": headline,
        "scope": "main-session transcripts; subagent transcripts are measured under `subagents`",
        "turn_unit": "one Claude Code promptId (pre-promptId transcripts: one human prompt)",
        "token_estimate": TOKEN_ESTIMATE_NOTE,
        "subagents": subagents,
    }
