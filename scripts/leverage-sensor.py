#!/usr/bin/env python3
"""
leverage-sensor.py — the sensor h of the bstack self-improvement loop.

Reads raw Claude Code session transcripts (facts the agent cannot fake) and
computes behavioral + outcome metrics tagged by RCS recursion level, compares
them to the reference r in .control/leverage-setpoints.yaml, and emits the error
e = r - h plus a per-level closure verdict.

This REPLACES the l0/l1 audit hooks, which read fields Claude Code never emits
(l0 latency_ms = 100% null; l1 tool_call_count = 100% zero). Every number here is
derived from transcript STRUCTURE (message.content[].type, tool_result.is_error),
never from the agent's own prose — so h ⟂ U (the sensor is causally independent
of the controller it grades).

Portability: the workspace and its Claude Code transcript directory are derived,
not hardcoded — so this runs in any bstack workspace, not just the origin one.

Outputs:
  stdout : human summary (default) | --json | --brief (SessionStart wire) | --closure
  append : <workspace>/.control/leverage-metrics.jsonl  (time series)
  write  : <workspace>/.control/leverage-state.json      (latest snapshot + errors)

Usage:
  leverage-sensor.py [--workspace DIR] [--transcripts GLOB] [--window N]
                     [--json|--brief|--closure] [--cached] [--throttle SEC] [--no-store]
"""
import argparse
import glob
import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

HOME = os.path.expanduser("~")


def resolve_workspace(arg=None):
    if arg:
        return os.path.abspath(arg)
    env = os.environ.get("BSTACK_WORKSPACE") or os.environ.get("BROOMVA_WORKSPACE")
    if env:
        return os.path.abspath(env)
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        if top:
            return top
    except Exception:
        pass
    return os.getcwd()


def transcript_glob(workspace, arg=None):
    """Claude Code stores transcripts at ~/.claude/projects/<mangled-abspath>/*.jsonl
    where the workspace absolute path is mangled by replacing every non-alnum/underscore
    char with '-'. Derive it so the sensor is workspace-agnostic."""
    if arg:
        return arg
    env = os.environ.get("CLAUDE_TRANSCRIPTS")
    if env:
        return env
    # Claude Code replaces EVERY non-alphanumeric char (incl. '_' and '.') with
    # '-'. Verified against a real projects dir: /private/var/folders/g9/_dhv_...
    # is stored as -private-var-folders-g9--dhv-... — so '_' must be hyphenated
    # too. Keeping '_' silently mismatches any path with an underscore (e.g.
    # work/sde_vault, build_dir) → 0 files → false "sensor DEAD" forever.
    mangled = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(workspace))
    return os.path.join(HOME, ".claude", "projects", mangled, "*.jsonl")


# --- detection patterns (transcript facts, not self-report) -----------------
CONTINUE_RE = re.compile(
    r"^\s*(continue|proceed|go on|go ahead|keep going|keep it up|carry on|"
    r"resume|next|go|ship it|do it|yes[,. ]*(continue|proceed|go|please)?|"
    r"lgtm|approved?)\s*(please|pls|now|thanks?)?[.!\s]*$",
    re.IGNORECASE,
)
READ_BEFORE_EDIT_RE = re.compile(
    r"not been read|read (it|the file)( yet)? (first|before)|"
    r"must read the file before|read .* before (edit|writ)|"
    r"has been modified since|modified since (you |it was )?read",
    re.IGNORECASE,
)
NUDGE_RE = re.compile(
    r"\b(continue|proceed|go ahead|keep going|keep it up|carry on|resume|"
    r"go on|ship it|lgtm)\b",
    re.IGNORECASE,
)
# Knowledge consumption: a Skill(kg/checkit) call OR a direct Read/Grep/Glob of
# the entity store (the kg LLM-as-index thesis). Detecting only the skill name is
# a carrier-state false-0.0. Path fragments are configurable via setpoints.knowledge_paths.
DEFAULT_KG_READ = r"research/entities|research/notes|docs/knowledge-index|knowledge"
KG_SKILLS = {"kg", "checkit"}
PRODUCT_EDIT_RE = re.compile(r"/(apps|core|work|freelance|crm|packages|services)/", re.IGNORECASE)
META_EDIT_RE = re.compile(
    r"/(research|docs|\.control|\.claude|skills|scripts|bstack)/|"
    r"/(CLAUDE|AGENTS|METALAYER)\.md$|/Makefile$",
    re.IGNORECASE,
)
INJECTED_MARKERS = ("<command-", "<local-command", "system-reminder",
                    "Autonomous loop", "autonomous-loop", "caveat:")

# Fallback level map (used when a setpoint omits `level:`). RCS recursion levels:
# L0 external plant (tool), L1 agent internal (reflex), L2 meta-control, L3 governance.
DEFAULT_LEVELS = {
    "m1": "L1", "m2": "L0", "m3": "L0", "m4": "L0", "m5": "L2", "m6": "L3",
}


DEGRADED_SETPOINTS = {"window_days": 7, "metrics": []}


