#!/usr/bin/env python3
"""ask_ledger.py — the human dependency chain, made queryable (BRO-2179).

Dep-Chain (P14) makes an agent enumerate what the CODE depends on before writing.
This does the same for what the ARC depends on a HUMAN for, and turns two prose
disciplines into queries:

    "did this ask earn the right to exist?"   -> validate
    "which lanes can still run without you?"  -> lanes --runnable

Why it exists: across 100 long autonomous sessions, 18 halted the arc on a human,
none of the 18 carried an ask block a person could act on, 5% said what happens
if the human stays silent, and 81% named a dependency knowable before the arc even
started. The ledger is what makes "I only asked when I had to" auditable instead
of a claim.

Spec: docs/specs/2026-08-18-agent-handback-contract.html
"""

# --- BSTACK-ONLY: provenance ------------------------------------------------
# Synced from broomva/workspace@3e1e37575 scripts/ask_ledger.py (BRO-3410, BRO-3421). Every
# local change is a BSTACK-ONLY block or line: the `python3 -I` import shim, the
# directory sweep (`validate --all`, `open --all`), `archive` refused, and repo_of
# reading `.broomva/asks` (BRO-3413). Resync: copy the workspace file, re-apply
# them, and move the pin in tests/test_ask_ledger.py, which checks that this file
# minus them is that commit byte for byte.
# --- END BSTACK-ONLY ---------------------------------------------------------
from __future__ import annotations

import argparse
import contextlib
import hashlib
import datetime as _dt
import json
import os
import subprocess
import re
import secrets
import stat as stat_mod
import sys
import time
from pathlib import Path

# --- BSTACK-ONLY: import shim ----------------------------------------------
# hooks/ask-origin-stamp-hook.sh invokes this file with `python3 -I` (BRO-2652):
# no cwd, script dir, PYTHONPATH or user site on sys.path, so a yaml.py planted
# in the session's repo cannot replace the real module. -I also hides a PyYAML
# installed with `pip install --user`, so on ImportError the user site is
# APPENDED (never inserted first): the standard library and the interpreter's
# own site-packages still win. See scripts/leverage-sensor.py for the same
# pattern (BRO-2652 established it there first).
try:
    import yaml
except ImportError:
    import site
    _user_site = site.getusersitepackages()
    if isinstance(_user_site, str) and os.path.isdir(_user_site) and _user_site not in sys.path:
        sys.path.append(_user_site)
    import yaml
# --- END BSTACK-ONLY ---------------------------------------------------------

CLASSES = {"credential", "authority", "decision", "artifact", "scope", "external", "idea"}
# A blocking ask has NO default, which means silence stalls a lane. That is only
# ever legitimate for these three, and the row must say which one it is.
NO_DEFAULT_REASONS = {"irreversible", "externally-visible", "spends-money"}
REQUIRED_ASK_FIELDS = ("id", "ask", "class", "gates", "exhausted", "blocking", "preanswerable")
# The ladder rungs, in order. An `exhausted` entry names the rung it reports on, so
# "I climbed the ladder" is checkable rather than assertable: the first draft accepted
# `exhausted: ["placeholder"]` as proof the ask had earned its place.
RUNGS = ("read", "preauth", "delegate", "reroute", "loop", "default")
_RUNG_RE = re.compile(r"^\s*(%s)\s*:" % "|".join(RUNGS), re.I)
MIN_RUNGS = 2

# --- decisions: the rung-6 record (BRO-2199) -------------------------------
# Rung 6 of the ladder says "take the reversible default, log it, tell the human
# afterwards" — and until now nothing defined where "log it" writes. A choice taken
# on the user's behalf that leaves no record is indistinguishable from one nobody
# made; the user still inherits it. These entries are that record.
#
# Note the asymmetry with `asks`: a decision here NEVER became an ask, because it
# died at rung 6. So there is deliberately no `exhausted`-to-decision cross-check —
# `default:` appearing in an ask's `exhausted` means the rung was tried and FAILED
# (no safe default existed), which is the opposite of a decision being recorded.
VERDICTS = {"sound", "unsound", "needs-user"}
# Ordered, because confidence RANKS the report: least-confident first is the whole
# point. A set would lose the order and silently re-sort by hash.
CONFIDENCE = ("low", "medium", "high")
REQUIRED_DECISION_FIELDS = ("id", "decision", "scenario", "gap", "reach", "verdict", "confidence")
# The measured failure mode is not a missing scenario, it is a scenario COMPRESSED
# back to its headline by a closeout rewrite. This floor is a PROXY for "walked
# scenario", and a weak one: 120 characters of padding defeats it. It catches the
# compression it was built for and claims nothing more. See the docstring.
SCENARIO_MIN = 120


# --- the decision layer: fields a person can answer from (2026-09-20) ------
# The audit layer (class/gates/exhausted/preanswerable) proves the ask EARNED its
# place. It does not help anyone answer it. Measured on the corpus that prompted
# this: 37 open asks, 0 blocking, median 13 days old, 0 offering options, 24 of 37
# over 200 characters, and 11 whose every referenced PR was already merged. The
# operator's verdict on reading them was "I can't understand a thing".
#
# Spec: .control/asks/DECISION-LAYER.md
DECISION_FIELDS = ("headline", "stakes", "why_you", "options")
# Facets. `project` answers "whose inbox is this"; `topics` answer "what else is
# like this". An arc ends; a topic outlives it — `linear-auth` recurs in four
# separate ledgers and only a shared topic makes that one problem instead of four.
TAG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PROJECT_MAX = 32
TOPIC_MAX = 24
TOPICS_MAX = 5
OPTION_FIELDS = ("id", "label", "consequence", "reversible")
HEADLINE_MAX = 100
# The register that made the corpus unreadable: primitive refs, ticket ids,
# session slugs and backticked commands. A bare PR number is deliberately allowed
# — the reader knows what a PR is; they do not know what `P5 step 1c` is.
_HEADLINE_JARGON = (
    (re.compile(r"\bP\d{1,2}\b"), "primitive reference (P<n>)"),
    (re.compile(r"\bBRO-\d+\b"), "ticket id"),
    (re.compile(r"`[^`]+`"), "backticked command or path"),
    (re.compile(r"\b[a-z]+-[a-z]+-\d+\b"), "session slug"),
)


# From this day on, an OPEN ask must carry the decision layer without anyone passing
# a flag (BRO-2841). The layer landed 2026-09-20, so every ask raised after it had
# the contract to write to. Measured 2026-10-05: open asks with no layer reached the
# owner in Maestro as the raw question, up to 803 characters, with nothing to tap.
# Older asks are exempt by date, not grandfathered silently: the backfill gave the open
# ones a layer where the ask was still live, and `--require-decision` forces it on all.
DECISION_LAYER_REQUIRED_FROM = _dt.date(2026, 9, 21)


def validate_decision_layer(ask: dict, where: str, required: bool = False,
                            why_required: str = "") -> list[str]:
    """Check the human-facing half of an ask. Silent on asks that have none."""
    errs: list[str] = []
    present = [k for k in DECISION_FIELDS if ask.get(k) is not None]
    if not present:
        if required:
            errs.append(f"{where}: no decision layer (need {', '.join(DECISION_FIELDS)})"
                        + (f" — {why_required}" if why_required else ""))
        return errs
    # Half a decision layer is worse than none: the UI would show a headline with
    # no way to act on it.
    for key in DECISION_FIELDS:
        if ask.get(key) is None:
            errs.append(f"{where}: decision layer started but '{key}' is missing")

    headline = ask.get("headline")
    if isinstance(headline, str):
        flat = " ".join(headline.split())
        if len(flat) > HEADLINE_MAX:
            errs.append(f"{where}: headline is {len(flat)} chars (max {HEADLINE_MAX})")
        if not flat.endswith("?"):
            errs.append(f"{where}: headline must be a question ending in '?'")
        for pattern, label in _HEADLINE_JARGON:
            hit = pattern.search(flat)
            if hit:
                errs.append(f"{where}: headline contains a {label}: {hit.group(0)!r}")

    project = ask.get("project")
    if project is not None:
        flat = str(project).strip()
        if not TAG_RE.match(flat) or len(flat) > PROJECT_MAX:
            errs.append(f"{where}: project {flat!r} must be lowercase-hyphenated, <={PROJECT_MAX} chars")

    topics = ask.get("topics")
    if topics is not None:
        if not isinstance(topics, list) or not (1 <= len(topics) <= TOPICS_MAX):
            errs.append(f"{where}: topics must be a list of 1-{TOPICS_MAX} tags")
        else:
            seen_topics: set[str] = set()
            for topic in topics:
                flat = str(topic).strip()
                if not TAG_RE.match(flat) or len(flat) > TOPIC_MAX:
                    errs.append(f"{where}: topic {flat!r} must be lowercase-hyphenated, <={TOPIC_MAX} chars")
                if flat in seen_topics:
                    errs.append(f"{where}: duplicate topic {flat!r}")
                seen_topics.add(flat)

    options = ask.get("options")
    if options is not None:
        if not isinstance(options, list) or not (2 <= len(options) <= 4):
            errs.append(f"{where}: options must be a list of 2-4 entries")
        else:
            seen: set[str] = set()
            for i, opt in enumerate(options):
                at = f"{where} option {i}"
                if not isinstance(opt, dict):
                    errs.append(f"{at}: must be a mapping")
                    continue
                for key in OPTION_FIELDS:
                    if opt.get(key) is None:
                        errs.append(f"{at}: missing '{key}'")
                if not isinstance(opt.get("reversible"), bool):
                    errs.append(f"{at}: 'reversible' must be true or false")
                if "status_quo" in opt and not isinstance(opt["status_quo"], bool):
                    errs.append(f"{at}: 'status_quo' must be true or false")
                oid = str(opt.get("id"))
                # `maestro:other` is Maestro's own Other row (BRO-3146): an option with that id would
                # be claimed when Other was picked. Only that id; every other id stays valid.
                if oid == "maestro:other":
                    errs.append(f"{at}: option id 'maestro:other' is reserved for Maestro's own rows (its Other row)")
                if oid in seen:
                    errs.append(f"{at}: duplicate option id {oid!r}")
                seen.add(oid)
            # status_quo marks the option that changes nothing, so Maestro files no
            # work when it is taken. Optional: the key is the only signal, since
            # reading "keep as is" out of a label is a heuristic on option names.
            # Two do-nothing options would make the mark meaningless.
            still = [o for o in options if isinstance(o, dict) and o.get("status_quo") is True]
            if len(still) > 1:
                errs.append(f"{where}: at most one option may carry status_quo: true (found {len(still)})")
            picked = [o for o in options if isinstance(o, dict) and o.get("recommended") is True]
            if len(picked) != 1:
                # Zero recommendations puts the work back on the human, which is the
                # failure this layer exists to fix. Two is not a recommendation.
                errs.append(f"{where}: exactly one option must carry recommended: true (found {len(picked)})")
            elif picked[0].get("reversible") is False:
                alt = [o for o in options if o is not picked[0] and o.get("reversible") is True]
                if alt:
                    errs.append(
                        f"{where}: recommends the irreversible option {picked[0].get('id')!r} "
                        f"while a reversible one exists — recommending an irreversible default "
                        f"spends the operator's authority without asking")
    return errs


# --- the answer's own words: a note, and an Other answer (BRO-3146) --------
# The owner, 2026-10-16: "I can't add optional text or instructions besides the
# choice options". An answer may carry `answer_note` (your words beside the option
# you took: a condition, an instruction) and, instead of an option, `answer_other`
# (none of the options fit; your answer in words). `answer` keeps the full text as
# before, so a reader that only knows `answer` loses nothing. An ask opts out of
# Other with `allow_other: false`; an idea never takes one (develop, park or kill is
# what Maestro acts on). Absent keys read as before: every existing ledger passes.
ANSWER_WORD_KEYS = ("answer_note", "answer_other")


def validate_answer_words(ask: dict, where: str) -> list[str]:
    """Check an answer's note and Other keys, and the ask's `allow_other` opt-out."""
    errs: list[str] = []
    if "allow_other" in ask and not isinstance(ask["allow_other"], bool):
        errs.append(f"{where}: 'allow_other' must be true or false")
    given = {}
    for key in ANSWER_WORD_KEYS:
        value = ask.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            errs.append(f"{where}: '{key}' must be your words (a non-empty string) or absent, got {value!r}")
            continue
        given[key] = value
    for key in given:
        if ask.get("answered_at") in (None, "") and ask.get("defaulted_at") in (None, ""):
            errs.append(f"{where}: '{key}' with no 'answered_at': an answer's words need the answer's time")
    # The words ride beside the full text, never instead of it: a reader that knows only
    # `answer` (or the key the entry already used) must still read the whole answer.
    if given and not any(ask.get(k) not in (None, "") for k in ("answer", "answered", "resolution", "outcome")):
        errs.append(f"{where}: {', '.join(sorted(given))} with no 'answer' text: write the full answer there too")
    if "answer_note" in given and ask.get("answer_option") in (None, "") and "answer_other" not in given:
        errs.append(f"{where}: 'answer_note' is words beside an option or an Other answer, and this answer has neither")
    if "answer_other" in given:
        if not (isinstance(ask.get("options"), list) and ask.get("options")):
            errs.append(f"{where}: 'answer_other' on an ask with no options: its words are the answer itself, "
                        f"written as 'answer'")
        if ask.get("class") == IDEA_CLASS:
            errs.append(f"{where}: an idea is answered develop, park or kill, never with an Other answer")
        elif ask.get("answer_option") not in (None, ""):
            errs.append(f"{where}: takes an option and an Other answer; an answer is one or the other "
                        f"(words beside an option go in 'answer_note')")
        if ask.get("allow_other") is False:
            errs.append(f"{where}: answered Other, but the ask says allow_other: false")
    return errs


# --- decision layer v2: asks you can decide at a glance (BRO-3167) --------
# The owner, of ask A1 on 2026-10-16: "I cant really tell or make a decision". It
# asked him to approve a spec with no link to the spec, no picture of what was being
# chosen, no plain summary and no tradeoffs. Five optional fields carry those, and
# the asking agent scales them to the decision: a yes/no needs none of them.
#
#   summary    2-4 plain sentences: what is decided and what changes
#   tradeoffs  rows: an option (or a free label) x cost, risk, time, reversibility
#   sources    what the agent read: tickets, PRs, specs, docs, files, URLs, sessions
#   media      on the ask and on each option: images, video, SVG, HTML, side by side
#   details    long-form markdown behind a disclosure
#
# Required, from RICH_REQUIRED_FROM on and only on an open ask: a `summary` once
# the ask has 3+ options or is about a spec or a design, and a source for any
# docs/specs/ path its words name. Everything else stays the agent's judgement
# (the rubric in .control/asks/DECISION-LAYER.md). Spec: same file, "Decision
# layer v2". Composes with BRO-3146's typed kinds: every field sits beside a
# `response` block; option media and option tradeoff rows apply to the kinds that
# carry options, free rows to the rest.
RICH_REQUIRED_FROM = _dt.date(2026, 10, 17)
SUMMARY_MIN, SUMMARY_MAX = 40, 700
DETAILS_MAX = 8000
TRADEOFF_AXES = ("cost", "risk", "time", "reversibility")
TRADEOFF_ROWS_MAX = 8
TRADEOFF_CELL_MAX = 160
TRADEOFF_LABEL_MAX = 60
SOURCE_KINDS = ("ticket", "pr", "doc", "spec", "url", "file", "session")
SOURCES_MAX = 10
SOURCE_LABEL_MAX = 80
# Local paths are served to Maestro by extension and size, so the validator holds an
# ask to exactly what Maestro will show: a file it would refuse is refused here first.
MEDIA_EXTENSIONS = {
    "image": (".png", ".jpg", ".jpeg", ".gif", ".webp"),
    "video": (".mp4", ".webm", ".mov"),
    "svg": (".svg",),
    "html": (".html", ".htm"),
}
MEDIA_MAX_BYTES = {"image": 5 * 2**20, "video": 25 * 2**20, "svg": 2**20, "html": 2 * 2**20}
MEDIA_ASK_MAX = 6
MEDIA_OPTION_MAX = 4
CAPTION_MAX = 120
_TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}-\d+$")
# https, printable ASCII only (a host, then an optional path): the same pattern as Maestro's shared/rich-ask.ts.
# Whitespace classes differ between Python and JS (\ufeff is whitespace to JS only), so neither uses one; a URL
# with other characters is written percent-encoded.
_HTTPS_RE = re.compile(r"^https://[!-.0-~]+(?:/[!-~]*)?$")
SOURCE_TEXT_MAX_BYTES = 2 * 2**20  # a local doc, spec or file Maestro opens as text (shared/rich-ask.ts)
# "About a spec or a design": the words an ask uses for one, or media / a spec source.
_DESIGN_WORDS = re.compile(r"\b(specs?|designs?|mockups?|wireframes?|variants?|variations?)\b|docs/specs/|\.pen\b",
                           re.I)
