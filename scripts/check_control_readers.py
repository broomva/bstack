#!/usr/bin/env python3
"""check_control_readers.py — ratchet the code files that mention the legacy `.control` folder.

The contract, in two sentences: the ratchet lists every tracked code file that
mentions the legacy folder name `.control` at all; a pull request may not add a
file to that list.

New code spells neither folder name (`.control` nor `.broomva`) and resolves
paths through the generated path reader: scripts/broomva_paths.py,
scripts/lib/broomva-paths.sh or scripts/lib/broomva_paths.rs. Existing mentions
are grandfathered in scripts/control_readers_baseline.json, and the list only
shrinks. A file that names both folders is listed like any other: nothing pairs
a `.control` mention with a `.broomva` one.

What counts as a MENTION is one pattern, MENTION below, used for every file.

This checker counts files that MENTION `.control`. That is not the "unpaired
readers" count of the workspace's sensor (scripts/control_readers_count.py in
broomva/workspace), and the two numbers are not comparable.

Which files are code: tracked files with a code extension or name (CODE_EXT,
CODE_NAMES), except those under a directory named exactly `docs`, `research`,
`archive`, `.control`, `.broomva` or `node_modules` at any depth (whole path
components: `scripts/mydocs/x.py` is code).

On a pull request CI also passes `--base-ref <base>`: the PR's baseline may not
contain a path the base branch's baseline lacks, so a change cannot add a file
and write it into the baseline at once. The one exception is a rename: an added
path is allowed when the same change drops a baseline entry and
`git diff --name-status -M <base>...HEAD` reports the drop renamed to the
addition. If the base has no baseline file yet (the change that first introduces
it), the comparison is skipped with a note and only the set check applies.

Limits. It is a ratchet against drift, not a defence against someone evading it:
  - a mention assembled from pieces (`"." + "control"`, an f-string, a variable
    holding the name) is not found; review covers those;
  - a comment counts: a code file whose comment mentions the folder is listed.
    Reword the comment.

This file is itself listed (it has to spell the name it looks for) and sits in
the baseline.

Usage: python3 scripts/check_control_readers.py [--selftest | --print-baseline | --base-ref REF]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = "scripts/control_readers_baseline.json"
BASELINE = ROOT / BASELINE_PATH
HINT = (
    "A file listed here mentions the legacy folder name `.control` and is not in the\n"
    "baseline. Do not spell the folder name: resolve the path through the generated\n"
    "path reader (scripts/broomva_paths.py, scripts/lib/broomva-paths.sh,\n"
    "scripts/lib/broomva_paths.rs). Naming `.broomva` as well does not help; any\n"
    "mention counts, comments included (reword the comment). Do not add the file to\n"
    "the baseline. A mention assembled from pieces is not found; review covers that.\n"
    "The base comparison is skipped, with a note, only when the base branch has no\n"
    "baseline file yet (the change that first introduces it)."
)

CODE_EXT = {".py", ".sh", ".bash", ".zsh", ".ts", ".tsx", ".js", ".mjs", ".cjs",
            ".rs", ".go", ".yml", ".toml", ".template"}
CODE_NAMES = {"Makefile", "pre-commit", "pre-push", "commit-msg"}
# Directory names skipped wherever they occur in a path, matched as WHOLE path
# components: `a/docs/x.py` is skipped, `scripts/mydocs/x.py` and
# `archived/x.py` are not.
SKIP_DIRS = frozenset({"docs", "research", "archive", ".control", ".broomva", "node_modules"})

# The one definition of a MENTION of the legacy folder: the text `.control` where
#   left:  the dot starts the text, or follows a character that is not a letter,
#          digit or `_` (any script, \w) and not `)` or `]`; so `self.control` and
#          `x).control` are attribute access, not the folder;
#   right: `control` is followed by `/`, a single or double quote, a backtick, a
#          `:` (a PATH-style list, `$HOME/.control:$PATH`), or the end of a line;
#          so `.controller` and `.control(` are not mentions.
MENTION = re.compile(r"""(?<![\w)\]])\.control(?=[/'"`:]|$)""", re.M)


def mentions(text: str) -> bool:
    return MENTION.search(text) is not None


def is_code(path: str) -> bool:
    *dirs, name = path.split("/")
    if SKIP_DIRS.intersection(dirs):
        return False
    return name in CODE_NAMES or Path(name).suffix in CODE_EXT


def readers(root: Path) -> set[str]:
    """Tracked code files under ``root`` that mention the legacy folder."""
    result = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    found = set()
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", errors="surrogateescape")
        if not is_code(path) or not (root / path).is_file():
            continue  # a tracked file deleted in the checkout reads nothing
        if mentions((root / path).read_text(encoding="utf-8", errors="replace")):
            found.add(path)
    return found


SELFTEST = (
    # (text, mentions?) — both answers must be reachable, or a zero means nothing.
    ('read(".control/asks")', True),
    ('join(r, ".control", "asks")', True),
    ('[ -d "$W/.control" ]', True),
    ('read(".control/asks"); read(".broomva/asks")', True),
    ('self.control = 1', False),
    ('x.controller("a/b")', False),
    ('read(".broomva/asks")', False),
)


def selftest() -> int:
    bad = 0
    for text, want in SELFTEST:
        if mentions(text) != want:
            print(f"[FAIL] {text!r}: {not want}, wanted {want}")
            bad += 1
    print(f"selftest: {len(SELFTEST) - bad}/{len(SELFTEST)}")
    return 1 if bad else 0


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def load_baseline(text: str, where: str) -> set[str]:
    baseline = json.loads(text)
    readers_list = baseline["readers"]
    if len(set(readers_list)) != baseline["count"]:
        raise ValueError(f"{where}: baseline count does not match its reader list")
    return set(readers_list)


def renames(base: str) -> dict[str, str]:
    """Destination -> source for each file this change renamed since ``base``."""
    out = git("diff", "--name-status", "-z", "-M", f"{base}...HEAD", "--")
    if out.returncode != 0:
        raise RuntimeError(f"git diff {base}...HEAD failed: {out.stderr.strip()}")
    fields = out.stdout.split("\0")
    moved = {}
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        if status.startswith(("R", "C")):
            if status.startswith("R"):
                moved[fields[i + 2]] = fields[i + 1]
            i += 3
        else:
            i += 2
    return moved


def compare_with_base(base: str, expected: set[str]) -> int:
    """Fail when this change's baseline gained a path the base's baseline lacks."""
    if git("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").returncode != 0:
        raise RuntimeError(f"base ref {base!r} is not a commit in this checkout (fetch more history)")
    shown = git("show", f"{base}:{BASELINE_PATH}")
    if shown.returncode != 0:
        print(f"Note: {BASELINE_PATH} does not exist at {base}; base comparison skipped "
              "(this change introduces the baseline).")
        return 0
    before = load_baseline(shown.stdout, f"{base}:{BASELINE_PATH}")
    removed = before - expected
    moved = renames(base)
    widened = []
    for path in sorted(expected - before):
        source = moved.get(path)
        if source in removed:
            removed.discard(source)  # one dropped entry pays for one renamed addition
            print(f"Baseline entry renamed: {source} -> {path}")
        else:
            widened.append(path)
    if widened:
        print(f"Baseline gained files that {base} does not list (the baseline may only shrink):")
        for path in widened:
            print(f"  {path}")
        print(HINT)
        return 1
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    base = None
    if "--base-ref" in sys.argv:
        index = sys.argv.index("--base-ref")
        if index + 1 >= len(sys.argv) or not sys.argv[index + 1]:
            raise ValueError("--base-ref needs a ref")
        base = sys.argv[index + 1]
    current = readers(ROOT)
    if "--print-baseline" in sys.argv:
        # Prints only; the committed file is replaced by hand so a widening shows in review.
        print(json.dumps({"count": len(current), "readers": sorted(current)}, indent=2))
        return 0
    expected = load_baseline(BASELINE.read_text(), BASELINE_PATH)
    count = len(current)
    limit = len(expected)
    print(f"Files mentioning .control: {count}; baseline: {limit}")
    status = 0
    # The ratchet is on the set, not the number: a file may leave, none may be added. Comparing counts alone
    # would let a new file in whenever an old one was removed in the same change.
    new = sorted(current - expected)
    if new:
        print("New files mentioning .control since baseline:")
        for path in new:
            print(f"  {path}")
        print(HINT)
        status = 1
    elif count < limit:
        print(f"Count fell to {count}; tighten the baseline.")
    # The baseline lives in the change under review, so on a pull request it is
    # itself compared with the base branch's copy.
    if base is not None and compare_with_base(base, expected):
        status = 1
    return status


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"Reader check failed: {exc}", file=sys.stderr)
        sys.exit(2)