def coerce_setpoints(raw, path="<setpoints>"):
    """Normalize whatever YAML produced into the shape the rest of the file assumes.

    `yaml.safe_load` returns whatever the document says, and the document is
    hand-edited: a root list (`- id: m6`), a scalar, `metrics: [oops]`, or a metric
    entry with no `id` are all VALID YAML. Each one used to reach
    `{m["id"]: m for m in setpoints.get("metrics", [])}` and raise, which the
    SessionStart hook then swallows via `|| true` -- no brief, no error, no signal.

    Structural validity is checked ONCE here, on the only path that reads the file,
    rather than at each consumer. The cached path already refused a non-mapping policy
    (round 4); doing it only there was a one-site fix for a two-site defect.

    A malformed ENTRY is dropped with a warning while the rest of the policy stands,
    because one bad row should not disarm every other setpoint."""
    if not isinstance(raw, dict):
        print(f"[leverage-sensor] WARN setpoints ({path}) is a {type(raw).__name__}, "
              "expected a mapping — treating as empty", file=sys.stderr)
        return dict(DEGRADED_SETPOINTS)
    metrics = raw.get("metrics")
    if metrics is None:
        metrics = []
    elif not isinstance(metrics, list):
        print(f"[leverage-sensor] WARN setpoints.metrics is a {type(metrics).__name__}, "
              "expected a list — treating as empty", file=sys.stderr)
        metrics = []
    clean, seen = [], set()
    for i, m in enumerate(metrics):
        if not isinstance(m, dict):
            print(f"[leverage-sensor] WARN setpoints.metrics[{i}] is a "
                  f"{type(m).__name__}, expected a mapping — skipped", file=sys.stderr)
            continue
        mid = m.get("id")
        if not isinstance(mid, str) or not mid.strip():
            print(f"[leverage-sensor] WARN setpoints.metrics[{i}] has no usable `id` "
                  "— skipped", file=sys.stderr)
            continue
        mid = mid.strip()
        if mid in seen:
            print(f"[leverage-sensor] WARN setpoints.metrics[{i}] duplicates id "
                  f"{mid!r} — later entry skipped", file=sys.stderr)
            continue
        seen.add(mid)
        clean.append(dict(m, id=mid))
    out = dict(raw)
    out["metrics"] = clean
    return out


def load_setpoints(path):
    try:
        import yaml
        with open(path) as f:
            return coerce_setpoints(yaml.safe_load(f), path)
    except Exception as e:
        print(f"[leverage-sensor] WARN could not load setpoints ({path}): {e}", file=sys.stderr)
        return dict(DEGRADED_SETPOINTS)


def iter_lines(path):
    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except OSError:
        return


def user_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [it.get("text", "") for it in content
                 if isinstance(it, dict) and it.get("type") == "text"]
        return " ".join(p for p in parts if p)
    return ""


def is_nudge(text):
    t = text.strip()
    if not t or len(t) >= 80:
        return False
    if any(m.lower() in t.lower() for m in INJECTED_MARKERS):
        return False
    return bool(CONTINUE_RE.match(t) or NUDGE_RE.search(t))


def analyze(glob_pat, window_days, kg_read_re):
    cutoff = time.time() - window_days * 86400
    files = [f for f in glob.glob(glob_pat) if os.path.getmtime(f) >= cutoff]

    sessions = continue_nudges = tool_results = tool_errors = 0
    read_before_edit = sandbox_bypass = edits = kg_sessions = 0
    meta_sessions = product_sessions = 0

    for path in files:
        sessions += 1
        used_kg = edited_product = edited_meta = False
        for obj in iter_lines(path):
            t = obj.get("type")
            if t == "assistant":
                for it in obj.get("message", {}).get("content", []):
                    if not isinstance(it, dict) or it.get("type") != "tool_use":
                        continue
                    name = it.get("name", "")
                    inp = it.get("input", {}) if isinstance(it.get("input"), dict) else {}
                    if name == "Bash" and inp.get("dangerouslyDisableSandbox"):
                        sandbox_bypass += 1
                    if name in ("Edit", "Write", "MultiEdit"):
                        edits += 1
                        fp = str(inp.get("file_path", ""))
                        if PRODUCT_EDIT_RE.search(fp):
                            edited_product = True
                        elif META_EDIT_RE.search(fp):
                            edited_meta = True
                    if name == "Skill" and str(inp.get("skill", "")).lower() in KG_SKILLS:
                        used_kg = True
                    elif name in ("Read", "Grep", "Glob"):
                        # Match ONLY path-bearing fields, never the whole stringified
                        # input — a Grep(pattern="knowledge", path="/unrelated/src") is a
                        # search FOR the word, not a read OF the entity store. The pattern
                        # is agent-authored free text (would break h ⟂ U); the path is
                        # structural. Glob's `pattern` IS its path expression, so include it.
                        if name == "Read":
                            kg_target = str(inp.get("file_path", ""))
                        elif name == "Grep":
                            kg_target = str(inp.get("path", ""))
                        else:  # Glob
                            kg_target = str(inp.get("pattern", "")) + " " + str(inp.get("path", ""))
                        kt = kg_target.lower()
                        if kg_read_re.search(kt) or "kg load" in kt:
                            used_kg = True
            elif t == "user":
                content = obj.get("message", {}).get("content")
                if isinstance(content, list):
                    for it in content:
                        if isinstance(it, dict) and it.get("type") == "tool_result":
                            tool_results += 1
                            if it.get("is_error"):
                                tool_errors += 1
                                body = it.get("content")
                                text = body if isinstance(body, str) else json.dumps(body)
                                if READ_BEFORE_EDIT_RE.search(text or ""):
                                    read_before_edit += 1
                txt = user_text(content)
                if txt:
                    if is_nudge(txt):
                        continue_nudges += 1
                    if "/kg load" in txt.lower() or "kg load" in txt.lower():
                        used_kg = True
        if used_kg:
            kg_sessions += 1
        if edited_product:
            product_sessions += 1
        elif edited_meta:
            meta_sessions += 1

    s = max(sessions, 1)
    working = meta_sessions + product_sessions
    metrics = {
        "m1_continue_nudges_per_session": round(continue_nudges / s, 3),
        "m2_tool_error_rate": round(tool_errors / max(tool_results, 1), 4),
        "m3_read_before_edit_rate": round(read_before_edit / max(edits, 1), 4),
        "m4_permission_bypass_per_session": round(sandbox_bypass / s, 3),
        "m5_kg_load_rate": round(kg_sessions / s, 4),
        "m6_meta_work_session_ratio": round(meta_sessions / working, 4) if working else None,
    }
    raw = {
        "sessions_analyzed": sessions, "continue_nudges": continue_nudges,
        "tool_results": tool_results, "tool_errors": tool_errors,
        "read_before_edit_errors": read_before_edit, "sandbox_bypasses": sandbox_bypass,
        "edits": edits, "kg_sessions": kg_sessions,
        "meta_sessions": meta_sessions, "product_sessions": product_sessions,
    }
    return metrics, raw