_SPEC_PATH = re.compile(r"docs/specs/[\w./-]+\.(?:html?|md)")


# The whitespace both readers agree on (Maestro's shared/rich-ask.ts `WS`): ASCII only. Python's strip() and
# split() also take \x1c-\x1f and JS's trim() takes \u00a0 and \ufeff, so a value one reader trims and the
# other keeps would pass here and be dropped there.
_WS = " \t\n\r\f\v"
_WS_RUN = re.compile(r"[ \t\n\r\f\v]+")


def _flat(value: str) -> str:
    return _WS_RUN.sub(" ", value.strip(_WS))


def _words(value, where: str, key: str, cap: int, floor: int = 1) -> list[str]:
    if not isinstance(value, str) or not value.strip(_WS):
        return [f"{where}: '{key}' must be words (a non-empty string), got {value!r}"]
    flat = _flat(value)
    if not floor <= len(flat) <= cap:
        return [f"{where}: '{key}' is {len(flat)} chars (want {floor}-{cap})"]
    return []


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _safe_rel(ref: str, anchor: bool = True) -> bool:
    """A repo-relative path that stays in the repo: no absolute path, no `..`, no scheme, no backslash, no
    control character. A source may end in a `#anchor`; a media path may not (`anchor=False`), because Maestro
    reads a media path as written. Maestro's `safeRelative` (shared/rich-ask.ts) is the same rule."""
    if _CONTROL.search(ref) or (not anchor and "#" in ref):
        return False
    path = ref.split("#", 1)[0]
    return bool(path) and not path.startswith(("/", "~")) and ":" not in path and "\\" not in path and \
        not any(part in ("..", ".", "") for part in path.split("/"))


def _local(where: str, label: str, ref: str, repo: Path | None, kind: str | None = None) -> list[str]:
    """A local path's existence (exact case, inside the repo) and, for media, its size cap."""
    path = ref.split("#", 1)[0]
    if repo is None:
        return []
    if repo is _UNKNOWN_REPO:
        return [f"{where}: {label} {path!r} cannot be checked: the ledger is not in a .control/asks "
                f"directory, so its repo is unknown"]
    if not _repo_path_exists(repo, path):
        return [f"{where}: {label} {path!r} does not exist in the repo"]
    # Maestro refuses a file whose real path leaves the ask's home (a symlink out), and anything not a file;
    # so does this. A link to the repo root resolves to the root itself: not inside it, and not a file.
    real, root = (repo / path).resolve(), repo.resolve()
    if root not in real.parents:
        return [f"{where}: {label} {path!r} leads outside the repo (a link); Maestro will not show it"]
    if not real.is_file():
        return [f"{where}: {label} {path!r} is not a file"]
    info = real.stat()
    # A hard link reaches a file outside the repo with no link to resolve; Maestro refuses one, and git never
    # makes one in a working tree.
    if info.st_nlink > 1:
        return [f"{where}: {label} {path!r} is a hard link; Maestro will not show it"]
    if info.st_size == 0:
        return [f"{where}: {label} {path!r} is empty; there is nothing to show"]
    cap, what = (MEDIA_MAX_BYTES[kind], f"{kind} cap") if kind is not None else (SOURCE_TEXT_MAX_BYTES, "doc cap")
    if info.st_size > cap:
        return [f"{where}: {label} {path!r} is {info.st_size} bytes, over the {what} of {cap} that Maestro serves"]
    return []


def _media_errors(media, where: str, cap: int, repo: Path | None) -> list[str]:
    if not isinstance(media, list) or not media:
        return [f"{where}: 'media' must be a list of 1-{cap} entries"]
    errs: list[str] = []
    if len(media) > cap:
        errs.append(f"{where}: 'media' holds {len(media)} entries, at most {cap}")
    for i, entry in enumerate(media):
        at = f"{where} media {i}"
        if not isinstance(entry, dict):
            errs.append(f"{at}: must be a mapping")
            continue
        kind = entry.get("kind")
        if kind not in MEDIA_EXTENSIONS:
            errs.append(f"{at}: kind {kind!r} not one of {sorted(MEDIA_EXTENSIONS)}")
            continue
        unknown = set(entry) - {"kind", "path", "url", "caption"}
        if unknown:
            errs.append(f"{at}: unknown key {sorted(unknown)[0]!r} (kind, path or url, caption)")
        path, url = entry.get("path"), entry.get("url")
        if (path is None) == (url is None):
            errs.append(f"{at}: needs exactly one of 'path' or 'url'")
            continue
        if url is not None:
            if not isinstance(url, str) or not _HTTPS_RE.match(url.strip(_WS)):
                errs.append(f"{at}: url must be https://, got {url!r}")
        else:
            if not isinstance(path, str) or not _safe_rel(path.strip(_WS), anchor=False):
                errs.append(f"{at}: path {path!r} must be a repo-relative path inside the repo, with no #anchor")
            elif not path.strip(_WS).lower().endswith(MEDIA_EXTENSIONS[kind]):
                errs.append(f"{at}: path {path!r} has the wrong extension for {kind} "
                            f"({', '.join(MEDIA_EXTENSIONS[kind])})")
            else:
                errs.extend(_local(at, "media path", path.strip(_WS), repo, kind))
        if entry.get("caption") is not None:
            errs.extend(_words(entry.get("caption"), at, "caption", CAPTION_MAX))
    return errs


def _source_errors(sources, where: str, repo: Path | None) -> list[str]:
    if not isinstance(sources, list) or not sources:
        return [f"{where}: 'sources' must be a list of 1-{SOURCES_MAX} entries"]
    errs: list[str] = []
    if len(sources) > SOURCES_MAX:
        errs.append(f"{where}: 'sources' holds {len(sources)} entries, at most {SOURCES_MAX}")
    for i, src in enumerate(sources):
        at = f"{where} source {i}"
        if not isinstance(src, dict):
            errs.append(f"{at}: must be a mapping {{kind, ref, label}}")
            continue
        kind, ref = src.get("kind"), src.get("ref")
        if kind not in SOURCE_KINDS:
            errs.append(f"{at}: kind {kind!r} not one of {list(SOURCE_KINDS)}")
            continue
        if not isinstance(ref, str) or not ref.strip(_WS):
            errs.append(f"{at}: needs a 'ref'")
            continue
        ref = ref.strip(_WS)
        if kind == "ticket" and not _TICKET_RE.match(ref):
            errs.append(f"{at}: ticket ref {ref!r} must be a ticket id like BRO-3167")
        elif kind == "pr" and not _FULL_PR_REF.match(ref):
            errs.append(f"{at}: pr ref {ref!r} must be owner/repo#N")
        elif kind == "url" and not _HTTPS_RE.match(ref):
            errs.append(f"{at}: url ref {ref!r} must be https://")
        elif kind == "session" and not _SESSION_REF.match(ref):
            errs.append(f"{at}: session ref {ref!r} must be a session id (8 hex, or a uuid)")
        elif kind in ("doc", "spec", "file"):
            if not _safe_rel(ref):
                errs.append(f"{at}: {kind} ref {ref!r} must be a repo-relative path inside the repo")
            else:
                errs.extend(_local(at, f"{kind} ref", ref, repo))
        if src.get("label") is not None:
            errs.extend(_words(src.get("label"), at, "label", SOURCE_LABEL_MAX))
        unknown = set(src) - {"kind", "ref", "label"}
        if unknown:
            errs.append(f"{at}: unknown key {sorted(unknown)[0]!r} (kind, ref, label)")
    return errs


def _tradeoff_errors(rows, where: str, option_ids: list[str]) -> list[str]:
    if not isinstance(rows, list) or not rows:
        return [f"{where}: 'tradeoffs' must be a list of 1-{TRADEOFF_ROWS_MAX} rows"]
    errs: list[str] = []
    if len(rows) > TRADEOFF_ROWS_MAX:
        errs.append(f"{where}: 'tradeoffs' holds {len(rows)} rows, at most {TRADEOFF_ROWS_MAX}")
    seen: set[str] = set()
    for i, row in enumerate(rows):
        at = f"{where} tradeoff {i}"
        if not isinstance(row, dict):
            errs.append(f"{at}: must be a mapping")
            continue
        unknown = set(row) - {"option", "label", *TRADEOFF_AXES}
        if unknown:
            errs.append(f"{at}: unknown key {sorted(unknown)[0]!r} "
                        f"(option or label, then {', '.join(TRADEOFF_AXES)})")
        option, label = row.get("option"), row.get("label")
        if option is not None and label is not None:
            errs.append(f"{at}: names both an option and a label; a row is one or the other")
        elif option is None and label is None:
            errs.append(f"{at}: names neither an option nor a label")
        elif option is not None:
            if str(option) not in option_ids:
                errs.append(f"{at}: names option {str(option)!r}, which the ask does not offer")
            elif str(option) in seen:
                errs.append(f"{at}: option {str(option)!r} has a row twice")
            seen.add(str(option))
        else:
            errs.extend(_words(label, at, "label", TRADEOFF_LABEL_MAX))
        axes = [k for k in TRADEOFF_AXES if row.get(k) is not None]
        if not axes:
            errs.append(f"{at}: needs at least one of {', '.join(TRADEOFF_AXES)}")
        for k in axes:
            if not isinstance(row[k], str) or not row[k].strip(_WS):
                errs.append(f"{at}: '{k}' must be words, got {row[k]!r}")
            elif len(_flat(row[k])) > TRADEOFF_CELL_MAX:
                errs.append(f"{at}: '{k}' is over {TRADEOFF_CELL_MAX} chars; put the long form in 'details'")
    return errs


def _ask_words(ask: dict, ledger: dict | None = None) -> str:
    """Every scalar the ask holds, at any depth: the text the person deciding can be shown, by construction.

    No field list. Four rounds of P20 (r2-r5, BRO-3167) each found one more field Maestro draws that a list
    here skipped (question, details, the tradeoff table, the summary). Maestro shows the values the ask holds
    plus the ledger's `arc` and `initiative` (read below); the parity corpus, maestro-paseo
    server/fixtures/ask_reader_parity.yaml, holds both readers to that. Reading values Maestro does not show
    only costs a summary, which the rule already accepts."""
    parts: list[str] = []

    def walk(value) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif value is not None:
            parts.append(_text(value) or "")

    walk(ask)
    # Maestro also shows two of the ledger's own fields with every ask: its `arc`, and its `initiative` when the
    # ask has none (decisionOf). Read both.
    for key in ("arc", "initiative"):
        walk((ledger or {}).get(key))
    return " ".join(parts)


def rich_required(ask: dict, ledger: dict) -> list[str]:
    """Why this ask must carry a summary (empty: it need not). Open asks raised from the cutoff only."""
    if ask.get("class") == IDEA_CLASS or is_closed(ask):
        return []
    day = asked_day(ask, ledger)
    if day is None or day < RICH_REQUIRED_FROM:
        return []
    why = []
    options = ask.get("options")
    if isinstance(options, list) and len(options) >= 3:
        why.append(f"it has {len(options)} options")
    words = _ask_words(ask, ledger)
    has_media = bool(ask.get("media")) or any(
        isinstance(o, dict) and o.get("media") for o in (options if isinstance(options, list) else []))
    sources = ask.get("sources") if isinstance(ask.get("sources"), list) else []
    spec_source = any(isinstance(s, dict) and s.get("kind") == "spec" for s in sources)
    if _DESIGN_WORDS.search(words) or has_media or spec_source:
        why.append("it is about a spec or design")
    return why


def validate_rich(ask: dict, where: str, ledger: dict, repo: Path | None = None) -> list[str]:
    """Check decision layer v2 (BRO-3167): its fields' shapes, its local paths, and when a summary is due."""
    errs: list[str] = []
    if ask.get("summary") is not None:
        errs.extend(_words(ask["summary"], where, "summary", SUMMARY_MAX, SUMMARY_MIN))
    if ask.get("details") is not None:
        value = ask["details"]
        if not isinstance(value, str) or not value.strip():
            errs.append(f"{where}: 'details' must be markdown (a non-empty string), got {value!r}")
        elif len(value) > DETAILS_MAX:
            errs.append(f"{where}: 'details' is {len(value)} chars (max {DETAILS_MAX}); link a doc as a source")
    options = ask.get("options") if isinstance(ask.get("options"), list) else []
    option_ids = [str(o.get("id")) for o in options if isinstance(o, dict)]
    if ask.get("tradeoffs") is not None:
        errs.extend(_tradeoff_errors(ask["tradeoffs"], where, option_ids))
    if ask.get("sources") is not None:
        errs.extend(_source_errors(ask["sources"], where, repo))
    if ask.get("media") is not None:
        errs.extend(_media_errors(ask["media"], where, MEDIA_ASK_MAX, repo))
    for i, opt in enumerate(options):
        if isinstance(opt, dict) and opt.get("media") is not None:
            errs.extend(_media_errors(opt["media"], f"{where} option {i}", MEDIA_OPTION_MAX, repo))

    why = rich_required(ask, ledger)
    if why and ask.get("summary") is None:
        errs.append(f"{where}: needs a 'summary' ({' and '.join(why)}; an open ask raised on or after "
                    f"{RICH_REQUIRED_FROM}) — 2-4 plain sentences on what is decided and what changes")
    if why:
        words = _ask_words(ask, ledger)
        cited = {str(s.get("ref", "")).split("#", 1)[0] for s in (ask.get("sources") or [])
                 if isinstance(s, dict)} if isinstance(ask.get("sources"), list) else set()
        for spec in sorted(set(_SPEC_PATH.findall(words))):
            if spec not in cited:
                errs.append(f"{where}: names {spec} but does not list it in 'sources'; the person "
                            f"deciding needs the link")
    return errs


# Sentinel: the ledger's path is known but it does not sit in a .control/asks directory.
_UNKNOWN_REPO = Path("/nonexistent/ask-ledger-unknown-repo")


def repo_of(path: Path | None) -> Path | None:
    """The repo a ledger's local paths are relative to: the parent of its .control/asks."""
    if path is None:
        return None
    parent = path.resolve().parent
    if parent.name == "asks" and parent.parent.name == ".broomva": return parent.parent.parent  # BSTACK-ONLY  # noqa: E701
    if parent.name == "asks" and parent.parent.name == ".control":
        return parent.parent.parent
    return _UNKNOWN_REPO


