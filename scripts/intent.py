#!/usr/bin/env python3
"""intent.py — the Stage-1 artifact: intent.md (BRO-2542).

Before design or code, the intent of a piece of work is written down: the problem,
the proposed outcome, the affected users and systems, the constraints, and the open
questions. It is committed to a shared home, so the author and the timestamp join the
record through the commit itself.

    new <slug>              create <dir>/<date>-<slug>.md from the template
    lint <file>...          every required section present and filled, status valid
    status <file>           print the Status from the header line
    set-status <file> <s>   rewrite the header's Status in place (nothing else changes)

Invariant: an intent that lints clean has a title, an author, a recognised status, and
all five required sections with real content. A placeholder is any `<...>` outside code
spans, code fences and HTML comments, other than an autolink such as
`<https://example.com>`. The template is made of placeholders, so a freshly created
intent fails lint until someone fills it in. Put literal angle brackets in code
(`Vec<String>`) so they are not read as unfilled.

Exit codes:
    0  ok
    1  lint found a problem; `new` refused to overwrite an existing file; `status` or
       `set-status` found no valid header line
    2  usage error: invalid slug, date or status; missing file or template

Source: Anthropic Academy, "AI-native SDLC playbook", capture intent.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SECTIONS = ("Problem", "Proposed outcome", "Affected users and systems",
            "Constraints", "Open questions")
STATUSES = ("draft", "accepted", "rejected", "superseded")
SLUG_RE = re.compile(r"[a-z0-9-]+")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
TITLE_RE = re.compile(r"^#\s+Intent:[ \t]*(?P<title>.*?)\s*$")
HEADER_RE = re.compile(
    r"^Author:[ \t]*(?P<author>.*?)\.[ \t]+Status:[ \t]*(?P<status>[A-Za-z-]+)\.?[ \t]*$")
H1_RE = re.compile(r"^#(?!#)\s")
H2_RE = re.compile(r"^##(?!#)\s+(?P<name>.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
PLACEHOLDER_RE = re.compile(r"<[^<>\n]+>")
AUTOLINK_RE = re.compile(r"<[A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*>")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
INLINE_CODE_RE = re.compile(r"(`+)(?:(?!\1).)+?\1")
DEFAULT_TEMPLATE = Path(__file__).resolve().parent.parent / "references" / "templates" / "intent.md"


class IntentError(Exception):
    """A usage error: exit 2."""


def _blank_comments(text: str) -> str:
    """Drop HTML comments but keep their newlines, so line numbers still line up."""
    return COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def parse(text: str) -> dict:
    """Structure of an intent: title, header, sections (with body and first line), and
    every placeholder found outside code and comments."""
    lines = [ln.rstrip("\r") for ln in _blank_comments(text).split("\n")]
    doc: dict = {"title": None, "title_line": None, "author": None, "status": None,
                 "header_line": None, "sections": [], "placeholders": []}
    in_fence = False
    current = None
    for n, line in enumerate(lines, 1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            if current is not None:
                current["body"].append(line)
            continue
        if in_fence:
            if current is not None:
                current["body"].append(line)
            continue
        prose = AUTOLINK_RE.sub("", INLINE_CODE_RE.sub("", line))
        for m in PLACEHOLDER_RE.finditer(prose):
            doc["placeholders"].append((n, m.group(0)))
        if doc["title"] is None and H1_RE.match(line):
            t = TITLE_RE.match(line)
            doc["title"] = t.group("title") if t else ""
            doc["title_line"] = n if t else None
            current = None
            continue
        h = HEADER_RE.match(line)
        if h and doc["header_line"] is None and current is None:
            doc["author"], doc["status"] = h.group("author").strip(), h.group("status")
            doc["header_line"] = n
            continue
        s = H2_RE.match(line)
        if s:
            current = {"name": s.group("name"), "line": n, "body": []}
            doc["sections"].append(current)
            continue
        if H1_RE.match(line):
            current = None
            continue
        if current is not None:
            current["body"].append(line)
    return doc


def _norm(name: str) -> str:
    return " ".join(name.split()).lower()


def lint_text(text: str) -> list[str]:
    """Problems as `line: message` (line 0 = the document as a whole)."""
    doc = parse(text)
    p: list[str] = []
    if doc["title_line"] is None:
        p.append("1: missing title line '# Intent: <title>'")
    elif not doc["title"]:
        p.append(f"{doc['title_line']}: title is empty")
    if doc["header_line"] is None:
        p.append("0: missing header line 'Author: <name>. Status: draft.'")
    else:
        if not doc["author"]:
            p.append(f"{doc['header_line']}: Author is empty")
        if doc["status"] not in STATUSES:
            p.append(f"{doc['header_line']}: Status {doc['status']!r} is not one of: "
                     f"{', '.join(STATUSES)}")
    by_name: dict[str, list[dict]] = {}
    for s in doc["sections"]:
        by_name.setdefault(_norm(s["name"]), []).append(s)
    for name in SECTIONS:
        found = by_name.get(_norm(name), [])
        if not found:
            p.append(f"0: missing section '## {name}'")
            continue
        if len(found) > 1:
            p.append(f"{found[1]['line']}: section '## {name}' appears {len(found)} times")
        if not "".join(found[0]["body"]).strip():
            p.append(f"{found[0]['line']}: section '## {name}' is empty")
    for n, ph in doc["placeholders"]:
        p.append(f"{n}: unfilled placeholder {ph}")
    return p


def _read(path: Path) -> str:
    # bytes, not read_text: universal-newline translation would turn CRLF into LF and
    # set-status would then rewrite every line ending, not just the Status token.
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        raise IntentError(f"no such file: {path}")
    except (OSError, UnicodeDecodeError) as e:
        raise IntentError(f"unreadable: {path}: {e}")


def git_user_name() -> str | None:
    """`git config user.name`, run with a filtered environment and fsmonitor off."""
    env = {k: v for k, v in os.environ.items()
           if k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
           or (k.startswith("GIT_") and k not in ("GIT_DIR", "GIT_WORK_TREE",
                                                   "GIT_CONFIG_PARAMETERS"))}
    try:
        p = subprocess.run(["git", "-c", "core.fsmonitor=false", "config", "user.name"],
                           env=env, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    name = p.stdout.strip()
    return name if p.returncode == 0 and name else None


def cmd_new(args) -> int:
    if not SLUG_RE.fullmatch(args.slug):
        raise IntentError(f"slug {args.slug!r} must match [a-z0-9-]+")
    date = args.date or _dt.date.today().isoformat()
    if not DATE_RE.fullmatch(date):
        raise IntentError(f"date {date!r} must be YYYY-MM-DD")
    try:
        _dt.date.fromisoformat(date)
    except ValueError:
        raise IntentError(f"date {date!r} is not a real date")
    template = Path(args.template) if args.template else DEFAULT_TEMPLATE
    if not template.is_file():
        raise IntentError(f"template not found: {template}")
    body = template.read_text(encoding="utf-8")
    title = args.title or args.slug.replace("-", " ").strip().capitalize()
    author = args.author if args.author is not None else git_user_name()
    for label, v in (("title", title), ("author", author)):
        if v is not None and ("\n" in v or "\r" in v):
            raise IntentError(f"--{label} must be a single line")
    body = body.replace("<title>", title, 1)
    if author:
        body = body.replace("<name>", author, 1)
    out = Path(args.dir) / f"{date}-{args.slug}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(out, "x", encoding="utf-8") as fh:
            fh.write(body)
    except FileExistsError:
        print(f"intent: {out} already exists; refusing to overwrite", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"path": str(out), "title": title, "author": author,
                          "status": "draft"}))
    else:
        print(out)
    return 0


def cmd_lint(args) -> int:
    report = []
    for f in args.files:
        report.append({"file": str(f), "problems": lint_text(_read(Path(f)))})
    bad = sum(bool(r["problems"]) for r in report)
    if args.json:
        print(json.dumps({"files": report, "failed": bad}, indent=2))
    else:
        for r in report:
            for prob in r["problems"]:
                print(f"{r['file']}:{prob}")
        print(f"intent: {len(report)} file(s), {bad} with problems")
    return 1 if bad else 0


def cmd_status(args) -> int:
    doc = parse(_read(Path(args.file)))
    if doc["header_line"] is None:
        print(f"intent: {args.file}: no 'Author: … Status: ….' header line", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"file": args.file, "title": doc["title"], "author": doc["author"],
                          "status": doc["status"], "valid": doc["status"] in STATUSES}))
    else:
        print(doc["status"])
    if doc["status"] not in STATUSES:
        print(f"intent: status {doc['status']!r} is not one of: {', '.join(STATUSES)}",
              file=sys.stderr)
        return 1
    return 0


def cmd_set_status(args) -> int:
    if args.status not in STATUSES:
        raise IntentError(f"status {args.status!r} is not one of: {', '.join(STATUSES)}")
    path = Path(args.file)
    raw = _read(path)
    header_line = parse(raw)["header_line"]
    if header_line is None:
        print(f"intent: {path}: no 'Author: … Status: ….' header line to rewrite",
              file=sys.stderr)
        return 1
    # split on "\n" only — the same split parse() numbers lines by — so the rewrite
    # touches the Status token and nothing else, CRLF endings included.
    lines = raw.split("\n")
    line = lines[header_line - 1]
    content = line.rstrip("\r")
    m = HEADER_RE.match(content)
    old = m.group("status")
    lines[header_line - 1] = (content[:m.start("status")] + args.status
                              + content[m.end("status"):] + line[len(content):])
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes("\n".join(lines).encode("utf-8"))
    os.replace(tmp, path)
    print(f"{path}: {old} -> {args.status}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bstack-intent", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="create <dir>/<date>-<slug>.md from the template")
    n.add_argument("slug")
    n.add_argument("--title")
    n.add_argument("--author", help="default: git config user.name")
    n.add_argument("--dir", default="intent")
    n.add_argument("--template", help=f"default: {DEFAULT_TEMPLATE.name} shipped with bstack")
    n.add_argument("--date", help="YYYY-MM-DD (default: today)")
    n.add_argument("--json", action="store_true")

    l = sub.add_parser("lint", help="check sections, placeholders and status")
    l.add_argument("files", nargs="+")
    l.add_argument("--json", action="store_true")

    s = sub.add_parser("status", help="print the Status from the header")
    s.add_argument("file")
    s.add_argument("--json", action="store_true")

    ss = sub.add_parser("set-status", help="rewrite the header's Status in place")
    ss.add_argument("file")
    ss.add_argument("status", help=" | ".join(STATUSES))

    args = ap.parse_args(argv)
    try:
        return {"new": cmd_new, "lint": cmd_lint, "status": cmd_status,
                "set-status": cmd_set_status}[args.cmd](args)
    except IntentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