def merge_ship_shadow(metrics, raw, workspace, max_age_sec=172800):
    """BRO-1707 SHADOW: merge the exogenous ship-signal (leverage-ship-sensor.py →
    .control/leverage-ship-state.json) as metric `m6s_meta_work_ship_ratio` if the
    state file is fresh (<48h). metric_id() splits on the first "_" → "m6s", which has
    NO setpoint, so evaluate() marks it `no_setpoint` and it can never become `worst`
    or reach the SessionStart nudge. It is present only for calibration until BRO-1709
    promotes it. Any error (missing/stale/malformed) leaves the sensor untouched."""
    try:
        ship_state = os.path.join(workspace, ".control", "leverage-ship-state.json")
        with open(ship_state) as f:
            sd = json.load(f)
        age = time.time() - datetime.fromisoformat(sd["measured_at"]).timestamp()
        r = sd.get("m6s_meta_work_ship_ratio")
        if age < max_age_sec and r is not None:
            metrics["m6s_meta_work_ship_ratio"] = r
            raw["ship_signal"] = sd.get("raw")
    except Exception:
        pass


def is_gradeable(val):
    """Whether a metric value may be compared to a target at all.

    A bool is an int in Python, so `False` sails through a numeric check and then
    grades `ok` against every lower-is-better target (False >= alert is False,
    False > target is False). NaN does the same, because EVERY comparison with NaN
    is False -- it looks like the healthiest possible reading. Both then certify a
    window nobody measured. Rejecting them here keeps the falsehood out of the
    grading path rather than hunting it in the renderers. (BRO-2168)"""
    if isinstance(val, (bool, str, bytes)):
        # bool first: it is an int subclass. str/bytes would otherwise be CONVERTED by
        # float() below, which would silently grade the string "0.4".
        return False
    try:
        f = float(val)
    except (TypeError, ValueError, OverflowError):
        # OverflowError is real: float(10**10000) raises rather than returning inf.
        return False
    return math.isfinite(f)


DIRECTIONS = ("lower_is_better", "higher_is_better")


def normalize_direction(raw):
    """Resolve a setpoint's `direction`, or refuse it.

    The old code was `if direction == "lower_is_better": ... else: <higher branch>`,
    so ANY unrecognized value -- `lower-is-better` with hyphens, a typo, a None from a
    partially-written setpoint -- silently selected the OPPOSITE polarity and inverted
    every comparison for that metric. A breach then reads as healthy. That is the same
    defect as the `status:` field this change exists to fix: an unrecognized enum value
    quietly taking a branch instead of being refused.

    Absent means `lower_is_better` (the historical default). `-`/space normalize to `_`,
    matching the setpoint-status rule, so a separator choice cannot flip a polarity.
    Anything still unrecognized returns None and the caller falls closed.

    Returns (direction|None, note|None)."""
    if raw is None:
        return DIRECTIONS[0], None
    d = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    if d in DIRECTIONS:
        return d, None
    return None, (f"setpoint direction {raw!r} is not one of {DIRECTIONS} "
                  "— not graded (an unrecognized direction would invert the comparison)")


def metric_id(key):
    return key.split("_", 1)[0]


