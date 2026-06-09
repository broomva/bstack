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
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NAME_MAX = 64
DESC_CEILING_DEFAULT = 1024  # portable Agent-Skills ceiling
_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):[ \t]*(.*)$")
_BLOCK_INDICATORS = {">", "|", ">-", "|-", ">+", "|+"}


def parse_frontmatter_fields(text: str) -> dict[str, str] | None:
    """Extract top-level scalar fields from the YAML frontmatter block.

    Dependency-free and deliberately minimal: handles single-line values
    (optionally quoted) and block/folded scalars (`>` / `|`). Not a general
    YAML parser — sufficient for name/description validation. Returns None when
    there is no frontmatter block at all.
    """
    m = re.match(r"^---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)", text, re.DOTALL)
    if not m:
        return None
    lines = m.group(1).split("\n")
    fields: dict[str, str] = {}
    i = 0
    n = len(lines)
    while i < n:
        km = _KEY_RE.match(lines[i])
        if not km:
            i += 1
            continue
        key, rest = km.group(1), km.group(2).strip()
        if rest in _BLOCK_INDICATORS:
            # Block/folded scalar: gather subsequent more-indented lines.
            folded = rest[0] == ">"
            i += 1
            block: list[str] = []
            base_indent: int | None = None
            while i < n:
                ln = lines[i]
                if ln.strip() == "":
                    block.append("")
                    i += 1
                    continue
                indent = len(ln) - len(ln.lstrip(" "))
                if base_indent is None:
                    base_indent = indent
                if indent < base_indent:
                    break
                block.append(ln[base_indent:])
                i += 1
            joiner = " " if folded else "\n"
            fields[key] = joiner.join(block).strip()
            continue
        # Single-line scalar; strip one layer of matching quotes.
        if len(rest) >= 2 and rest[0] == rest[-1] and rest[0] in ("'", '"'):
            rest = rest[1:-1]
        fields[key] = rest
        i += 1
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
