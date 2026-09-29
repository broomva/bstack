#!/usr/bin/env python3
"""hook_python_sites.py — every python call site a bstack hook can reach (BRO-2652).

A hook runs with the session's cwd: whatever repository the session is in. A bare
`python3 -` or `python3 -c` puts that directory first on sys.path, and
`python3 FILE` puts FILE's directory there, so a json.py or re.py in it replaces the
standard library module and runs as code. Every site below must say `python3 -I`.

One source of truth for two tests:
  tests/hook-python-isolation.test.sh           static completeness check (`check`)
  tests/hook-python-isolation-mutation.test.sh  one mutant per isolated site (`sites`)

Usage (ROOT is a bstack checkout or a copy of one):
  hook_python_sites.py ROOT reachable   the hook-reachable files, one per line
  hook_python_sites.py ROOT sites       path<TAB>line<TAB>isolated(0|1)<TAB>text
  hook_python_sites.py ROOT check       exit 1 on any un-isolated site not listed in
                                        ALLOWED_UNISOLATED, or a stale entry there

Reachable = every file hooks/hooks.json names through ${CLAUDE_PLUGIN_ROOT}, every
workspace hook `bstack bootstrap` deploys (WORKSPACE_HOOKS in scripts/bootstrap.sh),
and, transitively, every bstack file those name: in shell, any foo.sh / foo.py /
bin/foo on a non-comment line; in python, an imported sibling module or a quoted
"foo.py" (a sibling loaded by path). A root that cannot be read fails the check: the
list must never shrink in silence.
"""
import json
import os
import re
import sys

SEARCH_DIRS = ("scripts", "scripts/lib", "hooks")

# A file that NAMES another only in a message it prints. Following the edge would put
# a script the hook never runs into the reachable set (and into the mutation matrix,
# where no hook event could ever kill its mutant).
TEXT_ONLY_MENTIONS = {
    ("scripts/l3-stability-pretool-hook.sh", "scripts/compute-lambda.sh"):
        "named in the advisory reason the hook prints; the hook never runs it",
    ("scripts/bstack-autoupdate-hook.sh", "bin/bstack"):
        "named in the upgrade guidance the hook prints; the hook never runs it",
}

# Sites deliberately left without -I. Key: (path, the invocation text from `python3`
# through its first argument). Each needs a reason a reviewer can check.
ALLOWED_UNISOLATED = {
    ("scripts/conversation-bridge-hook.sh", 'python3 "$BOOKKEEPING"'):
        "runs the bookkeeping skill (not part of this repo). bookkeeping.py puts its "
        "own directory first on sys.path itself (sys.path.insert(0, _SCRIPTS_DIR)), so "
        "-I here would not isolate it, and it would hide the user-site packages it "
        "imports lazily (PyYAML). cwd is not on sys.path for python3 FILE",
}

# `python3` as a command word, then its first argument.
INVOKE_RE = re.compile(r"(?<![\w./-])(?:exec\s+)?python3?(?:\.\d+)?(?=\s)\s+(\S+)")
PY_SPAWN_RE = re.compile(r"sys\.executable|[\"']python3?(?:\.\d+)?[\"']")


def _read(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
        return f.read()


def _resolve(root, name):
    if name.startswith("bin/"):
        return name if os.path.isfile(os.path.join(root, name)) else None
    for d in SEARCH_DIRS:
        rel = f"{d}/{name}"
        if os.path.isfile(os.path.join(root, rel)):
            return rel
    return None


def roots(root):
    out = ["hooks/hooks.json"]
    data = json.loads(_read(root, "hooks/hooks.json"))
    for blocks in data["hooks"].values():
        for block in blocks:
            for hook in block["hooks"]:
                out += re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w./-]+)", hook["command"])
    m = re.search(r"^WORKSPACE_HOOKS=\(([^)]*)\)", _read(root, "scripts/bootstrap.sh"), re.M)
    if not m:
        raise SystemExit("hook_python_sites: WORKSPACE_HOOKS=(...) not found in scripts/bootstrap.sh")
    out += [f"scripts/{n}" for n in m.group(1).split()]
    missing = [r for r in out if not os.path.isfile(os.path.join(root, r))]
    if missing:
        raise SystemExit(f"hook_python_sites: roots do not exist: {missing}")
    return out


def references(root, rel):
    text = _read(root, rel)
    names = set()
    if rel.endswith(".py"):
        for m in re.finditer(r"^\s*(?:from\s+\.?(\w+)\s+import|import\s+(\w+))", text, re.M):
            names.add((m.group(1) or m.group(2)) + ".py")
        names.update(re.findall(r"[\"']([\w-]+\.py)[\"']", text))
    else:
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            names.update(re.findall(r"([\w-]+\.(?:sh|py))\b", line))
            names.update("bin/" + n for n in re.findall(r"\bbin/([\w-]+)", line))
    found = set()
    for n in names:
        target = _resolve(root, n)
        if target and target != rel and (rel, target) not in TEXT_ONLY_MENTIONS:
            found.add(target)
    return found


def reachable(root):
    seen, todo = [], list(roots(root))
    while todo:
        rel = todo.pop(0)
        if rel in seen:
            continue
        seen.append(rel)
        todo.extend(sorted(references(root, rel) - set(seen)))
    return sorted(seen)


def sites(root):
    """(path, line, isolated, text) for every python invocation in a reachable file."""
    out = []
    for rel in reachable(root):
        lines = _read(root, rel).splitlines()
        for i, line in enumerate(lines, 1):
            if rel.endswith(".py"):
                if PY_SPAWN_RE.search(line) and not line.lstrip().startswith("#"):
                    window = " ".join(lines[i - 1:i + 2])
                    iso = "'-I'" in window or '"-I"' in window
                    out.append((rel, i, iso, line.strip()))
                continue
            if line.lstrip().startswith("#"):
                continue
            for m in INVOKE_RE.finditer(line):
                if line[:m.start()].rstrip().endswith("command -v"):
                    continue
                arg = m.group(1)
                if not (arg[0] in "-\"'$\\" or "/" in arg or arg.endswith(".py")):
                    continue  # prose: "python3 required", "python3 not available"
                text = m.group(0).replace("exec ", "", 1).strip()
                out.append((rel, i, arg == "-I", text))
    return out


def _key(rel, text):
    return (rel, text.replace('\\"', '"'))


def check(root):
    bad, used = [], set()
    for rel, line, iso, text in sites(root):
        if iso:
            continue
        k = _key(rel, text)
        if k in ALLOWED_UNISOLATED:
            used.add(k)
            print(f"  allowed without -I: {rel}:{line}: {text} ({ALLOWED_UNISOLATED[k]})")
            continue
        bad.append(f"{rel}:{line}: {text}")
    stale = [f"{p}: {t}" for (p, t) in ALLOWED_UNISOLATED if (p, t) not in used]
    for b in bad:
        print(f"  NOT ISOLATED: {b}")
    for s in stale:
        print(f"  STALE ALLOWED_UNISOLATED entry (no such site): {s}")
    return 1 if (bad or stale) else 0


def main(argv):
    if len(argv) != 3 or argv[2] not in ("reachable", "sites", "check"):
        print(__doc__, file=sys.stderr)
        return 2
    root, mode = argv[1], argv[2]
    if mode == "reachable":
        print("\n".join(reachable(root)))
        return 0
    if mode == "sites":
        for rel, line, iso, text in sites(root):
            print(f"{rel}\t{line}\t{int(iso)}\t{text}")
        return 0
    return check(root)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
