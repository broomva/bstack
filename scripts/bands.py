#!/usr/bin/env python3
"""bands.py — a deterministic control-band detector that writes intent.md (BRO-2542).

Purpose: watch one metric series, decide whether it has left its control band,
and — only at the tiers that permit it — turn the breach into an intent.md a
human or a read-only agent can act on. The graduated response comes from
https://academy.claude.com/courses/ai-native-sdlc-playbook/closing-the-loop-on-metrics :

    "At 1σ the script only logs, at 2σ it invokes Claude read-only to diagnose,
     and at 3σ Claude may act, though only by opening a PR into the review gate
     or triggering a pre-approved runbook."

INVARIANT: detection never involves a model. The tier is a pure function of
(config, series): mean and sample standard deviation over a rolling window, plus
the four Western Electric rules. The intent file is a pure function of (config,
result, date). A model enters only downstream, through `diagnose-cmd`, and it
gets no shell: its tools are exactly an ALLOWLIST of Read, Grep, Glob and LS, and
any other token (every Bash entry, a write tool, an MCP tool, Agent, Skill) is
refused when the config loads. Whatever the diagnosis needs from CI or history, the
workflow pre-fetches into the checkout. The command adds --restricted (no
code-running tools; user, project and local settings files ignored),
--strict-mcp-config (no MCP servers), --tools, --permission-mode dontAsk and
--disallowedTools for the write tools. That is the grant of the flags passed here,
not a sandbox: managed settings still apply, and Read can read any file in the
working directory, .git/config included, so the checkout must hold no credentials.

Second invariant: an unmeasurable band never presents as a healthy one. Too few
baseline points yields tier `insufficient_baseline`, never `none`, and under
`--fail-on` it exits 4 rather than 0.

Algorithm
  - Points are sorted by t (ISO date or datetime; a trailing Z means UTC).
  - The evaluation window is the last 8 points.
  - The rolling window ends at the last point: `rolling_<N>d` holds the points
    with t > last - N days; `rolling_<N>` holds the last N points. The baseline
    is the part of the rolling window that lies BEFORE the evaluation window.
  - Fewer than min_baseline_points baseline points -> `insufficient_baseline`.
  - z = (v - mean) / std with the sample std (ddof=1). When std == 0, z is 0
    for v == mean and +inf / -inf otherwise: a zero-variance baseline has a
    zero-width band, so any departure is maximally out of it. JSON cannot carry
    infinity, so an infinite z is written as the string "+inf" or "-inf".
  - Western Electric rules over the evaluation window, counting only the breach
    side(s) that `direction` allows ("beyond kσ" is strict: |z| > k):
      R1  the last point is beyond 3σ
      R2  2 of the last 3 are beyond 2σ on the same side
      R3  4 of the last 5 are beyond 1σ on the same side
      R4  the last 8 are all on the same side of the mean (z == 0 breaks it)
  - Tier: 3sigma if R1; else 2sigma if R2; else 1sigma if R3 or R4; else none.

Subcommands
  check <bands.yaml> --series F|- [--json] [--fail-on 1sigma|2sigma|3sigma]
  intent <bands.yaml> --result R --out-dir D [--date YYYY-MM-DD] [--force] [--json]
  series ci-failure-rate --runs-json F|- [--include-today] [--now ISO]
  diagnose-cmd <bands.yaml> (--result R | --tier T) --intent FILE

Exit codes
  0  ok (including "breach found" when --fail-on is not given, and a
     diagnose-cmd skip when no diagnose tools are configured)
  2  config or input error: bad YAML, schema violation, unreadable series,
     a diagnose tool outside the allowlist, a result that does not match its config
  3  check --fail-on T: the tier is at or above T
  4  check --fail-on T: the baseline is insufficient, so the band is unmeasured.
     Not 0: a gate that read "unmeasured" as "healthy" would pass anything.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import io
import json
import math
import re
import statistics
import sys
from pathlib import Path

EVAL_WINDOW = 8
INSUFFICIENT = "insufficient_baseline"
# Ranked: --fail-on compares positions in this tuple.
BREACH_ORDER = ("none", "1sigma", "2sigma", "3sigma")
BAND_TIERS = ("1sigma", "2sigma", "3sigma")
ALL_TIERS = (INSUFFICIENT,) + BREACH_ORDER
# Ranked too. A tier may be configured to do LESS than the course allows (a
# cautious team can make 3sigma only diagnose) but never more: 1sigma may only
# log, 2sigma may at most diagnose.
ACTIONS = ("log", "diagnose", "propose")
ACTION_CAP = {"1sigma": "log", "2sigma": "diagnose", "3sigma": "propose"}
TIER_KEYS = {"log": {"action"}, "diagnose": {"action", "tools"},
             "propose": {"action", "routes", "tools"}}
DIRECTIONS = {"high": (1,), "low": (-1,), "both": (1, -1)}
RULES = {
    "R1": "the last point is beyond 3σ",
    "R2": "2 of the last 3 points are beyond 2σ on the same side",
    "R3": "4 of the last 5 points are beyond 1σ on the same side",
    "R4": "the last 8 points are all on the same side of the mean",
}
# Diagnose is read-only by contract, so its tools are an ALLOWLIST, compared
# exactly (case included), and it holds no shell. A deny-list let `Bash(gh *)` and
# `mcp__*` through; a list of "read-only" Bash prefixes let `git log --output=<f>`
# write. The set of things that can write is open; the set needed to read is this.
DIAGNOSE_TOOLS = ("Read", "Grep", "Glob", "LS")
# Denied on the command line as well. --restricted already ignores the settings
# files whose allow rules could pre-approve them (under -p, the user-scope ones);
# this keeps the write tools denied even if that flag's meaning ever changes.
WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
REQUIRED_KEYS = ("metric", "baseline", "min_baseline_points", "rules", "direction",
                 "tiers", "intent")
OPTIONAL_KEYS = ("description",)

_METRIC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_BASELINE_RE = re.compile(r"^rolling_([1-9][0-9]*)(d?)$")
_ROUTE_RE = re.compile(r"^(pull_request|runbook:[A-Za-z0-9][A-Za-z0-9_.-]*)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

DIAGNOSE_PROMPT = (
    "Read the intent file at {intent}. A deterministic control-band detector wrote it: "
    "a metric left its control band, and the file records the anomaly, its evidence, "
    "the affected systems and the open questions. Diagnose the most likely cause. This "
    "is a read-only diagnosis with no shell: read the code and any CI logs or history "
    "the workflow fetched into the working directory, and write nothing. Return your "
    "diagnosis as your reply, a markdown section headed '## Diagnosis' that the caller "
    "appends to the intent file: the most likely cause, the evidence for it, what would "
    "confirm or refute it, and whether this looks like a real regression or a baseline "
    "shift that should re-anchor the band."
)


class BandsError(Exception):
    """A config or input error. Every one exits 2."""


# --- config -----------------------------------------------------------------

def _yaml():
    """PyYAML, imported lazily so `series` and `--help` work without it."""
    try:
        import yaml
    except ImportError:
        raise BandsError("PyYAML is required to read a bands config "
                         "(python3 -m pip install pyyaml)")
    return yaml


def parse_baseline(spec: str) -> tuple[int, str]:
    """`rolling_30d` -> (30, "days"); `rolling_30` -> (30, "points")."""
    m = _BASELINE_RE.match(spec) if isinstance(spec, str) else None
    if not m:
        raise BandsError(f"baseline must be rolling_<N>d or rolling_<N>, got {spec!r}")
    return int(m.group(1)), ("days" if m.group(2) else "points")


def tool_tokens(tools: str) -> list[str]:
    """Split an --allowedTools string on commas/whitespace OUTSIDE parentheses,
    so a refused `Bash(git log *)` is reported as one token."""
    out, cur, depth = [], [], 0
    for ch in tools:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if depth == 0 and (ch == "," or ch.isspace()):
            if cur:
                out.append("".join(cur))
                cur = []
            continue
        cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def tool_errors(tools) -> list[str]:
    """Why a diagnose tools string is not on the allowlist. Empty list means it is.

    Fails closed: a token is accepted only if it EQUALS one of DIAGNOSE_TOOLS. So
    every Bash entry, a write tool, `mcp__*`, `Agent`, `Skill`, a lower-cased
    `read`, or a token starting with '-' (which the CLI would parse as a flag) is
    refused without needing a rule of its own. A Bash entry gets its own message,
    because the fix for it is upstream: pre-fetch the data, do not grant the shell.
    """
    if not isinstance(tools, str) or not tools.strip():
        return ["diagnose needs a non-empty 'tools' string (e.g. \"Read,Grep,Glob\")"]
    allowed = set(DIAGNOSE_TOOLS)
    errs = []
    for tok in tool_tokens(tools):
        if tok in allowed:
            continue
        if tok.partition("(")[0].strip().lower() == "bash":
            errs.append(f"tool {tok!r} is refused: the diagnosis gets no shell. The "
                        f"workflow pre-fetches the data the diagnosis reads (CI logs, run "
                        f"lists, history) into the checkout; the allowlist is "
                        f"{', '.join(DIAGNOSE_TOOLS)}")
        else:
            errs.append(f"tool {tok!r} is not on the read-only diagnose allowlist "
                        f"({', '.join(DIAGNOSE_TOOLS)})")
    return errs


def normalized_tools(tools: str) -> str:
    """The tools string as it is granted: the exact tokens tool_errors checked."""
    return ",".join(tool_tokens(tools))


def base_tools(tools: str) -> str:
    """The tool names the session may see at all (--tools), deduplicated in
    first-seen order."""
    names: list[str] = []
    for t in tool_tokens(tools):
        if t not in names:
            names.append(t)
    return " ".join(names)


def validate_config(cfg) -> list[str]:
    """Return every schema error. Empty list means the config is usable."""
    if not isinstance(cfg, dict):
        return ["top level must be a mapping"]
    errs: list[str] = []
    for k in REQUIRED_KEYS:
        if k not in cfg:
            errs.append(f"missing key: {k}")
    for k in cfg:
        if k not in REQUIRED_KEYS + OPTIONAL_KEYS:
            errs.append(f"unknown key: {k!r} (a misspelt key would otherwise be ignored)")

    metric = cfg.get("metric")
    if "metric" in cfg and not (isinstance(metric, str) and _METRIC_RE.match(metric)):
        errs.append(f"metric must be a slug of [A-Za-z0-9_.-] (it names the intent file), "
                    f"got {metric!r}")
    if "description" in cfg and not (isinstance(cfg["description"], str)
                                     and cfg["description"].strip()):
        errs.append("description, when present, must be a non-empty string")

    mbp = cfg.get("min_baseline_points")
    if "min_baseline_points" in cfg and (isinstance(mbp, bool) or not isinstance(mbp, int)
                                         or mbp < 2):
        errs.append(f"min_baseline_points must be an integer >= 2 (a sample std needs "
                    f"two points), got {mbp!r}")
        mbp = None

    if "baseline" in cfg:
        try:
            n, unit = parse_baseline(cfg["baseline"])
        except BandsError as e:
            errs.append(str(e))
        else:
            # A points window can be checked statically: if it cannot hold enough
            # baseline points behind the evaluation window, the band is never
            # measurable and every run would report insufficient_baseline.
            if unit == "points" and isinstance(mbp, int) and n - EVAL_WINDOW < mbp:
                errs.append(f"baseline {cfg['baseline']} leaves at most {max(0, n - EVAL_WINDOW)} "
                            f"point(s) behind the {EVAL_WINDOW}-point evaluation window, fewer "
                            f"than min_baseline_points={mbp}: the band could never be measured")

    if "rules" in cfg and cfg["rules"] != "western_electric":
        errs.append(f"rules must be 'western_electric' (the only supported value), "
                    f"got {cfg['rules']!r}")
    if "direction" in cfg and cfg["direction"] not in DIRECTIONS:
        errs.append(f"direction must be one of {sorted(DIRECTIONS)}, got {cfg['direction']!r}")

    tiers = cfg.get("tiers")
    if "tiers" in cfg:
        if not isinstance(tiers, dict):
            errs.append("tiers must be a mapping of 1sigma/2sigma/3sigma")
        else:
            errs.extend(_validate_tiers(tiers))

    intent = cfg.get("intent")
    if "intent" in cfg:
        if not isinstance(intent, dict):
            errs.append("intent must be a mapping with 'affected'")
        else:
            for k in intent:
                if k != "affected":
                    errs.append(f"intent: unknown key {k!r}")
            aff = intent.get("affected")
            if not (isinstance(aff, str) and aff.strip()):
                errs.append("intent.affected must be a non-empty string (who and what the "
                            "breach touches)")
    return errs


def _validate_tiers(tiers: dict) -> list[str]:
    errs: list[str] = []
    for k in tiers:
        if k not in BAND_TIERS:
            errs.append(f"tiers: unknown tier {k!r} (expected {list(BAND_TIERS)})")
    for tier in BAND_TIERS:
        where = f"tiers.{tier}"
        t = tiers.get(tier)
        if t is None:
            errs.append(f"{where}: missing")
            continue
        if not isinstance(t, dict):
            errs.append(f"{where}: must be a mapping with 'action'")
            continue
        action = t.get("action")
        if action not in ACTIONS:
            errs.append(f"{where}: action must be one of {list(ACTIONS)}, got {action!r}")
            continue
        if ACTIONS.index(action) > ACTIONS.index(ACTION_CAP[tier]):
            errs.append(f"{where}: action {action!r} exceeds what {tier} permits "
                        f"(at most {ACTION_CAP[tier]!r})")
        for k in t:
            if k not in TIER_KEYS[action]:
                errs.append(f"{where}: key {k!r} does not apply to action {action!r}")
        # Wherever `tools` appears, its only consumer is diagnose-cmd, so the same
        # read-only allowlist applies to a propose tier's tools as to a diagnose tier's.
        if action == "diagnose" or "tools" in t:
            errs.extend(f"{where}: {e}" for e in tool_errors(t.get("tools")))
        if action == "propose":
            routes = t.get("routes")
            if not isinstance(routes, list) or not routes:
                errs.append(f"{where}: propose needs a non-empty 'routes' list "
                            f"(pull_request and/or runbook:<name>)")
            else:
                for r in routes:
                    if not (isinstance(r, str) and _ROUTE_RE.match(r)):
                        errs.append(f"{where}: route {r!r} is not pull_request or "
                                    f"runbook:<name>")
    return errs


def load_config(path) -> dict:
    yaml = _yaml()
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise BandsError(f"no bands config at {path}")
    except OSError as e:
        raise BandsError(f"{path}: {e}")
    try:
        cfg = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise BandsError(f"{path}: unparseable YAML: {e}")
    errs = validate_config(cfg)
    if errs:
        raise BandsError(f"{path}: invalid bands config:\n  " + "\n  ".join(errs))
    return cfg


# --- series -------------------------------------------------------------------

def parse_t(t) -> _dt.datetime:
    """An ISO date or datetime as a naive UTC datetime, for sorting and windows."""
    if not isinstance(t, str) or not t.strip():
        raise BandsError(f"t must be an ISO date or datetime string, got {t!r}")
    s = t.strip()
    try:
        if _DATE_RE.match(s):
            d = _dt.date.fromisoformat(s)
            return _dt.datetime(d.year, d.month, d.day)
        if s[-1] in "Zz":
            s = s[:-1] + "+00:00"          # 3.10's fromisoformat rejects a bare Z
        dt = _dt.datetime.fromisoformat(s)
    except ValueError:
        raise BandsError(f"t {t!r} is not an ISO date or datetime")
    if dt.tzinfo is not None:
        dt = dt.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return dt


def _number(v, where: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise BandsError(f"{where}: v must be a number, got {v!r}")
    f = float(v)
    if not math.isfinite(f):
        raise BandsError(f"{where}: v must be finite, got {v!r}")
    return f


def parse_series(text: str, source: str = "series") -> list[dict]:
    """JSON `[{"t":..,"v":..}]` (extra keys ignored) or CSV with a `t,v` header.

    Returns points sorted by t, each {"t": original string, "v": float, "_key":
    datetime}. Duplicate timestamps are an error: two values for one instant
    leave the series ambiguous, and silently keeping either one is a guess.
    """
    s = text.lstrip("﻿").strip()
    if not s:
        raise BandsError(f"{source}: series is empty")
    rows: list[tuple] = []
    if s[0] in "[{":
        try:
            data = json.loads(s)
        except json.JSONDecodeError as e:
            raise BandsError(f"{source}: unparseable JSON: {e}")
        if not isinstance(data, list):
            raise BandsError(f"{source}: a JSON series must be an array of {{t, v}}")
        for i, p in enumerate(data):
            if not isinstance(p, dict) or "t" not in p or "v" not in p:
                raise BandsError(f"{source}[{i}]: each point needs 't' and 'v'")
            rows.append((f"{source}[{i}]", p["t"], p["v"]))
    else:
        reader = csv.reader(io.StringIO(s))
        header = [h.strip() for h in next(reader)]
        if header != ["t", "v"]:
            raise BandsError(f"{source}: a CSV series needs a 't,v' header, got {header}")
        for lineno, rec in enumerate(reader, start=2):
            if not any(c.strip() for c in rec):
                continue
            where = f"{source}:{lineno}"
            if len(rec) != 2:
                raise BandsError(f"{where}: expected 2 columns, got {len(rec)}")
            try:
                v = float(rec[1].strip())
            except ValueError:
                raise BandsError(f"{where}: v {rec[1]!r} is not a number")
            rows.append((where, rec[0].strip(), v))
    if not rows:
        raise BandsError(f"{source}: series has no points")

    points, seen = [], {}
    for where, t, v in rows:
        key = parse_t(t)
        if key in seen:
            raise BandsError(f"{where}: duplicate t {t!r} (also {seen[key]!r})")
        seen[key] = t
        points.append({"t": t.strip(), "v": _number(v, where), "_key": key})
    points.sort(key=lambda p: p["_key"])
    return points


def read_input(spec: str) -> str:
    if spec == "-":
        return sys.stdin.read()
    try:
        return Path(spec).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise BandsError(f"no such file: {spec}")
    except OSError as e:
        raise BandsError(f"{spec}: {e}")


# --- detection ----------------------------------------------------------------

def zscore(v: float, mean: float, std: float) -> float:
    if std == 0:
        if v == mean:
            return 0.0
        return math.inf if v > mean else -math.inf
    return (v - mean) / std


def western_electric(zs: list, direction: str) -> list[str]:
    """The rules that fire over `zs` (oldest first), on the allowed side(s)."""
    sides = DIRECTIONS[direction]

    def beyond(z, k, s):
        return z is not None and s * z > k

    last3, last5, last8 = zs[-3:], zs[-5:], zs[-8:]
    fired = []
    if zs and any(beyond(zs[-1], 3, s) for s in sides):
        fired.append("R1")
    if len(last3) == 3 and any(sum(beyond(z, 2, s) for z in last3) >= 2 for s in sides):
        fired.append("R2")
    if len(last5) == 5 and any(sum(beyond(z, 1, s) for z in last5) >= 4 for s in sides):
        fired.append("R3")
    if len(last8) == 8 and any(all(beyond(z, 0, s) for z in last8) for s in sides):
        fired.append("R4")
    return fired


def tier_for(fired: list[str]) -> str:
    if "R1" in fired:
        return "3sigma"
    if "R2" in fired:
        return "2sigma"
    if "R3" in fired or "R4" in fired:
        return "1sigma"
    return "none"


def action_for(cfg: dict, tier: str) -> str:
    return cfg["tiers"][tier]["action"] if tier in BAND_TIERS else "none"


def _z_out(z):
    if z is None or math.isfinite(z):
        return z
    return "+inf" if z > 0 else "-inf"


def _z_in(z, where: str):
    if z is None:
        return None
    if z == "+inf":
        return math.inf
    if z == "-inf":
        return -math.inf
    if isinstance(z, bool) or not isinstance(z, (int, float)) or not math.isfinite(z):
        raise BandsError(f"{where}: z must be a number, '+inf', '-inf' or null, got {z!r}")
    return float(z)


def evaluate(cfg: dict, points: list[dict], config_path="") -> dict:
    """The check result. Pure: same (cfg, points) in, same dict out."""
    n_win, unit = parse_baseline(cfg["baseline"])
    evald = points[-EVAL_WINDOW:]
    before = points[:-EVAL_WINDOW] if len(points) > EVAL_WINDOW else []
    if unit == "days":
        cutoff = points[-1]["_key"] - _dt.timedelta(days=n_win)
        base = [p for p in before if p["_key"] > cutoff]
    else:
        base = before[max(0, len(points) - n_win):]

    mbp = cfg["min_baseline_points"]
    baseline = {"window": cfg["baseline"], "n": len(base), "min_points": mbp,
                "mean": None, "std": None,
                "from": base[0]["t"] if base else None,
                "to": base[-1]["t"] if base else None}
    if len(base) < mbp:
        tier, fired, zs = INSUFFICIENT, [], [None] * len(evald)
    else:
        vals = [p["v"] for p in base]
        mean, std = statistics.mean(vals), statistics.stdev(vals)
        baseline["mean"], baseline["std"] = mean, std
        zs = [zscore(p["v"], mean, std) for p in evald]
        fired = western_electric(zs, cfg["direction"])
        tier = tier_for(fired)
    return {
        "metric": cfg["metric"],
        "tier": tier,
        "action": action_for(cfg, tier),
        "rules_fired": fired,
        "direction": cfg["direction"],
        "baseline": baseline,
        "evaluated": [{"t": p["t"], "v": p["v"], "z": _z_out(z)} for p, z in zip(evald, zs)],
        "config_path": str(config_path),
    }


# --- intent -----------------------------------------------------------------

def load_result(path) -> dict:
    """A `check --json` result, validated enough that rendering cannot lie.

    The tier must be the one its own rules_fired imply: a hand-edited result that
    says 3sigma over an R3-only breach would otherwise mint an intent claiming
    more than the detector found.
    """
    try:
        data = json.loads(read_input(str(path)))
    except json.JSONDecodeError as e:
        raise BandsError(f"{path}: unparseable JSON: {e}")
    if not isinstance(data, dict):
        raise BandsError(f"{path}: result must be a JSON object")
    metric = data.get("metric")
    if not (isinstance(metric, str) and _METRIC_RE.match(metric)):
        raise BandsError(f"{path}: metric must be a slug, got {metric!r}")
    tier = data.get("tier")
    if tier not in ALL_TIERS:
        raise BandsError(f"{path}: tier must be one of {list(ALL_TIERS)}, got {tier!r}")
    fired = data.get("rules_fired")
    if not isinstance(fired, list) or any(r not in RULES for r in fired):
        raise BandsError(f"{path}: rules_fired must be a list drawn from {list(RULES)}")
    implied = INSUFFICIENT if tier == INSUFFICIENT else tier_for(fired)
    if tier == INSUFFICIENT and fired:
        raise BandsError(f"{path}: an insufficient baseline cannot fire rules")
    if implied != tier:
        raise BandsError(f"{path}: tier {tier!r} does not match rules_fired {fired} "
                         f"(which imply {implied!r})")
    b = data.get("baseline")
    if not isinstance(b, dict):
        raise BandsError(f"{path}: baseline must be an object")
    ev = data.get("evaluated")
    if not isinstance(ev, list) or not ev:
        raise BandsError(f"{path}: evaluated must be a non-empty list")
    for i, p in enumerate(ev):
        where = f"{path}: evaluated[{i}]"
        if not isinstance(p, dict) or not isinstance(p.get("t"), str):
            raise BandsError(f"{where}: needs a string 't'")
        p["v"] = _number(p.get("v"), where)
        p["z"] = _z_in(p.get("z"), where)
    if tier in BAND_TIERS:
        for k in ("mean", "std"):
            b[k] = _number(b.get(k), f"{path}: baseline.{k}")
        if not isinstance(b.get("n"), int) or isinstance(b.get("n"), bool):
            raise BandsError(f"{path}: baseline.n must be an integer")
    return data


def _fmt(x: float) -> str:
    return f"{x:.6g}"


def _fmt_z(z) -> str:
    if z is None:
        return "n/a"
    if math.isinf(z):
        return "+inf" if z > 0 else "-inf"
    return f"{z:+.2f}"


def _cell(v) -> str:
    """Table-cell safe: an unescaped pipe would split the row."""
    return (str(v).replace("\\", "\\\\").replace("|", "\\|")
            .replace("\r", " ").replace("\n", " ").strip())


def _permits(cfg: dict, tier: str) -> str:
    """What this tier lets Claude do. The tools named here are the ones
    `diagnose-cmd` grants at this tier, normalised the same way, so the command
    can never exceed what the intent records."""
    t_cfg = cfg["tiers"][tier]
    tools = diagnose_tools(cfg, tier)
    shown = f"`{normalized_tools(tools)}`" if tools else "no tools (none configured)"
    if t_cfg["action"] == "diagnose":
        return (f"This tier ({tier}, action: diagnose) permits a read-only diagnosis only. "
                f"Claude may use {shown} and may not edit files, open pull "
                f"requests or trigger runbooks. The diagnosis comes back as a "
                f"`## Diagnosis` section for a human to act on.")
    lines = [f"This tier ({tier}, action: propose) permits Claude to act, but only "
             f"through these routes:", ""]
    for r in t_cfg["routes"]:
        if r == "pull_request":
            lines.append("- open a pull request into the review gate")
        else:
            lines.append(f"- trigger the pre-approved runbook `{r.split(':', 1)[1]}`")
    lines += ["", "Nothing else, and nothing that bypasses review."]
    if tools:
        lines += ["", f"A read-only diagnosis with {shown} may run first."]
    return "\n".join(lines)


def render_intent(cfg: dict, result: dict, config_path) -> str:
    """intent.md in the Stage 1: Plan shape. Deterministic, no model."""
    metric, tier = result["metric"], result["tier"]
    b, ev, fired = result["baseline"], result["evaluated"], result["rules_fired"]
    last = ev[-1]
    mean, std = b["mean"], b["std"]
    desc = cfg.get("description")
    subject = f"`{metric}`" + (f" ({desc.strip()})" if desc else "")
    out = [
        f"# Intent: {metric} breached its {tier} control band",
        "",
        "Author: bands detector (deterministic, no model). Status: draft.",
        f"Source: config `{config_path}`; rules fired: {', '.join(fired)}.",
        "",
        "## Problem",
        "",
        f"{subject} was {_fmt(last['v'])} at {last['t']} (z = {_fmt_z(last['z'])}), against "
        f"a baseline mean of {_fmt(mean)} ± {_fmt(std)} (sample standard deviation) over "
        f"{b['n']} points from {b.get('from')} to {b.get('to')} ({b.get('window')}).",
        "",
        f"Rules fired (Western Electric, breach side: {cfg['direction']}):",
        "",
    ]
    out += [f"- {r}: {RULES[r]}" for r in fired]
    out += ["", "## Evidence", "", "| t | value | z |", "|---|---|---|"]
    out += [f"| {_cell(p['t'])} | {_fmt(p['v'])} | {_fmt_z(p['z'])} |" for p in ev]
    out += [
        "",
        "## Proposed outcome",
        "",
        f"Return `{metric}` to within its 1σ band, [{_fmt(mean - std)}, {_fmt(mean + std)}] "
        f"(baseline mean ± one sample standard deviation), and keep it there.",
        "",
        _permits(cfg, tier),
        "",
        "## Affected users and systems",
        "",
        cfg["intent"]["affected"].strip(),
        "",
        "## Constraints",
        "",
        "- Detection is deterministic: mean and sample standard deviation over a rolling "
        "window, plus Western Electric rules. No model was involved in raising this intent.",
        "- Any change goes through the normal pull-request review gate.",
        "- This intent grants no production access.",
        "",
        "## Open questions",
        "",
        "- Is this a real regression, or a baseline shift that should re-anchor the band?",
    ]
    if std == 0:
        out.append("- The baseline had zero variance, so any departure reads as an infinite "
                   "z. Is a zero-width band meaningful for this metric?")
    return "\n".join(out) + "\n"


def _check_date(s: str) -> str:
    if not (isinstance(s, str) and _DATE_RE.match(s)):
        raise BandsError(f"--date must be YYYY-MM-DD, got {s!r}")
    try:
        _dt.date.fromisoformat(s)
    except ValueError:
        raise BandsError(f"--date {s!r} is not a calendar date")
    return s


# --- series adapter -------------------------------------------------------------

# For CI health a run that timed out or never started failed. cancelled, skipped,
# neutral and action_required say nothing about whether the code works.
FAILED_CONCLUSIONS = ("failure", "timed_out", "startup_failure")

def ci_failure_rate(runs, now: _dt.datetime | None = None,
                    include_today: bool = False) -> list[dict]:
    """`gh run list --json conclusion,createdAt,status` -> daily failure share.

    Counts completed runs. A run that timed out or never started failed, as far as
    CI health goes, so the failures are FAILED_CONCLUSIONS; the successes are
    `success`. Everything else (in progress, cancelled, skipped, neutral,
    action_required, ...) is neither and is not counted. A day is the UTC
    date of createdAt. Days with no counted run are omitted, not written as 0: a
    zero there would read as "nothing failed" when nothing was measured. `n` is
    the number of runs counted that day (ignored by `check`). gh lists 20 runs by
    default, so a caller wants `--limit` large enough to cover the window.

    The current UTC day (and any later one) is dropped unless include_today: it is
    incomplete, and as the LAST point it is exactly the point R1 and R2 judge. A
    scheduled run at 06:00 UTC would otherwise compare six hours of today (one
    failure in two runs) against full days and open a 3σ intent from one failure.
    `now` is naive UTC and defaults to the clock; tests pass it.
    """
    today = (now or _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)).date()
    if not isinstance(runs, list):
        raise BandsError("runs JSON must be an array (the output of gh run list --json)")
    days: dict[str, list[int]] = {}
    for i, r in enumerate(runs):
        if not isinstance(r, dict):
            raise BandsError(f"runs[{i}]: must be an object")
        conclusion = r.get("conclusion")
        if r.get("status") != "completed" or (conclusion != "success"
                                              and conclusion not in FAILED_CONCLUSIONS):
            continue
        try:
            day = parse_t(r.get("createdAt")).date().isoformat()
        except BandsError as e:
            raise BandsError(f"runs[{i}].createdAt: {e}")
        if not include_today and day >= today.isoformat():
            continue
        counts = days.setdefault(day, [0, 0])
        counts[conclusion in FAILED_CONCLUSIONS] += 1
    return [{"t": d, "v": f / (s + f), "n": s + f}
            for d, (s, f) in sorted(days.items()) if s + f]


# --- diagnose ---------------------------------------------------------------------

def diagnose_tools(cfg: dict, tier: str):
    """The tools string a diagnosis at `tier` runs with, or None if none is configured.

    The breaching tier's own `tools` when it defines them, else the 2sigma tier's.
    Using 2sigma's unconditionally let the command exceed what a 3sigma intent
    recorded, and made a valid cautious config (2sigma: log) fail every run.
    """
    tiers = cfg.get("tiers") or {}
    tools = (tiers.get(tier) or {}).get("tools")
    if tools is None:
        tools = (tiers.get("2sigma") or {}).get("tools")
    return tools


def diagnose_argv(cfg: dict, tier: str, intent_path):
    """The exact argv for a read-only diagnosis at `tier`, or None when no diagnose
    tools are configured. Refuses any tool outside the allowlist.

    Checked here as well as in validate_config: this function is importable, and a
    caller that built `cfg` by hand must not get a write-capable command out of it.
    """
    tools = diagnose_tools(cfg, tier)
    if tools is None:
        return None
    errs = tool_errors(tools)
    if errs:
        raise BandsError("refusing to build a diagnose command: " + "; ".join(errs))
    # --restricted removes the code-running tools (none is named in --tools) and
    # ignores the user, project and local settings files, so no allow rule or hook
    # from them applies; --strict-mcp-config with no --mcp-config loads no MCP
    # server; --tools limits the built-in tools the session can see to the
    # allowlisted names; dontAsk denies whatever is not pre-approved instead of
    # prompting; --disallowedTools denies the write tools. Managed settings still
    # apply: a grant, not a sandbox. Never --dangerously-skip-permissions.
    return ["claude", "-p", DIAGNOSE_PROMPT.format(intent=intent_path),
            "--restricted", "--strict-mcp-config",
            "--tools", base_tools(tools),
            "--allowedTools", normalized_tools(tools),
            "--disallowedTools", " ".join(WRITE_TOOLS),
            "--permission-mode", "dontAsk",
            "--output-format", "json"]


# --- CLI ------------------------------------------------------------------------------

def _summary(r: dict) -> str:
    b = r["baseline"]
    if r["tier"] == INSUFFICIENT:
        return (f"{r['metric']}: tier={INSUFFICIENT} (baseline n={b['n']} < min "
                f"{b['min_points']}); the band is unmeasured, not healthy")
    last = r["evaluated"][-1]
    z = _z_in(last["z"], "z")
    return (f"{r['metric']}: tier={r['tier']} action={r['action']} "
            f"rules={','.join(r['rules_fired']) or '-'} baseline n={b['n']} "
            f"mean={_fmt(b['mean'])} std={_fmt(b['std'])} last={_fmt(last['v'])} "
            f"(z={_fmt_z(z)})")


def cmd_check(args) -> int:
    cfg = load_config(args.config)
    points = parse_series(read_input(args.series), args.series)
    result = evaluate(cfg, points, args.config)
    print(json.dumps(result, indent=2) if args.json else _summary(result))
    if args.fail_on:
        if result["tier"] == INSUFFICIENT:
            print(f"bands: {result['metric']} has an insufficient baseline; the band is "
                  f"unmeasured, which --fail-on does not treat as healthy (exit 4)",
                  file=sys.stderr)
            return 4
        if BREACH_ORDER.index(result["tier"]) >= BREACH_ORDER.index(args.fail_on):
            return 3
    return 0


def cmd_intent(args) -> int:
    cfg = load_config(args.config)
    result = load_result(args.result)
    metric, tier = result["metric"], result["tier"]
    if metric != cfg["metric"]:
        raise BandsError(f"result is for metric {metric!r} but the config is for "
                         f"{cfg['metric']!r}")

    def report(path, created: bool, action: str, message: str) -> int:
        # --json is the machine contract: a caller proceeds (diagnose, open a PR)
        # only on created=true. `created` is false whenever the file already
        # existed, --force or not, so a re-run never re-diagnoses a filed intent.
        if args.json:
            # dedupe_key names the incident, not the day: a sustained breach keeps
            # one open PR per metric and tier (branch bands/<dedupe_key>).
            print(json.dumps({"path": str(path) if path else None, "created": created,
                              "tier": tier, "action": action,
                              "dedupe_key": f"{metric}-{tier}"}))
        else:
            print(message)
        return 0

    if tier not in BAND_TIERS:
        return report(None, False, "none", f"{metric}: tier {tier}; no breach to write an "
                                           f"intent for")
    action = action_for(cfg, tier)
    if result.get("action") != action:
        raise BandsError(f"result says action {result.get('action')!r} for {tier} but this "
                         f"config says {action!r}; re-run check with this config")
    if action == "log":
        return report(None, False, action,
                      f"log only: {metric} is at {tier} ({', '.join(result['rules_fired'])}); "
                      f"no intent written")
    date = _check_date(args.date) if args.date else \
        parse_t(result["evaluated"][-1]["t"]).date().isoformat()
    out_dir = Path(args.out_dir)
    path = out_dir / f"{date}-{metric}-{tier}.md"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        if existed and not args.force:
            print(f"bands: {path} exists; not overwritten (pass --force)", file=sys.stderr)
            return report(path, False, action, str(path))
        path.write_text(render_intent(cfg, result, args.config), encoding="utf-8")
    except OSError as e:
        raise BandsError(f"cannot write {path}: {e}")
    return report(path, not existed, action, str(path))


def cmd_series(args) -> int:
    try:
        runs = json.loads(read_input(args.runs_json))
    except json.JSONDecodeError as e:
        raise BandsError(f"{args.runs_json}: unparseable JSON: {e}")
    now = parse_t(args.now) if args.now else None
    print(json.dumps(ci_failure_rate(runs, now=now, include_today=args.include_today),
                     indent=2))
    return 0


def cmd_diagnose(args) -> int:
    cfg = load_config(args.config)
    if args.result:
        result = load_result(args.result)
        if result["metric"] != cfg["metric"]:
            raise BandsError(f"result is for metric {result['metric']!r} but the config is "
                             f"for {cfg['metric']!r}")
        tier = result["tier"]
    else:
        tier = args.tier
    # A skip is exit 0, never 2: a config that diagnoses nothing is a valid config,
    # and a caller under `set -e` must not fail every run because of it.
    if tier not in BAND_TIERS:
        print(json.dumps({"skip": f"tier {tier} has nothing to diagnose"}))
        return 0
    argv = diagnose_argv(cfg, tier, args.intent)
    if argv is None:
        print(json.dumps({"skip": "no diagnose tools configured"}))
        return 0
    if not Path(args.intent).is_file():
        raise BandsError(f"no intent file at {args.intent}")
    print(json.dumps(argv))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bands", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="evaluate a series against its control band")
    c.add_argument("config", help="bands YAML")
    c.add_argument("--series", required=True, help="JSON [{t,v}] or CSV t,v; '-' for stdin")
    c.add_argument("--json", action="store_true", help="print the full result as JSON")
    c.add_argument("--fail-on", choices=BAND_TIERS,
                   help="exit 3 when the tier is at or above this (4 if unmeasured)")

    i = sub.add_parser("intent", help="write intent.md for a diagnose/propose-tier result")
    i.add_argument("config", help="bands YAML")
    i.add_argument("--result", required=True, help="output of `check --json`")
    i.add_argument("--out-dir", required=True)
    i.add_argument("--date", help="YYYY-MM-DD (default: the date of the last evaluated point)")
    i.add_argument("--force", action="store_true", help="overwrite an existing intent file")
    i.add_argument("--json", action="store_true",
                   help='print {"path", "created", "tier", "action", "dedupe_key"}; '
                        'created is false when the file already existed')

    s = sub.add_parser("series", help="build a series from raw data")
    s.add_argument("kind", choices=["ci-failure-rate"])
    s.add_argument("--runs-json", required=True,
                   help="gh run list --json conclusion,createdAt,status output; '-' for stdin")
    s.add_argument("--include-today", action="store_true",
                   help="keep the current, incomplete UTC day (dropped by default)")
    s.add_argument("--now", help="ISO date/datetime to treat as now (default: the clock)")

    d = sub.add_parser("diagnose-cmd", help="print the read-only diagnose argv as JSON")
    d.add_argument("config", help="bands YAML")
    d.add_argument("--intent", required=True, help="the intent file to diagnose")
    which = d.add_mutually_exclusive_group(required=True)
    which.add_argument("--result", help="output of `check --json`; its tier picks the tools")
    which.add_argument("--tier", choices=BAND_TIERS, help="the breaching tier")

    args = ap.parse_args(argv)
    handler = {"check": cmd_check, "intent": cmd_intent, "series": cmd_series,
               "diagnose-cmd": cmd_diagnose}[args.cmd]
    try:
        return handler(args)
    except BandsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
