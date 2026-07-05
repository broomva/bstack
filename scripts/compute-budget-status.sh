#!/usr/bin/env bash
# bstack/scripts/compute-budget-status.sh — Multi-layer RCS health reader.
#
# Reads all four audit logs (.control/audit/l[0-3]-*.jsonl) plus the
# canonical parameters.toml, computes per-layer observed metrics over each
# layer's τ_a window, compares against paper-cited setpoints, and emits a
# composite verdict.
#
# Consumed by:
#   - scripts/doctor.sh §19 (multi-layer health section)
#   - .github/workflows/l3-stability.yml (CI multi-layer PR comment — v0.15.0+)
#   - /dogfood receipt cross-reference (deferred to dogfood v0.2.0)
#
# Usage:
#   bash scripts/compute-budget-status.sh                  # JSON output
#   bash scripts/compute-budget-status.sh --human          # human-readable
#   bash scripts/compute-budget-status.sh --workspace=...  # custom workspace
#   bash scripts/compute-budget-status.sh --trend          # composite-ω drift
#                                                          # (writes 1 history
#                                                          # line per call; reads
#                                                          # last 7d to compute
#                                                          # slope + verdict)
#
# Trend mode (v0.19.0+):
#   - WITHOUT --trend: point-in-time composite-ω + per-layer verdicts.
#   - WITH --trend: appends current snapshot to
#       .control/audit/composite-omega-history.jsonl
#     then computes a least-squares linear fit over the last 7d window:
#       - slope (omega per second)
#       - last value
#       - baseline (median of first day in window)
#       - verdict: stable_flat | drift_up | drift_down | volatile
#   - Trend output composes with --human and --json the same way as point-in-
#     time output (the "trend" key is added under JSON; a "Trend (7d): ..."
#     line is added under human).
#
# Exit codes:
#   0 — all layers stable
#   1 — at least one layer flagged unstable (observed metric violated budget)
#   2 — parameters config not found
#   3 — python3 / tomllib unavailable

set -uo pipefail

WORKSPACE="${BROOMVA_WORKSPACE:-$PWD}"
FORMAT="json"
TREND=0
BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for arg in "$@"; do
    case "$arg" in
        --workspace=*) WORKSPACE="${arg#*=}" ;;
        --human)       FORMAT="human" ;;
        --json)        FORMAT="json" ;;
        --trend)       TREND=1 ;;
        --help|-h)
            grep -E '^#( |$)' "$0" | sed 's/^# \?//' | head -34
            exit 0
            ;;
    esac
done

# Locate parameters config
CONFIG=""
if [ -f "$WORKSPACE/.control/rcs-parameters.toml" ]; then
    CONFIG="$WORKSPACE/.control/rcs-parameters.toml"
elif [ -f "$WORKSPACE/research/rcs/data/parameters.toml" ]; then
    CONFIG="$WORKSPACE/research/rcs/data/parameters.toml"
else
    CONFIG="$BSTACK_REPO/assets/templates/rcs-parameters.toml.template"
fi

if [ ! -f "$CONFIG" ]; then
    echo "compute-budget-status: parameters config not found at $CONFIG" >&2
    exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "compute-budget-status: python3 not available" >&2
    exit 3
fi

python3 - "$CONFIG" "$WORKSPACE" "$FORMAT" "$TREND" <<'PYEOF'
import sys, json, time, math
from pathlib import Path

try:
    import tomllib
except ImportError:
    print("compute-budget-status: tomllib not available (Python >= 3.11 required)", file=sys.stderr)
    sys.exit(3)

config_path, workspace, fmt = sys.argv[1], sys.argv[2], sys.argv[3]
trend_enabled = sys.argv[4] == "1" if len(sys.argv) > 4 else False

with open(config_path, "rb") as f:
    params = tomllib.load(f)

# Per-level tau_a + lambda
level_params = {lvl["id"]: lvl for lvl in params.get("levels", [])}
cached_lambda = params.get("derived", {}).get("lambda", {})

audit_dir = Path(workspace) / ".control" / "audit"
now_ms = int(time.time() * 1000)

def read_jsonl(path):
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
    return rows

def in_window(row, window_ms, now=now_ms):
    ts = row.get("ts", 0)
    return ts >= (now - window_ms)

def percentile(values, p):
    if not values:
        return None
    s = sorted(values)
    k = int(round((p / 100.0) * (len(s) - 1)))
    return s[max(0, min(k, len(s) - 1))]

