#!/usr/bin/env bash
# rules-registry.sh — bstack's bridge to the `correct` skill (BRO-4147).
#
# bstack installs hooks. The `correct` skill (broomva/skills) replays them: it keeps a
# rule registry in the workspace's governance folder and runs each rule's proofs and
# controls through the hook entry the harness would run. bstack ships the rows for its
# own hooks (references/rules-pack.yaml) and does not carry a copy of the runner.
#
#   rules-registry.sh locate            print the path of the skill's correct.py
#   rules-registry.sh init [--dry-run]  give the workspace its registry, with bstack's rows,
#                                       and replay it. A workspace that has a registry gets
#                                       nothing written. --dry-run lists what a workspace
#                                       without a registry would get and writes nothing.
#   rules-registry.sh verify            a workspace that has a registry: replay it and say
#                                       which of bstack's rows it lacks. Writes nothing.
#   rules-registry.sh check [OPTIONS]   replay the workspace's registry; OPTIONS go to the
#                                       skill's `check` (--json, --only ID, --registry FILE ...)
#
# None of these changes the policy file. The workspace is $BROOMVA_WORKSPACE, else the
# current directory.
#
# The skill: $BSTACK_CORRECT names its correct.py outright (CI, tests, a checkout, a copy
# installed some other way). Otherwise it is looked for in ~/.claude/skills/correct and
# ~/.agents/skills/correct, and of two copies the one that has `init --pack` is used.
#
# $BSTACK_RULES_BUDGET_S, when set, is how many seconds the whole command may take
# (scripts/lib/run-with-budget.py). bootstrap and doctor set it: a registry with many rows
# replays for minutes, and a copy of the skill that hangs must not hang them.
#
# Exit codes
#   0  done, and no row failed (gap rows and known misses are listed in the output)
#   1  a row did not hold
#   2  the command could not run (a bad argument or budget, a file that is not a registry)
#   3  the correct skill is not installed, or the installed copy is too old for `init`
#   4  the workspace has no rule registry
#   5  the budget ran out, so nothing is known
#   6  `init` or `verify`: the workspace already had a registry, no row of it failed, and
#      it lacks part of bstack's pack (the output lists what)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The budget is the outermost thing: the script runs itself again inside it, so everything
# below (finding the skill, reading the workspace, the skill's run) is inside the limit, and
# a budget that is not a number stops here, before anything is read or written.
if [ -n "${BSTACK_RULES_BUDGET_S:-}" ] && [ -z "${BSTACK_RULES_IN_BUDGET:-}" ]; then
    BSTACK_RULES_IN_BUDGET=1 exec python3 -I "$SCRIPT_DIR/lib/run-with-budget.py" "$BSTACK_RULES_BUDGET_S" \
        bash "${BASH_SOURCE[0]}" "$@"
fi

BSTACK_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE="${BROOMVA_WORKSPACE:-$PWD}"
PACK="$BSTACK_REPO/references/rules-pack.yaml"
INSTALL_HINT="npx skills add broomva/skills --skill correct -g"

# shellcheck source=scripts/lib/broomva-paths.sh
. "$SCRIPT_DIR/lib/broomva-paths.sh"
# shellcheck source=scripts/lib/plugin-preference.sh
. "$SCRIPT_DIR/lib/plugin-preference.sh"

