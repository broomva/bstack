#!/usr/bin/env python3
"""bstack workspace — multi-workspace registry manager (Phase 8, v0.10.0).

Maintains ``~/.broomva/global/registry.yaml`` — an opt-in roster of
bstack-governed workspaces on this host. Federation is read-only
aggregation; each workspace owns its own state. Nothing here mutates
state outside the registry file.

Subcommands:
  register [--path PATH] [--name NAME] [--tag TAG]... [--json]
                                 Add (or refresh) a workspace entry.
  list [--json]                  Print registered workspaces.
  info [--path PATH] [--json]    Show this workspace's registration state.
  deregister [--name NAME | --path PATH] [--json]
                                 Remove a workspace entry.

Exit codes:
  0  success
  2  invalid arguments
  3  registry parse error / schema mismatch
  4  target not found (deregister missing name/path)
  5  duplicate name on register (with different path)
"""

import argparse
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

DEFAULT_REGISTRY = os.environ.get(
    "BSTACK_REGISTRY",
    str(Path.home() / ".broomva" / "global" / "registry.yaml"),
)

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _now_iso():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _yaml_load(path):
    """Load YAML registry. Uses PyYAML if available, falls back to a
    deliberately-tiny inline parser sufficient for the registry shape."""
    if not path.exists():
        return None
    text = path.read_text()
    try:
        import yaml

        return yaml.safe_load(text) or {}
    except ImportError:
        return _yaml_minimal_parse(text)


def _yaml_minimal_parse(text):
    """Bare-minimum YAML parser that handles our registry shape.

    Supports: ``key: value`` scalars at the top level, plus a single
    ``workspaces:`` sequence whose items are dicts with scalar values
    (optionally including a ``tags:`` sequence). This is the exact
    structure validated by ``schemas/workspaces.v1.json``. Anything
    richer falls through to ImportError-style failure handled upstream.
    """
    data = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line or line.lstrip().startswith("#"):
            i += 1
            continue
        if not line.startswith(" "):
            if line.endswith(":"):
                key = line[:-1].strip()
                if key == "workspaces":
                    items, i = _yaml_parse_sequence(lines, i + 1, indent=2)
                    data["workspaces"] = items
                    continue
                data[key] = {}
            else:
                k, _, v = line.partition(":")
                data[k.strip()] = _yaml_scalar(v.strip())
        i += 1
    return data


def _yaml_parse_sequence(lines, start, indent):
    items = []
    i = start
    current = None
    in_tags = False
    while i < len(lines):
        line = lines[i].rstrip()
        if not line:
            i += 1
            continue
        stripped = line.lstrip()
        leading = len(line) - len(stripped)
        if leading < indent:
            break
        if leading == indent and stripped.startswith("- "):
            current = {}
            items.append(current)
            in_tags = False
            rest = stripped[2:].strip()
            if rest:
                k, _, v = rest.partition(":")
                current[k.strip()] = _yaml_scalar(v.strip())
        elif leading == indent + 2 and current is not None:
            if stripped.startswith("- "):
                if in_tags:
                    current.setdefault("tags", []).append(_yaml_scalar(stripped[2:].strip()))
            else:
                k, _, v = stripped.partition(":")
                k = k.strip()
                v = v.strip()
                if k == "tags" and not v:
                    in_tags = True
                    current["tags"] = []
                else:
                    in_tags = False
                    current[k] = _yaml_scalar(v)
        else:
            break
        i += 1
    return items, i


def _yaml_scalar(s):
    if s == "":
        return ""
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    return s


