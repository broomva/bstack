#!/usr/bin/env python3
"""Validate SKILL.md frontmatter against the Agent Skills open standard.

The open standard (https://agentskills.io/specification) defines the portable
SKILL.md contract adopted across Claude Code, Cursor, Gemini CLI, Codex, Goose,
and ~40 other tools:

  - name         required · ≤64 chars · ^[a-z0-9]+(-[a-z0-9]+)*$ · == parent dir
  - description  required · non-empty · ≤1024 chars (the portable ceiling)

The `description` is the single highest-leverage field — it is the routing
signal the agent reads to decide when to invoke the skill, and it sits
permanently in the system prompt, which is why the spec caps it. Claude Code
tolerates up to 1536 chars and makes `name` optional; authoring to the stricter
1024-char ceiling keeps a skill portable across every client.

Severity policy (non-breaking by design — mirrors the bookkeeping unquoted-date
lint, BRO-1449): only a genuinely unroutable skill is an ERROR; standard-
conformance nits (name casing/length, description over the portable ceiling,
name absent where Claude Code allows it) are WARNINGS so the validator stays
useful over a mixed real-world skill ecosystem without failing it wholesale.

  ERROR (exit 1):  no frontmatter block · description missing or empty
  WARNING (exit 0): name missing · name >64 · name not lowercase-hyphen ·
                    name != parent dir · description > ceiling

Dependency-free (no PyYAML) so it runs in any minimal CI. Usage:

  validate-skill-frontmatter.py [--ceiling N] [--quiet] PATH [PATH ...]

PATH may be a SKILL.md file or a directory (recursed for **/SKILL.md).
"""
from __future__ import annotations

import argparse
import re
import sys
import os
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NAME_MAX = 64
DESC_CEILING_DEFAULT = 1024  # portable Agent-Skills ceiling
_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):[ \t]*(.*)$")
_BLOCK_INDICATORS = {">", "|", ">-", "|-", ">+", "|+"}
# Test hook: set SKILL_FM_NO_YAML=1 to force the dependency-free fallback parser
# even when PyYAML is installed, so both code paths get exercised in CI.
_USE_YAML = os.environ.get("SKILL_FM_NO_YAML") != "1"


def _coerce_field_dict(data) -> dict[str, str]:
    """Normalize a parsed YAML mapping to a {str: str} field dict.

    name/description are the only fields we validate and both must be strings;
    a non-string value (None, list, number) is stringified so downstream
    len()/strip() never crash — a malformed list-valued `description` becomes
    its repr, which is long and correctly trips the ceiling warning.
    """
    out: dict[str, str] = {}
    if not isinstance(data, dict):
        return out
    for k, v in data.items():
        if v is None:
            out[str(k)] = ""
        elif isinstance(v, str):
            out[str(k)] = v
        else:
            out[str(k)] = str(v)
    return out


def parse_frontmatter_fields(text: str) -> dict[str, str] | None:
    """Extract top-level scalar fields from the YAML frontmatter block.

    Primary path uses PyYAML (correct on every YAML scalar form — plain
    multiline, folded `>`, literal `|`, inline `#` comments, quoting). When
    PyYAML is unavailable (minimal CI), falls back to a hardened hand-parser
    that covers the same cases best-effort. Returns None only when there is NO
    frontmatter block; an empty block (`---\\n---`) returns {} so the caller
    emits the correct "description missing" error rather than "no block".
    """
    # `(.*?\n)?` makes the body optional so an empty block still matches.
    m = re.match(r"^---[ \t]*\n(.*?\n)?---[ \t]*(?:\n|$)", text, re.DOTALL)
    if not m:
        return None
    block = m.group(1) or ""

    if _USE_YAML:
        try:
            import yaml
            data = yaml.safe_load(block)
            return {} if data is None else _coerce_field_dict(data)
        except ImportError:
            pass
        except Exception:
            # Malformed YAML — fall through to the hand-parser, which is more
            # forgiving and still recovers name/description in practice.
            pass

    return _hand_parse_fields(block)


def _strip_inline_comment(value: str) -> str:
    """Strip a YAML inline comment (whitespace-preceded `#`) from a plain scalar.

    Matches YAML semantics: `desc text  # note` → `desc text`; `a#b` (no
    leading whitespace) is NOT a comment and is left intact. Quoted values are
    handled by the caller and never reach here.
    """
    cm = re.search(r"[ \t]#", value)
    return value[:cm.start()].rstrip() if cm else value