# Setpoint lifecycle. A setpoint may be stood down to "shadow" while its
# replacement calibrates: MEASURED, NOT GRADED. Its value is still computed,
# still written to leverage-state.json and still rendered, but it can never be
# ranked, become `worst`, or emit its actuator.
#
# Grading a stood-down setpoint anyway is a FALSE SHIELD -- it steers the agent
# on the authority of a reference the policy file has already retired. m6
# carried `status: shadow-pending-m7` from 2026-08-16 and kept emitting
# "freeze NEW governance/primitive edits" as an L3 ALERT, because evaluate()
# read every setpoint field except this one. (BRO-2168)
#
# Absence of `status` means live -- the common case, and the fail-safe
# direction: an UNRECOGNIZED value keeps grading and reports itself, because a
# noisy alarm is recoverable while a silently dropped shield is not.
GRADED_SETPOINT_STATUSES = frozenset({"live", "active", "graded"})
SHADOW_SETPOINT_STATUSES = frozenset({"shadow", "retired", "disabled", "deprecated"})
# The ONLY qualifier a lifecycle word may carry. A stand-down is always "pending"
# something, so `shadow-pending-m7` is well-formed while `shadow-live`,
# `shadow-typo` and `retired-nope` are not. Accepting an arbitrary suffix would
# let `shadow-live` -- which READS like "shadow is live" -- silently drop a
# shield, the exact fail-safe inversion this design exists to prevent.
SETPOINT_STATUS_QUALIFIER = "pending"


def setpoint_grading(sp):
    """Decide whether one setpoint's value may be graded, ranked and actuated.

    Returns (graded: bool, note: str|None). A non-None `note` marks a status this
    function does not recognize: it is graded ANYWAY and reported, because a noisy
    alarm is recoverable while a silently dropped shield is not.

    Only an ABSENT `status` key is implicitly live. An explicitly empty or null
    one is malformed, not a shorthand for live, and says so."""
    if "status" not in sp:
        return True, None
    raw = sp.get("status")
    # `-` and `_` are both plausible from a hand-edited YAML file; normalize so the
    # separator choice cannot decide whether a shield stands.
    s = str(raw).strip().lower().replace("_", "-") if raw is not None else ""
    if not s:
        return True, f"empty setpoint status {raw!r} — graded as live"
    if s in GRADED_SETPOINT_STATUSES:
        return True, None
    word, _, qualifier = s.partition("-")
    if word in SHADOW_SETPOINT_STATUSES:
        # A bare lifecycle word is a complete statement and stands the setpoint down.
        if not qualifier:
            return False, None
        # `-pending` PROMISES a successor, so it must name one. `shadow-pending` and
        # `shadow-pending-` do not, and standing down on them would drop a shield on
        # a half-written value -- the same silent-drop this rule exists to prevent.
        head, _, successor = qualifier.partition("-")
        # The successor must NAME something: at least one alphanumeric character.
        # `shadow-pending-.` or `shadow-pending--` name nothing, and standing a
        # shield down on a placeholder is the failure this rule exists to stop.
        if head == SETPOINT_STATUS_QUALIFIER and any(c.isalnum() for c in successor):
            return False, None
        return True, (f"setpoint status {raw!r} is malformed "
                      f"(expected {word!r} or '{word}-{SETPOINT_STATUS_QUALIFIER}-<successor>') "
                      f"— graded as live")
    return True, f"unrecognized setpoint status {raw!r} — graded as live"