# --- idea triage: what a closed session left to explore (BRO-2802) --------
# A session ends with "next steps" and "ideas", and until now they became Low
# backlog tickets nobody triaged: the owner named it, "some of those are close
# documented only but no developed further". An idea is an ask of its own class,
# in one ledger per initiative (`.control/asks/ideas-<initiative>.yaml`), answered
# develop / park / kill. It never blocks a lane, so the audit layer's ladder
# (gates, exhausted, preanswerable) does not apply: an idea is not a last resort,
# it is a triage. The decision layer is required instead: an idea nobody can
# answer in ten seconds is the backlog ticket again.
IDEA_CLASS = "idea"
# What marks an ask as answered or closed, as archive reads it; an idea carrying
# any of these must say which option it took.
ANSWERED_KEYS = ("answered_at", "answered", "answer", "resolution", "outcome", "defaulted_at")
ANSWERED_STATUSES = ("resolved", "archived", "rerouted", "closed", "answered", "withdrawn", "defaulted")
IDEA_OPTIONS = ("develop", "park", "kill")
IDEA_SOURCE_KINDS = ("session", "pr", "report")
IDEA_FIELDS = ("initiative", "idea", "source", "evidence", "wake")
# The audit-layer fields an idea need not carry (they are still checked when present).
IDEA_EXEMPT = ("gates", "exhausted", "preanswerable")
IDEA_MAX = 160
INITIATIVE_MAX = 40
# `.yaml` only: CI validates `ideas-*.yaml`, so an `ideas-*.yml` would escape it.
IDEAS_LEDGER_RE = re.compile(r"^ideas-([a-z0-9]+(?:-[a-z0-9]+)*)\.yaml$")
_FULL_PR_REF = re.compile(r"^[\w.-]+/[\w.-]+#\d+$")
# A session id: its first 8 hex, or the whole UUID.
_SESSION_REF = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?$")
# Where to look: a ticket, a PR, a URL, or a path in the repo (with a directory,
# so a bare word such as "TODO" is not a path). Not free prose. A path must also
# exist, which validate_ideas_ledger checks when it knows the repo.
_EVIDENCE_RE = re.compile(r"^(BRO-\d+|[\w.-]+/[\w.-]+#\d+|https?://\S+|[\w.][\w.-]*(?:/[\w.-]+)+)$")
_EVIDENCE_LINK = re.compile(r"^(BRO-\d+|[\w.-]+/[\w.-]+#\d+|https?://\S+)$")


def _iso_date(value) -> _dt.date | None:
    """A YYYY-MM-DD date, from YAML's date or a string; a datetime is not a date."""
    if isinstance(value, _dt.datetime):
        return None
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        try:
            return _dt.date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _day_of(value) -> _dt.date | None:
    """The day of a date, a datetime, or a string starting YYYY-MM-DD."""
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        return _iso_date(value.strip()[:10])
    return None


def _instant_of(value) -> _dt.datetime | None:
    """A whole ISO date-time (a trailing Z allowed), as UTC; a bare date is its midnight; else None."""
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day, tzinfo=_dt.timezone.utc)
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    # Python < 3.11 needs seconds: "2026-10-05T21:00+00:00" -> insert ":00".
    raw = re.sub(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})([+-]\d{2}:\d{2})$", r"\1:00\2", raw)
    try:
        parsed = _dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_dt.timezone.utc)


# JavaScript's String.prototype.trim() set (WhiteSpace + LineTerminator), which Maestro's `text` uses. Python's
# strip() differs both ways: it also strips \x1c-\x1f and \x85, and it keeps \ufeff.
_JS_TRIM = ("\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
            "\u2028\u2029\u202f\u205f\u3000\ufeff")


def _text(value) -> str | None:
    """A ledger value as Maestro reads it (server/control-asks.ts `text`): a date, a number or a boolean is its
    words; a string is trimmed, and blank is none; a list, a map or null is none."""
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if not isinstance(value, str):
        return None
    return value.strip(_JS_TRIM) or None


def is_closed(ask: dict) -> bool:
    """Answered, resolved, rerouted, defaulted or archived: no longer a question for anyone.

    One definition, Maestro's (server/control-asks.ts `stateOf`): an archived_at stamp, any outcome key holding
    a value as `_text` reads one, or a terminal status. The parity corpus (maestro-paseo
    server/fixtures/ask_reader_parity.yaml, which scripts/test_ask_reader_parity.py fetches) holds the cases.
    """
    status = (_text(ask.get("status")) or "").lower()
    return (_text(ask.get("archived_at")) is not None
            or any(_text(ask.get(k)) is not None for k in ANSWERED_KEYS)
            or status.startswith(ANSWERED_STATUSES))


def asked_day(ask: dict, ledger: dict) -> _dt.date | None:
    """The day an ask was raised: the LATER of its own `asked_at` and the ledger's `opened`.

    Most legacy asks carry no `asked_at` (some carry a boolean `asked_at_hour_0`,
    which says when in the arc, not when on the calendar). An ask cannot predate the
    ledger it lives in, so `opened` is a lower bound: taking the later of the two
    means a backdated or mistyped `asked_at` cannot carry a new ask under the cutoff.
    """
    days = [d for d in (_day_of(ask.get("asked_at")), _day_of(ledger.get("opened"))) if d is not None]
    return max(days) if days else None


def validate_idea(ask: dict, where: str) -> list[str]:
    """The idea-triage class's own rules. The decision layer is checked by the caller."""
    errs: list[str] = []
    for key in IDEA_FIELDS:
        value = ask.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            errs.append(f"{where}: an idea needs '{key}'")
    initiative = ask.get("initiative")
    if initiative not in (None, ""):
        flat = str(initiative)
        if not TAG_RE.match(flat) or len(flat) > INITIATIVE_MAX:
            errs.append(f"{where}: initiative {flat!r} must be an initiative id "
                        f"(lowercase-hyphenated, <={INITIATIVE_MAX} chars)")
    idea = ask.get("idea")
    if isinstance(idea, str) and not idea.strip():
        idea = None
    if idea not in (None, ""):
        if not isinstance(idea, str) or "\n" in idea.strip():
            errs.append(f"{where}: 'idea' must be one line")
        elif len(idea.strip()) > IDEA_MAX:
            errs.append(f"{where}: 'idea' is {len(idea.strip())} chars (max {IDEA_MAX}); "
                        f"one line a person reads, the rest belongs in the evidence")
    source = ask.get("source")
    if source not in (None, ""):
        if not isinstance(source, dict):
            errs.append(f"{where}: 'source' must be a mapping {{kind, ref}}")
        else:
            kind, ref = source.get("kind"), source.get("ref")
            if kind not in IDEA_SOURCE_KINDS:
                errs.append(f"{where}: source kind {kind!r} not one of {list(IDEA_SOURCE_KINDS)}")
            if not isinstance(ref, str) or not ref.strip():
                errs.append(f"{where}: source needs a 'ref'")
            elif kind == "pr" and not _FULL_PR_REF.match(ref.strip()):
                errs.append(f"{where}: a pr source ref must be owner/repo#N, got {ref!r}")
            elif kind == "session" and not _SESSION_REF.match(ref.strip()):
                errs.append(f"{where}: a session source ref must be a hex session id, got {ref!r}")
            extra = sorted(set(source) - {"kind", "ref", "note"})
            if extra:
                errs.append(f"{where}: source has unknown keys {extra}")
    evidence = ask.get("evidence")
    if evidence not in (None, ""):
        if not isinstance(evidence, str) or not _EVIDENCE_RE.match(evidence.strip()):
            errs.append(f"{where}: evidence {evidence!r} must be a ticket (BRO-N), a PR "
                        f"(owner/repo#N), a URL or a repo path")
    wake = ask.get("wake")
    if wake not in (None, ""):
        day = _iso_date(wake)
        if day is None:
            errs.append(f"{where}: wake {wake!r} must be a YYYY-MM-DD date")
        else:
            asked_day = _day_of(ask.get("asked_at"))
            if asked_day and day < asked_day:
                errs.append(f"{where}: wake {day} is before the idea was asked ({asked_day})")
    if ask.get("blocking") is not False:
        errs.append(f"{where}: an idea never blocks a lane, so 'blocking' must be false")
    options = ask.get("options")
    if isinstance(options, list):
        ids = sorted(str(o.get("id")) for o in options if isinstance(o, dict))
        if ids != sorted(IDEA_OPTIONS):
            errs.append(f"{where}: an idea's options are exactly {list(IDEA_OPTIONS)}, got {ids}")
    # An answer is develop, park or kill, and when: what Maestro acts on (develop
    # files held work, park keeps the wake). A resolved idea with only prose says neither.
    chosen = ask.get("answer_option")
    answered_at = ask.get("answered_at")
    closed = is_closed(ask)
    if chosen is not None and chosen not in IDEA_OPTIONS:
        errs.append(f"{where}: answer_option {chosen!r} is not one of {list(IDEA_OPTIONS)}")
    if chosen is not None and answered_at in (None, ""):
        errs.append(f"{where}: an answered idea needs 'answered_at'")
    if closed and chosen is None:
        errs.append(f"{where}: an answered idea needs 'answer_option' (one of {list(IDEA_OPTIONS)})")
    answered_instant = _instant_of(answered_at)
    if answered_at not in (None, "") and answered_instant is None:
        errs.append(f"{where}: answered_at {answered_at!r} is not a date and time")
    asked_instant = _instant_of(ask.get("asked_at"))
    if asked_instant and answered_instant and answered_instant < asked_instant:
        errs.append(f"{where}: answered {answered_at}, before it was asked ({ask.get('asked_at')})")
    return errs


def asks_of(ledger: dict) -> list | None:
    """The asks list Maestro shows (server/control-asks.ts `asksKey`): `asks` when it is a list, else
    `open_asks` when that is; no key is no asks; None when `asks` is something else."""
    raw = ledger.get("asks")
    if isinstance(raw, list):
        return raw
    if isinstance(ledger.get("open_asks"), list):
        return ledger["open_asks"]
    return [] if raw is None else None


def validate_ideas_ledger(ledger: dict, path: Path) -> list[str]:
    """Where ideas live: one ledger per initiative, `ideas-<initiative>.yaml`, holding ideas only."""
    errs: list[str] = []
    asks = [a for a in asks_of(ledger) or [] if isinstance(a, dict)]
    match = IDEAS_LEDGER_RE.match(path.name)
    ideas = [a for a in asks if a.get("class") == IDEA_CLASS]
    if not match:
        for a in ideas:
            errs.append(f"ask {a.get('id')}: an idea lives in .control/asks/ideas-<initiative>.yaml, "
                        f"not in {path.name}")
        return errs
    initiative = match.group(1)
    if ledger.get("arc") != f"ideas-{initiative}":
        errs.append(f"arc must be 'ideas-{initiative}', the ledger's own name (got {ledger.get('arc')!r})")
    for a in asks:
        where = f"ask {a.get('id')}"
        if a.get("class") != IDEA_CLASS:
            errs.append(f"{where}: {path.name} holds ideas only (class: idea), got {a.get('class')!r}")
        elif a.get("initiative") not in (None, "") and str(a.get("initiative")) != initiative:
            errs.append(f"{where}: initiative {a.get('initiative')!r} is not this ledger's ({initiative!r})")
    # The join key must resolve: an idea for an initiative with no file is filed
    # nowhere. A ledger with no .control/initiatives beside it fails too: a check
    # that cannot look is not a pass.
    control = path.resolve().parent.parent
    initiatives = control / "initiatives"
    if not initiatives.is_dir():
        errs.append(f"no .control/initiatives directory beside {path.name} "
                    f"(an ideas ledger lives in .control/asks/)")
    elif not (initiatives / f"{initiative}.yaml").is_file():
        errs.append(f"no initiative file .control/initiatives/{initiative}.yaml for {path.name}")
    # Evidence that is a repo path must exist in that repo.
    repo = control.parent
    for a in ideas:
        ev = a.get("evidence")
        if isinstance(ev, str) and _EVIDENCE_RE.match(ev.strip()) and not _EVIDENCE_LINK.match(ev.strip()):
            if not _repo_path_exists(repo, ev.strip()):
                errs.append(f"ask {a.get('id')}: evidence path {ev.strip()!r} does not exist in the repo")
    return errs


def _repo_path_exists(repo: Path, rel: str) -> bool:
    """`rel` exists inside `repo` with exactly that case: macOS's disk would match
    `Docs/` for `docs/` and Linux CI would not; `..` never leaves the repo."""
    parts = Path(rel).parts
    if any(part in ("..", "") for part in parts):
        return False
    here = repo
    for part in parts:
        try:
            if part not in os.listdir(here):
                return False
        except OSError:
            return False
        here = here / part
    return True


class LedgerError(Exception):
    pass


# Maestro's YAML parser (`yaml` 2.9) refuses a file once one anchor's uses (the anchor itself counted) times the
# aliases nested in it reach 100: 99 `*p` read, 100 do not. The validator refuses a superset: more aliases than
# this in all, or any alias inside an anchored node (nesting is how a small file expands without bound).
MAX_ALIASES = 99


def _refuse_unsafe_yaml(text: str) -> None:
    """Refuse what the two readers parse apart: nested or excess aliases, and explicit tags (`!!null merge` is
    null to PyYAML and "merge" to Maestro's parser; `!!int x` throws here)."""
    aliases = 0
    open_anchors: list[bool] = []   # per open collection: is it, or something around it, anchored
    for event in yaml.parse(text, Loader=_StrictLoader):
        if isinstance(event, yaml.AliasEvent):
            aliases += 1
            if aliases > MAX_ALIASES:
                raise LedgerError(f"more than {MAX_ALIASES} aliases; Maestro refuses such a file")
            if any(open_anchors):
                raise LedgerError(f"an alias inside an anchored node at line {event.start_mark.line + 1}; "
                                  "write the value out")
            continue
        tag = getattr(event, "tag", None)
        if isinstance(event, (yaml.ScalarEvent, yaml.CollectionStartEvent)) and tag not in (None, "!"):
            raise LedgerError(f"an explicit tag {tag!r} at line {event.start_mark.line + 1}; the readers "
                              "resolve tags apart, so write the plain value")
        if isinstance(event, yaml.CollectionStartEvent):
            open_anchors.append(event.anchor is not None)
        elif isinstance(event, yaml.CollectionEndEvent):
            open_anchors.pop()


def _parse_ledger_text(path: Path, text: str) -> dict:
    """The parse half of `load()`, taking text directly rather than re-reading the
    file — so a caller that already holds an open, locked file descriptor (see
    `stamp_origin`) parses the bytes it is holding instead of racing a second,
    unlocked open against itself."""
    try:
        _refuse_unsafe_yaml(text)
        # Strict: PyYAML's safe_load keeps the LAST of two duplicate keys without a word, so a file with
        # two `status:` lines validated here while Maestro refused to read it (BRO-3109).
        data = yaml.load(text, Loader=_StrictLoader)
    except yaml.YAMLError as e:
        raise LedgerError(f"{path}: unparseable YAML: {e}")
    except LedgerError as e:  # a duplicate key, an alias or a tag
        raise LedgerError(f"{path}: {e}")
    except (ValueError, TypeError, KeyError, OverflowError) as e:  # `2026-02-30` as a date, say
        raise LedgerError(f"{path}: a value PyYAML cannot build: {e}")
    if not isinstance(data, dict):
        raise LedgerError(f"{path}: top level must be a mapping")
    return data


def load(path: Path) -> dict:
    # Opened without blocking and judged by the open fd, so a FIFO (even one swapped in
    # after a check by name) is refused rather than read with no deadline.
    # The one boundary: every way the read can fail leaves as a LedgerError, which each
    # command reports as `error: …` with exit 2, never a traceback (P20 continuation,
    # workspace#1132: an unreadable or non-UTF-8 ledger traced back).
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        raise LedgerError(f"no ledger at {path}")
    except OSError as exc:  # a socket cannot be opened at all; a mode-000 file cannot be read
        if os.path.exists(path) and not os.path.isfile(path):
            raise LedgerError(f"{path} is not a regular file")
        raise LedgerError(f"{path} cannot be read: {exc.strerror or exc}")
    try:
        if not stat_mod.S_ISREG(os.fstat(fd).st_mode):  # judged before wrapping: a folder fails to wrap
            raise LedgerError(f"{path} is not a regular file")
        with os.fdopen(fd, encoding="utf-8") as fh:
            fd = -1  # the file object owns it now
            text = fh.read()
    except UnicodeDecodeError as exc:
        raise LedgerError(f"{path} is not UTF-8 text (byte {exc.start})")
    except OSError as exc:
        raise LedgerError(f"{path} cannot be read: {exc.strerror or exc}")
    finally:
        if fd >= 0:
            os.close(fd)
    return _parse_ledger_text(path, text)


