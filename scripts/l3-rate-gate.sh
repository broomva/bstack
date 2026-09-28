#!/usr/bin/env bash
# bstack/scripts/l3-rate-gate.sh — Governance commit rate limiter (Gate G1/G2).
#
# Enforces the τ_a₃ assumption from the RCS stability budget: governance-class
# mutations (L3 paths) must not exceed one commit per τ_a₃ window (default
# 86400s = 1 day). Faster churn pushes λ₃ negative and destabilizes the whole
# RCS hierarchy.
#
# Path patterns are read from $WORKSPACE/.control/rcs-parameters.toml under
# [gates.l3_paths]. If the file is missing, falls back to bstack's default:
#   CLAUDE.md, AGENTS.md, .control/policy.yaml,
#   .control/rcs-parameters.toml, METALAYER.md
#
# τ_a₃ is read from the [[levels]] entry with id="L3" (default 86400 if missing).
#
# Usage:
#   bash scripts/l3-rate-gate.sh                 # check current rate
#   bash scripts/l3-rate-gate.sh --staged        # include staged-but-uncommitted (for pre-commit hook)
#   bash scripts/l3-rate-gate.sh --json          # JSON output
#   bash scripts/l3-rate-gate.sh --warn-only     # always exit 0, only print warning
#   bash scripts/l3-rate-gate.sh --window=3600   # override τ_a₃ in seconds
#
# ── The correction lane ──────────────────────────────────────────────────────
#
# The budget above counts every L3 modification the same way, and its only
# exemption is CREATION (BRO-1435). That makes correcting a claim the tree has
# already falsified cost exactly as much as adding a new rule, which is backwards
# on this gate's own theory: churn is penalised because L2/L1 must re-converge
# against a moved rule, and a correction MOVES THE RULE BACK toward the tree it
# describes. It reduces divergence rather than adding surface.
#
# Observed cost of not having this, measured in a downstream workspace on
# 2026-09-09: a session established that a provider feature could not be enabled
# on that arm, and could not commit the correction to the governance file saying
# otherwise, because two earlier changes had spent the window hours earlier. The repository therefore carried a statement its own
# code had disproved, with the fix queued behind a clock.
#
# A correction is DECLARED, two ways, because the two readers see different things:
#
#   - counting history: a `L3-Correction: <reason>` trailer in the commit message
#   - counting staged:  BSTACK_L3_CORRECTION="<reason>" in the environment,
#     because `pre-commit` runs BEFORE a commit message exists and therefore
#     cannot read a trailer at all
#
# Declared corrections spend a SEPARATE, SMALLER budget (default 3/window,
# `gates.l3_paths.correction_budget`) rather than being exempt.
#
# Read the trailer as ATTRIBUTION, NOT AUTHORIZATION. It is self-asserted, and a
# self-asserted exemption is the endogenous-reference failure this repo's own
# `.control/leverage-setpoints.yaml` warns about — "a reference the agent
# authored is endogenous; a dead sensor announces itself, an endogenous reference
# reads as green". Three things bound it: the budget is finite, so a false claim
# buys 3 and not infinity; the reason is required and non-empty; and every claim
# is permanent in the commit message where a pattern of abuse is greppable. This
# lane lowers the cost of honesty. It does not verify the claim, and nothing here
# should ever be cited as though it did.
#
# Exit codes:
#   0 — within budget (or --warn-only)
#   1 — budget exceeded (more than 1 L3 commit in τ_a₃ window)
#   2 — parameters config malformed or git not available

set -uo pipefail

WORKSPACE="${BROOMVA_WORKSPACE:-$PWD}"
BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

INCLUDE_STAGED=0
FORMAT="human"
WARN_ONLY=0
WINDOW=""

for arg in "$@"; do
    case "$arg" in
        --staged)        INCLUDE_STAGED=1 ;;
        --json)          FORMAT="json" ;;
        --human)         FORMAT="human" ;;
        --warn-only)     WARN_ONLY=1 ;;
        --window=*)      WINDOW="${arg#*=}" ;;
        --help|-h)
            grep -E '^#( |$)' "$0" | sed 's/^# \?//' | head -28
            exit 0
            ;;
        *)
            echo "l3-rate-gate: unknown flag: $arg" >&2
            exit 2
            ;;
    esac
done

