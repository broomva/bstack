#!/usr/bin/env python3
"""Generate the path-registry readers from references/paths.toml (BRO-3216).

    python3 scripts/gen_paths.py           # write the readers
    python3 scripts/gen_paths.py --check   # exit 1 if a committed reader is stale

Writes scripts/broomva_paths.py (Python 3.9+, stdlib only) and
scripts/lib/broomva-paths.sh (bash 3.2+) from the templates in
scripts/lib/paths-templates/, substituting the table. Each embeds the table and the sha256
of paths.toml, so no reader looks for the registry at run time (unified-layout
ADR, decision 2). Needs Python 3.11+ for tomllib; the readers it writes do not.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "references" / "paths.toml"
PY_OUT = ROOT / "scripts" / "broomva_paths.py"
SH_OUT = ROOT / "scripts" / "lib" / "broomva-paths.sh"

NAME = re.compile(r"^[a-z][a-z0-9_]*$")
REL = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")
# A machine legacy location: absolute or ~/, the same safe characters as REL. The
# readers paste it into generated code, so nothing outside this set may pass.
LEGACY = re.compile(r"^(~|)/[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")


class RegistryError(ValueError):
    pass


def load(raw: bytes) -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - depends on the interpreter
        raise SystemExit("gen_paths.py needs Python 3.11+ (tomllib); the readers it writes run on 3.9")
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as err:
        raise RegistryError(f"not valid TOML: {err}") from err


def _rel(name: str, value: object) -> str:
    if not isinstance(value, str) or not REL.match(value) or set(value.split("/")) & {".", ".."}:
        raise RegistryError(f"{name}: path must be relative, without `.`/`..` parts or spaces: {value!r}")
    return value


def validate(data: dict) -> tuple[list[tuple[str, str, bool, bool]], list[tuple[str, str]], list[tuple[str, str, str | None]]]:
    """The repo, repo_state and machine tables as ordered rows, or RegistryError."""
    unknown = set(data) - {"version", "roots", "repo", "repo_state", "machine"}
    if unknown:
        raise RegistryError(f"unknown tables or keys: {sorted(unknown)} (a typo would be dropped silently)")
    if type(data.get("version")) is not int or data["version"] != 1:
        raise RegistryError("version must be the integer 1")
    for table in ("roots", "repo", "repo_state", "machine"):
        if not isinstance(data.get(table, {}), dict):
            raise RegistryError(f"[{table}] must be a table, not {type(data[table]).__name__}")
    if set(data.get("roots", {})) != {"repo", "machine", "workspace"}:
        raise RegistryError("roots must hold exactly repo, machine and workspace")
    roots = data.get("roots", {})
    if roots.get("repo") != {"dir": ".broomva", "legacy": ".control"}:
        raise RegistryError("roots.repo must be {dir = \".broomva\", legacy = \".control\"}")
    if roots.get("machine") != {"env": "BROOMVA_HOME", "default": "~/.broomva"}:
        raise RegistryError("roots.machine must be {env = \"BROOMVA_HOME\", default = \"~/.broomva\"}")
    ws = roots.get("workspace", {})
    if ws.get("env") != ["BROOMVA_WORKSPACE", "MAESTRO_WORKSPACE", "BSTACK_WORKSPACE"] or ws.get("default") != "~/broomva":
        raise RegistryError("roots.workspace must list BROOMVA_WORKSPACE, MAESTRO_WORKSPACE, BSTACK_WORKSPACE")
    repo: list[tuple[str, str, bool, bool]] = []
    for name, entry in data.get("repo", {}).items():
        if not NAME.match(name) or not isinstance(entry, dict):
            raise RegistryError(f"repo.{name}: bad name or entry")
        if set(entry) - {"path", "owner", "legacy"}:
            raise RegistryError(f"repo.{name}: unknown keys {sorted(set(entry) - {'path', 'owner', 'legacy'})}")
        legacy = entry.get("legacy", True)
        owner = entry.get("owner", False)
        if not isinstance(legacy, bool) or not isinstance(owner, bool):
            raise RegistryError(f"repo.{name}: legacy and owner are booleans")
        repo.append((name, _rel(f"repo.{name}", entry.get("path")), legacy, owner))
    state: list[tuple[str, str]] = []
    for name, entry in data.get("repo_state", {}).items():
        if not NAME.match(name) or not isinstance(entry, dict) or set(entry) != {"path"}:
            raise RegistryError(f"repo_state.{name}: an entry is {{ path = ... }}")
        if name in {r[0] for r in repo}:
            raise RegistryError(f"repo_state.{name}: also a repo name; the two share one namespace")
        state.append((name, _rel(f"repo_state.{name}", entry.get("path"))))
    # Repo and repo_state paths share .control/ (legacy) and must not nest: `a` and
    # `a/b` cannot both exist when `a` is a file. Compared case-insensitively, since
    # `Policy.yaml` and `policy.yaml` are one file on APFS.
    in_repo = [r[1] for r in repo] + [r[1] for r in state]
    # One component deep: the readers' containment check covers .broomva/ and the
    # entry, so a nested path would leave an unchecked folder in between.
    for p in in_repo:
        if "/" in p:
            raise RegistryError(f"repo paths are one component deep: {p!r}")
    folded = [p.lower() for p in in_repo]
    if len(set(folded)) != len(folded):
        raise RegistryError("two repo or repo_state names share a path (compared case-insensitively)")
    for a in folded:
        for b in folded:
            if b.startswith(a + "/"):
                raise RegistryError(f"repo paths nest: {a!r} holds {b!r}")
    machine: list[tuple[str, str, str | None]] = []
    for name, entry in data.get("machine", {}).items():
        if not NAME.match(name) or not isinstance(entry, dict):
            raise RegistryError(f"machine.{name}: bad name or entry")
        if set(entry) - {"path", "legacy"}:
            raise RegistryError(f"machine.{name}: unknown keys")
        legacy = entry.get("legacy")
        if legacy is not None and (not isinstance(legacy, str) or not LEGACY.match(legacy)
                                   or set(legacy.split("/")) & {".", ".."}):
            raise RegistryError(f"machine.{name}: legacy must be an absolute or ~/ path: {legacy!r}")
        machine.append((name, _rel(f"machine.{name}", entry.get("path")), legacy))
    machine_paths = [r[1].lower() for r in machine]
    if len(set(machine_paths)) != len(machine_paths):
        raise RegistryError("machine: two names share a path (compared case-insensitively)")
    if not repo or not machine:
        raise RegistryError("repo and machine tables must not be empty")
    return repo, state, machine


def owner_paths(repo: list[tuple[str, str, bool, bool]]) -> list[str]:
    out = []
    for _, path, legacy, owner in repo:
        if owner:
            out.append(f".broomva/{path}")
            if legacy:
                out.append(f".control/{path}")
    return out


TEMPLATES = ROOT / "scripts" / "lib" / "paths-templates"


def render(raw: bytes) -> tuple[str, str]:
    repo, state, machine = validate(load(raw))
    sha = hashlib.sha256(raw).hexdigest()
    owners = owner_paths(repo)
    py_repo = "\n".join(f"    {n!r}: ({p!r}, {legacy!r}, {owner!r})," for n, p, legacy, owner in repo)
    py_machine = "\n".join(f"    {n!r}: ({p!r}, {legacy!r})," for n, p, legacy in machine)
    py_owner = "\n".join(f"    {p!r}," for p in owners)
    py_state = "\n".join(f"    {n!r}: {p!r}," for n, p in state)
    # Repo-scope names that must never sit at the top of the machine root: every repo
    # path's first part, except where a machine name already has that spelling.
    machine_top = {p.split("/")[0].lower() for _, p, _ in machine}
    never = sorted({p.split("/")[0] for _, p, _, _ in repo} - {m for m in machine_top})
    never = [n for n in never if n.lower() not in machine_top]
    py_never = "\n".join(f"    {n!r}," for n in never)
    py = ((TEMPLATES / "broomva_paths.py.tmpl").read_text().replace("@@SHA@@", sha).replace("@@REPO@@", py_repo)
          .replace("@@MACHINE@@", py_machine).replace("@@OWNER@@", py_owner)
          .replace("@@STATE@@", py_state).replace("@@NEVERMACHINE@@", py_never))
    # Patterns are quoted: a name such as `esac` or `in` must stay a word, not syntax.
    sh_repo = "\n".join(f"        '{n}') printf '%s\\n' '{p} {1 if legacy else 0} {1 if owner else 0}' ;;"
                        for n, p, legacy, owner in repo)
    sh_machine = "\n".join(f"        '{n}') printf '%s\\n' '{p} {legacy or '-'}' ;;" for n, p, legacy in machine)
    sh_legacy = " ".join(f"'{p}'" for _, p, legacy, _ in repo if legacy)
    sh_state = "\n".join(f"        '{n}') printf '%s\\n' '{p}' ;;" for n, p in state)
    sh = ((TEMPLATES / "broomva-paths.sh.tmpl").read_text().replace("@@SHA@@", sha)
          .replace("@@OWNER@@", " ".join(owners))
          .replace("@@REPO@@", sh_repo).replace("@@MACHINE@@", sh_machine).replace("@@LEGACYLIST@@", sh_legacy)
          .replace("@@STATE@@", sh_state))
    return py, sh


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if a committed reader is stale")
    args = parser.parse_args(argv)
    try:
        py, sh = render(REGISTRY.read_bytes())
    except RegistryError as e:
        print(f"references/paths.toml: {e}", file=sys.stderr)
        return 2
    stale = [out for out, text in ((PY_OUT, py), (SH_OUT, sh)) if not out.exists() or out.read_text() != text]
    if args.check:
        for out in stale:
            print(f"stale: {os.path.relpath(out, ROOT)} (run python3 scripts/gen_paths.py)", file=sys.stderr)
        return 1 if stale else 0
    for out, text in ((PY_OUT, py), (SH_OUT, sh)):
        out.write_text(text)
    PY_OUT.chmod(0o755)
    print(f"wrote {PY_OUT.relative_to(ROOT)} and {SH_OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