def _preauth_hit(ask: dict, preauth: dict) -> dict | None:
    """Return the standing grant matching this ask, if any.

    A grant matches when every key it names matches the ask. Whether a match should
    SUPPRESS the ask depends on `granted`, and the caller must check it — see below.
    """
    for grant in preauth.get("grants") or []:
        match = grant.get("match") or {}
        if not match:
            continue                      # an empty match would swallow everything
        if all(ask.get(k) == v for k, v in match.items() if k != "action"):
            action = match.get("action")
            if action is None or action.lower() in str(ask.get("ask", "")).lower():
                return grant
    return None


def _suppresses(grant: dict | None) -> bool:
    """Only `granted: true` settles a question.

    `granted: false` records the OPPOSITE — "always ask" — so treating it as a hit
    would silently swallow the one class of ask the operator most wants to see. The
    first draft did exactly that, and the shipped preauth file's only row is a
    `granted: false` for publishing to a public URL: the inversion would have
    suppressed every public-publish approval. Found by cross-model review.
    """
    return bool(grant) and grant.get("granted") is True


def _ask_layer_errors(ask: dict, where: str, ledger: dict, require_all: bool = False) -> list[str]:
    """One ask's decision-layer errors under the date rule (or for every ask, if forced)."""
    day = asked_day(ask, ledger)
    by_date = not is_closed(ask) and day is not None and day >= DECISION_LAYER_REQUIRED_FROM
    why = "" if require_all else \
        f"an open ask raised {day} (on or after {DECISION_LAYER_REQUIRED_FROM}) must carry one"
    return validate_decision_layer(ask, where, required=require_all or by_date, why_required=why)


def decision_layer_errors(ledger: dict, require_all: bool = False, repo: Path | None = None) -> list[str]:
    """The decision-layer half of `validate`, alone.

    For checking the rule across ledgers whose OTHER fields predate the current
    schema (legacy `question:`/`summary:` rows that fail the audit-layer checks):
    the rule must hold there too, and it cannot wait for those rows to be rewritten.
    """
    errs: list[str] = []
    for i, ask in enumerate(asks_of(ledger) or []):
        if not isinstance(ask, dict):
            continue
        aid = ask.get("id")
        where = f"ask {aid}" if aid else f"asks[{i}]"
        forced = require_all or ask.get("class") == IDEA_CLASS
        errs.extend(_ask_layer_errors(ask, where, ledger, require_all=forced))
        errs.extend(validate_answer_words(ask, where))
        errs.extend(validate_rich(ask, where, ledger, repo))
    return errs


# --- BSTACK-ONLY: sweep a directory of ledgers ------------------------------
def sweep(path: Path) -> list[Path]:
    """Every ledger a path denotes: a directory yields its *.yaml, sorted; a file
    yields itself.

    Arcs are per-ledger but VISIBILITY is not — "what is this repo waiting on a human
    for" spans every open arc, and answering it by running the CLI once per file is a
    loop nobody runs. Measured in SRI 2026-09-11: six ledgers, four schema-invalid and
    two unparseable, undetected since 2026-09-08 because no command ever read more
    than one.
    """
    if path.is_dir():
        return sorted(path.glob("*.yaml"))
    return [path]


def load_sweep(path: Path) -> tuple[list[tuple[Path, dict]], list[tuple[Path, str]]]:
    """Load every ledger under `path`, returning (loaded, broken).

    A broken ledger is COLLECTED, never raised. The single-file path exits 2 on an
    unparseable file, which is right for one arc and wrong for a sweep: the first bad
    file would hide every ledger after it, and the bad file is exactly the one whose
    neighbours you now want to check.
    """
    loaded: list[tuple[Path, dict]] = []
    broken: list[tuple[Path, str]] = []
    for f in sweep(path):
        try:
            loaded.append((f, load(f)))
        except LedgerError as e:
            broken.append((f, str(e)))
    return loaded, broken


# --- END BSTACK-ONLY ---------------------------------------------------------
def validate(ledger: dict, preauth: dict | None = None,
             require_decisions: bool = False,
             require_decision: bool = False,
             path: Path | None = None) -> list[str]:
    """Return a list of errors. Empty list means the ledger is well-formed.

    `path`, when given, also checks where ideas live (validate_ideas_ledger).
    """
    errs: list[str] = []
    preauth = preauth or {}
    repo = repo_of(path)

    for key in ("arc", "opened", "tz", "lanes", "asks"):
        if key not in ledger:
            errs.append(f"missing top-level key: {key}")
    lanes = ledger.get("lanes") or []
    if not isinstance(lanes, list):
        errs.append("lanes must be a list")
        lanes = []
    elif "lanes" in ledger and not lanes:
        errs.append("lanes is empty — an arc with no lanes has nothing to park or run")
    done = ledger.get("done") or []
    if not isinstance(done, list):
        errs.append("done must be a list")
        done = []
    for d in done:
        if d not in lanes:
            errs.append(f"done names '{d}', which is not a declared lane")
    asks = asks_of(ledger)
    if asks is None:
        errs.append("asks must be a list")
        return errs

    seen: set[str] = set()
    for i, ask in enumerate(asks):
        where = f"asks[{i}]"
        if not isinstance(ask, dict):
            errs.append(f"{where}: must be a mapping")
            continue
        aid = ask.get("id")
        where = f"ask {aid}" if aid else where
        is_idea = ask.get("class") == IDEA_CLASS
        for f in REQUIRED_ASK_FIELDS:
            if f not in ask and not (is_idea and f in IDEA_EXEMPT):
                errs.append(f"{where}: missing field '{f}'")
        if "ask" in ask and not (isinstance(ask["ask"], str) and ask["ask"].strip()):
            errs.append(f"{where}: 'ask' must be the question as non-empty text, got {ask['ask']!r}")
        errs.extend(_ask_layer_errors(ask, where, ledger, require_all=require_decision or is_idea))
        errs.extend(validate_answer_words(ask, where))
        errs.extend(validate_rich(ask, where, ledger, repo))
        if is_idea:
            errs.extend(validate_idea(ask, where))
        if aid in seen:
            errs.append(f"{where}: duplicate id")
        seen.add(aid)

        for flag in ("blocking", "preanswerable"):
            if flag in ask and not isinstance(ask.get(flag), bool):
                errs.append(
                    f"{where}: '{flag}' must be a real boolean, got {ask.get(flag)!r} — "
                    f"a quoted 'true' reads as a string and silently takes the wrong branch"
                )
        answered = ask.get("answered_at")
        # YAML parses an unquoted 2026-08-18T21:25:00Z into a datetime, so a real
        # timestamp was being rejected as malformed. Accept both shapes.
        if isinstance(answered, (_dt.datetime, _dt.date)):
            answered = answered.isoformat()
        if answered is not None and not (isinstance(answered, str) and answered.strip()):
            errs.append(
                f"{where}: 'answered_at' must be null or a non-empty timestamp, got "
                f"{answered!r} — an empty string would release a genuinely unanswered lane"
            )

        klass = ask.get("class")
        if klass is not None and klass not in CLASSES:
            errs.append(f"{where}: class '{klass}' not one of {sorted(CLASSES)}")

        gates = ask.get("gates") or []
        if not isinstance(gates, list):
            errs.append(f"{where}: gates must be a list")
        else:
            for g in gates:
                # Unconditional: guarding this on `if lanes` made it vacuous exactly when
                # the ledger was most malformed (an empty lanes list accepted any gate).
                if g not in lanes:
                    errs.append(f"{where}: gates unknown lane '{g}'")

        # THE load-bearing rule: asking is the last resort, not the interface.
        exhausted = ask.get("exhausted")
        if "exhausted" in ask:
            if not isinstance(exhausted, list) or not exhausted:
                errs.append(
                    f"{where}: 'exhausted' is empty — an ask that skipped the autonomy ladder "
                    f"is a defect, not a question."
                )
            else:
                named = {m.group(1).lower() for m in
                         (_RUNG_RE.match(str(e)) for e in exhausted) if m}
                unlabelled = [str(e)[:40] for e in exhausted if not _RUNG_RE.match(str(e))]
                if unlabelled:
                    errs.append(
                        f"{where}: 'exhausted' entries must name the rung they report on "
                        f"({'|'.join(RUNGS)}), e.g. 'read: no prior decision on disk'. "
                        f"Unlabelled: {unlabelled}"
                    )
                if len(named) < MIN_RUNGS:
                    errs.append(
                        f"{where}: 'exhausted' records {len(named)} rung(s), needs >= {MIN_RUNGS}. "
                        f"Reaching the ask means more than one route was tried and failed; a "
                        f"single line certifies nothing."
                    )
        blocking = ask.get("blocking")
        has_default = ask.get("default") not in (None, "")
        if blocking is True:
            if has_default:
                errs.append(f"{where}: blocking:true but a default is set — then it is not blocking")
            reason = ask.get("no_default_because")
            if reason not in NO_DEFAULT_REASONS:
                errs.append(
                    f"{where}: blocking:true requires no_default_because in "
                    f"{sorted(NO_DEFAULT_REASONS)} (got {reason!r}). Every other choice has a "
                    f"reversible default and must not stall a lane."
                )
        elif blocking is False and not has_default:
            errs.append(f"{where}: blocking:false requires a default — silence must never be fatal")

        hit = _preauth_hit(ask, preauth)
        if _suppresses(hit) and ask.get("answered_at") is None:
            errs.append(
                f"{where}: already settled by a standing grant "
                f"({hit.get('note') or hit.get('match')}) — do not re-raise it"
            )
    errs.extend(validate_decisions(ledger))
    if path is not None:
        errs.extend(validate_ideas_ledger(ledger, path))
    # Absent `decisions:` is legal by default (backward compatibility, and an arc
    # that genuinely decided nothing must validate). But that leaves the headline
    # claim — "every choice taken on your behalf is recorded" — unenforced, so the
    # caller that CAN assert it (a closing handback, CI on an arc branch) opts in.
    if require_decisions and not (ledger.get("decisions") or []):
        errs.append(
            "no 'decisions' recorded, but --require-decisions was set — an arc that "
            "took reversible defaults and logged none is indistinguishable from one that "
            "decided nothing, and the user still inherits the difference"
        )
    return errs


def validate_decisions(ledger: dict) -> list[str]:
    """Errors in the `decisions:` list. Empty list means well-formed.

    Absent `decisions:` is legal and silent — a ledger written before this existed,
    or an arc that genuinely decided nothing, must keep validating. What this cannot
    detect is the arc that DID take defaults and recorded none; no property of this
    file distinguishes that from an honest empty. That gap belongs to the handback
    skill, which renders the block, not to the schema.
    """
    errs: list[str] = []
    decisions = ledger.get("decisions")
    if decisions is None:
        return errs
    if not isinstance(decisions, list):
        return ["decisions must be a list"]

    ask_ids = {
        str(a.get("id")) for a in (ledger.get("asks") or []) if isinstance(a, dict)
    }
    seen: set[str] = set()
    for i, d in enumerate(decisions):
        where = f"decisions[{i}]"
        if not isinstance(d, dict):
            errs.append(f"{where}: must be a mapping")
            continue
        did = d.get("id")
        # str(), not the raw value: a list or dict id is unhashable and would
        # crash `seen.add` — the validator falling over on malformed input is the
        # one failure mode a validator may not have.
        did_key = str(did)
        where = f"decision {did}" if isinstance(did, str) and did.strip() else where
        for f in REQUIRED_DECISION_FIELDS:
            if f not in d:
                errs.append(f"{where}: missing field '{f}'")
                continue
            # Presence is not content. An empty string satisfied every rule below
            # while saying nothing, which is precisely the disclosure this file
            # exists to force. Enum fields are checked separately just after.
            if f in ("verdict", "confidence"):
                # Their VALUES are enum-checked below, but that check is guarded
                # on `is not None`, so a present-but-null field passed both here
                # and there. Type them.
                if not isinstance(d.get(f), str) or not d.get(f).strip():
                    errs.append(
                        f"{where}: '{f}' must be a non-empty string, got {d.get(f)!r}"
                    )
                continue
            v = d.get(f)
            if not isinstance(v, str) or not v.strip():
                errs.append(
                    f"{where}: '{f}' must be a non-empty string, got {v!r} — a present-but-blank "
                    f"field discloses nothing while passing every check"
                )
        # Banked is settled: a choice already in the ledger is a given for later
        # passes. Two entries under one id means one of them silently re-decided it.
        if did_key in seen:
            errs.append(f"{where}: duplicate id — banked is settled, a decision is recorded once")
        seen.add(did_key)

        verdict = d.get("verdict")
        # `verdict not in VERDICTS` raises TypeError on an unhashable value (a
        # dict or list from hand-edited YAML), so the validator crashed on
        # exactly the malformed input it exists to reject. Membership is only
        # asked once the value is known to be a string.
        if not isinstance(verdict, str):
            verdict = None
        if verdict is not None and verdict not in VERDICTS:
            errs.append(f"{where}: verdict {verdict!r} not one of {sorted(VERDICTS)}")
        conf = d.get("confidence")
        if not isinstance(conf, str):
            conf = None
        if conf is not None and conf not in CONFIDENCE:
            errs.append(f"{where}: confidence {conf!r} not one of {list(CONFIDENCE)}")

        # THE load-bearing rule, and the exact mirror of the blocking/default rule
        # on asks: not asking is only defensible when the choice can be undone.
        # A decision with neither a reversal nor a named reason is the arc claiming
        # a right it did not earn.
        reversal = d.get("reversal")
        has_reversal = isinstance(reversal, str) and reversal.strip() != ""
        reason = d.get("irreversible_because")
        if has_reversal and reason is not None:
            errs.append(
                f"{where}: has both 'reversal' and 'irreversible_because' — if it can be "
                f"undone it is not irreversible. Say which."
            )
        elif not has_reversal:
            # Rung 6 licenses REVERSIBLE defaults only. An irreversible choice
            # taken on the user's behalf is not a logged default — it is an ask
            # that should have reached them. Recording it is still right (better
            # disclosed than not), but it must not read as licensed: it has to
            # carry the needs-user verdict and point at the ask.
            if reason in NO_DEFAULT_REASONS and verdict != "needs-user":
                errs.append(
                    f"{where}: irreversible_because={reason!r} but verdict is {verdict!r} — "
                    f"rung 6 licenses reversible defaults only, so an irreversible choice must "
                    f"be recorded as 'needs-user' and name the ask that carried it to the human"
                )
            if reason not in NO_DEFAULT_REASONS:
                errs.append(
                    f"{where}: needs a non-empty 'reversal' (how the user undoes this), or "
                    f"'irreversible_because' in {sorted(NO_DEFAULT_REASONS)} (got {reason!r}). "
                    f"An irreversible choice taken without asking is the one thing rung 6 "
                    f"never licensed."
                )

        # A patch layered on a bad decision preserves the decision. The entry names
        # the property that must hold in general, or it has not said anything.
        if verdict == "unsound":
            corrected = d.get("corrected_decision")
            if not (isinstance(corrected, str) and corrected.strip()):
                errs.append(
                    f"{where}: verdict 'unsound' requires 'corrected_decision' — the decision "
                    f"to redo from, not an edit to layer on top"
                )
        # needs-user is reserved for genuinely user-only calls. One that never
        # reached the user is not a verdict, it is a dropped ask.
        if verdict == "needs-user":
            ref = d.get("ask")
            if ref is None:
                errs.append(
                    f"{where}: verdict 'needs-user' requires 'ask' naming the ask that carries "
                    f"it — a user-only call that never became an ask is a dropped ask"
                )
            elif str(ref) not in ask_ids:
                errs.append(f"{where}: 'ask' names {ref!r}, which is not an ask in this ledger")

        scenario = d.get("scenario")
        headline = str(d.get("decision") or "")
        if isinstance(scenario, str):
            if len(scenario.strip()) < SCENARIO_MIN:
                errs.append(
                    f"{where}: 'scenario' is {len(scenario.strip())} chars, floor is "
                    f"{SCENARIO_MIN} — an entry compressed to its headline makes the reader "
                    f"interrogate it, which is the failure this field exists to prevent"
                )
            elif len(scenario.strip()) <= len(headline.strip()):
                errs.append(
                    f"{where}: 'scenario' is no longer than 'decision' — it must walk the "
                    f"case (trigger, what the work does today, what the alternative would do), "
                    f"not restate the headline"
                )
    return errs