# Locate parameters config (mirrors compute-lambda.sh)
CONFIG=""
if [ -f "$WORKSPACE/.control/rcs-parameters.toml" ]; then
    CONFIG="$WORKSPACE/.control/rcs-parameters.toml"
elif [ -f "$WORKSPACE/research/rcs/data/parameters.toml" ]; then
    CONFIG="$WORKSPACE/research/rcs/data/parameters.toml"
else
    CONFIG="$BSTACK_REPO/assets/templates/rcs-parameters.toml.template"
fi

# Default L3 paths (fallback if config has no [gates.l3_paths])
DEFAULT_L3_PATHS=(
    "CLAUDE.md"
    "AGENTS.md"
    ".control/policy.yaml"
    ".control/rcs-parameters.toml"
    "METALAYER.md"
)

# Read L3 paths + tau_a from config (via Python for robust TOML parsing)
#
# Governance values come from the config or from the documented default --
# never from the caller's environment. Optional keys are emitted only when the
# config SETS them, so without this reset an ambient `CORRECTION_BUDGET=999`
# survives into `${CORRECTION_BUDGET:-3}` and the correction lane becomes
# unbounded: no config edit, no --no-verify, no trace in the commit message.
# That defeats the first of the three bounds this lane's own header claims
# ("the budget is finite, so a false claim buys 3 and not infinity"). The same
# hole held for TAU_A_L3 whenever the L3 level omitted tau_a, and for all three
# whenever no config was read at all, so all three are reset here. Any future
# optional key must be reset here too.
CORRECTION_BUDGET=
TAU_A_L3=
L3_PATHS=()
if [ -f "$CONFIG" ] && command -v python3 >/dev/null 2>&1; then
    # The config is DATA and is read as data (BRO-2651). This block used to
    # print shell assignments and eval them, with each pattern wrapped in double
    # quotes and tau_a pasted bare, so a config holding
    #     patterns = ["$(touch /tmp/x)"]     or     tau_a = "86400; touch /tmp/x"
    # ran the command. In CI the config comes from the PR head, so anyone able
    # to open a PR could execute on the runner. Now python validates every value
    # and prints tab-separated KEY<TAB>VALUE records; bash assigns each value
    # with a plain assignment and re-checks the numeric ones. No value is ever
    # parsed as shell. Control characters are refused in python, so a newline
    # or tab inside a value cannot forge a record.
    #
    # A config that is present but malformed fails CLOSED with exit 2 (the code
    # this header already reserves for it). It used to be dropped for the
    # defaults in silence, which let a broken governance file relax its own gate.
    # A missing TOML parser is not a malformed config: the defaults apply, and
    # a warning is printed because nothing was read.
    #
    # Quoting note: no backticks in this heredoc. It is nested inside $(), where a
    # literal backtick is a bash-3.2 parse hazard (tests/bash32-parse-safety.test.sh).
    CONFIG_RECORDS="$(python3 - "$CONFIG" <<'PYEOF'
import math, sys

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        print("NOPARSER\t-")
        sys.exit(0)

def bad(msg):
    print("l3-rate-gate: malformed config " + sys.argv[1] + ": " + msg, file=sys.stderr)
    sys.exit(3)

def clean(s):
    return not any(ord(c) < 32 or ord(c) == 127 for c in s)

try:
    with open(sys.argv[1], "rb") as f:
        data = tomllib.load(f)
except Exception as e:
    bad("not valid TOML (" + type(e).__name__ + ")")

gates = data.get("gates", {})
if not isinstance(gates, dict):
    bad("[gates] must be a table")
l3_gate = gates.get("l3_paths", {})
if not isinstance(l3_gate, dict):
    bad("[gates.l3_paths] must be a table")

patterns = l3_gate.get("patterns", [])
if not isinstance(patterns, list):
    bad("gates.l3_paths.patterns must be an array of strings")
if len(patterns) > 256:
    bad("gates.l3_paths.patterns has more than 256 entries")
for p in patterns:
    if not isinstance(p, str) or not p or len(p) > 1024 or not clean(p):
        bad("every gates.l3_paths.patterns entry must be a non-empty string "
            "of at most 1024 characters with no control characters")

