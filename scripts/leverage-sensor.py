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


def load_setpoints(path):
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[leverage-sensor] WARN could not load setpoints ({path}): {e}", file=sys.stderr)
        return {"window_days": 7, "metrics": []}


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


def metric_id(key):
    return key.split("_", 1)[0]


def evaluate(metrics, setpoints):
    by_id = {m["id"]: m for m in setpoints.get("metrics", [])}
    results = []
    for key, val in metrics.items():
        mid = metric_id(key)
        sp = by_id.get(mid, {})
        level = sp.get("level", DEFAULT_LEVELS.get(mid, "L?"))
        if not sp or val is None:
            results.append({"key": key, "value": val, "level": level, "status": "no_setpoint",
                            "name": sp.get("name", mid)})
            continue
        direction = sp.get("direction", "lower_is_better")
        target, alert = sp.get("target"), sp.get("alert")
        if target is None or alert is None:
            # reference slot present but not yet authored (r0 unsigned) — measured, not graded
            results.append({"key": key, "value": val, "level": level, "status": "unset_target",
                            "name": sp.get("name", mid), "actuator": sp.get("actuator", "")})
            continue
        if direction == "lower_is_better":
            status = "alert" if val >= alert else "warn" if val > target else "ok"
            gap = round(val - target, 4)
        else:
            status = "alert" if val <= alert else "warn" if val < target else "ok"
            gap = round(target - val, 4)
        results.append({
            "key": key, "value": val, "target": target, "alert": alert, "level": level,
            "direction": direction, "status": status, "gap": gap,
            "actuator": sp.get("actuator", ""), "name": sp.get("name", mid),
        })
    order = {"alert": 0, "warn": 1, "ok": 2, "unset_target": 3, "no_setpoint": 4}
    ranked = sorted([r for r in results if r["status"] in ("alert", "warn")],
                    key=lambda r: (order[r["status"]], -(r.get("gap") or 0)))
    return results, (ranked[0] if ranked else None)


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
    sensor_live = sessions > 0 and (tool_results > 0 or edits > 0)
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


def render_brief(record):
    worst = record.get("worst")
    lines = [f"[self-improvement loop] {record['sessions_analyzed']} sessions / {record['window_days']}d"]
    cl = record.get("closure", {})
    if cl and not cl.get("closed"):
        why = "sensor dead" if not cl.get("sensor_live") else \
              "levels not all live: " + ",".join(k for k, v in cl.get("levels", {}).items() if not v.get("live"))
        lines.append(f"⚠ loop NOT closed ({why}) — run `bstack doctor` §23")
    if cl and not cl.get("reference_authored"):
        lines.append("⚠ reference r0 is bstack-default (endogenous) — author + sign .control/leverage-setpoints.yaml")
    if not worst:
        lines.append("All authored setpoints within target." if worst is None else "")
        return "\n".join(x for x in lines if x)
    sign = "↑" if worst["direction"] == "lower_is_better" else "↓"
    lines.append(f"Worst gap [{worst['status'].upper()}] {worst['name']} ({worst.get('level','L?')}) = "
                 f"{worst['value']} (target {worst['target']}, {sign} off by {abs(worst['gap'])}).")
    lines.append(f"→ Corrective actuator: {worst['actuator']}")
    others = [r for r in record["results"]
              if r.get("status") == "alert" and r["key"] != worst["key"]]
    if others:
        lines.append("Other alerts: " + ", ".join(f"{o['name']}={o['value']}" for o in others))
    return "\n".join(lines)


def render_human(record):
    out = [f"Self-improvement loop — {record['sessions_analyzed']} sessions over "
           f"{record['window_days']}d  (measured {record['measured_at']})", ""]
    mark = {"ok": "ok  ", "warn": "WARN", "alert": "ALRT", "no_setpoint": "----", "unset_target": "r0? "}
    for lv in ["L0", "L1", "L2", "L3"]:
        rows = [r for r in record["results"] if r.get("level") == lv]
        if not rows:
            continue
        out.append(f"  ── {lv} ──")
        for r in rows:
            tgt = f"(target {r.get('target')}, alert {r.get('alert')})" if r["status"] not in ("no_setpoint", "unset_target") else ""
            out.append(f"  {mark.get(r['status'],'?')} {r.get('name', r['key']):<30} {str(r.get('value')):>8}   {tgt}")
    cl = record.get("closure", {})
    out.append("")
    out.append(f"  closure: {'CLOSED' if cl.get('closed') else 'OPEN'}  "
               f"(sensor_live={cl.get('sensor_live')}, levels_closed={cl.get('levels_closed')}, "
               f"reference_authored={cl.get('reference_authored')})")
    worst = record.get("worst")
    out.append(f"  Focus: {worst['name']} → {worst['actuator']}" if worst else "  All setpoints within target.")
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
                print(render_brief(st))
                return
        except Exception:
            pass

    setpoints = load_setpoints(setpoints_path)
    window = args.window if args.window is not None else setpoints.get("window_days", 7)
    kg_read_re = re.compile(setpoints.get("knowledge_paths", DEFAULT_KG_READ), re.IGNORECASE)
    metrics, raw = analyze(glob_pat, window, kg_read_re)
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