def sort_decisions(ledger: dict, grouped: bool = True) -> list[dict]:
    """Least-confident first. Grouped by verdict when asked, flat otherwise.

    Confidence ranks; verdict groups. Flat ordering exists because the report opens
    with the least-confident few OVERALL, whatever their group — a high-confidence
    `needs-user` must not outrank a low-confidence `sound` in that opening.
    """
    raw = ledger.get("decisions") or []
    ds = [d for d in raw if isinstance(d, dict)]

    def conf_rank(d: dict) -> int:
        c = d.get("confidence")
        return CONFIDENCE.index(c) if c in CONFIDENCE else -1

    if not grouped:
        return sorted(ds, key=lambda d: (conf_rank(d), str(d.get("id"))))
    order = {"needs-user": 0, "unsound": 1, "sound": 2}
    return sorted(
        ds, key=lambda d: (order.get(d.get("verdict"), 3), conf_rank(d), str(d.get("id")))
    )


ROW_CAP = 7


def render_handback_block(ledger: dict, cap: int = ROW_CAP) -> str:
    """The 🔀 Decided for you block, rendered — not described.

    The handback contract states four properties of this block: least-confident
    first, every row shows its undo, hard cap 7, overflow counted in the header.
    Stated in prose those are four things an agent may forget one at a time. This
    function is the executable form, so a test can delete any one of them and go
    red. (Workspace invariant: a discipline that maps to no machine-checkable
    behaviour is not a discipline.)

    Returns "" for an empty ledger: the block is omitted, not rendered empty. The
    contract's "always in this order" constrains ORDER, never presence — a block
    with no rows would assert that nothing was decided, which is a claim this
    function has no standing to make.
    """
    # needs-user rows are excluded: they reached the human and belong in the ⛔
    # ask block, keyed by their `ask`. Rendering them here too would print the
    # same item twice under contradictory headings — "decided for you" and
    # "blocked on you" — which was visible in this repo's own arc ledger.
    # Validate the cap FIRST. Placing this after the empty-row return made the
    # check path-dependent: an empty or all-needs-user ledger accepted cap=999
    # and only a populated one rejected it, so the bad call site would be found
    # by whichever ledger happened to be non-empty.
    # `isinstance(True, int)` is True in Python, so bool is excluded explicitly —
    # cap=True would silently mean cap=1 and hide six rows.
    if isinstance(cap, bool) or not isinstance(cap, int) or not 0 <= cap <= ROW_CAP:
        raise ValueError(f"cap must be an int in 0..{ROW_CAP}, got {cap!r}")
    rows = [d for d in sort_decisions(ledger, grouped=False)
            if d.get("verdict") != "needs-user"]
    if not rows:
        return ""
    total = len(rows)
    shown = rows[:cap]
    head = f"## 🔀 Decided for you — {total} choice{'s' if total != 1 else ''}, least-confident first"
    if total > cap:
        # Never silently truncate: a capped list that does not say it is capped
        # reads as the complete one.
        head = (f"## 🔀 Decided for you — {len(shown)} of {total} shown, least-confident first"
                f" (full list in the ledger)")
    out = [head, "", "| # | I decided | Why it was mine to take | Undo |", "|---|---|---|---|"]
    for i, d in enumerate(shown, 1):
        undo = d.get("reversal")
        if not (isinstance(undo, str) and undo.strip()):
            # validate() rejects this shape, but the renderer must not quietly
            # print an empty cell if it is ever handed one — the undo IS the
            # justification for not having asked.
            undo = f"**IRREVERSIBLE** ({d.get('irreversible_because')}) — see ask {d.get('ask')}"
        what = str(d.get("decision") or "")
        # An unsound row that says only "this was wrong" is the shape the ledger
        # exists to replace. validate_decisions REQUIRES corrected_decision on
        # every unsound entry, and it is the actionable half — the property that
        # must hold in general, not an edit to layer on top — so it has to reach
        # the reader, not just the schema.
        if d.get("verdict") == "unsound":
            fix = str(d.get("corrected_decision") or "").strip()
            if fix:
                what += f" — **redo from:** {fix}"
            else:
                # validate() rejects this shape. Rendering a tidy "(unstated)"
                # normalised a contract violation into ordinary-looking output,
                # so a direct caller that skipped validation saw a plausible
                # block instead of a broken one. Mark it unmistakably and say so
                # on stderr — a missing redo is the entry's whole point missing.
                what += " — **redo from:** ⚠ MISSING (invalid entry: validate the ledger)"
                print(f"render_handback_block: decision {d.get('id')!r} is unsound with no "
                      f"corrected_decision — the ledger is invalid", file=sys.stderr)
        cells = [str(i), _md(what), _md(d.get("gap")), _md(undo)]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def _md(v) -> str:
    """Cell-safe: an unescaped pipe in prose would split the row and shift every
    later column, silently reattributing an undo to the wrong decision.

    TRUST CONTRACT, and it is narrow: this guarantees TABLE STRUCTURE only —
    that N cells in produce N cells out. It does NOT sanitise markdown or HTML.
    A ledger is authored by the agent that ran the arc, and the block renders
    `**bold**` on purpose, so emphasis inside a cell is by design; but an entry
    quoting content from a file the agent read can carry arbitrary markdown into
    the handback. Every cell has always had this property — `decision`, `gap`
    and `reversal` alike — and `corrected_decision` is one more, not a new class.
    If a ledger ever accepts third-party text, escaping has to move up to
    markdown semantics and this docstring is the place that stops being true."""
    # Backslashes FIRST. Escaping pipes alone turns an input that already
    # contains "\\|" into "\\\\|", which markdown renders as a literal backslash
    # and then splits on the pipe — reintroducing the exact bug at one remove.
    return (str(v if v is not None else "")
            .replace("\\", "\\\\")
            .replace("|", "\\|")
            .replace("\r", " ")
            .replace("\n", " ")
            .replace("\u2028", " ")
            .replace("\u2029", " ")
            .strip())


def runnable_lanes(ledger: dict) -> tuple[list[str], dict[str, list[str]]]:
    """(lanes that can still run, {parked lane: [ask ids that park it]}).

    A lane is parked only by an OPEN ask that is BLOCKING. An ask with a default
    does not park anything — the arc takes the default and keeps going.

    A lane listed in the ledger's top-level `done:` is finished and is neither
    runnable nor parked. Without it every declared lane counted as runnable forever,
    so `lanes --runnable` could never go empty and a finished arc could never
    legitimately stop — the exact opposite of the invariant it encodes. Found by
    cross-model review against this file's own shipped example.
    """
    done = set(ledger.get("done") or [])
    lanes = [l for l in (ledger.get("lanes") or []) if l not in done]
    parked: dict[str, list[str]] = {}
    for ask in ledger.get("asks") or []:
        if not isinstance(ask, dict):
            continue
        if ask.get("answered_at") is not None:
            continue
        if ask.get("blocking") is not True:
            continue
        for lane in ask.get("gates") or []:
            if lane in done:
                continue                  # a finished lane cannot be parked
            parked.setdefault(lane, []).append(str(ask.get("id")))
    return [l for l in lanes if l not in parked], parked


def _leverage(ask: dict) -> int:
    """Rank key from `unblocks`: its leading integer, else 0.

    "unblocks 9 of 18 rows" -> 9. The first draft advertised leverage ordering in
    --help and returned YAML insertion order, so the handback could surface the
    cheapest ask first — the opposite of the rule it exists to serve.
    """
    import re as _re
    m = _re.search(r"\d+", str(ask.get("unblocks") or ""))
    return int(m.group()) if m else 0


def open_asks(ledger: dict) -> list[dict]:
    """Open asks, most leverage first; blocking asks outrank non-blocking at equal
    leverage, because those are the ones that actually park a lane."""
    opens = [
        a for a in (ledger.get("asks") or [])
        if isinstance(a, dict) and a.get("answered_at") is None
    ]
    return sorted(
        opens,
        key=lambda a: (-_leverage(a), a.get("blocking") is not True, str(a.get("id"))),
    )


# --- origin: the routing key, stamped at creation (BRO-2179 follow-up) ------
# An ask ledger is git-tracked, so an identical copy of every ledger exists in
# every worktree. Nothing about the FILE says which session raised the ask, which
# means an answer has no address to go back to. Paseo exports PASEO_AGENT_ID and
# PASEO_AGENT_CWD into every agent session; recording them at ask-creation time is
# what turns "answer the ask" into a delivery rather than a guess.
#
# The write is a line insert rather than a yaml.dump round-trip on purpose: pyyaml
# does not preserve comments, and re-emitting reflows every folded scalar in the
# file. These ledgers are reviewed as diffs, so a 3-line change must read as one.
_ID_LINE = re.compile(r"^(\s*)(-\s+)?id:\s*(.+?)\s*$")
_TRAILING_COMMENT = re.compile(r"^(\S+)\s+#.*$")


def _unquote_id(raw: str) -> str:
    """Normalize the RAW text `_ID_LINE` captured after `id:` to what `load()`
    actually parsed, so the two can be compared.

    `_ID_LINE` is a line regex, not a YAML parser, so it captures everything up
    to end-of-line verbatim. Three shapes round-trip differently through PyYAML
    than through this regex: an id written quoted (`id: "A1"`) PARSES to the
    unquoted `A1` that `load()` and `targets` hold; a plain scalar followed by a
    trailing comment (`id: A1  # note`) PARSES to just `A1`, the comment never
    part of the value; and the two COMBINED (`id: "A1"  # note`) need the
    comment stripped FIRST — doing the quote-check first on the combined shape
    compares `raw[-1]` (the comment's last character) against `raw[0]` (the
    opening quote), which never match, so the quote strip silently no-ops and
    the quotes survive into the "already stamped" comparison. Strip order is
    therefore comment, then quotes, never the reverse — round 2 of P20 Stratum
    B (bstack#130) reproduced this exact combined case after round 1 had only
    covered the quote and the comment shapes separately. Ask ids are never
    YAML-escaped internally (no embedded quotes or `#`), so both strips
    round-trip exactly, in either order ON THEIR OWN — only the combination
    is order-sensitive.
    """
    m = _TRAILING_COMMENT.match(raw)
    if m:
        raw = m.group(1)
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("\"", "'"):
        return raw[1:-1]
    return raw


# Asks raised from this day on are expected to name their asker (BRO-2918). Of 51
# ledgers on main on 2026-10-08, 3 carried an origin, and routing filed 6 of 14
# answers as new "Act on your answer" sessions because nobody was named. Older asks
# are left to the router's fallback rather than flagged: their sessions are gone.
ORIGIN_EXPECTED_FROM = _dt.date(2026, 10, 8)


def _routable(origin: object) -> bool:
    """An origin the routers can address: a mapping with an `agent_id`. Maestro's
    router (maestro-paseo control-asks.ts) reads only that key, and both readers
    take a string or an id-less mapping as no origin at all."""
    return isinstance(origin, dict) and bool(origin.get("agent_id"))


def _maestro_origin(origin: object) -> bool:
    """An ask Maestro filed for a caller it could not verify (`ask_ledger.py file
    --maestro-session`): `{via: maestro, session, verified: false}`, no `agent_id`.
    It names its asker as far as anyone can, so it is an origin to keep: `stamp`
    without `--ask` never overwrites it, and it is never warned about as missing.
    A stamp there would route the owner's answer to whichever session ran the
    hook, not to the one that asked (BRO-3406, maestro#11 P20 round 3)."""
    return isinstance(origin, dict) and origin.get("via") == "maestro"


def _kept(origin: object) -> bool:
    """An origin a default `stamp` leaves alone."""
    return _routable(origin) or _maestro_origin(origin)


def origin_warnings(ledger: dict) -> list[str]:
    """Open asks raised on or after ORIGIN_EXPECTED_FROM that name no asker.

    A warning, never an error: CI validates landed ledgers outside any Paseo
    session, where there is no id to stamp, so failing there would block the
    merge without being fixable. Ideas are exempt: an idea with no origin routes
    to "none" (its develop answer files its own work), which is not a failure.
    """
    out = []
    for ask in ledger.get("asks") or []:
        if (not isinstance(ask, dict) or is_closed(ask) or ask.get("class") == IDEA_CLASS
                or _kept(ask.get("origin"))):
            continue
        day = asked_day(ask, ledger)
        if day is not None and day >= ORIGIN_EXPECTED_FROM:
            out.append(f"ask {ask.get('id')}: no origin, so its answer has no session to go back to "
                       f"(run `ask_ledger.py stamp <ledger>` in the session that asks)")
    return out


def _strip_origin_blocks(lines: list[str], ids: set[str]) -> list[str]:
    """Remove any `origin:` block already belonging to one of `ids`, so a forced
    re-stamp replaces it instead of appending a second one. PyYAML silently keeps
    whichever duplicate key comes LAST, so an unremoved old block would make the
    file read as re-stamped while `load()` still returned the ORIGINAL session.

    Tracks ask boundaries by indent, not by id value alone: an ask's `options:`
    entries carry their own `- id: <option-id>` lines, which `_ID_LINE` also
    matches. Only an id line at the same indent as the FIRST id line seen (the
    ask list's own indent) is treated as entering a new ask; a deeper one is an
    option and must not reset which ask's `origin:` we are looking for.
    """
    out: list[str] = []
    i, n = 0, len(lines)
    ask_indent_len: int | None = None
    sibling_indent: str | None = None
    while i < n:
        line = lines[i]
        stripped = line.rstrip("\n")
        m = _ID_LINE.match(stripped)
        if m:
            # The key column, as stamp_origin measures it: `- id:` and a bare `id:` under it align.
            this_indent_len = len(m.group(1)) + len(m.group(2) or "")
            if ask_indent_len is None:
                ask_indent_len = this_indent_len
            if this_indent_len == ask_indent_len:
                sibling_indent = (
                    " " * (len(m.group(1)) + len(m.group(2) or ""))
                    if _unquote_id(m.group(3)) in ids else None
                )
            out.append(line)
            i += 1
            continue
        if sibling_indent is not None and stripped == f"{sibling_indent}origin:":
            i += 1
            while i < n:
                inner = lines[i].rstrip("\n")
                inner_indent = len(inner) - len(inner.lstrip(" "))
                if inner.strip() and inner_indent <= len(sibling_indent):
                    break
                i += 1
            continue
        out.append(line)
        i += 1
    return out


# --- the control-interface spec's lock (§5.5, broomva/workspace#861) -----------
# Maestro's answer writer (maestro-paseo server/paths.ts, locks.ts), the fleet's
# ledger_ask.py and Maestro's Rust store take this lock, not the flock: a mkdir'd
# directory named for the ledger, holding the holder's pid, start and a token.
# Every writer here takes it FIRST and then the flock, so a stamp and an owner's
# answer can no longer write over each other (BRO-3406). Ported from
# skills/orchestration/fleet/scripts/fleetlib/ledger_ask.py, which is held to the
# plugin by its own tests; the constants and the key are the plugin's.
LOCK_ROOT_ENV = "MAESTRO_PASEO_ASK_LOCKS"
LOCK_STALE_S = 120
RECLAIM_STALE_S = 60
LOCK_WAIT_S = 5.0


class LockBusy(LedgerError):
    """Another writer held the ledger's lock past the wait; nothing was written."""


def lock_root() -> Path:
    return Path(os.environ.get(LOCK_ROOT_ENV) or (Path.home() / ".local/state/control-asks/locks"))


def lock_key(ledger: Path) -> str:
    """The first 16 hex of SHA-256(realpath(dirname(L)) + "/" + basename(L)), as Maestro's lockKey."""
    return hashlib.sha256(f"{os.path.realpath(ledger.parent)}/{ledger.name}".encode("utf-8")).hexdigest()[:16]


