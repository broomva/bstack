#!/usr/bin/env bash
# bstack/scripts/compute-lambda.sh — Recompute RCS stability budget λᵢ from
# workspace parameters and report drift vs cached values.
#
# Implements the formula (papers/p0-foundations/main.tex, Theorem 1):
#
#     λᵢ = γᵢ − L_θᵢ·ρᵢ − L_dᵢ·ηᵢ − βᵢ·τ̄ᵢ − ln(νᵢ)/τ_aᵢ
#
# A level is individually stable iff λᵢ > 0. Composite stability rate
# ω = min_i λᵢ; the composite system is exponentially stable iff every λᵢ > 0.
#
# Usage:
#   bash scripts/compute-lambda.sh                  # JSON output, default config
#   bash scripts/compute-lambda.sh --human          # human-readable table
#   bash scripts/compute-lambda.sh --config=path    # custom parameters.toml
#   bash scripts/compute-lambda.sh --strict         # exit 3 on drift > 1e-4
#   bash scripts/compute-lambda.sh --help
#
# Config-file lookup order (first match wins):
#   1. $config_arg                                          (--config=path)
#   2. $WORKSPACE/.control/rcs-parameters.toml              (bstack-managed)
#   3. $WORKSPACE/research/rcs/data/parameters.toml         (paper canonical)
#   4. <bstack-repo>/assets/templates/rcs-parameters.toml.template  (fallback)
#
# Exit codes:
#   0 — all λᵢ > 0 (composite stable)
#   1 — at least one λᵢ ≤ 0 (composite unstable)
#   2 — parameters config not found
#   3 — drift > 1e-4 detected (only in --strict mode)
#   4 — python3 or tomllib unavailable (Python ≥ 3.11 required)

set -uo pipefail

CONFIG=""
FORMAT="json"
STRICT=0
WORKSPACE="${BROOMVA_WORKSPACE:-$PWD}"
BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for arg in "$@"; do
    case "$arg" in
        --config=*) CONFIG="${arg#*=}" ;;
        --human)    FORMAT="human" ;;
        --json)     FORMAT="json" ;;
        --strict)   STRICT=1 ;;
        --help|-h)
            grep -E '^#( |$)' "$0" | sed 's/^# \?//' | head -32
            exit 0
            ;;
        *)
            echo "compute-lambda: unknown flag: $arg" >&2
            exit 2
            ;;
    esac
done

# Locate config
if [ -z "$CONFIG" ]; then
    if [ -f "$WORKSPACE/.control/rcs-parameters.toml" ]; then
        CONFIG="$WORKSPACE/.control/rcs-parameters.toml"
    elif [ -f "$WORKSPACE/research/rcs/data/parameters.toml" ]; then
        CONFIG="$WORKSPACE/research/rcs/data/parameters.toml"
    else
        CONFIG="$BSTACK_REPO/assets/templates/rcs-parameters.toml.template"
    fi
fi

if [ ! -f "$CONFIG" ]; then
    echo "compute-lambda: parameters config not found at $CONFIG" >&2
    echo "                run 'bash $BSTACK_REPO/scripts/install-l3-stability.sh' to deploy a default" >&2
    exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "compute-lambda: python3 not available (need ≥ 3.11 for tomllib)" >&2
    exit 4
fi

python3 - "$CONFIG" "$FORMAT" "$STRICT" <<'PYEOF'
import sys, math, json
try:
    import tomllib
except ImportError:
    print("compute-lambda: tomllib not available (need Python ≥ 3.11)", file=sys.stderr)
    sys.exit(4)

config_path, fmt, strict_str = sys.argv[1], sys.argv[2], sys.argv[3]
strict = (strict_str == "1")

with open(config_path, "rb") as f:
    data = tomllib.load(f)

results = []
for lvl in data.get("levels", []):
    try:
        gamma = lvl["gamma"]
        L_theta = lvl["L_theta"]
        rho = lvl["rho"]
        L_d = lvl["L_d"]
        eta = lvl["eta"]
        beta = lvl["beta"]
        tau_bar = lvl["tau_bar"]
        nu = lvl["nu"]
        tau_a = lvl["tau_a"]
    except KeyError as e:
        print(f"compute-lambda: level {lvl.get('id','?')} missing parameter: {e}", file=sys.stderr)
        sys.exit(2)

    if nu < 1.0:
        print(f"compute-lambda: level {lvl['id']} has nu={nu} < 1.0; formula requires nu >= 1", file=sys.stderr)
        sys.exit(2)
    if tau_a <= 0:
        print(f"compute-lambda: level {lvl['id']} has tau_a={tau_a} <= 0; switching cost undefined", file=sys.stderr)
        sys.exit(2)

    adapt_cost = L_theta * rho
    design_cost = L_d * eta
    delay_cost = beta * tau_bar
    switch_cost = math.log(nu) / tau_a
    lam = gamma - adapt_cost - design_cost - delay_cost - switch_cost

    cached = data.get("derived", {}).get("lambda", {}).get(lvl["id"])
    drift = (lam - cached) if cached is not None else None

    results.append({
        "id": lvl["id"],
        "name": lvl.get("name", ""),
        "lambda": lam,
        "cached": cached,
        "drift": drift,
        "stable": lam > 0,
        "costs": {
            "gamma": gamma,
            "adapt": adapt_cost,
            "design": design_cost,
            "delay": delay_cost,
            "switch": switch_cost,
        },
    })

omega = min((r["lambda"] for r in results), default=0.0)
omega_level = min(results, key=lambda r: r["lambda"])["id"] if results else None
all_stable = all(r["stable"] for r in results)

if fmt == "human":
    print(f"RCS Stability Budget")
    print(f"  Config: {config_path}")
    print(f"")
    fmt_row = f"  {{:4}} {{:12}} {{:>14}} {{:>14}} {{:>10}} {{:>8}}"
    print(fmt_row.format("ID", "Name", "lambda(comp)", "lambda(cache)", "drift", "stable"))
    print(fmt_row.format("----", "------------", "--------------", "--------------", "----------", "--------"))
    for r in results:
        cached_str = f"{r['cached']:.6f}" if r["cached"] is not None else "-"
        drift_str  = f"{r['drift']:+.6f}" if r["drift"] is not None else "-"
        stable_str = "yes" if r["stable"] else "NO"
        print(fmt_row.format(r["id"], r["name"], f"{r['lambda']:.6f}", cached_str, drift_str, stable_str))
    print(f"")
    print(f"  composite omega = {omega:.6f}  (min lambda_i at {omega_level})")
    print(f"  all_stable = {all_stable}")
else:
    out = {
        "config": config_path,
        "levels": results,
        "composite_omega": omega,
        "composite_omega_level": omega_level,
        "all_stable": all_stable,
    }
    print(json.dumps(out, indent=2))

if not all_stable:
    sys.exit(1)

if strict:
    drifts = [abs(r["drift"]) for r in results if r["drift"] is not None]
    max_drift = max(drifts) if drifts else 0.0
    if max_drift > 1e-4:
        print(f"compute-lambda: --strict drift check failed; max drift = {max_drift:.6f} > 1e-4", file=sys.stderr)
        sys.exit(3)

sys.exit(0)
PYEOF