def _yaml_dump(data, path):
    """Write registry YAML atomically. Uses PyYAML if available, else
    a hand-rolled emitter that mirrors the registry shape exactly."""
    try:
        import yaml

        text = yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    except ImportError:
        text = _yaml_minimal_dump(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _yaml_minimal_dump(data):
    lines = []
    for key in ("schema_version", "generated_at"):
        if key in data:
            v = data[key]
            if isinstance(v, str):
                lines.append("{}: {}".format(key, v))
            else:
                lines.append("{}: {}".format(key, v))
    workspaces = data.get("workspaces") or []
    if workspaces is not None:
        lines.append("workspaces:")
        for ws in workspaces:
            first = True
            for k in ("name", "path", "bstack_version", "registered_at", "last_seen_at"):
                if k in ws:
                    prefix = "- " if first else "  "
                    first = False
                    lines.append("  {}{}: {}".format(prefix, k, ws[k]))
            tags = ws.get("tags")
            if tags:
                lines.append("    tags:")
                for t in tags:
                    lines.append("      - {}".format(t))
            if first:
                lines.append("  - {}")
    return "\n".join(lines) + "\n"


def _load_registry(path):
    if not path.exists():
        return {"schema_version": 1, "workspaces": []}
    raw = _yaml_load(path)
    if not isinstance(raw, dict):
        raise ValueError("registry is not a mapping: {}".format(path))
    if raw.get("schema_version") not in (1, "1"):
        raise ValueError(
            "registry schema_version != 1 (got {!r})".format(raw.get("schema_version"))
        )
    workspaces = raw.get("workspaces") or []
    if not isinstance(workspaces, list):
        raise ValueError("registry.workspaces is not a list")
    return {"schema_version": 1, "workspaces": workspaces}


def _save_registry(path, reg):
    payload = {
        "schema_version": 1,
        "generated_at": _now_iso(),
        "workspaces": reg.get("workspaces", []),
    }
    _yaml_dump(payload, path)


def _detect_bstack_version(workspace_path):
    """Return the bstack VERSION at ``workspace_path``, if present.

    Resolution order:
      1. ``$BSTACK_DIR/VERSION`` (if BSTACK_DIR is set)
      2. ``<workspace_path>/bstack/VERSION``
      3. ``<workspace_path>/VERSION``
      4. ``None`` if not found
    """
    candidates = []
    bstack_dir = os.environ.get("BSTACK_DIR")
    if bstack_dir:
        candidates.append(Path(bstack_dir) / "VERSION")
    candidates.extend(
        [
            workspace_path / "bstack" / "VERSION",
            workspace_path / "VERSION",
        ]
    )
    for c in candidates:
        if c.is_file():
            try:
                v = c.read_text().strip()
                if v:
                    return v
            except OSError:
                continue
    return None


def _validate_name(name):
    if not NAME_RE.match(name):
        raise ValueError(
            "name must match {!r} (got {!r})".format(NAME_RE.pattern, name)
        )


def _resolve_path(raw):
    p = Path(raw).expanduser().resolve()
    return p


def cmd_register(args):
    workspace_path = _resolve_path(args.path or os.getcwd())
    if not workspace_path.is_dir():
        print(
            "workspace: path does not exist or is not a directory: {}".format(
                workspace_path
            ),
            file=sys.stderr,
        )
        return 2
    name = args.name or workspace_path.name
    try:
        _validate_name(name)
    except ValueError as e:
        print("workspace: {}".format(e), file=sys.stderr)
        return 2

    registry_path = Path(args.registry).expanduser()
    try:
        reg = _load_registry(registry_path)
    except ValueError as e:
        print("workspace: {}".format(e), file=sys.stderr)
        return 3

    workspaces = reg["workspaces"]
    existing_by_name = {
        i: ws for i, ws in enumerate(workspaces) if ws.get("name") == name
    }
    existing_by_path = {
        i: ws for i, ws in enumerate(workspaces) if ws.get("path") == str(workspace_path)
    }

    if existing_by_name and not existing_by_path:
        print(
            "workspace: name {!r} already registered at a different path ({})".format(
                name, list(existing_by_name.values())[0].get("path")
            ),
            file=sys.stderr,
        )
        return 5

    target_idx = next(iter(existing_by_path), None)
    if target_idx is None:
        target_idx = next(iter(existing_by_name), None)

    bstack_version = _detect_bstack_version(workspace_path)
    now = _now_iso()
    if target_idx is None:
        entry = {
            "name": name,
            "path": str(workspace_path),
            "registered_at": now,
        }
        if bstack_version:
            entry["bstack_version"] = bstack_version
        if args.tag:
            entry["tags"] = list(args.tag)
        workspaces.append(entry)
        action = "registered"
    else:
        entry = dict(workspaces[target_idx])
        entry["name"] = name
        entry["path"] = str(workspace_path)
        entry.setdefault("registered_at", now)
        if bstack_version:
            entry["bstack_version"] = bstack_version
        entry["last_seen_at"] = now
        if args.tag:
            existing_tags = entry.get("tags") or []
            entry["tags"] = sorted(set(existing_tags) | set(args.tag))
        workspaces[target_idx] = entry
        action = "refreshed"

    _save_registry(registry_path, reg)

    if args.json:
        payload = {
            "action": action,
            "workspace": entry,
            "registry": str(registry_path),
            "count": len(workspaces),
        }
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(
            "  workspace {} ({} as {!r} at {})".format(
                action, "refreshed" if action == "refreshed" else "registered", name, workspace_path
            )
        )
        print("  registry: {} ({} total)".format(registry_path, len(workspaces)))
    return 0


def cmd_list(args):
    registry_path = Path(args.registry).expanduser()
    try:
        reg = _load_registry(registry_path)
    except ValueError as e:
        print("workspace: {}".format(e), file=sys.stderr)
        return 3
    workspaces = reg["workspaces"]
    if args.json:
        payload = {
            "registry": str(registry_path),
            "count": len(workspaces),
            "workspaces": workspaces,
        }
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if not workspaces:
        print("  workspace: no registered workspaces ({}).".format(registry_path))
        print("  Register one with: bstack workspace register")
        return 0
    print(
        "  workspace registry — {} entries ({})".format(
            len(workspaces), registry_path
        )
    )
    for ws in workspaces:
        name = ws.get("name", "?")
        path = ws.get("path", "?")
        version = ws.get("bstack_version", "?")
        seen = ws.get("last_seen_at") or ws.get("registered_at", "?")
        print("    {:<20} v{:<10} {:<32} ({})".format(name, version, path, seen))
    return 0


def cmd_deregister(args):
    if not args.name and not args.path:
        print(
            "workspace deregister: pass --name or --path",
            file=sys.stderr,
        )
        return 2
    registry_path = Path(args.registry).expanduser()
    try:
        reg = _load_registry(registry_path)
    except ValueError as e:
        print("workspace: {}".format(e), file=sys.stderr)
        return 3
    workspaces = reg["workspaces"]
    if args.path:
        target_path = str(_resolve_path(args.path))
        kept = [ws for ws in workspaces if ws.get("path") != target_path]
    else:
        kept = [ws for ws in workspaces if ws.get("name") != args.name]
    if len(kept) == len(workspaces):
        print(
            "workspace deregister: no entry matched (name={}, path={})".format(
                args.name, args.path
            ),
            file=sys.stderr,
        )
        return 4
    removed = [ws for ws in workspaces if ws not in kept]
    reg["workspaces"] = kept
    _save_registry(registry_path, reg)
    if args.json:
        payload = {
            "removed": removed,
            "registry": str(registry_path),
            "count": len(kept),
        }
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        for ws in removed:
            print(
                "  workspace deregistered: {!r} at {}".format(
                    ws.get("name"), ws.get("path")
                )
            )
        print("  registry: {} ({} remaining)".format(registry_path, len(kept)))
    return 0


def cmd_info(args):
    workspace_path = _resolve_path(args.path or os.getcwd())
    registry_path = Path(args.registry).expanduser()
    try:
        reg = _load_registry(registry_path)
    except ValueError as e:
        print("workspace: {}".format(e), file=sys.stderr)
        return 3
    workspaces = reg["workspaces"]
    target = next(
        (ws for ws in workspaces if ws.get("path") == str(workspace_path)), None
    )
    registered = target is not None
    payload = {
        "registry": str(registry_path),
        "workspace_path": str(workspace_path),
        "registered": registered,
        "entry": target,
        "count": len(workspaces),
    }
    if args.json:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if registered:
        print(
            "  workspace registered: {!r} (path={})".format(
                target.get("name"), workspace_path
            )
        )
        print("  registry: {}".format(registry_path))
    else:
        print("  workspace NOT registered (path={})".format(workspace_path))
        print("  Register with: bstack workspace register")
    return 0


def _add_registry_arg(p):
    p.add_argument(
        "--registry",
        default=DEFAULT_REGISTRY,
        help="Registry path (default: ~/.broomva/global/registry.yaml, overridable via $BSTACK_REGISTRY)",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="workspace",
        description="bstack workspace registry manager (Phase 8, v0.10.0).",
    )
    sub = parser.add_subparsers(dest="cmd")

    p_reg = sub.add_parser("register", help="Register or refresh a workspace")
    p_reg.add_argument("--path", default=None)
    p_reg.add_argument("--name", default=None)
    p_reg.add_argument("--tag", action="append", default=None)
    p_reg.add_argument("--json", action="store_true")
    _add_registry_arg(p_reg)

    p_list = sub.add_parser("list", help="List registered workspaces")
    p_list.add_argument("--json", action="store_true")
    _add_registry_arg(p_list)

    p_dereg = sub.add_parser("deregister", help="Remove a workspace entry")
    p_dereg.add_argument("--name", default=None)
    p_dereg.add_argument("--path", default=None)
    p_dereg.add_argument("--json", action="store_true")
    _add_registry_arg(p_dereg)

    p_info = sub.add_parser("info", help="Show registration state for a workspace")
    p_info.add_argument("--path", default=None)
    p_info.add_argument("--json", action="store_true")
    _add_registry_arg(p_info)

    args = parser.parse_args(argv)
    if args.cmd == "register":
        return cmd_register(args)
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "deregister":
        return cmd_deregister(args)
    if args.cmd == "info":
        return cmd_info(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