def _start_of(pid: int) -> int | None:
    """A process's start, in epoch seconds, from `ps -o etime=` (elapsed time, which no time zone shifts)."""
    try:
        out = subprocess.run(["ps", "-o", "etime=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    parts = out.strip().replace("-", ":").split(":")
    if not out.strip() or not all(p.isdigit() for p in parts):
        return None
    nums = [int(p) for p in parts]
    seconds = sum(unit * n for unit, n in zip([86400, 3600, 60, 1][-len(nums):], nums))
    return int(time.time()) - seconds


def _lock_alive(pid_line: str) -> bool:
    words = pid_line.split()
    if not words or not words[0].isdigit() or int(words[0]) <= 0:
        return False
    pid = int(words[0])
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    if len(words) < 2 or not words[1].lstrip("-").isdigit():
        return True
    start = _start_of(pid)
    return start is None or abs(start - int(words[1])) <= 2


def _lock_age(p: Path) -> float | None:
    try:
        return time.time() - p.stat().st_mtime
    except FileNotFoundError:
        return None


def _lock_stale(d: Path) -> bool:
    age = _lock_age(d)
    if age is None or age < LOCK_STALE_S:
        return False
    try:
        return not _lock_alive((d / "pid").read_text())
    except (OSError, UnicodeDecodeError):  # an old lock whose pid cannot be read is no holder's
        return True


def _lock_rmtree(d: Path) -> None:
    for child in d.glob("*"):
        try:
            child.unlink()
        except OSError:
            pass
    try:
        d.rmdir()
    except OSError:
        pass


def _lock_reclaim(d: Path) -> None:
    mutex = Path(str(d)[:-2] + ".reclaim.d")
    try:
        mutex.mkdir()
    except FileExistsError:
        age = _lock_age(mutex)
        if age is not None and age >= RECLAIM_STALE_S:
            _lock_rmtree(mutex)
        return
    try:
        if _lock_stale(d):
            aside = Path(f"{d}.stale-{secrets.token_hex(4)}")
            try:
                d.rename(aside)
            except OSError:
                return
            _lock_rmtree(aside)
    finally:
        _lock_rmtree(mutex)


@contextlib.contextmanager
def spec_lock(ledger: Path, wait: float = LOCK_WAIT_S):
    """Hold the ledger's §5.5 lock. Yields a check to call right before writing: it
    raises if the lock is no longer ours (reclaimed as stale meanwhile)."""
    root = lock_root()
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise LedgerError(f"could not make the lock folder {root}: {exc}; nothing was written")
    d = root / f"{lock_key(ledger)}.d"
    deadline = time.time() + wait
    while True:
        try:
            d.mkdir()
            break
        except FileExistsError:
            if _lock_stale(d):
                _lock_reclaim(d)
            if time.time() >= deadline:
                raise LockBusy(f"another writer holds the lock on {ledger}; nothing was written")
            time.sleep(0.05)
        except OSError as exc:
            raise LedgerError(f"could not take the lock on {ledger}: {exc}; nothing was written")
    token = secrets.token_hex(16)
    try:
        (d / "pid").write_text(f"{os.getpid()} {_start_of(os.getpid()) or int(time.time())}\n")
        (d / "token").write_text(token + "\n")
    except OSError as exc:
        _lock_rmtree(d)  # made but not finished: taken back, so no half-written lock is left
        raise LedgerError(f"could not take the lock on {ledger}: {exc}")

    def still_ours() -> None:
        try:
            if (d / "token").read_text().strip() == token:
                return
        except OSError:
            pass
        raise LockBusy(f"the lock on {ledger} was taken over; nothing was written")

    try:
        yield still_ours
    finally:
        try:
            if (d / "token").read_text().strip() == token:
                _lock_rmtree(d)
        except OSError:
            pass


class _LockedLedger:
    """The open ledger plus the §5.5 lock's ownership check, for `_locked_rw`'s callers."""

    def __init__(self, fh, still_ours):
        self.fh, self.still_ours = fh, still_ours

    def read(self) -> str:
        try:
            return self.fh.read()
        except UnicodeDecodeError as exc:  # the same boundary as load(), for the locked reopen
            raise LedgerError(f"the ledger is not UTF-8 text (byte {exc.start})")
        except OSError as exc:
            raise LedgerError(f"the ledger cannot be read: {exc.strerror or exc}")

    def write_all(self, text: str) -> None:
        """Rewrite the ledger in place, if the §5.5 lock is still ours. In place, not by
        rename: a copy of this script from before BRO-3406 (the bstack stamp hook runs
        one) takes only the flock on the file it opened, and a rename would leave it
        writing to an unlinked inode while it reported success (P20 round 2, #1114)."""
        self.still_ours()
        self.fh.seek(0)
        self.fh.write(text)
        self.fh.truncate()
        self.fh.flush()
        os.fsync(self.fh.fileno())


def _open_ledger(path: Path, flags: int) -> int:
    """An fd on `path`, which must be a regular file with one link (not a symlink, a hard link, a FIFO)."""
    try:
        fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        raise LedgerError(f"no ledger at {path}")
    except OSError as exc:
        if path.is_symlink():
            raise LedgerError(f"{path} is a symlink; write the ledger by its own path. Nothing was written")
        raise LedgerError(f"could not open {path}: {exc}; nothing was written")
    st = os.fstat(fd)
    if not stat_mod.S_ISREG(st.st_mode) or st.st_nlink != 1:
        os.close(fd)
        raise LedgerError(f"{path} is not a regular file with one name (links: {st.st_nlink}), so its "
                          "lock would not be every writer's; nothing was written")
    return fd


def stored_path(path: Path) -> Path:
    """`path` as the file system stores it: every folder and the name in their stored spelling, links
    resolved. That is the path the plugin's lockKey sees (Node's realpath returns stored spellings), so
    a case or Unicode alias of a folder or of the name (`~/Broomva`, `L.yaml`, an NFD `café` on APFS)
    takes the same §5.5 lock as the path Maestro discovered (P20 rounds 1-2, #1132). macOS gives it for
    the open file (F_GETPATH); elsewhere `realpath`, which a case-sensitive volume needs no more than."""
    fd = _open_ledger(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))  # a FIFO must not wait for a writer
    try:
        return _fd_path(fd) or Path(os.path.realpath(path))
    finally:
        os.close(fd)


def _fd_path(fd: int) -> Path | None:
    """The stored path of an open file (macOS F_GETPATH), or None where the platform has no such call."""
    try:
        import fcntl
        if hasattr(fcntl, "F_GETPATH"):
            return Path(os.fsdecode(fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).rstrip(b"\0")))
    except (ImportError, OSError):
        pass
    return None


@contextlib.contextmanager
def _locked_rw(path: Path, wait: float = LOCK_WAIT_S):
    """The ledger opened for read and write under BOTH locks, held until the block
    exits: the §5.5 mkdir lock the plugin's answers and Maestro take, then the
    flock, which a copy of this script from before BRO-3406 takes alone. Every
    writer of a ledger here (stamp, archive, file) goes through it and writes in
    place. The file is opened only once the §5.5 lock is held, so the bytes read are
    never an inode the plugin's answer has since renamed over. `fcntl` is
    POSIX-only; without it the flock is skipped rather than raising (a Windows
    import must not crash a hook).

    The §5.5 key names the file by its folder's real path and its own name, so a
    second name for one file would be a second lock on it: a writer through the
    alias would not wait for one through the target, and the later rename would
    drop its ask (Stratum A, BRO-3421). So the lock and the write use the stored
    path, and a symlink, a hard link or a non-regular file is refused, as the
    plugin's answer reads only a regular file. The flock is waited for within the
    same `wait` (a held flock exits 75 too)."""
    try:
        import fcntl
    except ImportError:
        fcntl = None
    if not (0 <= wait < float("inf")):
        raise LedgerError(f"the wait must be a finite number of seconds >= 0, got {wait!r}")
    deadline = time.time() + wait
    path = stored_path(path)
    with spec_lock(path, wait) as still_ours:
        fh = os.fdopen(_open_ledger(path, os.O_RDWR), "r+", encoding="utf-8")
        with fh:
            if _fd_path(fh.fileno()) not in (None, path):  # renamed (a case, a folder) since it was keyed
                raise LockBusy(f"{path} was renamed while its lock was taken; nothing was written")
            while fcntl is not None:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.time() >= deadline:
                        raise LockBusy(f"another writer holds the flock on {path}; nothing was written")
                    time.sleep(0.05)
            # A flock-only writer may have renamed a new file over the path while this one waited.
            try:
                now = os.stat(path, follow_symlinks=False)
            except FileNotFoundError:
                now = None
            held = os.fstat(fh.fileno())
            if now is None or (now.st_dev, now.st_ino) != (held.st_dev, held.st_ino):
                raise LockBusy(f"{path} was replaced or removed while its flock was awaited; nothing was written")
            yield _LockedLedger(fh, still_ours)


def stamp_origin(path: Path, ask_id: str | None, agent_id: str, cwd: str,
                 provider: str, session: str, force: bool = False,
                 claude_session: str = "") -> list[str]:
    """Insert an `origin:` block under each targeted ask. Returns the ids stamped.

    Targets every ask with no routable origin (any ask, with `force`). The edit is
    line-based to keep the diff small, so it is checked before it is written: the
    new text must parse to the old ledger with exactly the stamped asks' origins
    changed. An origin shape the line edit cannot rewrite (flow style, a comment
    on its line, a string or null origin) is refused with nothing written, never
    reported as stamped while the old value stays (BRO-2918, P20 round 2).

    Holds an exclusive advisory lock on `path` across the whole read-modify-
    verify-write cycle (P20 Stratum B round 1, bstack#130): two sessions both
    raising an ask in the SAME ledger near-simultaneously used to read-modify-
    write on separate, unlocked opens, a classic lost update — the second write
    silently clobbered the first one's `origin:` block, and both calls reported
    success. `fcntl` is POSIX-only; its absence (fail-open, not fail-closed,
    since every other write path here already assumes POSIX) degrades to the
    prior unlocked behaviour rather than raising, so a Windows import does not
    crash the hook.

    A targeted id with no stamped line (any of them, not only all of them) means
    the id was found in the parsed ledger but the LINE-based `_ID_LINE` regex never
    matched a line for it — some id shape neither `_unquote_id` nor this edit
    anticipates. Reporting that as "stamped 0 ask(s): (none — already stamped)"
    (the pre-fix behaviour) is indistinguishable from the correct zero, which is
    exactly how the quoted/commented-id bug above went unnoticed; raising makes
    the gap loud instead of silent (P20 Stratum B round 1 follow-up).
    """
    with _locked_rw(path) as fh:
        text = fh.read()
        ledger = _parse_ledger_text(path, text)
        # A Maestro filing's origin is overwritten only by a forced stamp that names
        # its ask: a sweep never replaces it, forced or not (BRO-3406).
        targets = {str(a.get("id")) for a in (ledger.get("asks") or [])
                   if isinstance(a, dict) and not (
                       _routable(a.get("origin")) and not force
                       or _maestro_origin(a.get("origin")) and not (force and ask_id is not None))}
        if ask_id is not None:
            if ask_id not in {str(a.get("id")) for a in (ledger.get("asks") or []) if isinstance(a, dict)}:
                raise LedgerError(f"no ask '{ask_id}' in {path}")
            targets &= {ask_id}

        origin = [(k, v) for k, v in (("agent_id", agent_id), ("provider", provider), ("cwd", cwd),
                                      ("session", session), ("claude_session", claude_session)) if v]
        new_text, stamped = _stamp_text(text, targets, origin)

        # Every targeted ask, not only all of them at once: a partial miss used to
        # stamp the rest, exit 0, and leave one ask silently without (P20 round 3).
        missing = targets - set(stamped)
        if missing:
            raise LedgerError(
                f"{path}: {len(missing)} ask(s) needed an origin "
                f"({', '.join(sorted(missing))}) but no line matched — an id shape "
                f"(quoting, a comment, something else) that the line edit cannot find. "
                f"Nothing was written; stamp will keep refusing until the shape is fixed by hand."
            )

        if stamped:
            _check_stamp(ledger, new_text, set(stamped), dict(origin))
            fh.write_all(new_text)
        # `with fh:` closes the descriptor on the way out, which releases the
        # flock — unlocking must never happen before the write above lands.
    return stamped


def _stamp_text(text: str, targets: set[str], origin: list[tuple[str, str]]) -> tuple[str, list[str]]:
    """The line edit `stamp` makes: an `origin:` block (replacing any old one) under
    each targeted ask's id line. Pure, so `file` can prove a stamp of what it wrote
    reads back cleanly before writing it."""
    lines = _strip_origin_blocks(text.splitlines(keepends=True), targets)
    out: list[str] = []
    stamped: list[str] = []
    for line in lines:
        out.append(line)
        m = _ID_LINE.match(line.rstrip("\n"))
        this_id = _unquote_id(m.group(3)) if m else None
        if not m or this_id not in targets:
            continue
        # The key column, not the leading whitespace: `  - id:` puts `id` at 4.
        indent = " " * (len(m.group(1)) + len(m.group(2) or ""))
        out.append(f"{indent}origin:\n")
        for key, value in origin:
            out.append(f"{indent}  {key}: {value}\n")
        stamped.append(this_id)
    return "".join(out), stamped


def _check_stamp(before: dict, text: str, stamped: set[str], origin: dict) -> None:
    """Refuse a stamp whose text does not read back as `before` with only the
    stamped asks' origins set to `origin`. Raises LedgerError; writes nothing."""
    try:
        after = yaml.load(text, Loader=_StrictLoader)
    except (yaml.YAMLError, LedgerError) as e:  # _StrictLoader raises LedgerError on a duplicate key
        raise LedgerError(f"stamp could not rewrite the origin cleanly ({e}); nothing was written. "
                          "Replace that origin by hand.")

    def without_origins(ledger: dict) -> dict:
        asks = [{k: v for k, v in a.items() if not (k == "origin" and str(a.get("id")) in stamped)}
                if isinstance(a, dict) else a for a in (ledger.get("asks") or [])]
        return {**ledger, "asks": asks}

    wrong = sorted(str(a.get("id")) for a in (after.get("asks") or []) if isinstance(a, dict)
                   and str(a.get("id")) in stamped and a.get("origin") != origin) \
        if isinstance(after, dict) else sorted(stamped)
    if wrong or not isinstance(after, dict) or without_origins(after) != without_origins(before):
        what = f"ask(s) {', '.join(wrong)}" if wrong else "another part of the ledger"
        raise LedgerError(f"stamp could not rewrite {what} cleanly (an origin in a shape the line edit "
                          "does not handle: flow style, a comment on its line, or a string or null origin); "
                          "nothing was written. Replace that origin by hand.")


# --- liveness: an ask that reality already answered ------------------------
# 11 of 37 open asks referenced only PRs that were already merged or closed, and
# one carried its own resolution in its question text. Nothing expired them,
# because nothing could: the ledger had no notion of an ask ceasing to be a real
# question. `liveness` is that notion, and it is deliberately narrow — a shell
# predicate would be unbounded authority for a cron to run.
# Deliberately broad. A miss retires a live exposure; a false hit leaves one extra
# row in an inbox. Those costs are not comparable.
_SECRET_HINT = re.compile(
    r"\b(secret|api[_\- ]?key|access[_\- ]?token|credential|rotate|rotated|revoke|"
    r"leaked|exposed|password|private[_\- ]?key)\b|\b\w*sk_[A-Za-z0-9]", re.I)
_PR_REF = re.compile(r"(?:([\w.-]+/[\w.-]+))?#(\d{1,5})\b")
_DEFAULT_REPO = "broomva/workspace"


def _pr_state(repo: str, number: str, cache: dict) -> str | None:
    """MERGED / CLOSED / OPEN, or None when it cannot be determined."""
    key = f"{repo}#{number}"
    if key in cache:
        return cache[key]
    try:
        out = subprocess.run(
            ["gh", "pr", "view", number, "--repo", repo, "--json", "state", "-q", ".state"],
            capture_output=True, text=True, timeout=25)
        state = out.stdout.strip().upper() if out.returncode == 0 else None
    except Exception:
        state = None
    # A lookup that failed is NOT evidence the PR is closed. Archiving on an
    # unreachable network would silently delete live questions.
    cache[key] = state or None
    return cache[key]