# bool is a subclass of int in Python, so a TOML correction_budget of true
# passes an isinstance(..., int) check; it is refused explicitly.
correction_budget = l3_gate.get("correction_budget")
if correction_budget is not None:
    if (
        not isinstance(correction_budget, int)
        or isinstance(correction_budget, bool)
        or not 0 <= correction_budget <= 1000
    ):
        bad("gates.l3_paths.correction_budget must be an integer from 0 to 1000")

levels = data.get("levels", [])
if not isinstance(levels, list):
    bad("levels must be an array of tables")
tau_a_l3 = None
for lvl in levels:
    if not isinstance(lvl, dict):
        bad("every [[levels]] entry must be a table")
    if lvl.get("id") == "L3":
        tau_a_l3 = lvl.get("tau_a")
        break
if tau_a_l3 is not None:
    # One year is far past any governance window; the ceiling also keeps the
    # later cutoff arithmetic well inside a 64-bit shell integer.
    if (
        not isinstance(tau_a_l3, (int, float))
        or isinstance(tau_a_l3, bool)
        or not math.isfinite(tau_a_l3)
        or not 1 <= tau_a_l3 <= 31536000
    ):
        bad("the tau_a of the L3 level must be a number of seconds from 1 to 31536000")

for p in patterns:
    print("PATH\t" + p)
if correction_budget is not None:
    print("CORRECTION_BUDGET\t" + str(correction_budget))
if tau_a_l3 is not None:
    # "%.0f" is what the shell applied to this value before, so a float tau_a
    # rounds exactly as it did.
    print("TAU_A_L3\t" + format(tau_a_l3, ".0f"))
PYEOF
)"
    CONFIG_RC=$?
    if [ "$CONFIG_RC" -ne 0 ]; then
        echo "l3-rate-gate: cannot read parameters config $CONFIG (python exit $CONFIG_RC); refusing to fall back to defaults" >&2
        exit 2
    fi
    TAB="$(printf '\t')"
    while IFS= read -r record; do
        [ -n "$record" ] || continue
        key="${record%%"$TAB"*}"
        value="${record#*"$TAB"}"
        case "$key" in
            PATH)
                L3_PATHS+=("$value") ;;
            CORRECTION_BUDGET|TAU_A_L3)
                # Re-checked here so the shell never trusts the reader for the
                # two values that later reach arithmetic.
                case "$value" in
                    ''|*[!0-9]*)
                        echo "l3-rate-gate: config value $key is not a whole number" >&2
                        exit 2 ;;
                esac
                if [ "$key" = "TAU_A_L3" ]; then TAU_A_L3="$value"; else CORRECTION_BUDGET="$value"; fi
                ;;
            NOPARSER)
                echo "l3-rate-gate: no TOML parser (python >= 3.11 or tomli); $CONFIG was NOT read, using the default L3 paths and window" >&2
                ;;
            *)
                echo "l3-rate-gate: unexpected record from the config reader" >&2
                exit 2 ;;
        esac
    done <<EOF
$CONFIG_RECORDS
EOF
fi

# Apply fallbacks — `${arr[*]:-}` is bash-3.2-safe (macOS default)
if [ -z "${L3_PATHS[*]:-}" ]; then
    L3_PATHS=("${DEFAULT_L3_PATHS[@]}")
fi
TAU_A_L3="${TAU_A_L3:-86400}"
if [ -n "$WINDOW" ]; then
    case "$WINDOW" in
        *[!0-9.]*|.*|*.|*.*.*)
            echo "l3-rate-gate: --window must be a number of seconds, got: $WINDOW" >&2
            exit 2 ;;
    esac
    TAU_A_L3="$WINDOW"
fi

# Cast tau_a to integer seconds (it may be a float in TOML)
TAU_A_L3_INT=$(printf '%.0f' "$TAU_A_L3" 2>/dev/null || echo "86400")

# Check git availability
if ! command -v git >/dev/null 2>&1; then
    echo "l3-rate-gate: git not available" >&2
    exit 2
fi

cd "$WORKSPACE" 2>/dev/null || { echo "l3-rate-gate: cannot cd to $WORKSPACE" >&2; exit 2; }

if ! git rev-parse --git-dir >/dev/null 2>&1; then
    # Not a git repo — nothing to gate on; exit 0 silently
    exit 0
fi

# Compute the cutoff timestamp (now - tau_a_l3)
NOW=$(date +%s)
CUTOFF=$((NOW - TAU_A_L3_INT))

