#!/usr/bin/env python3
"""ensure-hard-gates.py POLICY_FILE — give an older policy file its `gates.hard` list.

The rule runner reads the policy's `gates.hard` list and treats a policy without one as
unreadable. bstack's policy template had no such key before 0.53.0, so `bstack bootstrap`
runs this on the workspace's policy file before it writes the rule registry. Nothing else
runs it: `bstack rules init` and `bstack rules check` never change the policy file.

What it does, and all it does: when the file is a regular, writable file with LF line
endings that parses, `gates` is a block mapping and has no `hard` key, it inserts
`hard: []` with a comment as the first entry under `gates:`. The
`gates:` line itself, with any comment on it, is kept. The new text is parsed again and
written only when the one difference from the old document is the new empty list. It goes
to a temporary file beside the policy, which then replaces it, with the policy's own
permissions: a write that fails part-way leaves the policy as it was.

Exit status: 0 nothing to do, or the key was added (it says so); 3 the file was left alone
and the reason is on stderr (a symbolic link, a read-only file, CRLF line endings, no
`gates:` mapping, a form this script does not edit, a file that does not parse, PyYAML
missing); 2 the file could not be read or written.
"""
import os
import re
import site
import sys
import tempfile

ADDED = ("  # Hard gates the rule registry must account for: a gate with a `pattern` needs a row\n"
         "  # that replays it (`policy_gate:`). Added by bstack bootstrap; the control gate's own\n"
         "  # patterns are embedded in scripts/control-gate-hook.sh and replayed by the rows of\n"
         "  # bstack's references/rules-pack.yaml.\n"
         "  hard: []\n")


def left_alone(path: str, why: str) -> int:
    print(f"  {path}: {why}; add `hard: []` under `gates:` by hand", file=sys.stderr)
    return 3


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    path = argv[1]
    # A link is not followed and not replaced, and a file marked read-only is not written:
    # the workspace's own path lookup refuses a linked policy file, and replacing a file
    # through its directory would override the mark on it.
    if os.path.islink(path):
        return left_alone(path, "is a symbolic link, which this script does not follow or replace")
    if os.path.isfile(path) and not os.access(path, os.W_OK):
        return left_alone(path, "is marked read-only")
    if site.getusersitepackages() not in sys.path:
        sys.path.append(site.getusersitepackages())
    try:
        import yaml
    except ImportError:
        return left_alone(path, "PyYAML is not installed, so the file was not read")
    try:
        # newline="": line endings are read as they are, so a carriage return is seen below.
        with open(path, encoding="utf-8", newline="") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"  {path}: cannot be read ({exc})", file=sys.stderr)
        return 2
    # One carriage return anywhere and the file is left alone: the lines this script adds end
    # in LF, and it does not put two kinds of line ending in one file.
    if "\r" in text:
        return left_alone(path, "has CRLF line endings, and the lines this script adds end in LF")
    try:
        before = yaml.safe_load(text)
    except yaml.YAMLError:
        return left_alone(path, "does not parse as YAML")
    if not isinstance(before, dict) or not isinstance(before.get("gates"), dict):
        return left_alone(path, "has no `gates:` mapping")
    if "hard" in before["gates"]:
        return 0
    # The `gates:` line is kept whole (group 0), comment included; the key goes after it.
    after_text, count = re.subn(r"^gates:[ \t]*(#[^\r\n]*)?\n", lambda m: m.group(0) + ADDED, text, count=1,
                                flags=re.M)
    if count != 1:
        return left_alone(path, "writes `gates:` in a form this script does not edit")
    try:
        after = yaml.safe_load(after_text)
    except yaml.YAMLError:
        return left_alone(path, "would not parse with the key added")
    if after != dict(before, gates=dict(before["gates"], hard=[])):
        return left_alone(path, "would change more than `gates.hard` with the key added")
    folder = os.path.dirname(os.path.abspath(path))
    tmp = None
    try:
        mode = os.stat(path).st_mode & 0o777
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{os.path.basename(path)}.")
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(after_text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except OSError as exc:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
        print(f"  {path}: could not be written ({exc}); it is as it was", file=sys.stderr)
        return 2
    print(f"  added `gates.hard: []` to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
