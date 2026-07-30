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

Seven reports (1-5 are hygiene; 6-7 are correctness — skillify steps 3 and 5):
  1. Budget        — total description token cost vs ceiling (default 2% of 1M)
  2. Duplicates    — same skill name across >1 distinct realpath
  3. Registry      — coherence between companion-skills.yaml and installed roots
                     (registered-but-missing, installed-but-unregistered)
  4. Unused        — no invocation trace in recent session logs (--months window)
  5. Roots         — skill count per root
  6. Untested      — ships deterministic code (scripts/*.{py,sh,mjs,js,ts}) but no
                     tests; informational by default, a hard gate under --require-tests
                     (skillify step 3, BRO-1411)
  7. Eval coverage — trigger-eval state per skill: none / no_trigger_eval /
                     present_but_vacuous / covered; informational by default. Under
                     --require-evals it is a hard gate on present_but_vacuous ONLY —
                     an unmet trigger-coverage claim, or an evals/ dir holding
                     nothing gradable (skillify step 5, BRO-2005)

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

try:  # stdlib >= 3.11; .toml eval artifacts degrade to "cannot grade", never to a crash
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on python < 3.11
    tomllib = None

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


# --- report 7: trigger-eval coverage (skillify step 5, BRO-2005) -------------
#
# Semantics ported from `skillify_check.py` step 5 in the broomva/skills monorepo
# (skills/tooling/skillify/scripts/skillify_check.py — TRIGGER_ASSERTION_KEYS /
# _is_trigger_eval). Reimplemented rather than imported: bstack must stay
# self-contained, installable standalone, with no cross-repo import path.
#
# The lesson that check encodes, in one line: an EMPTY evals/ dir used to score
# "present", which is how the stack could report ~10% eval coverage while holding
# ZERO trigger assertions (BRO-2005 audit: 38/376 skills with an eval artifact, 0
# with a single should_trigger case). Presence is not assertion — grade the
# CONTENT. Report 6 already refuses that trap for the deterministic half; report 7
# closes it for the latent half, including for this auditor's own skill.
#
# What the gate may NOT do (review round 1, BRO-2005): it may not make `rm -rf
# evals/` the compliant move. Three skills on the real roster ship substantive
# suites in the Anthropic behavioural-eval schema (clerk-setup: 5 cases / 29
# expectations) and the scenario-eval schema (governed-autonomy-loop: 9 scenarios
# / 30 assertions, 5 bound to real pytest node ids). They are real evals that are
# not TRIGGER evals, and they never claimed to be. Gating them would mean deleting
# a working suite scores better than shipping one — an inverted incentive, which
# disqualifies a gate no matter how correct its intent. They classify
# `no_trigger_eval`, which is reported and never gated. `present_but_vacuous` is
# reserved for an artifact that actually REACHES FOR the trigger keys and misses:
# one-sided, empty-valued, self-contradictory, or ungradable.
#
# Two trigger-eval schemas are in use here and both must classify:
#   prompt sets    {"cases": [{"prompt": …, "should_trigger": true|false}]}
#                  — polarity lives in the VALUE; the CASE is the containing map
#   resolver evals {"should_fire": [prompt, …], "should_not_fire": [prompt, …]}
#                  — polarity lives in the KEY, which names a whole case SET; the
#                    two keys are distinct sets by construction
#
# Key-set invariant: every snake_case key has its camelCase twin, and every
# positive key has its negative counterpart. An asymmetric set silently grades one
# spelling and ignores the other. Pinned by tests/skill-audit.test.sh (T39).
TRIGGER_ASSERTION_KEYS = frozenset({
    "should_trigger",     "shouldTrigger",
    "should_not_trigger", "shouldNotTrigger",
    "should_fire",        "shouldFire",
    "should_not_fire",    "shouldNotFire",
    "negative_case",      "negativeCase",
})

# Keys whose bare presence means "this case must NOT fire".
_NEGATIVE_TRIGGER_KEYS = frozenset({
    "should_not_trigger", "shouldNotTrigger",
    "should_not_fire", "shouldNotFire",
    "negative_case", "negativeCase",
})

# KEY-polarity keys: the key NAMES a whole prompt set, so its value position holds
# PROMPTS, not a boolean. That is what separates them from the value-polarity keys
# (`should_trigger`, `should_not_trigger`, `negative_case`), whose value position
# holds the polarity itself. The split matters twice:
#   - one prompt is one prompt in any container — a bare string ("add a permission"),
#     a list, or a name->prompt map are all real coverage; requiring a boolean
#     spelling here rejected three real resolver-eval shapes.
#   - a dict under a VALUE-polarity key is a JSON Schema node, never a case, so it
#     must keep asserting nothing (BLOCKER-2). Under a key-polarity key a dict is a
#     named prompt map, so it does assert.
# Two key-polarity keys in one mapping are two DISTINCT sets by construction — see
# _tally_assertions, which reads this same set to decide that.
_KEY_POLARITY_KEYS = frozenset({
    "should_fire", "shouldFire",
    "should_not_fire", "shouldNotFire",
})

# JSON Schema keywords. A mapping carrying any of them under a key-polarity key is
# a node DESCRIBING the resolver format (`properties.should_fire: {"type": "array"}`)
# — BLOCKER-2's shape wearing the other key class. Admitting name->prompt maps under
# those keys would reopen it there without this guard.
_SCHEMA_NODE_KEYWORDS = frozenset({
    "type", "items", "properties", "$ref", "$defs", "definitions", "enum", "const",
    "oneOf", "anyOf", "allOf", "not", "format", "pattern", "required", "examples",
    "additionalProperties", "minItems", "maxItems", "default",
})

# Assertion keys of OTHER eval shapes (behavioural evals, scenario evals). They
# never make a trigger-coverage claim, so they never gate — they are counted only
# to say out loud, in the report, that the artifact is a real suite of another kind.
OTHER_ASSERTION_KEYS = frozenset({
    "expect", "expected", "expectation", "expectations", "expected_output",
    "assert", "asserts", "assertion", "assertions", "checks",
    "must_include", "must_not_include", "criteria", "rubric", "deterministic_test",
})

# Only structured artifacts are graded. A .md/.py/.sh file inside evals/ still
# counts as an artifact (so the dir is not "empty") but contributes no assertion.
# .jsonl / .toml match skillify_check.py's _EVAL_DATA_EXTS: two gates in one
# documented workflow must not disagree about what an eval artifact is.
EVAL_DATA_EXTS = {".json", ".jsonl", ".yaml", ".yml", ".toml"}

# Files that are NOT an eval artifact even though they live under evals/.
# Git cannot store an empty directory, so `evals/.gitkeep` IS the tracked form of
# the honest absence — grading it as a vacuous claim would put the only reachable
# "empty dir" state on the failing side of the gate.
_NON_ARTIFACT_NAMES = frozenset({
    ".gitkeep", ".keep", ".gitignore", ".gitattributes", ".DS_Store", "Thumbs.db",
})

EVAL_STATES = ("covered", "no_trigger_eval", "present_but_vacuous", "none")

_MAX_EVAL_DEPTH = 40

# Strings that carry a boolean. Anything else in a trigger key's value position
# (a "<FILL ME>" template placeholder, a prose note) asserts NOTHING — a scalar we
# cannot read as a polarity must never be read as a positive one.
_TRUE_STRINGS = frozenset({"true", "yes", "y", "1", "on"})
_FALSE_STRINGS = frozenset({"false", "no", "n", "0", "off"})

# Distinguishable parse outcomes. Both _UNREADABLE and None are ungradable and
# fail CLOSED; they are kept apart only so the reason string tells the truth about
# which one happened (an IO error is not "empty or unparseable").
_UNREADABLE = object()   # OSError — permissions, dangling symlink, vanished file
_UNSUPPORTED = object()  # a data ext this interpreter cannot parse (.toml < 3.11)


def _new_eval_counts() -> dict[str, int]:
    return {"positive": 0, "negative": 0, "trigger_keys": 0,
            "contradictory": 0, "other": 0, "depth_capped": 0}


def _assertion_polarity(key: str, value: object) -> str | None:
    """'positive' | 'negative' | None when the key asserts nothing.

    Base polarity comes from the key NAME; a falsey scalar FLIPS it — so
    `should_trigger: false` is a negative case and `should_not_fire: false` is a
    positive one. A non-empty list/tuple/set (the resolver-eval shape) keeps the
    key's polarity. An EMPTY one asserts nothing: `should_fire: []` is a
    placeholder, not coverage.

    What a free-form (non-boolean) scalar or a dict means depends on the KEY CLASS:

      value-polarity (`should_trigger`, `should_not_trigger`, `negative_case`) —
             the value position holds the polarity, so only a recognised boolean
             spelling asserts. Everything else is a placeholder or a type node:
             `should_trigger: "<FILL ME>"` in a TEMPLATE.json, and the JSON Schema
             shape `properties.should_trigger: {"type": "boolean"}`, which has a
             trigger key at a mapping position and zero cases. Scoring the schema
             as the eval it describes is the exact fail-open report 7 exists to
             catch (BLOCKER-2).
      key-polarity (`should_fire`, `should_not_fire`) — the value position holds
             PROMPTS. A non-empty string is one prompt, a non-empty name->prompt
             map is a set of them; both are real coverage. An EMPTY value of any
             shape still asserts nothing: `should_fire: []` / `should_fire: ""` is
             a placeholder, and a map carrying JSON Schema keywords is a type node
             describing the format, not a case set.
    """
    key_polarity = key in _KEY_POLARITY_KEYS
    if isinstance(value, (bool, int, float)):
        flipped = not value
    elif isinstance(value, str):
        low = value.strip().lower()
        if low in _TRUE_STRINGS:
            flipped = False
        elif low in _FALSE_STRINGS:
            flipped = True
        elif key_polarity and low:
            flipped = False   # a prompt, not a boolean
        else:
            return None
    elif isinstance(value, (list, tuple, set)):
        if not value:
            return None
        flipped = False
    elif isinstance(value, dict):
        if not value or not key_polarity or (_SCHEMA_NODE_KEYWORDS & value.keys()):
            return None
        flipped = False
    else:
        return None
    return "negative" if (key in _NEGATIVE_TRIGGER_KEYS) != flipped else "positive"


def _other_assertion_weight(value: object) -> int:
    """How many assertions a non-trigger assertion key carries (0 if empty)."""
    if value is None or (isinstance(value, (str, bytes)) and not value.strip()):
        return 0
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return 1


def _tally_assertions(node: object, counts: dict[str, int], depth: int = 0) -> None:
    """Tally trigger assertions by polarity over DISTINCT cases, at any depth.

    One assertion = one asserting KEY, never one prompt: `_assertion_polarity` is
    the single place that decides whether a key asserts anything, so an empty value
    cannot slip through as a zero-weight assertion. (Weighting a key-polarity key by
    len(value) would silently un-test that guard — `len([]) == 0` produces the same
    tally whether the empty-collection check runs or not.)

    Two-sidedness is a property of a case SET, not of a bag of keys. Where the
    polarity lives decides what a case is:

      value-polarity  {"prompt": …, "should_trigger": true}  — the case is the
                      containing mapping, so one mapping contributes at most one
                      side. A single case carrying `should_trigger: true` AND
                      `should_not_trigger: true` is self-contradictory: it counts
                      as NEITHER side and is reported, rather than certifying the
                      skill as two-sided all by itself.
      key-polarity    {"should_fire": [a, b], "should_not_fire": [c]} — the key
                      names the polarity of a whole prompt list, so the two keys
                      are distinct case sets by construction and the resolver
                      shape stays two-sided from one mapping (as it should). The
                      distinct-set property belongs to the KEY, not to the value's
                      container, so `should_fire: "a"` / `should_not_fire: "b"` is
                      two sets too — reading it off `isinstance(v, list)` instead
                      made those one contradictory case.

    A trigger key only counts as a real mapping KEY — the word "should_trigger"
    inside a `notes` prose blob must not certify a skill as covered.
    """
    if depth > _MAX_EVAL_DEPTH:
        counts["depth_capped"] += 1
        return
    if isinstance(node, dict):
        case_pos = case_neg = 0
        for k, v in node.items():
            key = str(k)
            if key in TRIGGER_ASSERTION_KEYS:
                counts["trigger_keys"] += 1
                polarity = _assertion_polarity(key, v)
                if polarity and (key in _KEY_POLARITY_KEYS
                                 or isinstance(v, (list, tuple, set))):
                    counts[polarity] += 1
                elif polarity == "positive":
                    case_pos += 1
                elif polarity == "negative":
                    case_neg += 1
            elif key in OTHER_ASSERTION_KEYS:
                counts["other"] += _other_assertion_weight(v)
            _tally_assertions(v, counts, depth + 1)
        if case_pos and case_neg:
            counts["contradictory"] += 1
        else:
            counts["positive"] += case_pos
            counts["negative"] += case_neg
    elif isinstance(node, list):
        for v in node:
            _tally_assertions(v, counts, depth + 1)


def _parse_jsonl(text: str) -> list:
    """Line-delimited JSON: one value per non-blank line.

    Strict on purpose — a malformed line raises, so the caller records the whole
    file as unparseable. Skipping the bad line instead would let a file whose
    lines ALL fail parse quietly report "structured, no trigger keys" (ungated)
    rather than "cannot be graded" (gated). Fail closed.
    """
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _parse_eval_artifact(path: Path):
    """Parse a structured eval artifact.

    Returns the parsed data, or one of three non-data outcomes:
      None          empty or malformed — ZERO assertions, never coverage
      _UNREADABLE   OSError (permissions, dangling symlink) — likewise zero
      _UNSUPPORTED  a data ext this interpreter cannot parse (.toml on < 3.11)

    A malformed prompts.json must not fail OPEN into 'covered'. The caller reports
    the first two as present_but_vacuous — an ungradable eval asserts exactly as
    much as a missing one, while still being a standing claim of coverage.
    _UNSUPPORTED is NOT a defect in the artifact, so it is not gated.
    """
    if path.suffix not in EVAL_DATA_EXTS:
        return _UNSUPPORTED
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return _UNREADABLE
    if not text.strip():
        return None
    try:
        if path.suffix == ".json":
            return json.loads(text)
        if path.suffix == ".jsonl":
            return _parse_jsonl(text)
        if path.suffix == ".toml":
            if tomllib is None:
                return _UNSUPPORTED
            return tomllib.loads(text)
        return yaml.safe_load(text)
    # JSONDecodeError and TOMLDecodeError are both ValueError subclasses.
    except (ValueError, yaml.YAMLError, RecursionError):
        return None


def _is_non_artifact(name: str) -> bool:
    """True for a file that lives under evals/ but is not an eval artifact.

    `.gitkeep` is the whole point: git cannot track an empty directory, so a skill
    that honestly has no evals yet but wants the directory present can only express
    it this way. A README is documentation about the (absent) evals, not an eval.

    README matching is EXTENSION-aware, not a bare name prefix: a README carries
    .md, .txt, or no extension. `README-cases.json` is a case set that happens to
    start with those six letters, and a prefix match classified a real two-sided
    suite as `none`.
    """
    if name in _NON_ARTIFACT_NAMES:
        return True
    p = Path(name)
    return p.stem.upper() == "README" and p.suffix.lower() in ("", ".md", ".txt")


def _is_run_output(rel: str) -> bool:
    """True for artifacts under evals/results/ — the OUTPUT of a run, not a case set.

    A results dump echoes the polarity of the cases it ran. Grading it as input
    lets a stale run certify a case set that has since been deleted.
    """
    return "results" in Path(rel).parts[:-1]


def _skill_eval_artifacts(skill_dir: Path) -> list[str]:
    """Every file under the skill's evals/ dir (recursive). Presence only.

    Scoped to evals/ deliberately: a `scripts/run-evals.sh` runner is not a case
    set, and misreading one as a vacuous eval would fail a hard gate on a skill
    that never claimed coverage.
    """
    d = skill_dir / "evals"
    if not d.is_dir():
        return []
    return sorted(
        str(f.relative_to(skill_dir))
        for f in d.rglob("*")
        if f.is_file() and "__pycache__" not in f.parts
    )


def classify_eval_coverage(skill_dir: Path) -> dict:
    """Classify one skill's trigger-eval state into exactly one of EVAL_STATES.

      none                — no evals/ dir, or it holds only placeholder files
                            (.gitkeep, .DS_Store, a README)
      no_trigger_eval     — a real, parseable artifact that uses NO trigger keys:
                            a behavioural/scenario/results suite of another shape.
                            Reported, never gated — it makes no claim to miss.
      present_but_vacuous — the artifact REACHES FOR the trigger keys and misses:
                            one-sided, empty-valued, self-contradictory, or
                            ungradable. The only gated state.
      covered             — >=1 positive AND >=1 negative trigger assertion over
                            DISTINCT cases

    One-sided stays in present_but_vacuous: a positives-only set cannot detect
    over-firing, and over-firing is the failure a skill description actually has.
    Ungradable stays there too — fail closed — but only when it is the ONLY thing
    in evals/. A gradable sibling outranks it in BOTH directions: `covered` wins,
    and so does `no_trigger_eval`. The gate must catch a false claim of coverage,
    not let one stray file gate the real suite standing next to it. The stray is
    still named, appended to the winning class's reason.
    """
    files = _skill_eval_artifacts(skill_dir)
    artifacts = [f for f in files if not _is_non_artifact(Path(f).name)]
    placeholders = [f for f in files if _is_non_artifact(Path(f).name)]

    base = {"artifacts": artifacts, "positive": 0, "negative": 0, "trigger_keys": 0,
            "contradictory": 0, "other_assertions": 0, "depth_capped": 0}

    if not artifacts:
        reason = ("no evals/ artifact" if not placeholders else
                  f"evals/ holds only non-artifact file(s): {', '.join(placeholders[:3])}")
        return {**base, "state": "none", "reason": reason}

    graded, run_output = [], []
    for rel in artifacts:
        if Path(rel).suffix not in EVAL_DATA_EXTS:
            continue
        (run_output if _is_run_output(rel) else graded).append(rel)

    counts = _new_eval_counts()
    unreadable: list[str] = []
    unparseable: list[str] = []
    unsupported: list[str] = []
    parsed: list[str] = []
    for rel in graded:
        data = _parse_eval_artifact(skill_dir / rel)
        if data is _UNREADABLE:
            unreadable.append(rel)
            continue
        if data is _UNSUPPORTED:
            unsupported.append(rel)
            continue
        if data is None:
            unparseable.append(rel)
            continue
        parsed.append(rel)
        before = counts["depth_capped"]
        _tally_assertions(data, counts)
        if counts["depth_capped"] > before:
            # Silently under-counting is how a deep artifact could lose its only
            # negative case and read as one-sided. Say so.
            print(f"skill-audit: warning: {skill_dir.name}/{rel}: nesting exceeds "
                  f"{_MAX_EVAL_DEPTH} levels — {counts['depth_capped'] - before} branch(es) "
                  f"not graded, trigger assertions may be under-counted", file=sys.stderr)

    pos, neg = counts["positive"], counts["negative"]
    out = {**base, "positive": pos, "negative": neg,
           "trigger_keys": counts["trigger_keys"],
           "contradictory": counts["contradictory"],
           "other_assertions": counts["other"],
           "depth_capped": counts["depth_capped"]}

    # Artifacts that could not be graded at all. Built once: the same list decides
    # the class when it is ALL there is, and is otherwise appended to whatever the
    # gradable artifacts said, so a stray file is never silently dropped.
    ungradable_bits = []
    if unreadable:
        ungradable_bits.append(f"unreadable (IO error): {', '.join(unreadable[:3])}")
    if unparseable:
        ungradable_bits.append(f"empty or unparseable: {', '.join(unparseable[:3])}")
    stray_note = ("" if not ungradable_bits else
                  " (plus ungradable sibling(s) — " + "; ".join(ungradable_bits) + ")")

    if pos and neg:
        return {**out, "state": "covered",
                "reason": f"{pos} positive / {neg} negative trigger assertion(s) over distinct "
                          f"cases{stray_note}"}

    if counts["trigger_keys"]:
        if pos or neg:
            have, missing = ("positive", "negative") if pos else ("negative", "positive")
            misses = "over-firing" if pos else "under-firing"
            reason = (f"{pos + neg} {have} trigger assertion(s) but no {missing} case "
                      f"— one-sided evals cannot catch {misses}")
        elif counts["contradictory"]:
            reason = (f"{counts['contradictory']} self-contradictory case(s) asserting both "
                      f"polarities at once — one case is one side, not both")
        else:
            reason = (f"{counts['trigger_keys']} trigger key(s), no value that carries a "
                      f"polarity (empty collection, placeholder string, or a schema/type node) "
                      f"— a placeholder is not an assertion")
        if counts["contradictory"] and (pos or neg):
            reason += f" ({counts['contradictory']} self-contradictory case(s) counted as neither)"
        return {**out, "state": "present_but_vacuous", "reason": reason}

    # Precedence: an ungradable artifact decides the class only when it is the ONLY
    # thing here. This is the same rule `covered` already got above — a real graded
    # suite standing next to a stray unparseable file IS that suite. Ranked first
    # instead, one 0-byte todo.json gated a fully graded 34-assertion behavioural
    # suite as a vacuous TRIGGER claim the entry's own trigger_keys (0) says it
    # never made.
    if ungradable_bits and not (counts["other"] or parsed):
        return {**out, "state": "present_but_vacuous",
                "reason": "eval artifact(s) could not be graded — " + "; ".join(ungradable_bits)}

    if counts["other"]:
        reason = (f"{counts['other']} non-trigger assertion(s) across {len(parsed)} artifact(s) "
                  f"— a real eval suite of another shape, not a trigger-coverage claim")
    elif parsed:
        reason = (f"{len(parsed)} structured artifact(s) with no trigger keys "
                  f"— no trigger-coverage claim to gate")
    elif unsupported:
        reason = (f"{len(unsupported)} artifact(s) this interpreter cannot parse: "
                  f"{', '.join(unsupported[:3])} (.toml needs python 3.11+)")
    elif run_output:
        reason = (f"{len(run_output)} run-output artifact(s) under evals/results/ "
                  f"— the output of a run, not a case set")
    else:
        reason = (f"{len(artifacts)} eval artifact(s), none JSON/JSONL/YAML/TOML: "
                  f"{', '.join(artifacts[:3])}")
    return {**out, "state": "no_trigger_eval", "reason": reason + stray_note}


def detect_eval_coverage(skills: list[dict]) -> dict:
    """{state -> [entry]} for every audited skill — skillify step 5 (BRO-2005).

    The latent counterpart to report 6. Report 6 asks whether the script a skill
    runs is tested; this asks whether the description that makes the skill fire at
    all is asserted — in both directions.
    """
    out: dict[str, list[dict]] = {s: [] for s in EVAL_STATES}
    for s in skills:
        skill_dir = Path(s["path"]).parent
        info = classify_eval_coverage(skill_dir)
        out[info["state"]].append({
            "name": s["name"], "dir": str(skill_dir), "reason": info["reason"],
            "artifacts": info["artifacts"],
            "positive": info["positive"], "negative": info["negative"],
            "trigger_keys": info["trigger_keys"], "contradictory": info["contradictory"],
            "other_assertions": info["other_assertions"],
            "depth_capped": info["depth_capped"],
        })
    for state in out:
        out[state].sort(key=lambda x: x["name"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(prog="bstack skills audit", description="Skill registry audit.")
    ap.add_argument("--roots", action="append", default=[], help="Additional skill root (repeatable).")
    ap.add_argument("--budget-tokens", type=int, default=20000, help="Token budget ceiling (default 20000 = 2%% of 1M).")
    ap.add_argument("--chars-per-token", type=int, default=4, help="Token-cost divisor (default 4).")
    ap.add_argument("--months", type=int, default=3, help="Usage-trace window for unused detection (default 3).")
    ap.add_argument("--no-logs", action="store_true", help="Skip usage-trace scanning.")
    ap.add_argument("--require-tests", action="store_true",
                    help="Gate: exit 1 if any skill ships deterministic code without tests (skillify step 3, BRO-1411).")
    ap.add_argument("--require-evals", action="store_true",
                    help="Gate: exit 1 if any skill ships an evals/ artifact that USES trigger keys "
                         "without establishing two-sided coverage, or whose evals/ holds nothing "
                         "gradable at all (skillify step 5, BRO-2005). Skills "
                         "with no evals (none) and skills whose evals are a real suite of another shape "
                         "(no_trigger_eval) are NOT gated — deleting an eval suite is never the fix.")
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

    # 7. Trigger-eval coverage (skillify step 5 — the latent half)
    eval_cov = detect_eval_coverage(skills)
    vacuous = eval_cov["present_but_vacuous"]

    # The eval gate fires on present_but_vacuous ONLY — never on `none`, never on
    # `no_trigger_eval`. `none` is the day-one baseline for nearly the whole roster
    # and an absence is honest. `no_trigger_eval` is a real suite of another shape
    # that never claimed trigger coverage; gating it would make `rm -rf evals/` the
    # cheapest way to go green, and a gate whose compliant move is deleting real
    # work is worse than no gate. What remains — an artifact that reaches for the
    # trigger keys one-sidedly, emptily, contradictorily, or unreadably — is a
    # standing claim of coverage that isn't there: exactly the state that let the
    # stack report ~10% coverage with 0 assertions. Same shape as --require-tests,
    # which also exempts the honest absence (markdown-only skills).
    gate_failed = bool(args.require_tests and untested) or bool(args.require_evals and vacuous)

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
            "eval_coverage": {
                "counts": {s: len(eval_cov[s]) for s in EVAL_STATES},
                **{s: eval_cov[s] for s in EVAL_STATES},
            },
            "require_evals": bool(args.require_evals),
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
    other_shape = eval_cov["no_trigger_eval"]
    print(f"## Eval coverage  [{len(eval_cov['covered'])} covered / {len(vacuous)} vacuous / "
          f"{len(other_shape)} no-trigger-eval / {len(eval_cov['none'])} none]")
    # The gated class has two sub-states and they need different words. An entry
    # with trigger_keys == 0 (a lone empty/unparseable/unreadable artifact) reached
    # for nothing, so "uses trigger keys" and "add the missing side" would both be
    # false OF THAT ENTRY — and its own reported trigger_keys says so. Split the
    # header and the advice on the same predicate the JSON exposes.
    claimed = [v for v in vacuous if v["trigger_keys"]]
    ungradable = [v for v in vacuous if not v["trigger_keys"]]
    if vacuous:
        print(f"  present_but_vacuous ({len(vacuous)}):")
        if claimed:
            print(f"    uses trigger keys without reaching two-sided coverage ({len(claimed)}):")
            for v in claimed:
                print(f"      {v['name']}: {v['reason']}")
        if ungradable:
            print(f"    ships an evals/ artifact that is not two-sided coverage ({len(ungradable)}):")
            for v in ungradable:
                print(f"      {v['name']}: {v['reason']}")
    if other_shape:
        print(f"  no_trigger_eval ({len(other_shape)}) — a real eval suite of another shape "
              f"(no trigger keys; never gated):")
        for o in other_shape:
            print(f"    {o['name']}: {o['reason']}")
    if eval_cov["covered"]:
        print(f"  covered ({len(eval_cov['covered'])}): "
              f"{', '.join(c['name'] for c in eval_cov['covered'])}")
    print(f"  none ({len(eval_cov['none'])}) — no evals/ artifact (honest absence; never gated)")
    if vacuous:
        if args.require_evals:
            print(f"  ⚠ {len(vacuous)} skill(s) ship an evals/ artifact that does not establish "
                  f"two-sided trigger coverage — --require-evals gate FAILED")
            if claimed:
                print(f"    {len(claimed)}: add the missing side — the artifact uses trigger keys "
                      f"but does not assert both directions over distinct cases")
            if ungradable:
                print(f"    {len(ungradable)}: make the artifact gradable — it is empty, "
                      f"unparseable, or unreadable, so it asserts nothing in either direction")
            print("    deleting evals/ is NOT a fix "
                  "(an eval suite of another shape is reported as no_trigger_eval and never gated)")
        else:
            print("  (informational — pass --require-evals to gate CI on this)")
    print()
    print("## Roots")
    for r, c in sorted(root_counts.items()):
        print(f"  {c:3d}  {r}")
    return 1 if gate_failed else 0


if __name__ == "__main__":
    sys.exit(main())
