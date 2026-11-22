#!/usr/bin/env python3
"""check_control_readers.py — ratchet the code files that read `.control` on one key.

The unified-layout ADR (BRO-3200) renames a repo's shared state from `.control/`
to `.broomva/`. Until a reader goes through the path registry it must name both.
This check answers one question per tracked code file: does every `.control/<X>`
reference have a matching `.broomva/<X>`?

  unpaired  some `.control/<X>` has no `.broomva/<X>` in the same file: a reader not
            yet switched to the registry, or a gate / tree reader not dual-keyed.
  paired    every `.control/<X>` is matched: dual-keyed for the move.

A reader switched to the registry names neither and leaves the count.

Pairing is by SUFFIX, not by "the file mentions both names":
  - `join(x, ".control", "asks")` and `".control/asks"` both normalise to `asks`;
  - machine-scope mentions (`~/.broomva`, `$HOME/.broomva`, `BROOMVA_HOME`) are
    ignored, as is `.broomva/loops`, which is not a renamed path;
  - a bare `.control` (the directory itself) pairs with a bare `.broomva`.

The ratchet is on the SET in scripts/control_readers_baseline.json: a reader may
leave, none may be added. Only tracked code is counted (by extension), read from
the checkout.

Ported from broomva/workspace (scripts/control_readers_count.py for the
classifier, scripts/check_control_readers.py for the ratchet) into one
standard-library file. The classifier is the workspace's, unchanged, so the two
repos count the same way; it has no exclusion for files that describe both names
on purpose, and this file is itself counted (its selftest strings below are
unpaired by design) and sits in the baseline, as the workspace's does.

Usage: python3 scripts/check_control_readers.py [--selftest | --print-baseline]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts/control_readers_baseline.json"

CODE_EXT = {".py", ".sh", ".bash", ".zsh", ".ts", ".tsx", ".js", ".mjs", ".cjs",
            ".rs", ".go", ".yml", ".toml", ".template"}
CODE_NAMES = {"Makefile", "pre-commit", "pre-push", "commit-msg"}
SKIP_PARTS = ("docs/", "research/", "archive/", ".control/", ".broomva/", "node_modules/")

# `.control` followed by `/suffix`, or a quoted `.control` segment followed by
# further quoted segments (join(..., ".control", "asks")), or a bare final segment.
SLASH = re.compile(r"\.(control|broomva)/([A-Za-z0-9_.\-]+)")
JOINED = re.compile(r"""["'`]\.(control|broomva)["'`]\s*,\s*["'`]([A-Za-z0-9_.\-]+)["'`]""")
BARE = re.compile(r"""[/"'`]\.(control|broomva)(?=["'`]|$|\s)""", re.M)
# Machine scope: these name ~/.broomva, never a repo root.
MACHINE = re.compile(r"""(~|HOME\}?|HOME["']?\s*[,)]|home\(\)|homedir\(\))\s*/?\s*\.broomva""")
NOT_RENAMED = {"loops"}


def refs(text: str) -> tuple[set[str], set[str]]:
    """Suffixes referenced under each root; "" stands for the bare directory."""
    control: set[str] = set()
    broomva: set[str] = set()
    machine_spans = [m.span() for m in MACHINE.finditer(text)]

    def is_machine(pos: int) -> bool:
        return any(a <= pos < b + 1 for a, b in machine_spans)

    for rx in (SLASH, JOINED):
        for m in rx.finditer(text):
            root, suffix = m.group(1), m.group(2)
            if root == "control":
                control.add(suffix)
            elif not is_machine(m.start()) and suffix not in NOT_RENAMED:
                broomva.add(suffix)
    # A quoted segment that a JOINED match already read with its suffix is not
    # also a bare-directory reference.
    joined_spans = [m.span() for m in JOINED.finditer(text)]
    for m in BARE.finditer(text):
        if any(a <= m.start() < b for a, b in joined_spans):
            continue
        root = m.group(1)
        if root == "control":
            control.add("")
        elif not is_machine(m.start()):
            broomva.add("")
    return control, broomva


def is_code(path: str) -> bool:
    if any(part in path for part in SKIP_PARTS) or path.startswith((".control", ".broomva")):
        return False
    name = path.rsplit("/", 1)[-1]
    return name in CODE_NAMES or Path(name).suffix in CODE_EXT


def readers(root: Path) -> set[str]:
    """Tracked code files under ``root`` with an unpaired `.control` reference."""
    result = subprocess.run(
        ["git", "-C", str(root), "grep", "-lzE", r"\.control([/\"'`]|$)", "--"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.decode(errors="replace"))
    found = set()
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", errors="surrogateescape")
        if not is_code(path):
            continue
        text = (root / path).read_text(encoding="utf-8", errors="replace")
        control, broomva = refs(text)
        if control and not control <= broomva:
            found.add(path)
    return found


SELFTEST = (
    # (text, expected) — a positive control: a zero "paired" count means nothing
    # unless the classifier can say "paired" at all.
    ('read(".control/asks"); read(".broomva/asks")', "paired"),
    ('p = join(r, ".control", "asks"); q = ".broomva/asks/x"', "paired"),
    ('[ -d "$W/.control" ] || [ -d "$W/.broomva" ]', "paired"),
    ('read(".control/asks"); home = "~/.broomva/asks"', "unpaired"),
    ('read(".control/policy.yaml"); loops = ".broomva/loops"', "unpaired"),
    ('ASKS = ".control/asks"; DEFAULT = HOME + "/.broomva"', "unpaired"),
    ('read(".control/asks"); read(".broomva/initiatives")', "unpaired"),
)


def selftest() -> int:
    bad = 0
    for text, want in SELFTEST:
        control, broomva = refs(text)
        got = "paired" if control and control <= broomva else "unpaired"
        if got != want:
            print(f"[FAIL] {text!r}: {got}, wanted {want}")
            bad += 1
    print(f"selftest: {len(SELFTEST) - bad}/{len(SELFTEST)}")
    return 1 if bad else 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    current = readers(ROOT)
    if "--print-baseline" in sys.argv:
        # Prints only; the committed file is replaced by hand so a widening shows in review.
        print(json.dumps({"count": len(current), "readers": sorted(current)}, indent=2))
        return 0
    baseline = json.loads(BASELINE.read_text())
    expected = set(baseline["readers"])
    if len(expected) != baseline["count"]:
        raise ValueError("baseline count does not match its reader list")
    count = len(current)
    limit = baseline["count"]
    print(f"Unpaired readers: {count}; baseline: {limit}")
    # The ratchet is on the set, not the number: a reader may leave, none may be added. Comparing counts alone
    # would let a new reader in whenever an old one was removed in the same change.
    new = sorted(current - expected)
    if new:
        print("New readers since baseline:")
        for path in new:
            print(f"  {path}")
        return 1
    if count < limit:
        print(f"Reader count fell to {count}; tighten the baseline.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"Reader check failed: {exc}", file=sys.stderr)
        sys.exit(2)