# Count L3-class commits in the window that MODIFIED an L3 file (--diff-filter=M).
# Additions (creation — e.g. the initial `bstack bootstrap` scaffold) are not
# mutations: there is no prior governance state to destabilize, so they do not
# consume the rate budget (BRO-1435). `grep -c .` counts robustly regardless of
# a trailing newline (fixes a latent off-by-one in the prior `wc -l` form).
# Split the window's L3 mutations into the two lanes. A commit is a declared
# CORRECTION iff its message carries a `L3-Correction:` trailer with a non-empty
# reason; everything else is an ordinary mutation. The loop is over the SHAs that
# already matched an L3 path in the window — a set that is 0-5 in practice, since
# exceeding it is the condition this gate exists to detect.
COUNT_COMMITTED=0
COUNT_CORRECTIONS=0
while IFS= read -r sha; do
    [ -n "$sha" ] || continue
    # `git interpret-trailers --parse` rather than a grep over the body: git
    # treats only the final paragraph as trailers, so a line starting with the
    # key in a MIDDLE paragraph is prose, not a declaration. A grep counted it
    # and handed out a free correction. Delegating the definition to git also
    # means folded and multi-line trailers behave the way every other tool here
    # already assumes.
    if git log -1 --format='%B' "$sha" 2>/dev/null \
        | git interpret-trailers --parse 2>/dev/null \
        | grep -qiE '^L3-Correction:[[:space:]]*[^[:space:]]'; then
        COUNT_CORRECTIONS=$((COUNT_CORRECTIONS + 1))
    else
        COUNT_COMMITTED=$((COUNT_COMMITTED + 1))
    fi
done <<EOF
$(git log --diff-filter=M --since="@$CUTOFF" --format='%H' -- "${L3_PATHS[@]}" 2>/dev/null)
EOF

COUNT_STAGED=0
STAGED_FILES=""
if [ "$INCLUDE_STAGED" = "1" ]; then
    # Count a staged L3 file as a mutation ONLY if it already exists at HEAD.
    # Newly-created L3 files (e.g. the initial `bstack bootstrap` scaffold) are
    # creation, not mutation — there is no prior governance state to destabilize,
    # so they are exempt from the rate budget (BRO-1435). If HEAD does not exist
    # yet (first commit ever), every path is a creation → exempt.
    staged_now="$(git diff --cached --name-only 2>/dev/null)"
    for path in "${L3_PATHS[@]}"; do
        if printf '%s\n' "$staged_now" | grep -qFx "$path"; then
            if git cat-file -e "HEAD:$path" 2>/dev/null; then
                COUNT_STAGED=$((COUNT_STAGED + 1))
                STAGED_FILES="$STAGED_FILES $path"
            fi
        fi
    done
fi

# Budget: 1 L3 commit per tau_a_l3 window. Exceeded if (committed + staged) > 1.
# `pre-commit` fires before a commit message exists, so the staged side cannot
# read a trailer and takes its declaration from the environment instead.
STAGED_IS_CORRECTION=0
if [ -n "${BSTACK_L3_CORRECTION:-}" ]; then
    STAGED_IS_CORRECTION=1
fi

BUDGET=1
CORRECTION_BUDGET="${CORRECTION_BUDGET:-3}"

EXCEEDED=0
EXCEEDED_LANE=""

if [ "$STAGED_IS_CORRECTION" = "1" ]; then
    # The lanes are independent, and this is the clause that carries the whole
    # fix. A declared correction is gated by the CORRECTION budget alone:
    # mutations already spent in this window are historical and must not block
    # it. The first cut tested (committed_mutations + staged) here, which reads
    # plausible and reproduces the exact failure this change exists to remove —
    # in that workspace the window held two committed mutations,
    # so a correction would still have been refused and nothing would have
    # changed. Test E is the regression guard for that mistake specifically.
    COUNT_CORRECTIONS=$((COUNT_CORRECTIONS + COUNT_STAGED))
    TOTAL=$COUNT_COMMITTED
    if [ "$COUNT_CORRECTIONS" -gt "$CORRECTION_BUDGET" ]; then
        # A lane that cannot be exhausted is an exemption with extra steps, and
        # a self-asserted exemption is unbounded by construction.
        EXCEEDED=1
        EXCEEDED_LANE="correction"
    fi
