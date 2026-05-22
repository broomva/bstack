#!/usr/bin/env bash
# bstack/scripts/compute-arc-status.sh — Per-arc closure-contract verdict reader.
#
# Reads .control/arcs.yaml (workspace) → falls back to the bundled template.
# For each declared arc, runs the sensor, reads the most recent termination
# event from .control/audit/arc-<id>.jsonl (if present), and emits a verdict:
#   - green   — sensor passes + termination predicate satisfied
#   - yellow  — sensor returned data but termination still pending
#   - red     — sensor failed OR termination predicate evaluated false
#   - unknown — sensor could not be run (missing source, no data yet)
#
# Mirrors the shape of scripts/compute-budget-status.sh exactly. This is the
# generalization of the 4-hard-coded-RCS-layer 5-tuple to N user-declared
# domain arcs. The agent's reasoning is the universal Π — when actuator.kind
# is "agent_reasoning" the controller binding is implicit.
#
# Consumed by:
#   - scripts/doctor.sh §20 (arcs-declared health section)
#   - future: CI workflow that surfaces per-arc verdicts on substantive PRs
#
# Usage:
#   bash scripts/compute-arc-status.sh                  # JSON output
#   bash scripts/compute-arc-status.sh --human          # human-readable table
#   bash scripts/compute-arc-status.sh --workspace=...  # custom workspace
#
# Exit codes:
#   0 — all arcs green
#   1 — at least one arc red
#   2 — arcs config not found AND no template (config missing)
#   3 — python3 unavailable

set -uo pipefail

WORKSPACE="${BROOMVA_WORKSPACE:-$PWD}"
FORMAT="json"
BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for arg in "$@"; do
    case "$arg" in
        --workspace=*) WORKSPACE="${arg#*=}" ;;
        --human)       FORMAT="human" ;;
        --json)        FORMAT="json" ;;
        --help|-h)
            grep -E '^#( |$)' "$0" | sed 's/^# \?//' | head -28
            exit 0
            ;;
    esac
done

# Locate arcs config — workspace overrides the bundled template.
CONFIG=""
if [ -f "$WORKSPACE/.control/arcs.yaml" ]; then
    CONFIG="$WORKSPACE/.control/arcs.yaml"
elif [ -f "$BSTACK_REPO/assets/templates/arcs.yaml.template" ]; then
    CONFIG="$BSTACK_REPO/assets/templates/arcs.yaml.template"
fi

if [ -z "$CONFIG" ] || [ ! -f "$CONFIG" ]; then
    echo "compute-arc-status: arcs config not found (looked at $WORKSPACE/.control/arcs.yaml and bundled template)" >&2
    exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "compute-arc-status: python3 not available" >&2
    exit 3
fi

python3 - "$CONFIG" "$WORKSPACE" "$FORMAT" <<'PYEOF'
import sys, json, time, re, subprocess
from pathlib import Path

config_path, workspace, fmt = sys.argv[1], sys.argv[2], sys.argv[3]


# ── Minimal YAML parser for the arcs.yaml shape ─────────────────────────────
# Mirrors the inline-parser approach used in scripts/workspace.py
# (_yaml_minimal_parse). Supports the exact shape validated by
# schemas/arcs.v1.json: top-level scalar keys + an `arcs:` sequence of dicts
# whose values are either scalars, dicts of scalars, or lists of scalars.
# We prefer PyYAML when available (richer + battle-tested), else fall back.

def yaml_load(path):
    text = Path(path).read_text()
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except ImportError:
        return _yaml_minimal(text)


def _indent_of(line):
    return len(line) - len(line.lstrip(" "))


def _scalar(v):
    v = v.strip()
    if v == "":
        return None
    if v.startswith("\"") and v.endswith("\"") and len(v) >= 2:
        return v[1:-1]
    if v.startswith("'") and v.endswith("'") and len(v) >= 2:
        return v[1:-1]
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if v.lower() in ("null", "~"):
        return None
    try:
        if "." in v:
            return float(v)
        return int(v)
    except ValueError:
        return v


