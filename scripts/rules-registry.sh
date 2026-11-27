#!/usr/bin/env bash
# rules-registry.sh — bstack's bridge to the `correct` skill (BRO-4147).
#
# bstack installs hooks. The `correct` skill (broomva/skills) proves them: it keeps a
# rule registry beside the policy file and replays each rule's proofs and controls
# through the hook entry the harness would run. bstack ships the rows for its own hooks
# (references/rules-pack.yaml) and does not carry a copy of the runner.
#
#   rules-registry.sh locate   print the path of the skill's correct.py
#   rules-registry.sh init     give the workspace its registry, with bstack's rows; then replay
#   rules-registry.sh check    replay the workspace's registry
#
# The workspace is $BROOMVA_WORKSPACE, else the current directory.
# $BSTACK_CORRECT names correct.py outright (CI, tests, a checkout); otherwise the
# installed skill is used.
#
# $BSTACK_RULES_BUDGET_S, when set, is how many seconds `check` may take (doctor sets it:
# a registry with many rows replays for minutes, and doctor is the quick look).
#
# Exit codes: 0 done and every row held; 1 a row did not hold; 2 the command could not
# run; 3 the correct skill is not installed (or too old for `init`); 4 the workspace has no
# rule registry; 5 `check` did not finish within the budget, so nothing is known.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BSTACK_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE="${BROOMVA_WORKSPACE:-$PWD}"
PACK="$BSTACK_REPO/references/rules-pack.yaml"
INSTALL_HINT="npx skills add broomva/skills --skill correct -g"

# shellcheck source=scripts/lib/broomva-paths.sh
. "$SCRIPT_DIR/lib/broomva-paths.sh"

locate() {
    local candidate
    if [ -n "${BSTACK_CORRECT:-}" ]; then
        # A named path that is not there is an error, never a reason to fall back to another copy.
        if [ -f "$BSTACK_CORRECT" ]; then
            printf '%s\n' "$BSTACK_CORRECT"
            return 0
        fi
        echo "rules-registry: BSTACK_CORRECT names $BSTACK_CORRECT, which is not a file" >&2
        return 2
    fi
    for candidate in "$HOME/.claude/skills/correct/scripts/correct.py" \
                     "$HOME/.agents/skills/correct/scripts/correct.py"; do
        if [ -f "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 3
}

# The policy file, or nothing when the workspace has none.
policy_file() {
    broomva_repo_path policy "$WORKSPACE" 2>/dev/null || true
}

# The rule runner reads the policy's `gates.hard` list and treats a policy without one as
# unreadable. bstack's policy template had no such key before 0.53.0, so a workspace
# bootstrapped earlier gets an empty list added under `gates:`. The file is parsed to decide
# and parsed again to confirm: the write happens only when the one difference is the new
# empty list. Anything else (no `gates:` mapping, a `gates:` written in flow style, a file
# that does not parse) is left alone and reported, and the replay then says what is missing.
ensure_hard_gates() {
    local policy
    policy="$(policy_file)"
    [ -n "$policy" ] && [ -f "$policy" ] || return 0
    python3 -I - "$policy" <<'PYEOF'
import re, site, sys
if site.getusersitepackages() not in sys.path:
    sys.path.append(site.getusersitepackages())
try:
    import yaml
except ImportError:
    sys.exit(0)  # the replay reports the missing PyYAML itself
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
def note(why):
    print(f"  {path}: {why}; add `hard: []` under `gates:` by hand", file=sys.stderr)
    sys.exit(0)
try:
    before = yaml.safe_load(text)
except yaml.YAMLError:
    note("does not parse as YAML")
if not isinstance(before, dict) or not isinstance(before.get("gates"), dict):
    note("has no `gates:` mapping")
if "hard" in before["gates"]:
    sys.exit(0)
added = ("gates:\n"
         "  # Hard gates the rule registry must account for: a gate with a `pattern` needs a row\n"
         "  # that proves it (`policy_gate:`). Added by bstack; the control gate's own patterns are\n"
         "  # embedded in scripts/control-gate-hook.sh and proven by references/rules-pack.yaml.\n"
         "  hard: []\n")
after_text, count = re.subn(r"^gates:[ \t]*(#.*)?\n", lambda _m: added, text, count=1, flags=re.M)
if count != 1:
    note("writes `gates:` in a form this script does not edit")
try:
    after = yaml.safe_load(after_text)
except yaml.YAMLError:
    note("would not parse with the key added")
expected = dict(before, gates=dict(before["gates"], hard=[]))
if after != expected:
    note("would change more than `gates.hard` with the key added")
open(path, "w", encoding="utf-8").write(after_text)
print(f"  added `gates.hard: []` to {path}")
PYEOF
}

cmd_init() {
    local correct rc=0
    correct="$(locate)" || rc=$?
    if [ "$rc" -eq 3 ]; then
        echo "the correct skill is not installed, so no rule registry was written."
        echo "install it, then run bootstrap again: $INSTALL_HINT"
        return 3
    fi
    [ "$rc" -eq 0 ] || return "$rc"
    # `init --pack` arrived with the pack format; an older copy of the skill refuses the option.
    if ! grep -q -- '--pack' "$correct"; then
        echo "the installed correct skill ($correct) predates \`init --pack\`, so no rule registry was written."
        echo "update it, then run bootstrap again: $INSTALL_HINT"
        return 3
    fi
    ensure_hard_gates || return 2
    python3 -I "$correct" init --root "$WORKSPACE" --pack "$PACK"
}

cmd_check() {
    local correct rc=0 policy
    correct="$(locate)" || rc=$?
    policy="$(policy_file)"
    if [ -z "$policy" ] || [ ! -f "$(dirname "$policy")/rules.yaml" ]; then
        echo "this workspace has no rule registry (\`bstack bootstrap\` writes one)."
        return 4
    fi
    if [ "$rc" -eq 3 ]; then
        echo "the workspace has a rule registry and the correct skill that replays it is not installed."
        echo "install it: $INSTALL_HINT"
        return 3
    fi
    [ "$rc" -eq 0 ] || return "$rc"
    if [ -z "${BSTACK_RULES_BUDGET_S:-}" ]; then
        python3 -I "$correct" check --root "$WORKSPACE" "$@"
        return
    fi
    # With a budget: the replay runs in its own process group, and the whole group is
    # stopped when the time is up, so no hook under replay is left running.
    python3 -I - "$BSTACK_RULES_BUDGET_S" "$correct" "$WORKSPACE" "$@" <<'PYEOF'
import os, signal, subprocess, sys
try:
    budget = float(sys.argv[1])
    if not budget > 0:
        raise ValueError
except ValueError:
    print(f"rules-registry: BSTACK_RULES_BUDGET_S must be a number of seconds above 0, got {sys.argv[1]!r}", file=sys.stderr)
    sys.exit(2)
correct, workspace, rest = sys.argv[2], sys.argv[3], sys.argv[4:]
proc = subprocess.Popen([sys.executable, "-I", correct, "check", "--root", workspace, *rest],
                        start_new_session=True)
try:
    sys.exit(proc.wait(timeout=budget))
except subprocess.TimeoutExpired:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()
    print(f"the replay did not finish within {budget:g} s, so nothing is known about the registry.")
    sys.exit(5)
PYEOF
}

case "${1:-}" in
    locate) locate ;;
    init)   cmd_init ;;
    check)  shift; cmd_check "$@" ;;
    *)
        sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
        exit 2
        ;;
esac