else
    # An exhausted correction lane does NOT block an ordinary mutation that has
    # budget: the two are separate allowances, not a shared pool.
    TOTAL=$((COUNT_COMMITTED + COUNT_STAGED))
    if [ "$TOTAL" -gt "$BUDGET" ]; then
        EXCEEDED=1
        EXCEEDED_LANE="mutation"
    fi
fi

# Format output
if [ "$FORMAT" = "json" ]; then
    # Patterns are config data. The reader refuses control characters, which
    # leaves backslash and double quote as the only characters JSON needs escaped.
    L3_PATHS_JSON=""
    for p in "${L3_PATHS[@]}"; do
        p="${p//\\/\\\\}"
        p="${p//\"/\\\"}"
        L3_PATHS_JSON="$L3_PATHS_JSON${L3_PATHS_JSON:+,}\"$p\""
    done
    cat <<EOF
{
  "window_seconds": $TAU_A_L3_INT,
  "cutoff_unix": $CUTOFF,
  "committed_in_window": $COUNT_COMMITTED,
  "staged_l3_files": $COUNT_STAGED,
  "staged_is_correction": $([ "$STAGED_IS_CORRECTION" = "1" ] && echo "true" || echo "false"),
  "corrections_in_window": $COUNT_CORRECTIONS,
  "total": $TOTAL,
  "budget": $BUDGET,
  "correction_budget": $CORRECTION_BUDGET,
  "exceeded_lane": "$EXCEEDED_LANE",
  "exceeded": $([ "$EXCEEDED" = "1" ] && echo "true" || echo "false"),
  "l3_paths": [$L3_PATHS_JSON]
}
EOF
else
    echo "L3 Rate Gate"
    echo "  Window:    ${TAU_A_L3_INT}s ($(( TAU_A_L3_INT / 3600 ))h)"
    echo "  Committed: $COUNT_COMMITTED L3 commit(s) in window"
    [ "$INCLUDE_STAGED" = "1" ] && echo "  Staged:    $COUNT_STAGED L3 file(s)$STAGED_FILES"
    if [ "$STAGED_IS_CORRECTION" = "1" ]; then
        echo "  Mutations: $TOTAL / $BUDGET (historical — not applied to a declared correction)"
        echo "  Corrections: $COUNT_CORRECTIONS / $CORRECTION_BUDGET allowed  <- judged on this lane"
    else
        echo "  Total:     $TOTAL / $BUDGET allowed"
        echo "  Corrections: $COUNT_CORRECTIONS / $CORRECTION_BUDGET allowed (declared)"
    fi
    if [ "$EXCEEDED" = "1" ]; then
        if [ "$EXCEEDED_LANE" = "correction" ]; then
            echo "  Status:    EXCEEDED — the declared-CORRECTION budget is spent"
        else
            echo "  Status:    EXCEEDED — L3 mutation rate violates RCS stability budget"
        fi
        echo ""
        echo "  Why this matters:"
        echo "    The RCS stability budget assumes one L3 mutation per tau_a_3 = ${TAU_A_L3_INT}s."
        echo "    Faster churn pushes lambda_3 negative and destabilizes the whole hierarchy."
        echo "    See: bstack/references/primitives.md \"L3 stability constraint\""
        echo ""
        echo "  Recommended:"
        echo "    - Postpone non-urgent governance changes until $(date -r "$((CUTOFF + TAU_A_L3_INT))" 2>/dev/null || echo "the next window")"
        if [ "$EXCEEDED_LANE" != "correction" ]; then
            echo "    - If this RESTORES correspondence between an L3 rule and the tree"
            echo "      (the rule states something the code has already falsified), declare"
            echo "      it: BSTACK_L3_CORRECTION=\"<reason>\" for the commit, plus an"
            echo "      L3-Correction: <reason> trailer so the window can count it later."
            echo "      That lane has its own budget ($CORRECTION_BUDGET/window) and is"
            echo "      attribution, not authorization — the claim is permanent and greppable."
        fi
        echo "    - Or split the change into multiple PRs across days"
        echo "    - If urgent, bypass with: git commit --no-verify (DOCUMENT WHY in commit body)"
    else
        echo "  Status:    OK — within budget"
    fi
fi

if [ "$EXCEEDED" = "1" ] && [ "$WARN_ONLY" = "0" ]; then
    exit 1
fi

exit 0