def evaluate(metrics, setpoints):
    # Tolerant of a raw, un-coerced policy: evaluate() is called directly as well as
    # through load_setpoints(), and a caller that skips the coercion must not crash.
    sp_list = setpoints.get("metrics") if isinstance(setpoints, dict) else None
    by_id = {m["id"]: m for m in sp_list
             if isinstance(m, dict) and isinstance(m.get("id"), str)} if isinstance(sp_list, list) else {}
    metrics = metrics if isinstance(metrics, dict) else {}
    results = []
    for key, val in metrics.items():
        mid = metric_id(key)
        sp = by_id.get(mid, {})
        level = sp.get("level", DEFAULT_LEVELS.get(mid, "L?"))
        if not sp:
            results.append({"key": key, "value": val, "level": level, "status": "no_setpoint",
                            "name": sp.get("name", mid)})
            continue
        # Resolved BEFORE the null-value check: a stood-down setpoint must stay
        # disclosed as stood down even on a blind read, or the one window where the
        # sensor measured nothing is also the window where the stand-down silently
        # disappears from the brief.
        graded, status_note = setpoint_grading(sp)
        if not graded:
            # Stood down. Keep target/alert on the row so the reader can see what
            # it WOULD have been graded against, but carry no gap and no actuator:
            # a row with an actuator is a row that steers.
            results.append({
                "key": key, "value": val, "level": level, "status": "shadow",
                "target": sp.get("target"), "alert": sp.get("alert"),
                "direction": sp.get("direction", "lower_is_better"),
                "setpoint_status": sp.get("status"), "name": sp.get("name", mid),
            })
            continue
        if not is_gradeable(val):
            row = {"key": key, "value": val, "level": level, "status": "no_setpoint",
                   "name": sp.get("name", mid)}
            if status_note:
                row["status_note"] = status_note
            results.append(row)
            continue
        direction, direction_note = normalize_direction(sp.get("direction"))
        if direction_note and not status_note:
            status_note = direction_note
        target, alert = sp.get("target"), sp.get("alert")
        # A threshold that is not a real finite number cannot decide anything. PyYAML
        # accepts `target: .nan`, and because EVERY comparison with NaN is False the
        # metric would grade `ok` and certify the window -- the same falsehood as a
        # NaN metric, entering from the policy side instead of the measurement side.
        # Fail closed to the existing "measured, not graded" state.
        if target is not None and not is_gradeable(target):
            target = None
        if alert is not None and not is_gradeable(alert):
            alert = None
        if direction is None or target is None or alert is None:
            # reference slot present but not yet authored (r0 unsigned) — measured, not graded
            row = {"key": key, "value": val, "level": level, "status": "unset_target",
                   "name": sp.get("name", mid), "actuator": sp.get("actuator", "")}
            # A malformed status must be reported on EVERY exit, not only the graded
            # one, or "every unrecognized status is reported" is false for any metric
            # whose reference is still unauthored.
            if status_note:
                row["status_note"] = status_note
            elif sp.get("target") is not None and target is None:
                row["status_note"] = (f"setpoint target {sp.get('target')!r} is not a finite "
                                      "number — not graded")
            elif sp.get("alert") is not None and alert is None:
                row["status_note"] = (f"setpoint alert {sp.get('alert')!r} is not a finite "
                                      "number — not graded")
            results.append(row)
            continue
        # Every accepted operand is normalized to float before comparison. is_gradeable()
        # admits anything float() accepts (Decimal, NumPy scalars), and mixing those with
        # a float threshold raises in `round(val - target, 4)` -- declaring a type
        # gradeable without making it comparable is a claim the code does not honour.
        val_n, target_n, alert_n = float(val), float(target), float(alert)
        if direction == "lower_is_better":
            status = "alert" if val_n >= alert_n else "warn" if val_n > target_n else "ok"
            gap = round(val_n - target_n, 4)
        else:
            status = "alert" if val_n <= alert_n else "warn" if val_n < target_n else "ok"
            gap = round(target_n - val_n, 4)
        row = {
            "key": key, "value": val, "target": target, "alert": alert, "level": level,
            "direction": direction, "status": status, "gap": gap,
            "actuator": sp.get("actuator", ""), "name": sp.get("name", mid),
        }
        if status_note:
            row["status_note"] = status_note
        results.append(row)
    order = {"alert": 0, "warn": 1, "ok": 2, "unset_target": 3, "no_setpoint": 4, "shadow": 5}
    ranked = sorted([r for r in results if r["status"] in ("alert", "warn")],
                    key=lambda r: (order[r["status"]], -(r.get("gap") or 0)))
    return results, (ranked[0] if ranked else None)


def sensor_is_live(raw):
    """A sensor that opened session files but extracted zero structural events is
    blind, not live. Single definition, used both to blind the metrics before
    grading and to report closure -- so the two can never disagree. (STI-1919)"""
    return raw.get("sessions_analyzed", 0) > 0 and (
        raw.get("tool_results", 0) > 0 or raw.get("edits", 0) > 0
    )


def closure_verdict(record, setpoints):
    """Per-RCS-level closure keyed on POSITIVE RAW EVENT COUNTS, not non-null metric
    values. A rate metric returns 0.0 (never None) even when the parser extracted zero
    events — so "value is not None" is vacuously true and would certify a wholesale-
    misread sensor as live (exactly the original bug's shape: structural events present,
    read as zero). Each level is LIVE only if the raw evidence its metrics are computed
    FROM was actually extracted:
      L0 (tool-error / read-before-edit / permission-bypass) ← tool_results>0 or edits>0
      L1 (continue-nudges, a per-session rate where 0 is a valid healthy reading) ← sessions>0
      L2 (kg-load, per-session rate; 0 is meaningful) ← sessions>0
      L3 (meta-work ratio) ← working editing sessions > 0
    sensor_live additionally requires the parser to have extracted STRUCTURE (tool_results
    or edits > 0), so a session file that parses to zero structural events (schema drift,
    the l0/l1 failure mode) is NOT live."""
    raw = record.get("raw", {})
    sessions = record["sessions_analyzed"]
    tool_results = raw.get("tool_results", 0)
    edits = raw.get("edits", 0)
    working = raw.get("meta_sessions", 0) + raw.get("product_sessions", 0)
    level_evidence = {
        "L0": (tool_results > 0 or edits > 0),
        "L1": sessions > 0,
        "L2": sessions > 0,
        "L3": working > 0,
    }
    levels = {}
    for r in record["results"]:
        lv = r.get("level", "L?")
        e = levels.setdefault(lv, {"live": False, "metrics": []})
        e["metrics"].append({"name": r.get("name"), "value": r.get("value")})
        if level_evidence.get(lv, False):
            e["live"] = True
    # a sensor that opened files but extracted zero structural events is blind, not live
    sensor_live = sensor_is_live(raw)  # STI-1919: one definition, shared with main()
    expected = ["L0", "L1", "L2", "L3"]
    levels_closed = all(levels.get(lv, {}).get("live") for lv in expected)
    authored_by = setpoints.get("authored_by", "unknown")
    reference_authored = authored_by not in ("bstack-default", "unknown", "", None)
    closed = bool(sensor_live and levels_closed)
    return {
        "closed": closed,
        "sensor_live": sensor_live,
        "levels_closed": levels_closed,
        "reference_authored": reference_authored,
        "authored_by": authored_by,
        "sessions": sessions,
        "levels": {lv: levels.get(lv, {"live": False, "metrics": []}) for lv in expected},
        "extra_levels": {lv: v for lv, v in levels.items() if lv not in expected},
    }


