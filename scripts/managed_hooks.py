#!/usr/bin/env python3
"""managed_hooks.py — which hooks survive allowManagedHooksOnly? (BRO-2542)

An organization that turns on `allowManagedHooksOnly` silently switches off every
project-scope governance hook: the control gate, the conversation bridge, the
write-safety gate. Nothing announces it; the hooks simply stop firing, and a hook
that does not fire leaves no trace. This tool answers the question before the key
is flipped (or explains it after): for every hook on every surface, does it run
today, and does it run under allowManagedHooksOnly?

Source: https://code.claude.com/docs/en/settings-reference#what-runs-under-allowmanagedhooksonly
(verbatim):

    * **Managed and SDK hooks run**: hooks from managed settings and hooks the Agent SDK registers in process
    * **Force-enabled plugin hooks run**: hooks from plugins your managed settings force-enable through `enabledPlugins`. Claude Code matches on the full `plugin@marketplace` ID, so a plugin with the same name from a different marketplace stays blocked. […]
    * **Everything else is blocked**: user, project, and local hooks, hooks from other plugins, and hooks declared in agent frontmatter
    * **Command-sourced plugins are disabled**: Claude Code also disables plugins with a `command` source, including plugins force-enabled in managed `enabledPlugins`, unless you set `disableCommandPluginSources` to `false` explicitly
    The `/goal` command can't run while this key is set, because it depends on hooks.

And from the `disableAllHooks` section of the same page (verbatim):

    In managed settings: Claude Code disables every configured hook, including
    managed ones, and keeps running the hooks the Agent SDK registers in process.
    In any other settings file: Claude Code disables user, project, local, and
    plugin hooks; managed hooks, Agent SDK hooks, and hooks from plugins
    force-enabled in managed enabledPlugins keep running.

Two columns per hook:
    today         the managed settings actually on disk, honored as written
                  (disableAllHooks at either reach, disableCommandPluginSources,
                  enabledPlugins precedence managed > local > project > user)
    managed-only  the same, with allowManagedHooksOnly true. `actual` when the
                  managed settings already set it true, otherwise `simulated`.

INVARIANT: a hook is reported as running under managed-only only if the docs above
say it runs — managed and not disabled by managed disableAllHooks, or from a plugin
whose exact `plugin@marketplace` ID managed settings force-enable and that is not a
command-sourced plugin still under the command-source block. A plugin whose hooks
file was not supplied is `unresolved`, never guessed.

Not modelled (and why): Agent SDK hooks are registered in process, not in any
file; agent- and skill-frontmatter hooks are blocked under managed-only by the
docs above; `--settings` flag values exist only for one run.

Output: human report on stdout; `--json` prints JSON instead (CRITICAL BLOCKED
lines then go to stderr); `--json PATH` writes JSON to PATH and still prints the
human report.

Exit codes:
    0  report printed
    1  --fail-on-critical and a critical hook is blocked under managed-only
    2  a settings, drop-in, or plugin hooks file is unreadable or invalid JSON,
       an explicit --managed-settings path does not exist, or --json PATH is unwritable
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CRITICAL = (
    "control-gate",
    "conversation-bridge",
    "check-file-write-safety",
    "linear-routing-gate",
    "test-lock-hook",
)
SURFACE_ORDER = ("managed", "user", "project", "local", "plugin")
# enabledPlugins and disableAllHooks follow settings precedence, highest first.
PLUGIN_PRECEDENCE = ("managed", "local", "project", "user")
NON_MANAGED_PRECEDENCE = ("local", "project", "user")


class ConfigError(Exception):
    """A file the report depends on cannot be trusted; maps to exit 2."""


def default_managed_path() -> Path:
    if sys.platform == "darwin":
        return Path("/Library/Application Support/ClaudeCode/managed-settings.json")
    if sys.platform.startswith("win"):
        return Path(r"C:\Program Files\ClaudeCode\managed-settings.json")
    return Path("/etc/claude-code/managed-settings.json")


def user_settings_path() -> Path:
    """`$CLAUDE_CONFIG_DIR/settings.json` when set (it relocates ~/.claude), else
    `$HOME/.claude/settings.json`."""
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    if cfg:
        return Path(cfg).expanduser() / "settings.json"
    return Path(os.environ.get("HOME") or Path.home()) / ".claude" / "settings.json"


def read_json(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"{path}: unreadable: {e.strerror or e}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path}: invalid JSON: {e}")
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a JSON object")
    return data


def deep_merge(a: dict, b: dict) -> dict:
    """The managed drop-in rule: later scalars replace, lists union without
    duplicates, nested objects merge key by key."""
    out = dict(a)
    for k, v in b.items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        elif isinstance(out.get(k), list) and isinstance(v, list):
            out[k] = out[k] + [x for x in v if x not in out[k]]
        else:
            out[k] = v
    return out


def managed_files(path: Path, explicit: bool) -> list[Path]:
    """managed-settings.json first, then managed-settings.d/*.json alphabetically,
    skipping hidden files — the order Claude Code merges them in."""
    files: list[Path] = []
    if path.exists():
        files.append(path)
    dropins = path.parent / "managed-settings.d"
    if dropins.is_dir():
        files.extend(sorted(
            (p for p in dropins.iterdir()
             if p.suffix == ".json" and not p.name.startswith(".")),
            key=lambda p: p.name,
        ))
    if explicit and not files:
        raise ConfigError(f"{path}: managed settings not found (no file, no managed-settings.d/)")
    return files


def extract_hooks(settings: dict, surface: str, source: Path,
                  plugin_id: str | None = None) -> list[dict]:
    hooks = settings.get("hooks")
    if hooks is None:
        return []
    if not isinstance(hooks, dict):
        raise ConfigError(f"{source}: 'hooks' must be an object mapping event -> list")
    out: list[dict] = []
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            raise ConfigError(f"{source}: hooks.{event} must be a list")
        for group in groups:
            inner = group.get("hooks", []) if isinstance(group, dict) else None
            if not isinstance(inner, list):
                raise ConfigError(f"{source}: hooks.{event} entries must be objects with a 'hooks' list")
            for h in inner:
                if not isinstance(h, dict):
                    raise ConfigError(f"{source}: hooks.{event} handler must be an object")
                kind = str(h.get("type", "command"))
                cmd = h.get("command") or h.get("url") or h.get("prompt") or json.dumps(h, sort_keys=True)
                out.append({
                    "surface": surface,
                    "plugin_id": plugin_id,
                    "event": event,
                    "matcher": str(group.get("matcher", "") or ""),
                    "type": kind,
                    "command": str(cmd),
                    "source": str(source),
                })
    return out


def _enabled_plugins(settings: dict, source: str) -> dict[str, bool]:
    ep = settings.get("enabledPlugins")
    if ep is None:
        return {}
    if not isinstance(ep, dict):
        raise ConfigError(f"{source}: 'enabledPlugins' must be an object")
    return {k: v for k, v in ep.items() if isinstance(v, bool)}


@dataclass
class Context:
    settings: dict[str, dict]                 # merged settings per surface
    sources: dict[str, str]                   # surface -> first file (for messages)
    plugin_sources: dict[str, str] = field(default_factory=dict)

    @property
    def managed(self) -> dict:
        return self.settings.get("managed", {})

    @property
    def managed_dah(self) -> bool:
        return self.managed.get("disableAllHooks") is True

    @property
    def amho_actual(self) -> bool:
        return self.managed.get("allowManagedHooksOnly") is True

    @property
    def dcps(self) -> bool | None:
        v = self.managed.get("disableCommandPluginSources")
        return v if isinstance(v, bool) else None

    def nm_dah(self) -> str | None:
        """Surface whose disableAllHooks value wins outside managed settings, if true.

        Precedence applies: a project `false` beats a user `true`."""
        for s in NON_MANAGED_PRECEDENCE:
            v = self.settings.get(s, {}).get("disableAllHooks")
            if isinstance(v, bool):
                return s if v else None
        return None

    def plugin_state(self, pid: str) -> tuple[bool | None, str | None]:
        for s in PLUGIN_PRECEDENCE:
            ep = _enabled_plugins(self.settings.get(s, {}), self.sources.get(s, s))
            if pid in ep:
                return ep[pid], s
        return None, None

    def forced(self, pid: str) -> bool:
        return _enabled_plugins(self.managed, "managed").get(pid) is True

    def command_blocked(self, pid: str, amho: bool) -> str | None:
        if self.plugin_sources.get(pid) != "command":
            return None
        if self.dcps is True:
            return "command-sourced plugin: disableCommandPluginSources is true in managed settings"
        if self.dcps is None and amho:
            return ("command-sourced plugin: disabled under allowManagedHooksOnly unless "
                    "disableCommandPluginSources is explicitly false")
        return None


def verdict(hook: dict, ctx: Context, amho: bool) -> tuple[bool, str]:
    """(runs, reason) for one hook under one column."""
    surface = hook["surface"]
    if ctx.managed_dah:
        return False, "disableAllHooks in managed settings disables every configured hook, including managed ones"
    if surface == "managed":
        return True, "managed hook"
    nm = ctx.nm_dah()
    if surface == "plugin":
        pid = hook["plugin_id"]
        enabled, where = ctx.plugin_state(pid)
        if enabled is False:
            return False, f"plugin disabled in {where} enabledPlugins"
        blocked = ctx.command_blocked(pid, amho)
        if blocked:
            return False, blocked
        force = ctx.forced(pid)
        if amho and not force:
            reason = "allowManagedHooksOnly: plugin is not force-enabled in managed enabledPlugins"
            name = pid.split("@", 1)[0]
            twins = sorted(o for o, v in _enabled_plugins(ctx.managed, "managed").items()
                           if v and o != pid and o.split("@", 1)[0] == name)
            if twins:
                reason += (f" (managed force-enables {', '.join(twins)}; the full "
                           f"plugin@marketplace ID must match)")
            return False, reason
        if not amho and nm and not force:
            return False, (f"disableAllHooks (effective from {nm} settings) disables plugin hooks "
                           f"not force-enabled by managed settings")
        if force:
            return True, "force-enabled in managed enabledPlugins"
        if where:
            return True, f"plugin enabled in {where} enabledPlugins"
        return True, "plugin not named in any enabledPlugins; assumed enabled (defaultEnabled not modelled)"
    if amho:
        return False, f"allowManagedHooksOnly blocks {surface} hooks"
    if nm:
        return False, f"disableAllHooks (effective from {nm} settings) disables {surface} hooks"
    return True, f"{surface} hook"


def _kv(value: str, what: str) -> tuple[str, str]:
    key, sep, rest = value.partition("=")
    name, at, market = key.partition("@")
    if not (sep and at and name and market and rest):
        raise argparse.ArgumentTypeError(f"{what} must be NAME@MARKETPLACE=VALUE, got {value!r}")
    return key, rest


def _plugin_hooks_arg(v: str) -> tuple[str, str]:
    return _kv(v, "--plugin-hooks")


def _plugin_source_arg(v: str) -> tuple[str, str]:
    return _kv(v, "--plugin-source")


def _regex_arg(v: str) -> str:
    try:
        re.compile(v)
    except re.error as e:
        raise argparse.ArgumentTypeError(f"--critical {v!r}: invalid regex: {e}")
    return v


def analyze(managed_path: Path | None, project_dir: Path,
            plugin_hooks: list[tuple[str, str]] | None = None,
            plugin_sources: list[tuple[str, str]] | None = None,
            critical: list[str] | None = None) -> dict:
    explicit = managed_path is not None
    managed_path = managed_path or default_managed_path()
    patterns = list(critical) if critical else list(DEFAULT_CRITICAL)

    settings: dict[str, dict] = {}
    sources: dict[str, str] = {}
    hooks: list[dict] = []

    mfiles = managed_files(managed_path, explicit)
    merged: dict = {}
    seen: set[str] = set()
    for f in mfiles:
        data = read_json(f)
        _enabled_plugins(data, str(f))
        merged = deep_merge(merged, data)
        for h in extract_hooks(data, "managed", f):
            # Drop-ins union their hook lists; an identical handler declared in two
            # files is one hook, not two.
            key = json.dumps([h["event"], h["matcher"], h["type"], h["command"]])
            if key not in seen:
                seen.add(key)
                hooks.append(h)
    settings["managed"] = merged
    sources["managed"] = str(managed_path)

    for surface, path in (
        ("user", user_settings_path()),
        ("project", project_dir / ".claude" / "settings.json"),
        ("local", project_dir / ".claude" / "settings.local.json"),
    ):
        sources[surface] = str(path)
        if not path.exists():
            settings[surface] = {}
            continue
        data = read_json(path)
        settings[surface] = data
        _enabled_plugins(data, str(path))
        hooks.extend(extract_hooks(data, surface, path))

    ctx = Context(settings=settings, sources=sources,
                  plugin_sources=dict(plugin_sources or []))
    resolved = dict(plugin_hooks or [])
    for pid in sorted(resolved):
        path = Path(resolved[pid]).expanduser()
        hooks.extend(extract_hooks(read_json(path), "plugin", path, plugin_id=pid))

    named: dict[str, None] = {}
    for s in PLUGIN_PRECEDENCE:
        for pid in _enabled_plugins(settings.get(s, {}), sources.get(s, s)):
            named.setdefault(pid)
    unresolved = []
    for pid in named:
        enabled, where = ctx.plugin_state(pid)
        if enabled and pid not in resolved:
            unresolved.append({
                "plugin_id": pid,
                "enabled_in": where,
                "force_enabled": ctx.forced(pid),
                "source_type": ctx.plugin_sources.get(pid),
            })

    critical_blocked = []
    for h in hooks:
        h["critical"] = any(re.search(p, h["command"]) for p in patterns)
        h["runs_today"], today_reason = verdict(h, ctx, ctx.amho_actual)
        h["runs_under_managed_only"], mo_reason = verdict(h, ctx, True)
        h["reason"] = {"today": today_reason, "managed_only": mo_reason}
        if h["critical"] and not h["runs_under_managed_only"]:
            critical_blocked.append(h)

    order = {s: i for i, s in enumerate(SURFACE_ORDER)}
    hooks.sort(key=lambda h: (order[h["surface"]], h["plugin_id"] or ""))

    def column(key: str, goal: bool) -> dict:
        runs = sum(1 for h in hooks if h[key])
        return {"goal_available": goal, "run": runs, "blocked": len(hooks) - runs}

    today_goal = not (ctx.amho_actual or ctx.managed_dah or ctx.nm_dah())
    return {
        "tool": "managed_hooks",
        "managed": {
            "path": str(managed_path),
            "files": [str(f) for f in mfiles],
            "allowManagedHooksOnly": ctx.managed.get("allowManagedHooksOnly"),
            "disableAllHooks": ctx.managed.get("disableAllHooks"),
            "disableCommandPluginSources": ctx.dcps,
        },
        "non_managed_disableAllHooks_from": ctx.nm_dah(),
        "managed_only_mode": "actual" if ctx.amho_actual else "simulated",
        "columns": {
            "today": column("runs_today", today_goal),
            # "/goal can't run while this key is set" — true by construction here.
            "managed_only": column("runs_under_managed_only", False),
        },
        "critical_patterns": patterns,
        "hooks": hooks,
        "unresolved": unresolved,
        "critical_blocked": [
            {k: h[k] for k in ("surface", "plugin_id", "event", "matcher", "command")}
            | {"reason": h["reason"]["managed_only"]}
            for h in critical_blocked
        ],
    }


def _critical_line(c: dict) -> str:
    where = c["surface"] + (f":{c['plugin_id']}" if c["plugin_id"] else "")
    return f"CRITICAL BLOCKED {where} {c['event']} {c['command'][:90]} — {c['reason']}"


def render(r: dict) -> str:
    m = r["managed"]
    files = f"{len(m['files'])} file(s)" if m["files"] else "none on disk"
    lines = [
        "managed-hooks — which hooks survive allowManagedHooksOnly",
        f"  managed settings: {m['path']} ({files}); managed-only column: {r['managed_only_mode']}",
    ]
    wheres = [h["surface"] + (f":{h['plugin_id']}" if h["plugin_id"] else "") for h in r["hooks"]]
    width = max([len("SURFACE")] + [len(w) for w in wheres])
    lines.append(f"  {'TODAY':<8} {'MGD-ONLY':<9} {'SURFACE':<{width}} {'EVENT':<18} COMMAND")
    for h, where in zip(r["hooks"], wheres):
        mark = "*" if h["critical"] else " "
        lines.append(
            f" {mark}{'run' if h['runs_today'] else 'BLOCKED':<8} "
            f"{'run' if h['runs_under_managed_only'] else 'BLOCKED':<9} "
            f"{where:<{width}} {h['event']:<18} {h['command'][:70]}"
        )
        if not h["runs_today"]:
            lines.append(f"      today: {h['reason']['today']}")
        if not h["runs_under_managed_only"] and h["reason"]["managed_only"] != h["reason"]["today"]:
            lines.append(f"      managed-only: {h['reason']['managed_only']}")
    if not r["hooks"]:
        lines.append("  (no hooks found on any surface)")
    t, mo = r["columns"]["today"], r["columns"]["managed_only"]
    lines.append(f"  today: {t['run']} run, {t['blocked']} blocked | managed-only: "
                 f"{mo['run']} run, {mo['blocked']} blocked   (* = critical)")
    lines.append(f"  /goal available: today={'yes' if t['goal_available'] else 'no'}, "
                 f"under allowManagedHooksOnly={'yes' if mo['goal_available'] else 'no'}")
    for u in r["unresolved"]:
        lines.append(f"  UNRESOLVED {u['plugin_id']} (enabled in {u['enabled_in']} settings"
                     f"{', force-enabled' if u['force_enabled'] else ''}): pass "
                     f"--plugin-hooks {u['plugin_id']}=PATH/hooks/hooks.json")
    lines.extend("  " + _critical_line(c) for c in r["critical_blocked"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="bstack managed-hooks", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--managed-settings", type=Path, metavar="PATH",
                    help=f"managed-settings.json (default: {default_managed_path()}); "
                         f"managed-settings.d/*.json beside it are merged")
    ap.add_argument("--project-dir", type=Path, default=Path.cwd(),
                    help="project whose .claude/settings{,.local}.json to read (default: cwd)")
    ap.add_argument("--plugin-hooks", action="append", type=_plugin_hooks_arg, default=[],
                    metavar="NAME@MARKETPLACE=PATH", help="a plugin's hooks/hooks.json, repeatable")
    ap.add_argument("--plugin-source", action="append", type=_plugin_source_arg, default=[],
                    metavar="NAME@MARKETPLACE=TYPE",
                    help="a plugin's marketplace source type; 'command' models the command-source rule")
    ap.add_argument("--critical", action="append", type=_regex_arg, metavar="REGEX",
                    help=f"critical-hook regex, repeatable (default: {', '.join(DEFAULT_CRITICAL)})")
    ap.add_argument("--fail-on-critical", action="store_true",
                    help="exit 1 if any critical hook is blocked under allowManagedHooksOnly")
    ap.add_argument("--json", nargs="?", const="-", metavar="PATH",
                    help="machine-readable report: bare --json prints it to stdout; "
                         "--json PATH writes it there and keeps the human report on stdout")
    args = ap.parse_args(argv)

    try:
        report = analyze(args.managed_settings, args.project_dir,
                         plugin_hooks=args.plugin_hooks, plugin_sources=args.plugin_source,
                         critical=args.critical)
    except ConfigError as e:
        print(f"managed-hooks: error: {e}", file=sys.stderr)
        return 2

    if args.json == "-":
        print(json.dumps(report, indent=2))
        # stdout stays pure JSON; the CRITICAL BLOCKED lines still reach a human.
        for c in report["critical_blocked"]:
            print(_critical_line(c), file=sys.stderr)
    else:
        if args.json:
            try:
                Path(args.json).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            except OSError as e:
                print(f"managed-hooks: error: --json {args.json}: {e.strerror or e}", file=sys.stderr)
                return 2
        print(render(report))
    if args.fail_on_critical and report["critical_blocked"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
