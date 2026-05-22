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
#
# Exit codes:
#   0 — all layers stable
#   1 — at least one layer flagged unstable (observed metric violated budget)
#   2 — parameters config not found
#   3 — python3 / tomllib unavailable

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
            grep -E '^#( |$)' "$0" | sed 's/^# \?//' | head -22
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

python3 - "$CONFIG" "$WORKSPACE" "$FORMAT" <<'PYEOF'
import sys, json, time, math
from pathlib import Path

try:
    import tomllib
except ImportError:
    print("compute-budget-status: tomllib not available (Python >= 3.11 required)", file=sys.stderr)
    sys.exit(3)

config_path, workspace, fmt = sys.argv[1], sys.argv[2], sys.argv[3]

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
else:
    print(json.dumps(report, indent=2))

if any_unstable:
    sys.exit(1)
sys.exit(0)
PYEOF