# ── L0: tools ────────────────────────────────────────────────────────────
# NOTE (v0.31.0): l0-tools.jsonl / l1-reflexes.jsonl are RETIRED (the writing
# hooks are no-op stubs), so these overlays read empty → L0/L1 verdict defaults
# to `stable`. The real L0/L1 signal now lives in the leverage-sensor
# (.control/leverage-state.json; doctor §16/§17/§23). Repointing this overlay at
# leverage-state is tracked in BRO-1699. The composite ω is unaffected (it comes
# from the static rcs-parameters.toml, not these logs).
l0_rows = read_jsonl(audit_dir / "l0-tools.jsonl")
l0_params = level_params.get("L0", {})
l0_tau_a_ms = int(float(l0_params.get("tau_a", 0.5)) * 1000)
l0_window = [r for r in l0_rows if in_window(r, l0_tau_a_ms)]
l0_latencies = [r["latency_ms"] for r in l0_window if isinstance(r.get("latency_ms"), (int, float))]
l0_errors = sum(1 for r in l0_window if r.get("is_error"))
l0_total = len(l0_window)

l0_observed = {
    "window_seconds": l0_tau_a_ms / 1000.0,
    "events_in_window": l0_total,
    "latency_ms_mean": round(sum(l0_latencies) / len(l0_latencies), 1) if l0_latencies else None,
    "latency_ms_p99": percentile(l0_latencies, 99),
    "exit_nonzero_rate": round(l0_errors / l0_total, 3) if l0_total else 0.0,
}
# L0 is hard to "violate" — verdict is `stable` unless extreme runaway
# (>10000 events in window or error rate >50%)
l0_unstable = l0_total > 10000 or (l0_total > 10 and l0_observed["exit_nonzero_rate"] > 0.5)
l0_warn = l0_total > 1000 or (l0_total > 10 and l0_observed["exit_nonzero_rate"] > 0.2)

# ── L1: reflexes ──────────────────────────────────────────────────────────
l1_rows = read_jsonl(audit_dir / "l1-reflexes.jsonl")
l1_params = level_params.get("L1", {})
l1_tau_a_ms = int(float(l1_params.get("tau_a", 30.0)) * 1000)
# L1 sessions can be long; use a larger window for session-level aggregation
# (treat tau_a as the per-session reflex hysteresis, not session count cadence).
# For practical purposes, the observed window for sessions = max(tau_a, 1h).
l1_window_ms = max(l1_tau_a_ms, 3600 * 1000)
l1_window = [r for r in l1_rows if in_window(r, l1_window_ms)]
l1_compliance = [r.get("compliance_rate") for r in l1_window if isinstance(r.get("compliance_rate"), (int, float))]
l1_dogfood_yes = sum(1 for r in l1_window if (r.get("anti_rationalization") or {}).get("value") == "yes")
l1_dogfood_present = sum(1 for r in l1_window if (r.get("anti_rationalization") or {}).get("present"))

l1_observed = {
    "window_seconds": l1_window_ms / 1000.0,
    "sessions_in_window": len(l1_window),
    "compliance_rate_mean": round(sum(l1_compliance) / len(l1_compliance), 3) if l1_compliance else None,
    "dogfood_receipt_yes_count": l1_dogfood_yes,
    "dogfood_receipt_present_count": l1_dogfood_present,
}
# L1 verdict: warn if mean compliance < 0.6; unstable if < 0.3
l1_unstable = bool(l1_observed["compliance_rate_mean"] is not None and l1_observed["compliance_rate_mean"] < 0.3)
l1_warn = bool(l1_observed["compliance_rate_mean"] is not None and 0.3 <= l1_observed["compliance_rate_mean"] < 0.6)

# ── L2: promotions ────────────────────────────────────────────────────────
l2_rows = read_jsonl(audit_dir / "l2-promotions.jsonl")
l2_params = level_params.get("L2", {})
l2_tau_a_ms = int(float(l2_params.get("tau_a", 3600.0)) * 1000)
l2_window = [r for r in l2_rows if in_window(r, l2_tau_a_ms)]
l2_budget = int(l2_window[-1]["budget"]) if l2_window and isinstance(l2_window[-1].get("budget"), (int, float)) else 5

l2_observed = {
    "window_seconds": l2_tau_a_ms / 1000.0,
    "promotions_in_window": len(l2_window),
    "budget": l2_budget,
}
l2_unstable = len(l2_window) > l2_budget
l2_warn = (len(l2_window) >= l2_budget) and not l2_unstable