usage() { sed -n '2,39p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

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

# What a replay did not look at, said next to its result whatever the result. The runner
# reads settings files; hooks that the bstack plugin provides are in the plugin's own
# hooks.json, which it does not read yet (BRO-3811). One line, on stderr: `check --json`
# and `--markdown` own stdout, and doctor repeats lines that start with `note:`.
note_unseen_sources() {
    [ -n "${HOME:-}" ] || return 0
    if bstack_plugin_enabled 2>/dev/null; then
        echo "note: the bstack plugin is enabled, and the hooks it provides are not in a settings file: this replay did not see them (BRO-3811)" >&2
    fi
}

# skill_or_stop WHAT -> the path of correct.py on stdout, or the reason on stdout and 3 / 2.
skill_or_stop() {
    local correct rc=0
    correct="$(locate)" || rc=$?
    if [ "$rc" -eq 3 ]; then
        echo "the correct skill is not installed, so $1."
        echo "install it: $INSTALL_HINT"
        return 3
    fi
    [ "$rc" -eq 0 ] || return "$rc"
    # `init --pack` arrived with the pack format; an older copy of the skill refuses the option.
    if ! has_pack_option "$correct"; then
        echo "the installed correct skill ($correct) predates \`init --pack\`, so $1."
        echo "update it: $INSTALL_HINT"
        return 3
    fi
    printf '%s\n' "$correct"
}

# with_pack CORRECT [--dry-run] -> the skill's `init --pack`, its 3 mapped to 6.
with_pack() {
    local correct="$1" rc=0; shift
    python3 -I "$correct" init --root "$WORKSPACE" --pack "$PACK" "$@" || rc=$?
    case "$rc" in
        0|1) note_unseen_sources ;;
        # The skill's 3: the registry was here before, no row failed, and it lacks part of the pack.
        3) note_unseen_sources; rc=6 ;;
    esac
    return "$rc"
}

cmd_init() {
    local correct rc=0 dry=()
    # Arguments are settled before anything is read or written: this command installs a hook.
    while [ $# -gt 0 ]; do
        case "$1" in
            --dry-run) dry=(--dry-run) ;;
            -h|--help) usage; return 0 ;;
            *) echo "rules-registry: init takes --dry-run and nothing else, got: $1" >&2; return 2 ;;
        esac
        shift
    done
    correct="$(skill_or_stop "no rule registry was written")" || rc=$?
    if [ "$rc" -ne 0 ]; then
        [ -z "$correct" ] || printf '%s\n' "$correct"
        return "$rc"
    fi
    with_pack "$correct" ${dry[@]+"${dry[@]}"}
}

cmd_verify() {
    local correct rc=0
    if [ $# -gt 0 ]; then
        case "$1" in -h|--help) usage; return 0 ;; esac
        echo "rules-registry: verify takes no argument, got: $1" >&2
        return 2
    fi
    # Only a workspace that has a registry: on one the skill's init writes nothing.
    if ! registry_file >/dev/null; then
        echo "this workspace has no rule registry (\`bstack bootstrap\` writes one)."
        return 4
    fi
    correct="$(skill_or_stop "the registry here was not replayed")" || rc=$?
    if [ "$rc" -ne 0 ]; then
        [ -z "$correct" ] || printf '%s\n' "$correct"
        return "$rc"
    fi
    with_pack "$correct"
}

cmd_check() {
    local correct rc=0 arg named=0
    for arg in "$@"; do
        case "$arg" in
            -h|--help) usage; return 0 ;;
            --registry|--registry=*) named=1 ;;
        esac
    done
    correct="$(locate)" || rc=$?
    # A registry named on the command line is the skill's to find; otherwise it is looked
    # for here first, so "no registry" is told apart from "could not run".
    if [ "$named" -eq 0 ] && ! registry_file >/dev/null; then
        echo "this workspace has no rule registry (\`bstack bootstrap\` writes one)."
        return 4
    fi
    if [ "$rc" -eq 3 ]; then
        echo "the workspace has a rule registry and the correct skill that replays it is not installed."
        echo "install it: $INSTALL_HINT"
        return 3
    fi
    [ "$rc" -eq 0 ] || return "$rc"
    python3 -I "$correct" check --root "$WORKSPACE" "$@" || rc=$?
    case "$rc" in 0|1) note_unseen_sources ;; esac
    return "$rc"
}

command="${1:-}"
[ $# -eq 0 ] || shift
case "$command" in
    locate)       locate ;;
    init)         cmd_init "$@" ;;
    verify)       cmd_verify "$@" ;;
    check)        cmd_check "$@" ;;
    -h|--help|help) usage ;;
    *)            usage >&2; exit 2 ;;
esac
