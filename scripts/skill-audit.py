#!/usr/bin/env python3
"""skill-audit.py — skill registry audit (bstack v0.21.8).

Invoked as: `bstack skills audit [options]`

Crystallizes the "Skill Registry Audit" pattern (bstack-engine candidate ledger,
3/3 instances: Steipete's skill-cleaner + the 2026-05-25 manual inventory +
P7 Freshness as a degenerate single-dimension case). Adapts Steipete's
skill-cleaner (steipete/agent-scripts) algorithm for Claude Code + bstack:

  - token math identical: ceil(utf8_bytes / chars_per_token)
  - realpath-dedupe of symlinked roots (the .agents <-> workspace symlink case)
  - usage-trace scanning of Claude Code logs (~/.claude/projects/**/*.jsonl)
    rather than Codex's ~/.codex/history.jsonl

Six reports (1-5 are hygiene; 6 is correctness — skillify step 3, BRO-1411):
  1. Budget        — total description token cost vs ceiling (default 2% of 1M)
  2. Duplicates    — same skill name across >1 distinct realpath
  3. Registry      — coherence between companion-skills.yaml and installed roots
                     (registered-but-missing, installed-but-unregistered)
  4. Unused        — no invocation trace in recent session logs (--months window)
  5. Roots         — skill count per root
  6. Untested      — ships deterministic code (scripts/*.{py,sh,mjs,js,ts}) but no
                     tests; informational by default, a hard gate under --require-tests

Env overrides (test fixtures):
  BSTACK_DIR                  bstack root (for default companion-skills.yaml)
  BSTACK_AUDIT_ROOTS          colon-separated skill roots (overrides defaults)
  BSTACK_AUDIT_LOG_GLOB       glob for session logs (default ~/.claude/projects/**/*.jsonl)
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("skill-audit: python3 yaml module required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)

HOME = Path.home()
DEFAULT_ROOTS = [
    HOME / ".claude" / "skills",
    HOME / ".agents" / "skills",
    Path(os.environ.get("BROOMVA_ROOT", HOME / "broomva")) / "skills",
]


def parse_frontmatter(skill_md: Path) -> dict:
    """Extract YAML frontmatter (name, description) from a SKILL.md."""
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end]
    try:
        data = yaml.safe_load(block)
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def token_cost(text: str, chars_per_token: int) -> int:
    """Codex-identical: ceil(utf8_bytes / chars_per_token)."""
    if not text:
        return 0
    return math.ceil(len(text.encode("utf-8")) / chars_per_token)


def discover_skills(roots: list[Path]) -> list[dict]:
    """Walk roots for */SKILL.md (one level deep + monorepo skills/<name>/).
    realpath-dedupe so a symlinked root doesn't double-count.
    """
    seen_realpaths: set[str] = set()
    skills: list[dict] = []
    for root in roots:
        if not root.is_dir():
            continue
        # Each immediate child dir with a SKILL.md is a skill.
        for child in sorted(root.iterdir()):
            skill_md = child / "SKILL.md"
            if not skill_md.is_file():
                continue
            rp = os.path.realpath(skill_md)
            if rp in seen_realpaths:
                continue
            seen_realpaths.add(rp)
            fm = parse_frontmatter(skill_md)
            name = fm.get("name", child.name)
            desc = fm.get("description", "") or ""
            if isinstance(desc, list):
                desc = " ".join(str(d) for d in desc)
            skills.append({
                "name": str(name),
                "dir_name": child.name,
                "root": str(root),
                "path": str(skill_md),
                "realpath": rp,
                "desc_chars": len(str(desc)),
                "description": str(desc),
            })
    return skills


def load_registry(yaml_path: Path) -> list[dict]:
    if not yaml_path.is_file():
        return []
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    return data.get("skills", []) if isinstance(data, dict) else []


def scan_usage(skill_names: list[str], log_glob: str, months: int) -> set[str]:
    """Return the set of skill names with an invocation trace in recent logs.
    Heuristic (matches Steipete): a name appears as `$<name>`, `--skill <name>`,
    or `skills/<name>/SKILL.md` in a session JSONL within the window.
    """
    import time
    cutoff = time.time() - months * 31 * 24 * 3600
    used: set[str] = set()
    # Build one combined regex of all names (word-boundary-ish).
    if not skill_names:
        return used
    patterns = {n: re.compile(
        r"(?:\$" + re.escape(n) + r"\b|--skill\s+" + re.escape(n) + r"\b|skills/" + re.escape(n) + r"/SKILL\.md)"
    ) for n in skill_names}
    for fpath in glob.glob(log_glob, recursive=True):
        try:
            if os.path.getmtime(fpath) < cutoff:
                continue
            with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                blob = fh.read()
        except OSError:
            continue
        for n, pat in patterns.items():
            if n in used:
                continue
            if pat.search(blob):
                used.add(n)
    return used


CODE_EXTS = {".py", ".sh", ".mjs", ".js", ".ts"}


def _is_test_file(name: str) -> bool:
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith("_test.sh")
        or ".test." in name
    )


def _skill_code_files(skill_dir: Path) -> list[str]:
    """Deterministic code files a skill ships (scripts/ + skill root, one level).

    Test files are excluded — a skill whose only code IS its tests has nothing
    left to test. Markdown-only skills return [] and are exempt from the gate.
    """
    found: list[str] = []
    for sub in ("scripts", ""):
        d = skill_dir / sub if sub else skill_dir
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix in CODE_EXTS and not _is_test_file(f.name):
                found.append(str(f.relative_to(skill_dir)))
    return found


def _skill_has_tests(skill_dir: Path) -> bool:
    """True if the skill ships any test file (tests/ or scripts/ or root, one level)."""
    for sub in ("tests", "scripts", ""):
        d = skill_dir / sub if sub else skill_dir
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.is_file() and _is_test_file(f.name):
                return True
    return False


def detect_untested(skills: list[dict]) -> list[dict]:
    """Skills shipping deterministic code but no tests — skillify step 3 (BRO-1411).

    The correctness counterpart to the hygiene reports: `audit` already covers
    budget/duplicate/reachability; this covers "the script the skill runs is
    actually tested". Markdown-only skills are exempt (no deterministic code).
    """
    out: list[dict] = []
    for s in skills:
        skill_dir = Path(s["path"]).parent
        code = _skill_code_files(skill_dir)
        if code and not _skill_has_tests(skill_dir):
            out.append({"name": s["name"], "dir": str(skill_dir), "code_files": code})
    return sorted(out, key=lambda x: x["name"])


def main() -> int:
    ap = argparse.ArgumentParser(prog="bstack skills audit", description="Skill registry audit.")
    ap.add_argument("--roots", action="append", default=[], help="Additional skill root (repeatable).")
    ap.add_argument("--budget-tokens", type=int, default=20000, help="Token budget ceiling (default 20000 = 2%% of 1M).")
    ap.add_argument("--chars-per-token", type=int, default=4, help="Token-cost divisor (default 4).")
    ap.add_argument("--months", type=int, default=3, help="Usage-trace window for unused detection (default 3).")
    ap.add_argument("--no-logs", action="store_true", help="Skip usage-trace scanning.")
    ap.add_argument("--require-tests", action="store_true",
                    help="Gate: exit 1 if any skill ships deterministic code without tests (skillify step 3, BRO-1411).")
    ap.add_argument("--json", action="store_true", help="Machine-readable output.")
    args = ap.parse_args()

    # Resolve roots: env override > --roots > defaults.
    if os.environ.get("BSTACK_AUDIT_ROOTS"):
        roots = [Path(p) for p in os.environ["BSTACK_AUDIT_ROOTS"].split(":") if p]
    else:
        roots = list(DEFAULT_ROOTS)
        roots += [Path(p) for p in args.roots]

    bstack_dir = Path(os.environ.get("BSTACK_DIR", Path(__file__).resolve().parent.parent))
    registry = load_registry(bstack_dir / "references" / "companion-skills.yaml")

    skills = discover_skills(roots)
    names = sorted({s["name"] for s in skills})

    # 1. Budget. Clamp chars_per_token to >=1 so a bad flag can't ZeroDivision.
    cpt = max(1, args.chars_per_token)
    total_tokens = sum(token_cost(s["description"], cpt) for s in skills)
    budget_used_ratio = (total_tokens / args.budget_tokens) if args.budget_tokens else 0.0

    # 2. Duplicates — same name across >1 distinct realpath
    by_name: dict[str, list[dict]] = {}
    for s in skills:
        by_name.setdefault(s["name"], []).append(s)
    duplicates = {n: v for n, v in by_name.items() if len({x["realpath"] for x in v}) > 1}

    # 3. Registry coherence
    reg_names = {r["name"] for r in registry if "name" in r}
    installed_names = set(names)
    registered_missing = sorted(reg_names - installed_names)
    installed_unregistered = sorted(installed_names - reg_names)

    # 4. Unused
    log_glob = os.environ.get("BSTACK_AUDIT_LOG_GLOB", str(HOME / ".claude" / "projects" / "**" / "*.jsonl"))
    unused: list[str] = []
    if not args.no_logs:
        used = scan_usage(names, log_glob, args.months)
        unused = sorted(set(names) - used)

    # 5. Roots
    root_counts: dict[str, int] = {}
    for s in skills:
        root_counts[s["root"]] = root_counts.get(s["root"], 0) + 1

    # 6. Untested deterministic code (skillify step 3 — correctness, not hygiene)
    untested = detect_untested(skills)
    gate_failed = bool(args.require_tests and untested)

    if args.json:
        print(json.dumps({
            "total_skills": len(skills),
            "unique_names": len(names),
            "budget": {"total_tokens": total_tokens, "ceiling": args.budget_tokens, "used_ratio": round(budget_used_ratio, 3)},
            "duplicates": {n: [x["path"] for x in v] for n, v in duplicates.items()},
            "registry": {"registered_missing": registered_missing, "installed_unregistered": installed_unregistered},
            "unused": unused,
            "roots": root_counts,
            "untested": untested,
            "require_tests": bool(args.require_tests),
        }, indent=2))
        return 1 if gate_failed else 0

    # Human report
    print("# Skill Audit Report\n")
    print(f"discovered: {len(skills)} skills ({len(names)} unique names) across {len([r for r in roots if r.is_dir()])} roots\n")
    print("## Budget")
    print(f"  description tokens : {total_tokens:,} / {args.budget_tokens:,} ceiling  ({budget_used_ratio*100:.1f}%)")
    if budget_used_ratio > 1.0:
        print(f"  ⚠ OVER BUDGET by {(budget_used_ratio-1)*100:.1f}% — consider trimming descriptions or pruning unused skills")
    print()
    print(f"## Duplicates ({len(duplicates)})")
    if duplicates:
        for n, v in sorted(duplicates.items()):
            print(f"  {n}:")
            for x in v:
                print(f"    - {x['path']}")
    else:
        print("  (none)")
    print()
    print("## Registry coherence")
    print(f"  registered but NOT installed ({len(registered_missing)}): {', '.join(registered_missing) or '(none)'}")
    print(f"  installed but NOT registered ({len(installed_unregistered)}): {', '.join(installed_unregistered) or '(none)'}")
    print()
    if args.no_logs:
        print("## Unused\n  (skipped — --no-logs)")
    else:
        print(f"## Unused (no trace in last {args.months}mo)  [{len(unused)}]")
        print(f"  {', '.join(unused) or '(none — all skills show recent usage)'}")
    print()
    print(f"## Untested deterministic code  [{len(untested)}]")
    if untested:
        for u in untested:
            print(f"  {u['name']}: {', '.join(u['code_files'])}")
        if args.require_tests:
            print(f"  ⚠ {len(untested)} skill(s) ship code without tests — --require-tests gate FAILED")
        else:
            print("  (informational — pass --require-tests to gate CI on this)")
    else:
        print("  (none — every skill with deterministic code ships tests)")
    print()
    print("## Roots")
    for r, c in sorted(root_counts.items()):
        print(f"  {c:3d}  {r}")
    return 1 if gate_failed else 0


if __name__ == "__main__":
    sys.exit(main())
