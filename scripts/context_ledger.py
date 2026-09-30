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
that a structured field carries: an Agent prompt, a Write body, a Grep pattern. A
`kg load` query is read for the entity it names, whether it comes from Bash or from a
Skill(kg) call's args, because naming the entity is how `kg load` opens one. The one
other exception is stated below: shell reads are found by the sensor's own detector,
which does not respect quotes.

A pointer is followed by a Read or Grep of its path, a `kg load` naming it, an MCP id
field naming it, or `ctx board` (the owner's definition), and also by a shell read of
its path. This harness steers agents to Bash: on 2026-09-30 none of the followed KG
pointers was opened with the Read tool, so leaving shell reads out read 0 of 2,566.
Shell reads are detected by the SENSOR's own `bash_read_targets` -- the detector m5
is graded with -- so a fix to how a shell read is recognized reaches both. Its known
over-counts, all counted in `followed` and none in `followed_tools_only` (the strict
figure without shell reads):
  - it does not respect quotes: a read verb that starts a line inside a commit
    message or heredoc body counts;
  - a grep pattern that is itself a path counts;
  - `git diff -- path`, `git log -- path`, `wc` and `md5sum` count as reads, though
    they show a diff, a history or a count rather than the file.
  - it does not know comments: `make test  # later; cat <path>` counts the `cat`.
And its known under-counts: a path in a shell or loop variable; a read behind a shell
keyword or a wrapper (`do cat …`, `timeout 5 cat …`, `(cat …)`).
`kg load` and `ctx board` are found by the program a segment actually runs
(shlex-split, quotes respected, shell keywords such as `do`/`then` skipped); everything
from a heredoc on is dropped, and a command shlex cannot split is dropped -- both
under-count.

The ledger is SHADOW: measured, never graded. It cannot become `worst` and it emits no
actuator. Its liveness checks need no threshold. It is `blind` when it read files and
billed no byte, or parsed no record from them; `partial` when a record of a type it
bills was shown to the model (Claude Code's `rendered` field) and billed nothing, a
part of a billed record is unreadable, hook output names no event, a file parsed to no
record, or its time budget ran out. Doctor §29 fails on both (BRO-1696: a sensor that
goes dark must fail as a gap, never pass as a quiet reading). It does NOT detect a
renamed or new attachment type: a type it does not know is reported under
`unknown_visible` as data, and doctor says renames go undetected.

leverage-sensor.py loads this file by PATH, not by import: the hooks start the sensor
with `python3 -I`, which keeps this directory off sys.path (BRO-2652). Stdlib only.
"""
import json
import os
import re
import shlex
import time

SCHEMA = 3

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
# bill. A listing record that carries `rendered` is billed from it instead, minus the
# <system-reminder> tags: a delta also tells the model which MCP servers need auth or
# disconnected, and that notice is in no listing field (10 such records in the
# 2026-09-29 window). So a harness listing's bytes include the harness's own header
# lines, and older records without `rendered` are billed from the field alone; hooks
# and memory are always billed from their own fields.
HARNESS_FIELDS = {
    "skill_listing": "content",
    "mcp_instructions_delta": "addedBlocks",
    "agent_listing_delta": "addedLines",
    "deferred_tools_delta": "addedLines",
}
BILLED_TYPES = frozenset({"hook_success", "hook_additional_context", "instructions",
                          "nested_memory", *HARNESS_FIELDS})
# A LABEL, not an alarm: the attachment types the model may be shown that the ledger
# reports, unbilled, under `unattributed_visible` (turn plumbing and harness state). A
# shown type in neither this set nor BILLED_TYPES goes to `unknown_visible`. Claude
# Code's attachment format is undocumented and changes, so no rule over it can tell a
# renamed hook_success from a new notice without a tuned threshold; the ledger reports
# the type and does not guess.
KNOWN_UNBILLED = frozenset({
    "queued_command", "total_tokens_reminder", "edited_text_file", "session_context",
    "environment", "remote_session_change", "file", "silent_turn_reminder", "auto_mode",
    "invoked_skills", "model", "date", "read_truncation_notice", "ultra_effort_enter",
    "dynamic_skill", "compact_file_reference", "command_permissions",
    "batching_reminder_sent", "thinking_drop", "hook_system_message", "hook_cancelled",
    "prompt_snapshot", "deferred_tools_record", "task_status", "task_reminder",
    "hook_non_blocking_error", "date_change", "plan_mode", "plan_mode_exit",
    "auto_mode_exit", "ultra_effort_exit", "bash_output_audience_note",
    "structured_output", "max_turns_reached",
})

POINTER_KINDS = ("kg", "specs", "ctx", "memory")
REFLEX_KINDS = ("kg", "specs", "memory", "research")

# Paths, matched the same way in injected text (a pointer) and in a tool's path (a use).
KG_PATH_RE = re.compile(r"research/entities/([\w./-]+?)\.md\b")
# A specs pointer must name a file: MEMORY.md's naming template
# `docs/specs/YYYY-MM-DD-<slug>.html` is not a spec, and was 14 of 14 "pointers".
SPECS_PATH_RE = re.compile(r"\bdocs/specs/[\w./-]+\.(?:md|html?|pdf|txt|ya?ml|json|rst)\b")
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

RESEARCH_TOOLS = frozenset({"WebFetch", "WebSearch"})
RESEARCH_SKILLS = frozenset({
    "deep-research", "technical-research", "alpha-research", "literature-review",
    "last30days", "autoresearch", "financial-deep-research",
    "deep-dive-research-orchestrator", "interceptor-research", "checkit",
})
# Programs a segment may be wrapped in before the one it runs, and the wrapper flags
# that take a value (`sudo -u me kg.py load x` runs kg.py, not `me`).
_WRAPPERS = frozenset({"sudo", "env", "timeout", "nice", "nohup", "time", "command", "exec"})
_WRAPPER_VALUE_FLAGS = {"sudo": {"-u", "-g", "-C", "-h", "-p", "-U", "-r", "-t"},
                        "nice": {"-n"}, "env": {"-u", "-C", "-S"},
                        "timeout": {"-s", "-k", "--signal", "--kill-after"}}
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PYTHON_RE = re.compile(r"^python(?:\d+(?:\.\d+)?)?$")
_PYTHON_VALUE_FLAGS = frozenset({"-X", "-W"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash"})
# Keywords that open a compound command: `for x in a; do kg.py load y; done` runs kg.py.
_SHELL_KEYWORDS = frozenset({"if", "then", "else", "elif", "while", "until", "do", "{", "!"})
_KG_LOAD_VALUE_OPTS = frozenset({"--n", "-n", "--type", "--terms", "--limit"})
# The ledger's share of the Stop run: the hook's timeout is 25s, and m1-m6 are computed
# before the ledger starts, so a slow ledger must stop itself rather than let the
# timeout take the whole record with it.
DEFAULT_BUDGET_S = 12.0

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


def _unwrapped(shown):
    """Rendered text without the <system-reminder> tags Claude Code wraps it in."""
    return re.sub(r"</?system-reminder>\n?", "", shown).strip("\n")


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
        command = att.get("command") if isinstance(att.get("command"), str) else None
        return [(f"hook:{event or 'unknown'}:{source_tag(text, command)}", event, text, origin)]
    if at == "hook_additional_context":
        event = att.get("hookEvent") if isinstance(att.get("hookEvent"), str) else None
        body = att.get("content")
        # One entry per hook: a record that batches two hooks' text is two injections,
        # each paired with its own JSON twin.
        entries = body if isinstance(body, list) else [body]
        return [(f"hook:{event or 'unknown'}:{source_tag(e)}", event, e, "hac")
                for e in entries if isinstance(e, str) and e.strip()]
    if at == "instructions":
        files = att.get("files")
        # A file entry with no readable content is a dark part of a shown record: it is
        # returned with text=None so the caller counts it rather than skipping it.
        return [(f"memory:{f.get('type') or 'unknown'}" if isinstance(f, dict) else "memory:unknown",
                 None, f.get("content") if isinstance(f, dict) and isinstance(f.get("content"), str)
                 else None, "file")
                for f in (files if isinstance(files, list) else [None])]
    if at == "nested_memory":
        body = att.get("content")
        if isinstance(body, dict) and isinstance(body.get("content"), str):
            return [(f"memory:nested-{body.get('type') or 'unknown'}", None, body["content"], "file")]
        return [("memory:nested-unknown", None, None, "file")]
    if at in HARNESS_FIELDS:
        text = _unwrapped(shown) if shown else _as_text(att.get(HARNESS_FIELDS[at]))
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

    Every way this can be wrong is an under-count:
      - everything from a heredoc (`<<`) on is dropped, body and later commands alike
        (a body is authored text), including a `<<` inside a comment;
      - a token that starts with `#` drops everything after it up to the next newline,
        so a comment naming an entity is not a query for it. shlex is told comments are
        ordinary words, so that one cannot swallow the newline and join the next line
        onto its segment. Quotes are gone by then, so a quoted argument such as "#x"
        drops the rest of its line too, and a comment ending in a backslash also takes
        the next line;
      - a command shlex cannot split (an unbalanced quote, or an apostrophe in a
        comment) yields nothing."""
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|()<>\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    segs, seg, in_comment = [], [], False
    try:
        for tok in lex:
            if tok.startswith("<<"):
                break
            if in_comment:
                if "\n" not in tok or not all(c in ";&|()\n" for c in tok):
                    continue
                in_comment = False
            elif tok.startswith("#"):
                in_comment = True
                continue
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


def segment_programs(seg, depth=0):
    """(program, args) for what one segment runs: past VAR=value, wrappers such as
    `timeout 150` and their flag values, a python interpreter, `uv run`, and into
    `sh -c "..."`. So `KG_NO_POLICY=1 timeout 150 python3 -I kg.py load x` is
    ("kg.py", ["load", "x"]). `$CTX` is the ctx-core skill's documented alias."""
    i = 0
    while i < len(seg):
        tok = seg[i]
        if _ASSIGN_RE.match(tok) or tok in _SHELL_KEYWORDS:
            i += 1
            continue
        base = os.path.basename(tok)
        if base in _WRAPPERS:
            i += 1
            flags = _WRAPPER_VALUE_FLAGS.get(base, ())
            while i < len(seg) and seg[i].startswith("-"):
                i += 2 if seg[i] in flags else 1
            if base == "timeout" and i < len(seg) and re.match(r"^[\d.]+[smhd]?$", seg[i]):
                i += 1
            continue
        break
    rest = seg[i:]
    if not rest:
        return []
    name, args = os.path.basename(rest[0]).lower(), rest[1:]
    if name == "uv" and args[:1] == ["run"] and depth < 3:
        return segment_programs(args[1:], depth + 1)
    if name in _SHELLS and depth < 3:
        for j, a in enumerate(args):
            if re.match(r"^-[a-z]*c[a-z]*$", a) and j + 1 < len(args):
                return [p for inner in shell_segments(args[j + 1])
                        for p in segment_programs(inner, depth + 1)]
    if _PYTHON_RE.match(name):
        j = 0
        while j < len(args) and args[j].startswith("-"):
            if args[j] == "-c":
                return []              # inline code, not a script
            j += 2 if args[j] in _PYTHON_VALUE_FLAGS else 1
        if j >= len(args):
            return []
        name, args = os.path.basename(args[j]).lower(), args[j + 1:]
    if name in ("$ctx", "${ctx}"):
        name = "ctx"
    return [(name, args)]


def load_query_tokens(args):
    """The query words of `kg load <query...>`, normalized for slug matching. A redirect
    operator and its target (`> /tmp/out`, `>& 1`) are skipped; a bare file-descriptor
    number such as the `2` of `2>&1` is kept, and names no entity."""
    toks, skip = set(), False
    for a in args:
        if skip:
            skip = False
            continue
        if a and all(c in "<>&" for c in a):
            skip = True
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

    def __init__(self, path, kg_read_re, vocab):
        self.path = path
        self.kg_read_re = kg_read_re
        self.vocab = vocab
        self.own_records = self.fork_copies = self.malformed = 0
        self.unreadable_parts = 0   # parts of a billed record with no readable text
        self.unknown_visible = {}   # shown attachment types the ledger does not know
        self.billed_type_records = self.billed_type_rendered = 0
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
        self.shell_uses = {k: {} for k in POINTER_KINDS}  # the same, from shell reads
        self.kg_loads = []          # (idx, ts, tokens)
        self.board_cmds = []        # (idx, ts)
        self.reflexes = set()
        self.pointed = set()
        self.inherited = {k: set() for k in POINTER_KINDS}  # pointers in a fork's copied history

    # -- feeding ---------------------------------------------------------------------
    def feed(self, obj, idx):
        """One record. A record whose shape the ledger cannot read is counted, never
        allowed to take the rest of the window with it; a billed-type one also makes
        the ledger `partial`, because what it carried went unbilled."""
        try:
            self._feed(obj, idx)
        except (TypeError, AttributeError, ValueError, KeyError):
            self.malformed += 1
            att = obj.get("attachment") if isinstance(obj.get("attachment"), dict) else {}
            at = att.get("type")
            if isinstance(at, str) and at in BILLED_TYPES:
                self.unreadable_parts += 1

    def _feed(self, obj, idx):
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
                    if text:
                        self._mark_pointed(source, text)
                        for kind, keys in text_pointers(source, text)[0].items():
                            self.inherited[kind].update(keys)
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
                texts = attachment_texts(att, shown)
                # Coverage counts the records that carried injected text: a PreToolUse
                # `{}` is a billed type too, but there is nothing on it to check.
                if texts or shown:
                    self.billed_type_records += 1
                    self.billed_type_rendered += bool(shown)
                if shown:
                    self.visible[idx] = at
                for source, event, text, origin in texts:
                    if text is None:
                        self.unreadable_parts += 1
                        continue
                    self.candidates.append({"idx": idx, "ts": ts, "turn": self.turns - 1,
                                            "source": source, "event": event,
                                            "text": text, "origin": origin})
            elif shown:
                bucket = (self.unattributed_visible if at in KNOWN_UNBILLED
                          else self.unknown_visible)
                u = bucket.setdefault(str(at), {"records": 0, "bytes": 0})
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
                p = self.pointers[kind].setdefault(key, {"first": idx, "ts": ts, "sources": {}})
                # Each source is credited from ITS first injection: a read that came
                # before role-x named the pointer is not role-x's follow-through.
                p["sources"].setdefault(source, idx)
        self._mark_pointed(source, text)

    def _mark_pointed(self, source, text):
        found, research = text_pointers(source, text)
        self.pointed.update(k for k, keys in found.items() if keys)
        if research:
            self.pointed.add("research")

    # -- uses ------------------------------------------------------------------------
    def _use(self, kind, key, idx, ts, shell=False):
        table = self.shell_uses if shell else self.uses
        u = table[kind].get(key)
        if u is None:
            table[kind][key] = [idx, idx, ts]
        else:
            u[1], u[2] = idx, ts

    def _read_path(self, path, idx, ts, opened=True, shell=False):
        """One path a tool READ (or, for Glob, searched: opened=False). `shell` marks a
        read found by the sensor's shell-read detector, kept apart for the strict figure."""
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
            self._use("kg", key, idx, ts, shell)
        for key in SPECS_PATH_RE.findall(p):
            self._use("specs", key, idx, ts, shell)
        for key in MEMORY_PATH_RE.findall(p):
            if key != MEMORY_INDEX:      # the index is injected, not pointed at
                self._use("memory", key, idx, ts, shell)
        for key in HEX8_RE.findall(p):
            self._use("ctx", key, idx, ts, shell)

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
            for target in self.vocab["shell_read_targets"](cmd.lower()):
                self._read_path(target, idx, ts, shell=True)
            for seg in shell_segments(cmd):
                for prog, args in segment_programs(seg):
                    if prog in ("kg", "kg.py") and args[:1] == ["load"]:
                        self._kg_load(args[1:], idx, ts)
                    elif prog in ("ctx", "ctx.py") and args[:1] == ["board"]:
                        self.board_cmds.append((idx, ts))
        elif name == "Skill":
            skill = str(inp.get("skill") or "").lower()
            if skill in self.vocab["kg_skills"]:
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
    def used(self, kind, key, after_idx=None, after_ts=None, tools_only=False):
        """Whether a tool call used a pointer after a point in this session (after_idx)
        or, for a subagent reading its parent's pointer, after a moment (after_ts).
        `tools_only` leaves shell reads out: the strict, quote-proof figure."""
        def later(i, t):
            return i > after_idx if after_idx is not None else bool(t) and t > after_ts
        tables = (self.uses,) if tools_only else (self.uses, self.shell_uses)
        for table in tables:
            u = table[kind].get(key)
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


def read_session(path, iter_records, kg_read_re, vocab):
    s = _Session(path, kg_read_re, vocab)
    for idx, obj in enumerate(iter_records(path)):
        if isinstance(obj, dict):
            s.feed(obj, idx)
        else:
            s.malformed += 1
    s.finish()
    # The reader drops lines it cannot parse, and a line that parses to something other
    # than an object is not a record either, so a file of nothing but such lines would
    # otherwise look like a file with nothing in it.
    try:
        s.unparsed = not (s.own_records or s.fork_copies) and os.path.getsize(path) > 0
    except OSError:
        s.unparsed = False
    return s


def _group(source):
    return source.split(":", 1)[0]


def _aggregate(files, sessions, truncated=False):
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
    unbilled, unknown = {}, {}
    for s in live:
        for at, n in s.unbilled_visible.items():
            unbilled[at] = unbilled.get(at, 0) + n
        for at, v in s.unknown_visible.items():
            u = unknown.setdefault(at, {"records": 0, "bytes": 0})
            u["records"] += v["records"]
            u["bytes"] += v["bytes"]
    unknown_event = sum(s.unknown_event for s in live)
    unreadable = sum(s.unreadable_parts for s in sessions)
    typed = sum(s.billed_type_records for s in live)
    shown = sum(s.billed_type_rendered for s in live)

    unparsed = sum(s.unparsed for s in sessions)
    # A run the time budget cut short is `partial` whatever it did read: what it did
    # not read cannot be called absent.
    forks = sum(s.fork_copies for s in sessions)
    if not files:
        status, reason = "no_data", "no session file in the window"
    elif sessions and unparsed == len(sessions) and not truncated:
        status = "blind"
        reason = f"read {unparsed} non-empty file(s) and parsed no record from any of them"
    elif not live and not truncated and not unparsed:
        status = "no_data"
        reason = ("every record in the window was a fork copy" if forks
                  else "the window's session files hold no record")
    elif total == 0 and not truncated:
        status = "blind"
        if live:
            reason = (f"read {len(live)} session(s), {sum(s.own_records for s in live)} records, "
                      "and billed 0 injected bytes — the attachment schema no longer matches")
            if unparsed:
                reason += f"; {unparsed} non-empty file(s) parsed to no record"
        else:
            # No session of its own was read, so nothing says the schema changed.
            reason = f"no session of its own: {unparsed} non-empty file(s) parsed to no record"
            if forks:
                reason += f"; {forks} fork-copied record(s), which are the parent's"
    elif unbilled or unknown_event or unreadable or unparsed or truncated:
        # A KNOWN billed source going dark while the others keep the total above zero is
        # the same failure one level down. (A renamed type is not caught here: it is
        # reported under `unknown_visible`, and doctor says renames go undetected.)
        status = "partial"
        parts = [f"{n} {at} record(s) the model was shown billed 0 bytes"
                 for at, n in sorted(unbilled.items())]
        if unparsed:
            parts.append(f"{unparsed} non-empty file(s) parsed to no record")
        if unknown_event:
            parts.append(f"{unknown_event} hook output(s) with no hookEvent")
        if unreadable:
            parts.append(f"{unreadable} unreadable part(s) of billed records")
        if truncated:
            parts.append(f"time budget reached after {len(sessions)} of {len(files)} files")
        reason = "; ".join(parts)
    else:
        status, reason = "live", None

    return {
        "status": status, "status_reason": reason,
        "files": len(files), "sessions": len(live), "turns": turns,
        "fork_copied_records_skipped": sum(s.fork_copies for s in sessions),
        "malformed_records": sum(s.malformed for s in sessions),
        "memory_observable": "memory" in by_group,
        # How much of the window the `rendered` drift check can see: records written by
        # Claude Code before 2.1.280 carry no `rendered`, and for them only the
        # structural checks (unknown hookEvent, unreadable parts) apply.
        "drift_check_coverage": {"billed_type_records": typed, "with_rendered": shown,
                                 "share": _rate(shown, typed)},
        "totals": {
            "bytes": total, "est_tokens": total // 4,
            "by_group": {g: {"bytes": b, "est_tokens": b // 4} for g, b in sorted(by_group.items())},
            "per_session": _dist(per_session_total), "per_turn": _dist(per_turn_total),
        },
        "sources": sources,
        "not_injected": dict(sorted(not_injected.items(), key=lambda kv: -kv[1]["bytes"])),
        "unbilled_visible": dict(sorted(unbilled.items())),
        "unattributed_visible": dict(sorted(unattributed.items(), key=lambda kv: -kv[1]["bytes"])),
        "unknown_visible": dict(sorted(unknown.items())),
        "follow_through": _follow_through(live),
        "reflexes": _reflexes(live),
    }


def _follow_through(sessions):
    """Unit: one (session, pointer) pair, however often the pointer was re-injected."""
    by_kind = {k: {"injected": 0, "followed": 0, "followed_tools_only": 0} for k in POINTER_KINDS}
    by_source, kg_by_type = {}, {}
    opened = {k: {"opened": 0, "opened_never_injected": 0, "opened_before_injection_only": 0,
                  "opened_inherited_only": 0}
              for k in ("kg", "specs", "memory")}
    for s in sessions:
        for kind in POINTER_KINDS:
            for key, p in s.pointers[kind].items():
                hit = s.used(kind, key, after_idx=p["first"])
                by_kind[kind]["injected"] += 1
                by_kind[kind]["followed"] += hit
                by_kind[kind]["followed_tools_only"] += s.used(kind, key, after_idx=p["first"],
                                                               tools_only=True)
                if kind == "kg":
                    t = kg_by_type.setdefault(key.split("/", 1)[0] if "/" in key else "(flat)",
                                              {"injected": 0, "followed": 0})
                    t["injected"] += 1
                    t["followed"] += hit
                for source, first in p["sources"].items():
                    b = by_source.setdefault(source, {}).setdefault(kind, {"injected": 0, "followed": 0})
                    b["injected"] += 1
                    b["followed"] += s.used(kind, key, after_idx=first)
        for kind, o in opened.items():
            merged = dict(s.shell_uses[kind])
            for key, u in s.uses[kind].items():
                m = merged.get(key)
                merged[key] = u if m is None else [min(m[0], u[0]), max(m[1], u[1]), None]
            for key, (_, last_use, _) in merged.items():
                o["opened"] += 1
                p = s.pointers[kind].get(key)
                if p is None and key in s.inherited[kind]:
                    # A fork's model saw its parent's copied injection: not self-directed.
                    o["opened_inherited_only"] += 1
                elif p is None:
                    o["opened_never_injected"] += 1
                elif last_use < p["first"]:
                    o["opened_before_injection_only"] += 1
    for rows in [by_kind, kg_by_type] + list(by_source.values()):
        for v in rows.values():
            v["rate"] = _rate(v["followed"], v["injected"])
    for v in by_kind.values():
        v["rate_tools_only"] = _rate(v["followed_tools_only"], v["injected"])
    for o in opened.values():
        # Self-directed retrieval: the share of what the agent opened that nothing
        # injected into this session had pointed at.
        o["self_directed_share"] = _rate(o["opened_never_injected"], o["opened"])
    injected = sum(v["injected"] for v in by_kind.values())
    followed = sum(v["followed"] for v in by_kind.values())
    return {
        "unit": ("(session, pointer) pair; followed = a later tool call in the same session "
                 "opened it, shell reads included (m5's detector); followed_tools_only = Read/Grep "
                 "tools, kg load, ctx board and MCP ids only"),
        "overall": {"injected": injected, "followed": followed, "rate": _rate(followed, injected)},
        "by_kind": by_kind,
        # Every role-x KG entry carries its core_claim inline before the path, so an
        # unopened pointer may still have been used: this counts opens, not use.
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


def subagent_parent(path):
    """The session id a subagent transcript belongs to: the directory above `subagents`,
    for <id>/subagents/agent-*.jsonl and <id>/subagents/workflows/<wf>/agent-*.jsonl."""
    parts = os.path.normpath(path).split(os.sep)
    return parts[parts.index("subagents") - 1] if "subagents" in parts[1:] else None


def _via_subagents(block, main, subs):
    """Parent pointers the parent never opened but one of its own subagents did, after
    the pointer was injected."""
    kids = {}
    for s in subs:
        kids.setdefault(subagent_parent(s.path), []).append(s)
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


def _read_all(paths, iter_records, kg_read_re, vocab, deadline):
    """Sessions read before the deadline, and whether it cut the list short."""
    out = []
    for p in paths:
        if deadline is not None and time.monotonic() > deadline:
            return out, True
        out.append(read_session(p, iter_records, kg_read_re, vocab))
    return out, False


def analyze_context(files, kg_read_re, vocab, iter_records, subagent_files=(),
                    budget_s=DEFAULT_BUDGET_S):
    """The context_ledger block for one window of transcript files and their subagents.
    `vocab` carries m5's kg skill names and its shell-read detector, so the ledger and
    m5 recognise a shell READ the same way (m5 does not look for `kg load`). Main sessions are read first; the
    time budget, when it runs out, cuts the subagents before them."""
    deadline = None if budget_s is None else time.monotonic() + budget_s
    main, main_cut = _read_all(files, iter_records, kg_read_re, vocab, deadline)
    subs, sub_cut = _read_all(subagent_files, iter_records, kg_read_re, vocab, deadline)
    block = _aggregate(files, main, main_cut)
    sub = _aggregate(subagent_files, subs, sub_cut)
    _via_subagents(block, [s for s in main if s.own_records], [s for s in subs if s.own_records])
    # The subagent block keeps its own status, which doctor §29 checks separately: the
    # headline is computed from main sessions only, so only a main `partial` nulls it.
    headline = dict.fromkeys(HEADLINE_KEYS)
    if block["status"] == "live":
        # A partial window stores no headline: a value computed with a source missing
        # would enter the metrics history as a drop nobody caused.
        headline = {
            HEADLINE_KEYS[0]: block["totals"]["per_session"]["median"],
            HEADLINE_KEYS[1]: block["follow_through"]["by_kind"]["kg"]["rate"],
            HEADLINE_KEYS[2]: block["reflexes"]["any"]["rate"],
        }
    block["totals"]["bytes_including_subagents"] = block["totals"]["bytes"] + sub["totals"]["bytes"]
    subagents = {k: sub[k] for k in ("status", "status_reason", "files", "sessions", "turns",
                                     "totals", "sources", "unbilled_visible", "unknown_visible",
                                     "drift_check_coverage")}
    subagents["follow_through"] = {"by_kind": sub["follow_through"]["by_kind"]}
    subagents["reflexes"] = {"any": sub["reflexes"]["any"]}
    return {
        "schema": SCHEMA, **block, "headline": headline,
        "scope": "main-session transcripts; subagent transcripts are measured under `subagents`",
        "turn_unit": "one Claude Code promptId (pre-promptId transcripts: one human prompt)",
        "token_estimate": TOKEN_ESTIMATE_NOTE,
        "billing_unit": ("hooks and memory: the injected text, without Claude Code's per-record "
                         "wrapper; harness listings: `rendered` minus its tags when present "
                         "(so the harness's own header lines are included), else the listing field"),
        "subagents": subagents,
    }