def store(record, state_file, store_file):
    os.makedirs(os.path.dirname(store_file), exist_ok=True)
    with open(store_file, "a") as f:
        f.write(json.dumps(record) + "\n")
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(record, f, indent=2)
        # POSIX trailing newline. Governed repos may TRACK this file, so without it
        # every session leaves the workspace git-dirty and formatter gates (biome /
        # ultracite / prettier) fail on a repo that is otherwise green.
        f.write("\n")


GRADED_ROW_STATUSES = frozenset({"ok", "warn", "alert"})


def no_worst_line(record):
    """The line to print when nothing ranked as `worst`.

    "All within target" is a COMPLIANCE CLAIM, and it is only true when something was
    actually graded. Three distinct situations previously collapsed into it: a healthy
    window, a blind sensor, and a metric set that no longer matches any live setpoint
    (every row lands on no_setpoint/shadow/unset_target, so nothing ranks). Deciding on
    the graded rows themselves rather than on `closure` also stops a STALE closure
    block -- carried over by the cached path -- from certifying a fresh regrade, and
    stops an ABSENT closure from being read as a dead sensor."""
    rows = record.get("results")
    if not isinstance(rows, list):
        # A record whose result set is missing or the wrong type cannot support ANY
        # claim, least of all a compliance one.
        return "No setpoint graded — the result set is missing or unreadable."
    # A row only counts as graded if it carries a value: `{"status": "ok", "value": None}`
    # is an ungraded row wearing a graded label, and must not certify the window.
    # A BREACH CLAIM COUNTS EVEN WITHOUT A VALUE. Requiring `value is not None` on
    # this filter is how `[{"status":"ok","value":0},{"status":"alert","value":None}]`
    # certified: the alert row was filtered out for lacking a value and the ok row
    # carried the claim. A row saying "alert" is asserting a breach; whether it also
    # carries a number is irrelevant to whether we may say "within target".
    breached = [r for r in rows if isinstance(r, dict)
                and r.get("status") in ("alert", "warn")]
    # Certifying, by contrast, DOES require a value: `{"status":"ok","value":None}`
    # is an ungraded row wearing a graded label.
    graded = [r for r in rows if isinstance(r, dict)
              and r.get("status") in GRADED_ROW_STATUSES and is_gradeable(r.get("value"))]
    if breached:
        return ("Inconsistent record — " + ", ".join(
            f"{r.get('name', r.get('key'))}={r.get('value')} [{r.get('status')}]" for r in breached)
            + " but nothing ranked; not certified.")
    if graded:
        return "All graded setpoints within target."
    closure = record.get("closure")
    if isinstance(closure, dict) and closure.get("sensor_live") is False:
        return "No setpoint graded — the sensor read no structural events this window."
    return "No setpoint graded — no metric matched a live setpoint this window."


def shadow_notes(record):
    """Lines naming every stood-down setpoint and every unrecognized status.

    A shadow setpoint is deliberately ungraded, so it vanishes from the graded
    output entirely. Vanishing silently is the opposite failure to the one
    BRO-2168 fixed -- a shield can then be gone for windows without anyone
    noticing -- so its absence is stated rather than implied."""
    rows = record.get("results")
    if not isinstance(rows, list):
        return []
    out = []
    shadowed = [r for r in rows if isinstance(r, dict) and r.get("status") == "shadow"]
    if shadowed:
        out.append("shadow (measured, NOT graded): " + ", ".join(
            f"{r.get('name', r['key'])} = {r.get('value')} [{r.get('setpoint_status')}]"
            for r in shadowed))
    for r in rows:
        if isinstance(r, dict) and r.get("status_note"):
            out.append(f"⚠ {r.get('name', r['key'])}: {r['status_note']}")
    return out


