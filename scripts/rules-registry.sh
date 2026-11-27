#!/usr/bin/env bash
# rules-registry.sh — bstack's bridge to the `correct` skill (BRO-4147).
#
# bstack installs hooks. The `correct` skill (broomva/skills) replays them: it keeps a
# rule registry in the workspace's governance folder and runs each rule's proofs and
# controls through the hook entry the harness would run. bstack ships the rows for its
# own hooks (references/rules-pack.yaml) and does not carry a copy of the runner.
#
#   rules-registry.sh locate   print the path of the skill's correct.py
#   rules-registry.sh init     give the workspace its registry, with bstack's rows; then replay
#   rules-registry.sh check    replay the workspace's registry
#
# The workspace is $BROOMVA_WORKSPACE, else the current directory.
# $BSTACK_CORRECT names correct.py outright (CI, tests, a checkout); otherwise the
# installed skill is used, and of two installed copies the one that has `init --pack`.
#
# $BSTACK_RULES_BUDGET_S, when set, is how many seconds `init` or `check` may take. bootstrap
# and doctor set it: a registry with many rows replays for minutes, and a copy of the skill
# that hangs must not hang them.
#
# Exit codes
#   0  done, and no row failed (gap rows and known misses are listed in the output)
#   1  a row did not hold
#   2  the command could not run
#   3  the correct skill is not installed, or the installed copy is too old for `init`
#   4  the workspace has no rule registry
#   5  the budget ran out, so nothing is known
#   6  `init`: the workspace already had a registry, it replays, and it lacks part of
#      bstack's pack (the output lists what)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BSTACK_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE="${BROOMVA_WORKSPACE:-$PWD}"
PACK="$BSTACK_REPO/references/rules-pack.yaml"
INSTALL_HINT="npx skills add broomva/skills --skill correct -g"

# shellcheck source=scripts/lib/broomva-paths.sh
. "$SCRIPT_DIR/lib/broomva-paths.sh"
# shellcheck source=scripts/lib/plugin-preference.sh
. "$SCRIPT_DIR/lib/plugin-preference.sh"

has_pack_option() { grep -q -- '--pack' "$1" 2>/dev/null; }

# locate -> the path of correct.py on stdout. 2 when a named copy is not a file, 3 when no
# copy is installed.
locate() {
    local candidate first=""
    if [ -n "${BSTACK_CORRECT:-}" ]; then
        # A named path that is not there is an error, never a reason to fall back to another copy.
        if [ -f "$BSTACK_CORRECT" ]; then
            printf '%s\n' "$BSTACK_CORRECT"
            return 0
        fi
        echo "rules-registry: BSTACK_CORRECT names $BSTACK_CORRECT, which is not a file" >&2
        return 2
    fi
    # No HOME (a bare service environment) means no installed skill to find: the same
    # answer as a home without it, never an unbound-variable stop.
    [ -n "${HOME:-}" ] || return 3
    # Two harnesses keep their own copy of a skill and one can lag. The copy that has
    # `init --pack` wins wherever it is; an older one is used only when it is all there is
    # (it still replays a registry, and `init` then says it is too old).
    for candidate in "$HOME/.claude/skills/correct/scripts/correct.py" \
                     "$HOME/.agents/skills/correct/scripts/correct.py"; do
        [ -f "$candidate" ] || continue
        if has_pack_option "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
        [ -n "$first" ] || first="$candidate"
    done
    [ -n "$first" ] || return 3
    printf '%s\n' "$first"
}

# The policy file, or nothing when the workspace has none.
policy_file() {
    broomva_repo_path policy "$WORKSPACE" 2>/dev/null || true
}

# The registry, looked for where the skill looks for it: in each governance folder, the
# new name first. It is not tied to where the policy file is; a workspace in the middle of
# the folder rename can have the two in different folders.
registry_file() {
    local dir
    for dir in "$BROOMVA_REPO_DIR" "$BROOMVA_REPO_LEGACY_DIR"; do
        if [ -f "$WORKSPACE/$dir/rules.yaml" ]; then
            printf '%s\n' "$WORKSPACE/$dir/rules.yaml"
            return 0
        fi
    done
    return 1
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
         "  # embedded in scripts/control-gate-hook.sh and replayed by references/rules-pack.yaml.\n"
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

# run_correct CORRECT ARGS... -> the skill's exit status, or 5 when $BSTACK_RULES_BUDGET_S
# ran out. With a budget the skill runs in its own process group, and the whole group is
# stopped when the time is up, so no hook under replay is left running.
run_correct() {
    local correct="$1"; shift
    if [ -z "${BSTACK_RULES_BUDGET_S:-}" ]; then
        python3 -I "$correct" "$@"
        return
    fi
    python3 -I - "$BSTACK_RULES_BUDGET_S" "$correct" "$@" <<'PYEOF'
import os, signal, subprocess, sys
try:
    budget = float(sys.argv[1])
    if not budget > 0:
        raise ValueError
except ValueError:
    print(f"rules-registry: BSTACK_RULES_BUDGET_S must be a number of seconds above 0, got {sys.argv[1]!r}", file=sys.stderr)
    sys.exit(2)
proc = subprocess.Popen([sys.executable, "-I", *sys.argv[2:]], start_new_session=True)
try:
    sys.exit(proc.wait(timeout=budget))
except subprocess.TimeoutExpired:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()
    print(f"the rule registry step did not finish within {budget:g} s, so nothing is known about the registry.")
    sys.exit(5)
PYEOF
}

# What a passing replay did not look at, said next to it. The runner reads settings files;
# hooks that the bstack plugin provides are in the plugin's own hooks.json, which it does
# not read yet (BRO-3811).
note_unseen_sources() {
    [ -n "${HOME:-}" ] || return 0
    if bstack_plugin_enabled 2>/dev/null; then
        # On stderr: `check --json` and `--markdown` own stdout.
        echo "note: the bstack plugin is enabled here. The hooks it provides are not in a settings file," >&2
        echo "      so this replay did not see them (BRO-3811); it covers the hooks in the settings files." >&2
    fi
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
    if ! has_pack_option "$correct"; then
        echo "the installed correct skill ($correct) predates \`init --pack\`, so no rule registry was written."
        echo "update it, then run bootstrap again: $INSTALL_HINT"
        return 3
    fi
    ensure_hard_gates || return 2
    run_correct "$correct" init --root "$WORKSPACE" --pack "$PACK" || rc=$?
    case "$rc" in
        0) note_unseen_sources ;;
        # The skill's 3: the registry was here before, replays, and lacks part of the pack.
        3) note_unseen_sources; rc=6 ;;
    esac
    return "$rc"
}

cmd_check() {
    local correct rc=0
    correct="$(locate)" || rc=$?
    if ! registry_file >/dev/null; then
        echo "this workspace has no rule registry (\`bstack bootstrap\` writes one)."
        return 4
    fi
    if [ "$rc" -eq 3 ]; then
        echo "the workspace has a rule registry and the correct skill that replays it is not installed."
        echo "install it: $INSTALL_HINT"
        return 3
    fi
    [ "$rc" -eq 0 ] || return "$rc"
    run_correct "$correct" check --root "$WORKSPACE" "$@" || rc=$?
    [ "$rc" -ne 0 ] || note_unseen_sources
    return "$rc"
}

case "${1:-}" in
    locate) locate ;;
    init)   cmd_init ;;
    check)  shift; cmd_check "$@" ;;
    *)
        sed -n '2,29p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
        exit 2
        ;;
esac