def _yaml_minimal(text):
    """Bare-minimum YAML for the arcs.yaml shape (mirrors workspace.py)."""
    # Strip comments + trailing whitespace; keep blank lines as separators.
    raw_lines = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Inline comments: strip after `#` only if preceded by whitespace.
        m = re.search(r"\s+#.*$", line)
        if m:
            line = line[: m.start()]
        raw_lines.append(line.rstrip())

    data = {}
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]
        if not line.strip():
            i += 1
            continue
        if _indent_of(line) != 0:
            i += 1
            continue
        if line.endswith(":"):
            key = line[:-1].strip()
            if key == "arcs":
                items, i = _yaml_parse_arc_list(raw_lines, i + 1, base_indent=2)
                data["arcs"] = items
                continue
            data[key] = {}
        else:
            k, _, v = line.partition(":")
            data[k.strip()] = _scalar(v)
        i += 1
    return data


def _yaml_parse_block(lines, start, base_indent):
    """Parse a YAML block (sequence-of-dicts OR dict-with-nested) starting at
    line[start]. Stops when indent drops below base_indent.

    Returns (parsed_value, next_line_index). parsed_value is either:
      - a list of dicts when the first line at base_indent starts with `- `
      - a dict of scalars/lists/dicts when the first line is `key: ...`
    Handles arbitrary nesting recursively; built specifically for the
    arcs.yaml shape (dict keys whose values are scalars, lists of scalars,
    or sub-dicts whose values may themselves be lists)."""
    i = start
    # Skip leading blanks.
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return ({}, i)
    first = lines[i]
    if _indent_of(first) < base_indent:
        return ({}, i)
    stripped_first = first.lstrip()

    # Sequence-of-dicts mode: each item starts `- key: ...` at base_indent.
    if stripped_first.startswith("- "):
        items = []
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            ind = _indent_of(line)
            if ind < base_indent:
                break
            stripped = line.lstrip()
            if ind == base_indent and stripped.startswith("- "):
                # Start new item: parse the inline key (always present in
                # arcs.yaml — `- id: ...`) then any continuation lines at
                # base_indent+2.
                rest = stripped[2:]
                item = {}
                if rest:
                    k, _, v = rest.partition(":")
                    key = k.strip()
                    if v.strip():
                        item[key] = _scalar(v)
                    else:
                        # Inline key with empty value — gather nested block.
                        sub, ni = _yaml_parse_block(lines, i + 1, base_indent + 4)
                        item[key] = sub
                        i = ni
                        continue
                # Gather sibling keys at base_indent + 2.
                ni = i + 1
                sub, ni = _yaml_parse_block(lines, ni, base_indent + 2)
                if isinstance(sub, dict):
                    item.update(sub)
                items.append(item)
                i = ni
                continue
            # Defensive — line at base_indent that's not a `- ` shouldn't
            # happen in a well-formed sequence; advance to avoid infinite loop.
            i += 1
        return (items, i)

    # Dict mode: each line at base_indent is `key: ...`. Lists and sub-dicts
    # appear as deeper-indented blocks following an empty-value key.
    result = {}
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        ind = _indent_of(line)
        if ind < base_indent:
            break
        if ind > base_indent:
            # Shouldn't happen if caller respects indent contract.
            i += 1
            continue
        stripped = line.lstrip()
        # Top-of-block sequence detection: if the very first line at this
        # indent is `- ...`, the block is actually a list belonging to the
        # *previous* key. We never enter here in that case because that key
        # would have already been handled with its sub-block.
        if stripped.startswith("- "):
            break
        k, _, v = stripped.partition(":")
        key = k.strip()
        if v.strip():
            result[key] = _scalar(v)
            i += 1
            continue
        # Empty value — peek ahead to decide list vs dict.
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines) or _indent_of(lines[j]) <= base_indent:
            result[key] = {}
            i += 1
            continue
        peek = lines[j].lstrip()
        if peek.startswith("- "):
            # List of scalars (the only list shape arcs.yaml uses).
            sublist = []
            k2 = j
            sub_indent = _indent_of(lines[j])
            while k2 < len(lines):
                ll = lines[k2]
                if not ll.strip():
                    k2 += 1
                    continue
                lind = _indent_of(ll)
                if lind < sub_indent:
                    break
                if lind == sub_indent and ll.lstrip().startswith("- "):
                    sublist.append(_scalar(ll.lstrip()[2:]))
                    k2 += 1
                    continue
                # Deeper continuation — not used in arcs.yaml; bail out.
                break
            result[key] = sublist
            i = k2
            continue
        # Nested dict (sensor:, actuator:, termination:).
        sub_indent = _indent_of(lines[j])
        sub, ni = _yaml_parse_block(lines, j, sub_indent)
        result[key] = sub if isinstance(sub, dict) else {}
        i = ni
    return (result, i)