def ask_liveness(ask: dict, cache: dict, infer: bool = False) -> tuple[bool, str]:
    """(still_live, reason). Unknown always resolves to live."""
    if ask.get("archived_reason") and not ask.get("archived_at"):
        return False, str(ask["archived_reason"])

    # A secret NEVER expires on a PR. An exposed key outlives the pull request that
    # revealed it: `live-secret-in-bridged-doc` came out of PR #682 review, #682 is
    # merged, and a pr-open predicate would have silently retired a live key still
    # committed on main.
    #
    # Keying this on `class: credential` is NOT enough and was the first attempt:
    # measured on the real corpus, the three secret-rotation asks are classed
    # `external`, `authority` and `external`. None would have been caught. So the
    # guard reads the text, and its false positives cost nothing — refusing to
    # archive is the safe direction.
    if str(ask.get("class") or "").strip().lower() == "credential" or _SECRET_HINT.search(
        " ".join(str(v) for v in ask.values())
    ):
        return True, "mentions a secret or credential — never auto-archived"

    spec = ask.get("liveness")
    refs: list[tuple[str, str]] = []
    if isinstance(spec, dict):
        if spec.get("kind") != "pr-open":
            return True, "unknown liveness kind — treated as live"
        m = _PR_REF.search(str(spec.get("ref") or ""))
        if not m:
            return True, "unparseable liveness ref — treated as live"
        refs = [(m.group(1) or _DEFAULT_REPO, m.group(2))]
    elif infer:
        blob = " ".join(str(v) for v in ask.values())
        refs = [(m.group(1) or _DEFAULT_REPO, m.group(2))
                for m in _PR_REF.finditer(blob) if len(m.group(2)) >= 3]
        if not refs:
            return True, "no PR reference to infer from"
    else:
        return True, "no liveness predicate"

    states = {f"{r}#{n}": _pr_state(r, n, cache) for r, n in refs}
    if any(v is None for v in states.values()):
        return True, "a PR state could not be read — treated as live"
    if all(v in ("MERGED", "CLOSED") for v in states.values()):
        done = ", ".join(f"{k} {v.lower()}" for k, v in sorted(states.items()))
        return False, f"every referenced PR is resolved ({done})"
    return True, "at least one referenced PR is still open"


def archive_asks(path: Path, infer: bool = False, apply: bool = False) -> list[tuple[str, str]]:
    """Stamp status/archived_at/archived_reason on asks reality already answered."""
    ledger = load(path)
    cache: dict = {}
    doomed: list[tuple[str, str]] = []
    for ask in ledger.get("asks") or []:
        if not isinstance(ask, dict):
            continue
        # An idea is closed only by its triage (develop / park / kill), never because
        # the PR it came from merged: that PR is always merged (BRO-2802).
        if ask.get("class") == IDEA_CLASS:
            continue
        # `rerouted` is a terminal state too: the arc found another way and the ask
        # stopped being a question. Reading only resolved/archived archived one of
        # those and discarded the word that said so.
        if is_closed(ask):
            continue
        live, reason = ask_liveness(ask, cache, infer=infer)
        if not live:
            doomed.append((str(ask.get("id")), reason))

    if apply and doomed:
        judged = {str(a.get("id")): a for a in (ledger.get("asks") or []) if isinstance(a, dict)}
        stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Liveness is judged above, unlocked (it may call `gh` for each PR); what is
        # written is decided again from the text read under the lock. An owner's answer
        # that landed meanwhile closes the ask, and an archive written over it would put
        # a second `status:` in the entry, which Maestro refuses for the whole ledger
        # (P20 Stratum C, workspace#1114).
        with _locked_rw(path) as fh:
            text = fh.read()
            now = {str(a.get("id")): a for a in (_parse_ledger_text(path, text).get("asks") or [])
                   if isinstance(a, dict)}
            # Judged on the unlocked read: an ask that changed at all since (answered, re-reasoned,
            # removed) waits for the next run rather than being archived on a stale judgment.
            doomed = [(aid, reason) for aid, reason in doomed if now.get(aid) == judged.get(aid)]
            keys = {}
            for aid, reason in doomed:
                # Only add what is absent. A backfill agent may already have written
                # archived_reason; appending a second one yields a duplicate key, which
                # PyYAML silently accepts (last wins) and the plugin's JS parser rejects
                # outright — so the ledger would read fine here and be unreadable there.
                pairs = [(k, v) for k, v in (("status", "archived"), ("archived_at", stamp),
                                             ("archived_reason", json.dumps(reason)))
                         if now[aid].get(k) is None]
                if pairs:
                    keys[aid] = pairs
            if keys:
                new_text, _ = _insert_text(text, keys)
                _parse_ledger_text(path, new_text)  # a duplicate key refuses here, before the write
                fh.write_all(new_text)
        if doomed:
            _log_archive(path, doomed, stamp)
    return doomed


def insert_keys_after_id(path: Path, keys_by_ask: dict[str, list[tuple[str, str]]]) -> list[str]:
    """Insert `key: value` lines after each named ask's `id:` line, in place.

    A line insert rather than a yaml round-trip, for the same reason `stamp` is:
    re-emitting reflows every folded scalar in the file and turns a 3-line change
    into an unreviewable diff. Holds the same lock as `stamp_origin`, so an
    archive and a stamp never lose each other's write (P20 round 3).
    """
    with _locked_rw(path) as fh:
        new_text, touched = _insert_text(fh.read(), keys_by_ask)
        if touched:
            fh.write_all(new_text)
    return touched


def _insert_text(text: str, keys_by_ask: dict[str, list[tuple[str, str]]]) -> tuple[str, list[str]]:
    """`insert_keys_after_id`'s line edit, pure."""
    out: list[str] = []
    touched: list[str] = []
    for line in text.splitlines(keepends=True):
        out.append(line)
        m = _ID_LINE.match(line.rstrip("\n"))
        this_id = _unquote_id(m.group(3)) if m else None
        if not m or this_id not in keys_by_ask:
            continue
        indent = " " * (len(m.group(1)) + len(m.group(2) or ""))
        for key, value in keys_by_ask[this_id]:
            out.append(f"{indent}{key}: {value}\n")
        touched.append(this_id)
    return "".join(out), touched


# --- file: the writer of record appends an ask (BRO-3406) --------------------
# Maestro's agent-requests (BRO-3267) first wrote asks with its own YAML emitter.
# Three review rounds found the same class each time: an entry the validator
# accepted that this file's line-based writers (stamp, archive) later tripped on.
# So an ask is filed HERE, in those writers' conventions, and the result is proven
# to survive a stamp and an archive edit before anything is written.
# `\Z`, not `$`: `$` also matches before a final newline, and Maestro's reader trims one, so
# a uid `A1\n` was filed and then named the same ask as `A1` (P20 Stratum B, workspace#1114).
FILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\Z")
# An agent files a question, never its answer, its closing or its routing.
FILE_REFUSED_KEYS = ANSWERED_KEYS + ("answered_by", "status", "archived_at", "archived_reason", "origin")
FILE_LIMIT = 1 << 20   # bytes: over this no reader reads any ask in the ledger (Maestro's LEDGER_LIMIT)
FILE_WAIT_S = 30.0     # an agent can wait; an owner's answer waits LOCK_WAIT_S
_PLAIN_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# YAML 1.1 reads these plain words as booleans or null: such a key is quoted.
_YAML11_WORDS = {"y", "n", "yes", "no", "on", "off", "true", "false", "null"}
# What a double-quoted YAML scalar may hold raw. Everything else is escaped,
# including the three line breaks (U+0085, U+2028, U+2029) a reader would fold.
_YAML_RAW_OK = re.compile("[\t\x20-\x7E\xA0-\u2027\u202A-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]")


def _dq(value: str) -> str:
    """`value` as a double-quoted scalar every reader here reads back exactly."""
    out = []
    for ch in json.dumps(value, ensure_ascii=False)[1:-1]:
        if _YAML_RAW_OK.fullmatch(ch) or ch == "\\":
            out.append(ch)
        elif ord(ch) > 0xFFFF:
            out.append(f"\\U{ord(ch):08X}")
        else:
            out.append(f"\\u{ord(ch):04X}")
    return '"' + "".join(out) + '"'


def _yaml_key(key: str) -> str:
    return key if _PLAIN_KEY.match(key) and key.lower() not in _YAML11_WORDS else _dq(key)


def _yaml_inline(value) -> str | None:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # YAML 1.1 reads a float only with a dot and a signed exponent: `1e+300` is a string to PyYAML.
        text = json.dumps(value)
        return text.replace("e", ".0e", 1) if "e" in text and "." not in text else text
    if isinstance(value, str):
        return _dq(value)
    if value == [] or value == {}:
        return json.dumps(value)
    return None


def _yaml_block(value, indent: int) -> list[str]:
    """`value` (a mapping or a list) as block lines at `indent` spaces."""
    pad = " " * indent
    out: list[str] = []
    if isinstance(value, dict):
        for k, item in value.items():
            line = _yaml_inline(item)
            if line is not None:
                out.append(f"{pad}{_yaml_key(k)}: {line}\n")
            else:
                out.append(f"{pad}{_yaml_key(k)}:\n")
                out.extend(_yaml_block(item, indent + 2))
    else:
        for item in value:
            line = _yaml_inline(item)
            if line is not None:
                out.append(f"{pad}- {line}\n")
                continue
            inner = _yaml_block(item, indent + 2)
            # The item's first line takes the dash; the rest line up under it.
            out.append(f"{pad}- {inner[0][indent + 2:]}")
            out.extend(inner[1:])
    return out


def render_ask(entry: dict, indent: int) -> str:
    """One block-sequence entry at `indent`: `- id:` first, every other key at the
    id's column, as `stamp` and `archive` find them."""
    rest = {k: v for k, v in entry.items() if k != "id"}
    first = f"{' ' * indent}- id: {_yaml_inline(entry['id'])}\n"
    return first + "".join(_yaml_block(rest, indent + 2))


_ASKS_BLOCK = re.compile(r"^asks:[ \t]*(#.*)?$")
_ASKS_EMPTY = re.compile(r"^asks:[ \t]*\[\][ \t]*(#.*)?$")


def _append_ask(text: str, entry: dict) -> str:
    """`text` with `entry` appended to its top-level `asks:` block list."""
    lines = text.splitlines(keepends=True)
    at = [i for i, line in enumerate(lines) if line.startswith("asks:")]
    if len(at) != 1:
        raise LedgerError("the ledger has no top-level `asks:` to append to" if not at
                          else "the ledger has more than one `asks:` line")
    at = at[0]
    head = lines[at].rstrip("\r\n")
    if _ASKS_EMPTY.match(head):
        tail = "".join(lines[at + 1:])
        return "".join(lines[:at]) + "asks:\n" + render_ask(entry, 2) + tail
    if not _ASKS_BLOCK.match(head):
        raise LedgerError("the ledger's `asks` is not a block list, so an ask cannot be appended to it")
    indent, end = None, len(lines)
    for i in range(at + 1, len(lines)):
        body = lines[i].rstrip("\r\n")
        stripped = body.lstrip(" ")
        if not stripped or stripped.startswith("#"):
            continue
        col = len(body) - len(stripped)
        is_item = stripped == "-" or stripped.startswith("- ")
        if indent is None and is_item:
            indent = col
        if col == 0 and not (indent == 0 and is_item):
            end = i
            break
    # Blank lines and column-0 comments just before what follows stay with it.
    cut = end
    while cut > at + 1 and (not lines[cut - 1].strip() or lines[cut - 1].startswith("#")):
        cut -= 1
    before = "".join(lines[:cut])
    if not before.endswith("\n"):
        before += "\n"
    return before + render_ask(entry, 2 if indent is None else indent) + "".join(lines[cut:])


def _no_dupe_pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise LedgerError(f"the ask's JSON has the key {k!r} twice")
        out[k] = v
    return out


def _bad_constant(name):
    raise LedgerError(f"the ask's JSON holds {name}, which no ledger reader reads back")


FILE_DEPTH = 32
# Maestro's reader holds numbers as JSON doubles: a larger integer reads back as another one.
FILE_INT_MAX = 2 ** 53 - 1


def _check_values(value, depth: int = 0) -> None:
    if depth > FILE_DEPTH:
        raise LedgerError(f"the ask nests deeper than {FILE_DEPTH} levels")
    if isinstance(value, bool):
        return
    if isinstance(value, int) and abs(value) > FILE_INT_MAX:
        raise LedgerError(f"the ask holds an integer beyond what Maestro's reader reads back "
                          f"(|n| <= {FILE_INT_MAX}); write it as a string")
    if isinstance(value, float) and value == 0 and str(value).startswith("-"):
        raise LedgerError("the ask holds -0.0, which Maestro's reader reads back as 0; write 0")
    children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    for child in children:
        _check_values(child, depth + 1)


# YAML 1.2's core schema numbers (Maestro's reader) and every word PyYAML (1.1) reads as a non-string.
_YAML12_NUMBER = re.compile(r"[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+|([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][-+]?[0-9]+)?")


def _reads_as_text(name: str) -> bool:
    """Whether both readers read `name`, written plain, as this same string. An id a reader
    reads as a number, a bool or null is compared by its value there (`1.0` and `1` are one
    ask to Maestro), so a new one could collide with an existing ask's id unseen."""
    try:
        plain = yaml.safe_load(name)
    except (yaml.YAMLError, ValueError):  # ValueError: a date that is not one, `2026-02-30`
        return False
    # `Infinity` and `NaN` are how Maestro's reader (and the plugin's JS `String()`) render an
    # existing `.inf` or `.nan` id, which PyYAML renders `inf` and `nan` (P20 round 3, #1114).
    return plain == name and not _YAML12_NUMBER.fullmatch(name) and name.lower() not in ("infinity", "nan")