# ── L3: edits ─────────────────────────────────────────────────────────────
l3_rows = read_jsonl(audit_dir / "l3-edits.jsonl")
l3_params = level_params.get("L3", {})
l3_tau_a_ms = int(float(l3_params.get("tau_a", 86400.0)) * 1000)
l3_window = [r for r in l3_rows if in_window(r, l3_tau_a_ms)]
# Counts L3 edits in window; budget = 1 (matches l3-rate-gate.sh assumption)
l3_budget = 1
l3_observed = {
    "window_seconds": l3_tau_a_ms / 1000.0,
    "l3_edits_in_window": len(l3_window),
    "budget": l3_budget,
}
l3_unstable = len(l3_window) > l3_budget
l3_warn = len(l3_window) == l3_budget

# ── Compose verdict per layer ─────────────────────────────────────────────
def verdict(unstable, warn):
    if unstable:
        return "unstable"
    if warn:
        return "stable_warn"
    return "stable"

layers = [
    {
        "id": "L0",
        "name": "plant",
        "lambda_paper": cached_lambda.get("L0"),
        "observed": l0_observed,
        "verdict": verdict(l0_unstable, l0_warn),
    },
    {
        "id": "L1",
        "name": "autonomic",
        "lambda_paper": cached_lambda.get("L1"),
        "observed": l1_observed,
        "verdict": verdict(l1_unstable, l1_warn),
    },
    {
        "id": "L2",
        "name": "EGRI",
        "lambda_paper": cached_lambda.get("L2"),
        "observed": l2_observed,
        "verdict": verdict(l2_unstable, l2_warn),
    },
    {
        "id": "L3",
        "name": "governance",
        "lambda_paper": cached_lambda.get("L3"),
        "observed": l3_observed,
        "verdict": verdict(l3_unstable, l3_warn),
    },
]

warnings = []
if l1_warn or l1_unstable:
    warnings.append({"layer": "L1", "msg": f"compliance_rate_mean = {l1_observed['compliance_rate_mean']}"})
if l2_warn or l2_unstable:
    warnings.append({"layer": "L2", "msg": f"promotions {l2_observed['promotions_in_window']} / budget {l2_budget}"})
if l3_warn or l3_unstable:
    warnings.append({"layer": "L3", "msg": f"edits {l3_observed['l3_edits_in_window']} / budget {l3_budget}"})
if l0_warn or l0_unstable:
    warnings.append({"layer": "L0", "msg": f"events {l0_observed['events_in_window']} / err rate {l0_observed['exit_nonzero_rate']}"})

all_stable = all(l["verdict"] == "stable" for l in layers)
any_unstable = any(l["verdict"] == "unstable" for l in layers)
composite_omega_paper = min((l["lambda_paper"] for l in layers if l["lambda_paper"] is not None), default=None)

report = {
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "workspace": workspace,
    "layers": layers,
    "composite_omega_paper": composite_omega_paper,
    "all_layers_stable": all_stable,
    "warnings": warnings,
}