def _yaml_parse_arc_list(lines, start, base_indent):
    """Compatibility wrapper — the arcs: top-level key always opens a
    sequence-of-dicts at base_indent=2."""
    parsed, end = _yaml_parse_block(lines, start, base_indent)
    if isinstance(parsed, list):
        return parsed, end
    return [], end


# ── Load + validate arcs ────────────────────────────────────────────────────
arcs_data = yaml_load(config_path)
if not isinstance(arcs_data, dict):
    print(json.dumps({"error": "config not a mapping", "config_path": config_path}))
    sys.exit(2)

schema_version = arcs_data.get("schema_version")
if schema_version != 1:
    msg = f"unsupported schema_version: {schema_version!r} (expected 1)"
    if fmt == "human":
        print(f"compute-arc-status: {msg}")
    else:
        print(json.dumps({"error": msg, "config_path": config_path}))
    sys.exit(2)

arcs = arcs_data.get("arcs") or []

audit_dir = Path(workspace) / ".control" / "audit"
now_ms = int(time.time() * 1000)


def read_jsonl_tail(path, max_rows=50):
    if not path.exists():
        return []
    rows = []
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        return []
    return rows[-max_rows:]


def run_sensor(arc):
    """Returns (status, observed) where status is 'pass'/'fail'/'no_data'."""
    sensor = arc.get("sensor") or {}
    kind = sensor.get("kind")
    source = sensor.get("source") or ""
    expr = sensor.get("expr") or ""
    if not source:
        return ("no_data", {"reason": "sensor.source empty"})

    if kind == "exit_code":
        try:
            rc = subprocess.run(
                ["bash", "-c", source],
                cwd=workspace,
                timeout=10,
                capture_output=True,
            ).returncode
            return (("pass" if rc == 0 else "fail"), {"exit_code": rc})
        except Exception as e:
            return ("no_data", {"reason": f"exec failed: {e.__class__.__name__}"})

    if kind == "json_path":
        # Treat the source as a command; the expr is informational here.
        try:
            cp = subprocess.run(
                ["bash", "-c", source],
                cwd=workspace,
                timeout=10,
                capture_output=True,
                text=True,
            )
            if cp.returncode != 0:
                return ("fail", {"exit_code": cp.returncode, "stderr": cp.stderr[:160]})
            return ("pass", {"stdout_bytes": len(cp.stdout)})
        except Exception as e:
            return ("no_data", {"reason": f"exec failed: {e.__class__.__name__}"})

    if kind == "log_match":
        # Source = log path; expr = regex.
        p = Path(workspace) / source if not source.startswith("/") else Path(source)
        if not p.exists():
            return ("no_data", {"reason": f"log path missing: {source}"})
        try:
            text = p.read_text(errors="ignore")[-100_000:]
            if not expr:
                return ("no_data", {"reason": "log_match requires expr"})
            return (("pass" if re.search(expr, text) else "fail"), {"bytes_read": len(text)})
        except Exception as e:
            return ("no_data", {"reason": f"read failed: {e.__class__.__name__}"})

    if kind == "metric_threshold":
        # Source = command that prints a single number on stdout; expr informational.
        try:
            cp = subprocess.run(
                ["bash", "-c", source],
                cwd=workspace,
                timeout=10,
                capture_output=True,
                text=True,
            )
            if cp.returncode != 0:
                return ("fail", {"exit_code": cp.returncode})
            out = cp.stdout.strip().split()[0] if cp.stdout.strip() else ""
            try:
                val = float(out)
                return ("pass", {"value": val})
            except ValueError:
                return ("no_data", {"reason": f"non-numeric metric: {out!r}"})
        except Exception as e:
            return ("no_data", {"reason": f"exec failed: {e.__class__.__name__}"})

    return ("no_data", {"reason": f"unknown sensor.kind: {kind}"})