def parse_ask_json(raw: str) -> dict:
    """One ask from JSON, or LedgerError naming why it is not filed."""
    try:
        ask = json.loads(raw, object_pairs_hook=_no_dupe_pairs, parse_constant=_bad_constant)
    except json.JSONDecodeError as e:
        raise LedgerError(f"the ask is not JSON: {e}")
    except (ValueError, RecursionError) as e:  # an int past Python's digit limit, or nesting past the stack
        raise LedgerError(f"the ask is not JSON a ledger can hold: {type(e).__name__}")
    if not isinstance(ask, dict):
        raise LedgerError("the ask must be a JSON object")
    _check_values(ask)
    for name in ("id", "uid"):
        if name in ask and not (isinstance(ask[name], str) and FILE_ID_RE.match(ask[name])):
            raise LedgerError(f"the ask's {name} must be a string matching {FILE_ID_RE.pattern}")
        if name in ask and not _reads_as_text(ask[name]):
            raise LedgerError(f"the ask's {name} {ask[name]!r} reads as a number, a bool, null or a date to a "
                              "YAML reader, or names one, so it could name an existing ask; use another name")
    if "id" not in ask:
        raise LedgerError("the ask has no id")
    refused = sorted(k for k in ask if k in FILE_REFUSED_KEYS or k.startswith("answer_"))
    if refused:
        raise LedgerError(f"an agent never files {', '.join(refused)}: those answer, close or route an ask")
    try:
        json.dumps(ask, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        raise LedgerError("the ask holds a lone surrogate, which no ledger file can hold")
    return ask


_MAESTRO_TRIM = re.compile(r"^[\s\ufeff]+|[\s\ufeff]+$")


def _as_maestro_names(value) -> str:
    """An existing id or uid as Maestro's reader addresses it: trimmed of whitespace and U+FEFF as
    one set, as JS `trim()` does (Python's `strip()` keeps U+FEFF). Python's whitespace is wider
    than JS's, which only refuses more."""
    return _MAESTRO_TRIM.sub("", str(value))


def _id_lines(text: str) -> dict[str, int]:
    """How many `id:` lines (`stamp`'s and `archive`'s anchor) name each value."""
    counts: dict[str, int] = {}
    for line in text.splitlines():
        m = _ID_LINE.match(line)
        if m:
            v = _unquote_id(m.group(3))
            counts[v] = counts.get(v, 0) + 1
    return counts


def _without_asks(ledger: dict) -> dict:
    return {k: v for k, v in ledger.items() if k != "asks"}


def file_ask(path: Path, ask: dict, maestro_session: str | None = None, wait: float = FILE_WAIT_S) -> str:
    """Append `ask` to the existing ledger at `path`, under both locks. Returns its id.

    With `maestro_session`, the entry carries Maestro's unverified origin
    `{via: maestro, session, verified: false}`, which `stamp` keeps. Nothing is
    written unless the result reads as the ledger's asks plus exactly this one, adds
    no `validate` error, is under FILE_LIMIT, and survives a stamp and an archive
    edit of the new ask (each checked on the text, not assumed)."""
    entry = dict(ask)
    if maestro_session is not None:
        if not maestro_session.strip():
            raise LedgerError("an empty --maestro-session names nobody")
        entry["origin"] = {"via": "maestro", "session": maestro_session, "verified": False}
    new_id = entry["id"]
    repo = repo_of(path)
    preauth = {}
    if repo is not None and (repo / ".control" / "preauth.yaml").is_file():
        preauth = load(repo / ".control" / "preauth.yaml")
    with _locked_rw(path, wait) as fh:
        text = fh.read()
        before = _parse_ledger_text(path, text)
        was = asks_of(before)
        if "asks" not in before or was is None:
            raise LedgerError("the ledger has no `asks` list to append to")
        # As Maestro addresses them: trimmed. (A new name never reads as a number, a bool, null, a
        # date, Infinity or NaN, so an existing one that does cannot equal it in any reader.)
        taken = {_as_maestro_names(v) for a in was if isinstance(a, dict) for v in (a.get("id"), a.get("uid"))
                 if v is not None}
        for name in (new_id, entry.get("uid")):
            if name is not None and name in taken:
                raise LedgerError(f"the ledger already has an ask {name!r}; nothing was written")
        new_text = _append_ask(text, entry)
        if len(new_text.encode("utf-8")) > FILE_LIMIT:
            raise LedgerError(f"the ledger would be over {FILE_LIMIT} bytes, and then no reader reads any "
                              "ask in it; nothing was written")
        after = _parse_ledger_text(path, new_text)
        if _without_asks(after) != _without_asks(before) or asks_of(after) != [*was, entry]:
            raise LedgerError("the appended ledger does not read as the old one plus this ask; nothing was written")
        # Every ask's id lines, as stamp and archive find them: this one gains its own
        # and only its own (an option id equal to an ask id would take a stamp too).
        old_ids, new_ids = _id_lines(text), _id_lines(new_text)
        names = {str(a.get("id")) for a in asks_of(after) if isinstance(a, dict)}
        moved = sorted(n for n in names
                       if new_ids.get(n, 0) - old_ids.get(n, 0) != (n == new_id)
                       or n == new_id and old_ids.get(n, 0))
        if moved:
            raise LedgerError(f"an `id:` line would name {', '.join(map(repr, moved))} once too often, so a stamp "
                              "or an archive would edit the wrong entry; nothing was written")
        probe = [("agent_id", "probe"), ("session", "probe")]
        stamped_text, stamped = _stamp_text(new_text, {new_id}, probe)
        if stamped != [new_id]:
            raise LedgerError("a stamp of this ask would not find it; nothing was written")
        _check_stamp(after, stamped_text, {new_id}, dict(probe))
        archived_text, touched = _insert_text(new_text, {new_id: [("archived_reason", '"probe"')]})
        archived = _parse_ledger_text(path, archived_text) if touched == [new_id] else None
        if archived is None or asks_of(archived) != [*was, {**asks_of(after)[-1], "archived_reason": "probe"}]:
            raise LedgerError("an archive of this ask would not edit it cleanly; nothing was written")
        old_errs = validate(before, preauth, path=path)
        new_errs = list(validate(after, preauth, path=path))
        for e in old_errs:
            if e in new_errs:
                new_errs.remove(e)
        if new_errs:
            raise LedgerError("the ledger would gain error(s): " + "; ".join(new_errs) + "; nothing was written")
        fh.write_all(new_text)
    return new_id


class _StrictLoader(yaml.SafeLoader):
    """PyYAML accepts duplicate mapping keys; every other consumer of these files
    does not. Validating with a looser parser than the reader is how a file passes
    here and fails there."""


def _no_dupes(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise LedgerError(f"duplicate key {key!r} at line {key_node.start_mark.line + 1}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dupes)


def _log_archive(path: Path, doomed: list[tuple[str, str]], stamp: str) -> None:
    """Append-only record, so an auto-archive is reviewable after the fact."""
    log = Path.home() / ".config" / "broomva" / "ask-archive.jsonl"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            for aid, reason in doomed:
                fh.write(json.dumps({"ts": stamp, "ledger": str(path), "ask": aid,
                                     "reason": reason}) + "\n")
    except OSError:
        pass  # the stamp in the ledger is the record of note; the log is a convenience


# --- BSTACK-ONLY: `validate --all` / `open --all`, `archive` refused -----------
def _archive_refused() -> int:
    """`archive` is broomva/workspace's: it reads a bare `#N` as broomva/workspace#N and logs under
    ~/.config/broomva. Outside that repo it could retire a live ask, so bstack does not run it."""
    print("error: archive runs from broomva/workspace's scripts/ask_ledger.py, not bstack's "
          "(a bare #N there means broomva/workspace#N)", file=sys.stderr)
    return 2


def _add_sweep_flags(v, o) -> None:
    v.add_argument("--all", action="store_true",
                   help="treat the path as a directory and validate every ledger in it")
    o.add_argument("--all", action="store_true",
                   help="treat the path as a directory and pool open asks across every arc")


def _sweep_cmd(args) -> int:
    """`validate --all` / `open --all` over a directory of ledgers.

    Exit codes match the single-file contract so a caller can treat them alike:
    0 clean, 1 schema errors, 2 at least one ledger did not parse. A broken file
    ranks above a merely-invalid one because it is the only failure that hides
    content rather than describing it.
    """
    loaded, broken = load_sweep(args.ledger)

    if args.cmd == "open":
        for path, lg in loaded:
            arc = lg.get("arc") or path.stem
            for a in open_asks(lg):
                print(f"{arc}\t{a.get('id')}\t{a.get('class')}\t"
                      f"{a.get('unblocks', '?')}\t{str(a.get('ask', ''))[:80]}")
        for path, err in broken:
            # stderr, not stdout: a consumer piping `open --all` into a report must not
            # silently ingest an error line as if it were an ask.
            print(f"error: {err}", file=sys.stderr)
        return 2 if broken else 0

    # validate --all
    preauth = {}
    if args.preauth and args.preauth.exists():
        try:
            preauth = load(args.preauth)
        except LedgerError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2

    total_errs = 0
    for path, lg in loaded:
        errs = validate(lg, preauth, require_decisions=args.require_decisions,
                        require_decision=args.require_decision, path=path)
        total_errs += len(errs)
        for e in errs:
            print(f"  ERROR {path.name}: {e}")
        for w in origin_warnings(lg):
            print(f"  WARN  {path.name}: {w}")
    for path, err in broken:
        print(f"  BROKEN {path.name}: {err}")

    print(f"ask-ledger sweep: {len(loaded) + len(broken)} ledger(s), "
          f"{len(broken)} unparseable, {total_errs} error(s)")
    if broken:
        return 2
    return 1 if total_errs else 0


# --- END BSTACK-ONLY ---------------------------------------------------------
def _seconds(raw: str) -> float:
    """A finite, non-negative --wait: `nan` never reaches a deadline and `inf` is no bound (P20 r2, #1132)."""
    try:
        value = float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number of seconds: {raw!r}")
    if not (0 <= value < float("inf")):
        raise argparse.ArgumentTypeError(f"must be a finite number of seconds >= 0, got {raw!r}")
    return value


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="check a ledger is well-formed and every ask earned its place")
    v.add_argument("ledger", type=Path)
    v.add_argument("--preauth", type=Path, default=Path(".control/preauth.yaml"))
    v.add_argument("--require-decisions", action="store_true",
                   help="fail if no decisions were recorded (for a closing handback / arc CI)")
    v.add_argument("--require-decision", action="store_true",
                   help="fail ANY ask lacking a decision layer (headline/stakes/why_you/options); "
                        f"without it, only open asks raised on or after {DECISION_LAYER_REQUIRED_FROM} must carry one")

    l = sub.add_parser("lanes", help="which lanes can still run with no human input")
    l.add_argument("ledger", type=Path)
    l.add_argument("--runnable", action="store_true")
    l.add_argument("--parked", action="store_true")

    o = sub.add_parser("open", help="open asks, most leverage first")
    o.add_argument("ledger", type=Path)
    _add_sweep_flags(v, o)  # BSTACK-ONLY

    d = sub.add_parser("decisions", help="choices taken on the user's behalf, least-confident first")
    d.add_argument("ledger", type=Path)
    d.add_argument("--render-handback", action="store_true",
                   help="emit the 🔀 handback block as markdown")
    d.add_argument("--cap", type=int, default=ROW_CAP)
    d.add_argument("--flat", action="store_true",
                   help="least-confident first overall, ignoring verdict grouping")

    st = sub.add_parser("stamp", help="record which Paseo session raised an ask, so an answer can be routed back")
    st.add_argument("ledger", type=Path)
    st.add_argument("--ask", default=None, help="stamp one ask; default is every unstamped ask")
    st.add_argument("--agent-id", default=os.environ.get("PASEO_AGENT_ID", ""))
    st.add_argument("--cwd", default=os.environ.get("PASEO_AGENT_CWD", os.getcwd()))
    st.add_argument("--provider", default=os.environ.get("PASEO_AGENT_PROVIDER", "claude"))
    st.add_argument("--session", default="", help="human-readable session name; defaults to the cwd basename")
    st.add_argument("--claude-session", default=os.environ.get("CLAUDE_CODE_SESSION_ID", ""),
                    help="the Claude transcript id, so an ended session can be found and resumed")
    st.add_argument("--force", action="store_true", help="overwrite an existing origin")

    fi = sub.add_parser("file", help="append one ask (JSON) to an existing ledger, under both locks")
    fi.add_argument("ledger", type=Path)
    fi.add_argument("--json", required=True, help="a file holding the ask as JSON, or - for stdin")
    fi.add_argument("--maestro-session", default=None,
                    help="record Maestro's unverified origin {via: maestro, session, verified: false}")
    fi.add_argument("--wait", type=_seconds, default=FILE_WAIT_S, help="seconds to wait for the ledger's lock")

    ar = sub.add_parser("archive", help="retire asks that reality already answered")
    ar.add_argument("ledger", type=Path)
    ar.add_argument("--infer-pr-refs", action="store_true",
                    help="treat any #NNN in the ask as a liveness ref (one-time migration)")
    ar.add_argument("--apply", action="store_true", help="write the stamps; default is a dry run")

    args = ap.parse_args(argv)
    if getattr(args, "all", False): return _sweep_cmd(args)  # BSTACK-ONLY  # noqa: E701
    if args.cmd == "archive": return _archive_refused()  # BSTACK-ONLY  # noqa: E701
    try:
        ledger = load(args.ledger)
    except LedgerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.cmd == "stamp":
        if not args.agent_id:
            # Outside a Paseo session there is no id to record. Writing a blank one
            # would be worse than writing none: it reads as "stamped" to the UI.
            print("error: no --agent-id and PASEO_AGENT_ID is unset "
                  "(run inside a Paseo session, or pass --agent-id)", file=sys.stderr)
            return 2
        session = args.session or Path(args.cwd).name
        try:
            stamped = stamp_origin(args.ledger, args.ask, args.agent_id, args.cwd,
                                   args.provider, session, force=args.force,
                                   claude_session=args.claude_session)
        except LockBusy as e:
            print(f"error: {e}", file=sys.stderr)
            return 75  # EX_TEMPFAIL: try again
        except (LedgerError, OSError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"stamped {len(stamped)} ask(s): {', '.join(stamped) or '(none — already stamped)'}")
        return 0

    if args.cmd == "file":
        try:
            raw = sys.stdin.buffer.read() if args.json == "-" else Path(args.json).read_bytes()
            filed = file_ask(args.ledger, parse_ask_json(raw.decode("utf-8")), args.maestro_session, args.wait)
        except LockBusy as e:
            print(f"error: {e}", file=sys.stderr)
            return 75  # EX_TEMPFAIL: try again
        except UnicodeDecodeError as e:
            print(f"error: the ask is not UTF-8 ({e.reason} at byte {e.start}); nothing was written", file=sys.stderr)
            return 2
        except (LedgerError, OSError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"filed {filed} into {args.ledger}")
        return 0

    if args.cmd == "archive":
        try:
            doomed = archive_asks(args.ledger, infer=args.infer_pr_refs, apply=args.apply)
        except LockBusy as e:
            print(f"error: {e}", file=sys.stderr)
            return 75  # EX_TEMPFAIL: try again
        except (LedgerError, OSError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        for aid, reason in doomed:
            print(f"{'archived' if args.apply else 'would archive'} {aid}: {reason}")
        if not doomed:
            print("nothing to archive — every open ask is still a live question")
        return 0

    if args.cmd == "validate":
        preauth = {}
        if args.preauth and args.preauth.exists():
            try:
                preauth = load(args.preauth)
            except LedgerError as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
        errs = validate(ledger, preauth, require_decisions=args.require_decisions,
                        require_decision=args.require_decision, path=args.ledger)
        for e in errs:
            print(f"  ERROR {e}")
        for w in origin_warnings(ledger):
            print(f"  WARN  {w}")
        n = len(ledger.get("asks") or [])
        # Printed even when zero. "No decisions were recorded" and "decisions were
        # never a thing here" must not look alike to a reader auditing an arc.
        nd = len(ledger.get("decisions") or [])
        print(f"ask-ledger: {n} ask(s), {nd} decision(s), {len(errs)} error(s)")
        return 1 if errs else 0

    if args.cmd == "lanes":
        run, parked = runnable_lanes(ledger)
        if args.parked and not args.runnable:
            for lane, ids in sorted(parked.items()):
                print(f"{lane}\tparked by {','.join(ids)}")
            return 0
        for lane in run:
            print(lane)
        if not args.runnable:
            for lane, ids in sorted(parked.items()):
                print(f"# parked: {lane} (by {','.join(ids)})", file=sys.stderr)
        # An arc must not stop while a lane can still run; exit 3 says "keep going".
        return 3 if run else 0

    if args.cmd == "decisions":
        # Print nothing from a ledger we know is malformed. Listing the readable
        # rows of a broken file and exiting 0 is how a partial list gets mistaken
        # for the whole one — the reader cannot tell which entries were dropped.
        derrs = validate_decisions(ledger)
        if derrs:
            for e in derrs:
                print(f"  ERROR {e}", file=sys.stderr)
            print(
                f"decisions: ledger has {len(derrs)} error(s); refusing to render a "
                f"partial list. Fix it or run `validate` to see everything.",
                file=sys.stderr,
            )
            return 2
        if args.render_handback:
            block = render_handback_block(ledger, cap=args.cap)
            if block:
                print(block)
            else:
                print("# no decisions recorded — block omitted, not rendered empty",
                      file=sys.stderr)
            return 0
        rows = sort_decisions(ledger, grouped=not args.flat)
        if not rows:
            # An arc that recorded nothing says so on stderr. Silence would read as
            # "nothing was decided for you", which is a claim this file cannot make.
            print("# no decisions recorded in this ledger", file=sys.stderr)
        for d in rows:
            undo = d.get("reversal") or f"IRREVERSIBLE ({d.get('irreversible_because')})"
            print(
                f"{d.get('id')}\t{d.get('verdict')}\t{d.get('confidence')}\t"
                f"{str(d.get('decision', ''))[:70]}\tundo: {str(undo)[:60]}"
            )
        return 0

    if args.cmd == "open":
        for a in open_asks(ledger):
            print(f"{a.get('id')}\t{a.get('class')}\t{a.get('unblocks','?')}\t{str(a.get('ask') or '')[:100]}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