def render_brief(record):
    # This renders into the agent's SessionStart context, so a malformed record must
    # degrade to a truthful line rather than raise. `closure` has been observed as a
    # string, `worst` without its `actuator`/`gap`, and `sessions_analyzed` absent --
    # each of which raised here before. A crashed brief is silently swallowed by the
    # hook (`|| true`), which is the worst outcome: no signal AND no error. (BRO-2168)
    worst = record.get("worst")
    if not isinstance(worst, dict):
        worst = None
    lines = [f"[self-improvement loop] {record.get('sessions_analyzed', '?')} sessions / "
             f"{record.get('window_days', '?')}d"]
    cl = record.get("closure")
    if not isinstance(cl, dict):
        cl = {}
    if cl and not cl.get("closed"):
        why = "sensor dead" if not cl.get("sensor_live") else \
              "levels not all live: " + ",".join(
                  str(k) for k, v in (cl.get("levels") if isinstance(cl.get("levels"), dict) else {}).items()
                  if not (isinstance(v, dict) and v.get("live")))
        lines.append(f"⚠ loop NOT closed ({why}) — run `bstack doctor` §23")
    if cl and not cl.get("reference_authored"):
        lines.append("⚠ reference r0 is bstack-default (endogenous) — author + sign .control/leverage-setpoints.yaml")
    if not worst:
        # STI-1919 + BRO-2168: with no worst gap, "within target" is only true if
        # something was actually graded. no_worst_line() decides on the graded rows.
        lines.append(no_worst_line(record))
        lines.extend(shadow_notes(record))
        return "\n".join(x for x in lines if x)
    sign = "↑" if worst.get("direction") == "lower_is_better" else "↓"
    gap = worst.get("gap")
    gap_txt = f", {sign} off by {abs(gap)}" if isinstance(gap, (int, float)) and not isinstance(gap, bool) else ""
    lines.append(f"Worst gap [{str(worst.get('status', '?')).upper()}] "
                 f"{worst.get('name', worst.get('key', '?'))} ({worst.get('level', 'L?')}) = "
                 f"{worst.get('value')} (target {worst.get('target')}{gap_txt}).")
    actuator = worst.get("actuator")
    if actuator:
        lines.append(f"→ Corrective actuator: {actuator}")
    else:
        # No actuator means nothing to DO about the worst gap. Say so; an omitted line
        # reads as "nothing was wrong".
        lines.append("→ No corrective actuator declared for this setpoint.")
    others = [r for r in (record.get("results") or [])
              if isinstance(r, dict) and r.get("status") == "alert"
              and r.get("key") != worst.get("key")]
    if others:
        lines.append("Other alerts: " + ", ".join(f"{o['name']}={o['value']}" for o in others))
    lines.extend(shadow_notes(record))
    return "\n".join(lines)