def evaluate_termination(arc, sensor_status, last_event):
    """Apply termination kind+expr to determine verdict.
    Returns one of: 'green' | 'yellow' | 'red' | 'unknown'."""
    term = arc.get("termination") or {}
    kind = term.get("kind")
    expr = term.get("expr") or ""

    if sensor_status == "no_data":
        return "unknown"

    if kind == "exit_zero":
        return "green" if sensor_status == "pass" else "red"

    if kind == "predicate":
        # We can't safely eval arbitrary predicates; honor the most-recent
        # arc-<id>.jsonl event that recorded a verdict explicitly.
        if last_event and last_event.get("verdict"):
            v = last_event["verdict"]
            return v if v in ("green", "yellow", "red") else "yellow"
        # Fall back: sensor passing → yellow (running, not yet closed).
        return "yellow" if sensor_status == "pass" else "red"

    if kind == "score_threshold":
        # Honor last event's score if recorded; else treat as yellow.
        if last_event and isinstance(last_event.get("score"), (int, float)):
            score = last_event["score"]
            m = re.match(r"\s*score\s*>=\s*([0-9.]+)\s*$", expr)
            if m:
                threshold = float(m.group(1))
                return "green" if score >= threshold else "red"
            return "yellow"
        return "yellow" if sensor_status == "pass" else "red"

    if kind == "wallclock":
        # Wallclock terminations are time-bounded: green if last event within
        # tau_a; red if older. Sensor result modulates intermediate state.
        tau_a_ms = int(float(arc.get("tau_a", 0)) * 1000)
        if last_event and isinstance(last_event.get("ts"), (int, float)):
            age_ms = now_ms - int(last_event["ts"])
            if age_ms <= tau_a_ms:
                return "green"
            return "red"
        return "yellow" if sensor_status == "pass" else "unknown"

    return "unknown"


# ── Iterate arcs ────────────────────────────────────────────────────────────
arc_results = []
for arc in arcs:
    aid = arc.get("id", "?")
    events = read_jsonl_tail(audit_dir / f"arc-{aid}.jsonl")
    last_event = events[-1] if events else None
    sensor_status, observed = run_sensor(arc)
    verdict = evaluate_termination(arc, sensor_status, last_event)
    arc_results.append({
        "id": aid,
        "description": arc.get("description"),
        "sensor_kind": (arc.get("sensor") or {}).get("kind"),
        "actuator_kind": (arc.get("actuator") or {}).get("kind"),
        "termination_kind": (arc.get("termination") or {}).get("kind"),
        "tau_a_seconds": arc.get("tau_a"),
        "shield_ref": arc.get("shield_ref"),
        "sensor_status": sensor_status,
        "observed": observed,
        "last_event_ts": (last_event or {}).get("ts"),
        "verdict": verdict,
    })

any_red = any(a["verdict"] == "red" for a in arc_results)
all_green = bool(arc_results) and all(a["verdict"] == "green" for a in arc_results)

report = {
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "workspace": workspace,
    "config_path": config_path,
    "arc_count": len(arc_results),
    "arcs": arc_results,
    "all_green": all_green,
    "any_red": any_red,
}

if fmt == "human":
    print("Closure-Contract Arc Status")
    print(f"  Workspace:   {workspace}")
    print(f"  Config:      {config_path}")
    print(f"  Timestamp:   {report['timestamp']}")
    print(f"  Arc count:   {report['arc_count']}")
    print("")
    print(f"  {'ID':32} {'Sensor':14} {'Actuator':18} {'Termination':16} {'τ_a (s)':>10} {'Verdict':>10}")
    print(f"  {'─'*32} {'─'*14} {'─'*18} {'─'*16} {'─'*10} {'─'*10}")
    for a in arc_results:
        print(
            f"  {a['id'][:32]:32} "
            f"{(a['sensor_kind'] or '-')[:14]:14} "
            f"{(a['actuator_kind'] or '-')[:18]:18} "
            f"{(a['termination_kind'] or '-')[:16]:16} "
            f"{str(a['tau_a_seconds']):>10} "
            f"{a['verdict']:>10}"
        )
    print("")
    print(f"  all_green: {all_green}")
    print(f"  any_red:   {any_red}")
else:
    print(json.dumps(report, indent=2))

if any_red:
    sys.exit(1)
sys.exit(0)
PYEOF