# ── Trend mode (v0.19.0+) ────────────────────────────────────────────────
# Appends current snapshot to composite-omega-history.jsonl, then reads
# the last 7d and computes least-squares slope + verdict. The "trend"
# block is added to the report; format-specific rendering happens below.
trend = None
if trend_enabled and composite_omega_paper is not None:
    history_path = audit_dir / "composite-omega-history.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "ts": now_ms,
        "omega": composite_omega_paper,
        "per_layer": {l["id"]: l["lambda_paper"] for l in layers},
    }
    try:
        with history_path.open("a") as f:
            f.write(json.dumps(snapshot) + "\n")
    except Exception as e:
        # Recording failure is non-fatal — trend stays None.
        trend = {"error": f"history append failed: {e.__class__.__name__}"}

    if trend is None:
        # Read last 7d of history.
        window_ms = 7 * 24 * 60 * 60 * 1000
        cutoff = now_ms - window_ms
        history_rows = []
        try:
            with history_path.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                        if isinstance(r.get("ts"), (int, float)) and r["ts"] >= cutoff:
                            history_rows.append(r)
                    except Exception:
                        pass
        except Exception:
            pass

        # Compute slope (least-squares over (ts, omega)) when ≥ 2 points.
        pts = [(r["ts"], r["omega"]) for r in history_rows if isinstance(r.get("omega"), (int, float))]
        if len(pts) >= 2:
            n = len(pts)
            mean_x = sum(p[0] for p in pts) / n
            mean_y = sum(p[1] for p in pts) / n
            num = sum((p[0] - mean_x) * (p[1] - mean_y) for p in pts)
            den = sum((p[0] - mean_x) ** 2 for p in pts)
            slope = (num / den) if den else 0.0  # omega per millisecond
            slope_per_sec = slope * 1000.0
            last_val = pts[-1][1]
            # Baseline = median of first day's points (or first half if < 1 day).
            first_day_cutoff = pts[0][0] + 24 * 60 * 60 * 1000
            first_day = [p[1] for p in pts if p[0] <= first_day_cutoff] or [pts[0][1]]
            baseline = sorted(first_day)[len(first_day) // 2]
            deviation = last_val - baseline
            # Volatility = std / mean if mean > 0.
            mean_omega = mean_y
            variance = sum((p[1] - mean_omega) ** 2 for p in pts) / n
            stddev = variance ** 0.5
            volatility = (stddev / mean_omega) if mean_omega else 0.0

            # Verdict heuristic (drift takes precedence over volatility):
            #   drift_down — slope < 0 AND last has dropped > 1% below baseline
            #   drift_up   — slope > 0 AND last has risen   > 1% above baseline
            #   volatile   — coefficient of variation > 0.1 with no clear direction
            #   stable_flat — everything else
            # Note: thresholds are *relative* (baseline-normalized), not absolute
            # in omega units, because composite-ω can range across orders of
            # magnitude depending on workspace λ calibration.
            verdict = "stable_flat"
            relative_dev = (deviation / baseline) if baseline else 0.0
            if slope_per_sec < 0 and relative_dev < -0.01:
                verdict = "drift_down"
            elif slope_per_sec > 0 and relative_dev > 0.01:
                verdict = "drift_up"
            elif volatility > 0.1:
                verdict = "volatile"

            trend = {
                "window_seconds": int(window_ms / 1000),
                "points": n,
                "last": round(last_val, 6),
                "baseline": round(baseline, 6),
                "deviation": round(deviation, 6),
                "slope_per_second": slope_per_sec,
                "volatility": round(volatility, 4),
                "verdict": verdict,
            }
        else:
            trend = {
                "window_seconds": int(window_ms / 1000),
                "points": len(pts),
                "verdict": "stable_flat",
                "note": "need ≥ 2 points in 7d window to compute slope",
            }

if trend is not None:
    report["trend"] = trend

if fmt == "human":
    print("RCS Multi-Layer Budget Status")
    print(f"  Workspace: {workspace}")
    print(f"  Timestamp: {report['timestamp']}")
    print("")
    print(f"  {'ID':4} {'Name':12} {'λ paper':>10} {'Observed':>30} {'Verdict':>14}")
    print(f"  {'─'*4} {'─'*12} {'─'*10} {'─'*30} {'─'*14}")
    for l in layers:
        lp = f"{l['lambda_paper']:.6f}" if l['lambda_paper'] is not None else "-"
        obs = l["observed"]
        if l["id"] == "L0":
            summary = f"{obs['events_in_window']}ev / err={obs['exit_nonzero_rate']}"
        elif l["id"] == "L1":
            cr = obs.get("compliance_rate_mean")
            summary = f"{obs['sessions_in_window']}s / cr={cr if cr is not None else '-'}"
        elif l["id"] == "L2":
            summary = f"{obs['promotions_in_window']}/{obs['budget']} promotions"
        else:
            summary = f"{obs['l3_edits_in_window']}/{obs['budget']} edits"
        print(f"  {l['id']:4} {l['name']:12} {lp:>10} {summary:>30} {l['verdict']:>14}")
    print("")
    print(f"  composite_omega (paper): {composite_omega_paper}")
    print(f"  all_layers_stable:       {all_stable}")
    if warnings:
        print("")
        print("  Warnings:")
        for w in warnings:
            print(f"    - {w['layer']}: {w['msg']}")
    if trend is not None and "verdict" in trend:
        print("")
        last = trend.get("last", "-")
        baseline = trend.get("baseline", "-")
        slope = trend.get("slope_per_second", "-")
        verdict = trend.get("verdict", "-")
        points = trend.get("points", 0)
        print(f"  Trend (7d): last={last} baseline={baseline} slope={slope} verdict={verdict} (n={points})")
else:
    print(json.dumps(report, indent=2))

if any_unstable:
    sys.exit(1)
sys.exit(0)
PYEOF