def render_human(record):
    # Same tolerance contract as render_brief: degrade to a truthful line, never raise.
    out = [f"Self-improvement loop — {record.get('sessions_analyzed', '?')} sessions over "
           f"{record.get('window_days', '?')}d  (measured {record.get('measured_at', '?')})", ""]
    mark = {"ok": "ok  ", "warn": "WARN", "alert": "ALRT", "no_setpoint": "----",
            "unset_target": "r0? ", "shadow": "shdw"}
    # Group by RCS level, then sweep everything left over. Iterating a fixed
    # ["L0".."L3"] list DROPPED any row whose level was anything else -- `L4`, a
    # lowercase `l3`, or the `L?` that `DEFAULT_LEVELS.get(mid, "L?")` assigns to a
    # metric id it does not know. Those rows rendered NOWHERE, alerts included. Same
    # shape as the `status` and `direction` defects: an unrecognized enum value
    # silently choosing an outcome. Here the outcome was disappearance.
    all_rows = [r for r in (record.get("results") or []) if isinstance(r, dict)]
    known = ["L0", "L1", "L2", "L3"]
    seen = set()
    for lv in known + ["other"]:
        if lv == "other":
            rows = [r for r in all_rows if id(r) not in seen]
            label = "L? / unrecognized level"
        else:
            rows = [r for r in all_rows if r.get("level") == lv]
            label = lv
        seen.update(id(r) for r in rows)
        if not rows:
            continue
        lv = label
        out.append(f"  ── {lv} ──")
        for r in rows:
            if r["status"] == "shadow":
                tgt = f"(would-be target {r.get('target')} — NOT graded: {r.get('setpoint_status')})"
            elif r["status"] in ("no_setpoint", "unset_target"):
                tgt = ""
            else:
                tgt = f"(target {r.get('target')}, alert {r.get('alert')})"
            out.append(f"  {mark.get(r['status'],'?')} {r.get('name', r['key']):<30} {str(r.get('value')):>8}   {tgt}")
    cl = record.get("closure")
    if not isinstance(cl, dict):
        cl = {}
    out.append("")
    out.append(f"  closure: {'CLOSED' if cl.get('closed') else 'OPEN'}  "
               f"(sensor_live={cl.get('sensor_live')}, levels_closed={cl.get('levels_closed')}, "
               f"reference_authored={cl.get('reference_authored')})")
    metrics = record.get("metrics")
    m6s = metrics.get("m6s_meta_work_ship_ratio") if isinstance(metrics, dict) else None
    if m6s is not None:
        out.append(f"  [shadow] m6s_meta_work_ship_ratio = {m6s}  "
                   f"(exogenous ship-signal — NON-actuating, calibrating for BRO-1709)")
    worst = record.get("worst")
    worst = worst if isinstance(worst, dict) else None
    out.append(f"  Focus: {worst.get('name', worst.get('key', '?'))} → "
               f"{worst.get('actuator') or '(no actuator declared)'}" if worst
               else "  " + no_worst_line(record))
    out.extend(f"  {n}" for n in shadow_notes(record))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--transcripts", default=None, help="glob for CC transcript *.jsonl")
    ap.add_argument("--window", type=int, default=None, help="lookback window in days")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--brief", action="store_true", help="<=8-line summary for the SessionStart wire")
    ap.add_argument("--closure", action="store_true", help="emit machine-readable closure verdict (for doctor/CI)")
    ap.add_argument("--cached", action="store_true",
                    help="render brief from leverage-state.json if fresh (<24h); compute on miss")
    ap.add_argument("--no-store", action="store_true")
    ap.add_argument("--throttle", type=int, default=0,
                    help="skip recompute if state.json younger than N sec (Stop-hook throttle)")
    args = ap.parse_args()

    workspace = resolve_workspace(args.workspace)
    glob_pat = transcript_glob(workspace, args.transcripts)
    setpoints_path = os.path.join(workspace, ".control", "leverage-setpoints.yaml")
    state_file = os.path.join(workspace, ".control", "leverage-state.json")
    store_file = os.path.join(workspace, ".control", "leverage-metrics.jsonl")

    if args.throttle:
        try:
            st = json.load(open(state_file))
            if time.time() - datetime.fromisoformat(st["measured_at"]).timestamp() < args.throttle:
                return
        except Exception:
            pass

    if args.cached and not args.closure:
        try:
            st = json.load(open(state_file))
            if time.time() - datetime.fromisoformat(st["measured_at"]).timestamp() < 86400:
                # BRO-2168: RE-GRADE the cached metrics against the CURRENT setpoints
                # before rendering. The cache exists to skip analyze() -- the transcript
                # scan -- but evaluate() is pure and cheap, and the stored `results` /
                # `worst` were graded under whatever setpoints were live at write time.
                # Rendering them verbatim means a setpoint edit does not reach the
                # SessionStart brief until the cache expires, so standing a setpoint
                # down leaves its retired actuator steering for up to 24h. This is the
                # path SessionStart actually uses (knowledge-wakeup-hook.sh:
                # `--brief --cached --no-store`).
                try:
                    sp_now = load_setpoints(setpoints_path)
                except Exception:
                    sp_now = {}
                # load_setpoints DEGRADES rather than raising: an unreadable file
                # returns `metrics: []`. But a STRUCTURALLY wrong yet valid YAML policy
                # (`- id: m6` parses to a LIST) returns a non-mapping, on which .get()
                # raises -- so the type is checked, not assumed.
                if not isinstance(sp_now, dict) or not sp_now.get("metrics"):
                    # Policy is unreadable or empty, so NOTHING in the cache can be
                    # certified -- neither its ranked actuator nor its compliance.
                    #
                    # Emit one unambiguous state instead of a partially-suppressed
                    # record. Every earlier attempt to keep part of the cache (values
                    # but not the actuator; rows but not `worst`) produced a fresh way
                    # for the brief to contradict itself -- most recently a dropped
                    # `worst` beside retained `alert` rows, which read as
                    # "all within target" while the cache held an alert.
                    sessions = st.get("sessions_analyzed", "?") if isinstance(st, dict) else "?"
                    window = st.get("window_days", "?") if isinstance(st, dict) else "?"
                    print(f"[self-improvement loop] {sessions} sessions / {window}d")
                    print("⚠ .control/leverage-setpoints.yaml unreadable, empty or malformed — "
                          "nothing graded, no actuator emitted")
                    return
                # Re-grade UNCONDITIONALLY once policy is readable. Guarding on
                # `st.get("metrics")` let a cache with an empty metric set ({} is falsy)
                # keep its stored results and stale `worst`, which is the very leak this
                # path exists to close. evaluate({}, sp) yields ([], None), which
                # no_worst_line() reports honestly.
                try:
                    st["results"], st["worst"] = evaluate(st.get("metrics") or {}, sp_now)
                except Exception:
                    # A corrupt cached metric set must not take the brief down, but it
                    # must not be certified either: drop the whole grading rather than
                    # render half of it.
                    st["results"], st["worst"] = [], None
                print(render_brief(st))
                return
        except Exception:
            pass

    setpoints = load_setpoints(setpoints_path)
    window = args.window if args.window is not None else setpoints.get("window_days", 7)
    kg_read_re = re.compile(setpoints.get("knowledge_paths", DEFAULT_KG_READ), re.IGNORECASE)
    metrics, raw = analyze(glob_pat, window, kg_read_re)
    # STI-1919: a blind read must not emit a row that reads as a measurement.
    # With no structural events every metric computes to 0.0 from an empty
    # numerator -- at or better than every target -- and only closure.sensor_live
    # says otherwise. Null the graded values instead: a null cannot be compared to
    # a target, so evaluate() files it as "no_setpoint" and it can never become
    # `worst`. raw/ keeps the counts, so nothing is lost. This runs BEFORE
    # merge_ship_shadow because the ship signal is exogenous (BRO-1707) and a
    # blind transcript read says nothing about it.
    if not sensor_is_live(raw):
        metrics = dict.fromkeys(metrics)
    merge_ship_shadow(metrics, raw, workspace)
    results, worst = evaluate(metrics, setpoints)

    record = {
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "workspace": workspace, "window_days": window,
        "sessions_analyzed": raw["sessions_analyzed"],
        "metrics": metrics, "raw": raw, "results": results, "worst": worst,
    }
    record["closure"] = closure_verdict(record, setpoints)

    if not args.no_store:
        store(record, state_file, store_file)

    if args.closure:
        print(json.dumps(record["closure"], indent=2))
        # exit non-zero if the loop is not closed, so CI/doctor can gate on it
        sys.exit(0 if record["closure"]["closed"] else 1)
    elif args.json:
        print(json.dumps(record, indent=2))
    elif args.brief:
        print(render_brief(record))
    else:
        print(render_human(record))


if __name__ == "__main__":
    main()