def _hand_parse_fields(block: str) -> dict[str, str]:
    """Dependency-free fallback parser (best-effort; PyYAML is the primary path).

    Handles: single-line scalars (quoted or plain, with inline-# stripping),
    block/folded scalars (`|` / `>`), and PLAIN multiline scalars (continuation
    lines indented under a 0-indent key are folded with spaces). Top-level keys
    only (0 indent), which is all SKILL.md frontmatter needs.
    """
    lines = block.split("\n")
    fields: dict[str, str] = {}
    i = 0
    n = len(lines)
    while i < n:
        km = _KEY_RE.match(lines[i])
        # Only 0-indent lines are keys; anything else is consumed as a value.
        if not km or (len(lines[i]) - len(lines[i].lstrip(" \t"))) != 0:
            i += 1
            continue
        key, rest = km.group(1), km.group(2).strip()

        if rest in _BLOCK_INDICATORS:
            folded = rest[0] == ">"
            i += 1
            chunk: list[str] = []
            base_indent: int | None = None
            while i < n:
                ln = lines[i]
                if ln.strip() == "":
                    chunk.append("")
                    i += 1
                    continue
                indent = len(ln) - len(ln.lstrip(" "))
                if indent == 0:
                    break
                if base_indent is None:
                    base_indent = indent
                chunk.append(ln[base_indent:])
                i += 1
            fields[key] = (" " if folded else "\n").join(chunk).strip()
            continue

        # Quoted single-line scalar.
        if len(rest) >= 2 and rest[0] in ("'", '"') and rest[-1] == rest[0]:
            fields[key] = rest[1:-1]
            i += 1
            continue

        # Plain scalar: strip inline comment, then fold any indented
        # continuation lines (plain multiline) until the next 0-indent key.
        value = _strip_inline_comment(rest)
        i += 1
        cont: list[str] = []
        while i < n:
            ln = lines[i]
            if ln.strip() == "":
                break
            if (len(ln) - len(ln.lstrip(" \t"))) == 0:
                break  # next top-level key
            cont.append(_strip_inline_comment(ln.strip()))
            i += 1
        if cont:
            value = (value + " " + " ".join(cont)).strip()
        fields[key] = value
    return fields


def validate_skill(path: Path, ceiling: int = DESC_CEILING_DEFAULT) -> list[tuple[str, str]]:
    """Return a list of (severity, message) findings for one SKILL.md file."""
    findings: list[tuple[str, str]] = []
    text = path.read_text(errors="replace")
    fields = parse_frontmatter_fields(text)
    if fields is None:
        findings.append(("error", "no YAML frontmatter block (--- … ---)"))
        return findings

    # ── description: the required routing signal ──
    desc = fields.get("description", "")
    if not desc.strip():
        findings.append(("error", "description is missing or empty (required — it is the routing signal)"))
    elif len(desc) > ceiling:
        findings.append((
            "warning",
            f"description is {len(desc)} chars (> {ceiling} portable ceiling); "
            f"trim for cross-client portability",
        ))

    # ── name: required by the open standard; Claude Code defaults it to the dir ──
    name = fields.get("name")
    if name is None:
        findings.append(("warning", "name is absent (open standard requires it; Claude Code defaults it to the directory)"))
    else:
        if len(name) > NAME_MAX:
            findings.append(("warning", f"name is {len(name)} chars (> {NAME_MAX} max)"))
        if not NAME_RE.match(name):
            findings.append(("warning", f"name {name!r} is not lowercase-hyphen (^[a-z0-9]+(-[a-z0-9]+)*$)"))
        # parent-dir match only checks out for a file literally named SKILL.md
        if path.name == "SKILL.md" and name != path.parent.name:
            findings.append(("warning", f"name {name!r} != parent directory {path.parent.name!r}"))
    return findings


def iter_skill_files(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            out.extend(sorted(pp.rglob("SKILL.md")))
        elif pp.is_file():
            out.append(pp)
    # de-dup while preserving order
    seen: set[Path] = set()
    uniq: list[Path] = []
    for f in out:
        rp = f.resolve()
        if rp not in seen:
            seen.add(rp)
            uniq.append(f)
    return uniq


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate SKILL.md frontmatter (Agent Skills open standard)")
    ap.add_argument("paths", nargs="+", help="SKILL.md file(s) or directories to scan")
    ap.add_argument("--ceiling", type=int, default=DESC_CEILING_DEFAULT, help=f"description char ceiling (default {DESC_CEILING_DEFAULT})")
    ap.add_argument("--quiet", action="store_true", help="only print files with findings")
    args = ap.parse_args(argv)

    files = iter_skill_files(args.paths)
    if not files:
        print("validate-skill-frontmatter: no SKILL.md files found", file=sys.stderr)
        return 0

    n_err = 0
    n_warn = 0
    for f in files:
        findings = validate_skill(f, ceiling=args.ceiling)
        errs = [m for sev, m in findings if sev == "error"]
        warns = [m for sev, m in findings if sev == "warning"]
        n_err += len(errs)
        n_warn += len(warns)
        if findings:
            print(f"{f}")
            for m in errs:
                print(f"  [ERROR] {m}")
            for m in warns:
                print(f"  [warn]  {m}")
        elif not args.quiet:
            print(f"{f}\n  [ok]")

    print(f"\nSKILL.md frontmatter: {len(files)} checked · {n_err} error(s) · {n_warn} warning(s)")
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main())
