#!/usr/bin/env bash
# rules-registry.sh — bstack's bridge to the `correct` skill (BRO-4147).
#
# bstack installs hooks. The `correct` skill (broomva/skills) replays them: it keeps a
# rule registry in the workspace's governance folder and runs each rule's proofs and
# controls through the hook entry the harness would run. bstack ships the rows for its
# own hooks (references/rules-pack.yaml) and does not carry a copy of the runner.
#
#   rules-registry.sh locate            print the path of the skill's correct.py
#   rules-registry.sh status            say whether anything is where a registry goes and
#                                       whether a copy of the skill was found. It runs
#                                       nothing, so it does not say that the registry is
#                                       well formed or that the skill can replay it.
#   rules-registry.sh init [--dry-run]  give the workspace its registry, with bstack's rows,
#                                       and replay it. A workspace that has a registry gets
#                                       no file written and is replayed. --dry-run lists what
#                                       a workspace without a registry would get; where one
#                                       is, it says so. Either way it starts no hook.
#   rules-registry.sh verify            a workspace that has a registry: replay it and say
#                                       which of bstack's rows it lacks.
#   rules-registry.sh check [OPTIONS]   replay the workspace's registry; OPTIONS go to the
#                                       skill's `check` (--json, --only ID, --registry FILE,
#                                       spelled out in full). The skill's `check` never writes.
#
# None of these changes the policy file. `status` and `locate` write nothing and start
# nothing. `verify`, `check` and `init` on a workspace that has a registry write no file of
# their own, and they replay: a replay runs the workspace's hooks, and a hook may write (the
# skill's starter hook leaves scripts/__pycache__, BRO-4185).
#
# Where the registry is, what counts as one, and whether it can be replayed are the skill's
# to say. This script asks one question of its own, "is anything at all where a registry
# goes?", to tell a workspace that has none (exit 4) from every other case, and hands every
# other case to the skill. `verify` is the skill's `init` on a workspace where something is
# there: the skill writes nothing when a registry exists and refuses anything else.
#
# A replay takes as long as the registry's rows take, and this script waits for it, as it
# waits for any command: there is no time limit here. Ctrl-C stops it and the skill with it.
#
# The workspace is $BROOMVA_WORKSPACE, else the current directory.
#
# The skill: $BSTACK_CORRECT names its correct.py outright (CI, tests, a checkout, a copy
# installed some other way). Otherwise it is looked for in ~/.claude/skills/correct and
# ~/.agents/skills/correct, and of two copies the one that has `init --pack` is used.
#
# Exit codes
#   0  done, and no row failed (gap rows and known misses are listed in the output);
#      `status`: something is where a registry goes and a copy of the skill was found
#   1  a row did not hold
#   2  the command could not run (a bad argument, a file that is not a registry, a skill
#      that cannot be read or lacks what it needs)
#   3  no copy of the correct skill was found; for `init` and `verify`, also a copy too
#      old to have `init --pack` (`check` and `status` accept an older copy)
#   4  nothing is where a registry goes
#   6  `init` or `verify`: the workspace already had a registry, no row of it failed, and
#      it lacks part of bstack's pack (the output lists what)
#   (5 is not used.)
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

usage() { sed -n '2,56p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

has_pack_option() { grep -q -- '--pack' "$1" 2>/dev/null; }

# locate -> the path of correct.py on stdout. 2 when a named copy is not a file, 3 when no
# copy is installed.
locate() {
    local candidate first=""
    if [ -n "${BSTACK_CORRECT:-}" ]; then
        # A named path that is not there is an error, never a reason to fall back to another copy.
        if [ -f "$BSTACK_CORRECT" ] && [ -r "$BSTACK_CORRECT" ]; then
            printf '%s\n' "$BSTACK_CORRECT"
            return 0
        fi
        echo "rules-registry: BSTACK_CORRECT names $BSTACK_CORRECT, which is not a file this user can read" >&2
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
        # A copy that cannot be read is not a copy: it would look like one without the option.
        [ -f "$candidate" ] && [ -r "$candidate" ] || continue
        if has_pack_option "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
        [ -n "$first" ] || first="$candidate"
    done
    [ -n "$first" ] || return 3
    printf '%s\n' "$first"
}

# Is anything at all where a registry goes, in either governance folder? A file, a
# directory, a link that points nowhere: all count. What it is, and which folder wins, is
# the skill's to judge; this only tells "nothing here" from everything else.
registry_present() {
    local dir path
    for dir in "$BROOMVA_REPO_DIR" "$BROOMVA_REPO_LEGACY_DIR"; do
        path="$WORKSPACE/$dir/rules.yaml"
        if [ -e "$path" ] || [ -L "$path" ]; then
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
        # 4 and 6 mean something in this script and nothing in the skill: a copy that ends with
        # one of them did not say "no registry" or "lacks part of the pack".
        4|6) echo "rules-registry: the skill ended with status $rc, which it does not use" >&2; rc=2 ;;
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
    # A dry run where something already is where the registry goes has nothing to list: init
    # writes no file there. It stops here, because the skill's own dry run on a registry
    # that exists is the real run, replay and all, and a dry run must start no hook.
    if [ "${#dry[@]}" -gt 0 ] && registry_present; then
        echo "something is already where the rule registry goes, so init would write no file here."
        echo "nothing was run. \`bstack rules verify\` replays it and compares it with bstack's rows."
        return 0
    fi
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
    # Only a workspace where something is: there the skill's init writes no file (it replays
    # a registry that exists and refuses anything else). Where nothing is, init would write.
    if ! registry_present; then
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

# status -> 0 something is where a registry goes and a copy of the skill was found; 4 nothing
# is there; 3 something is there and no copy of the skill. Two lines on stdout. Nothing is
# run, so nothing is claimed about the registry's content or about whether a replay can run.
cmd_status() {
    local correct rc=0
    if [ $# -gt 0 ]; then
        case "$1" in -h|--help) usage; return 0 ;; esac
        echo "rules-registry: status takes no argument, got: $1" >&2
        return 2
    fi
    if ! registry_present; then
        echo "this workspace has no rule registry (\`bstack bootstrap\` writes one)."
        return 4
    fi
    correct="$(locate)" || rc=$?
    if [ "$rc" -eq 3 ]; then
        echo "something is where the rule registry goes, and no copy of the correct skill, which replays it, was found."
        echo "install it: $INSTALL_HINT"
        return 3
    fi
    [ "$rc" -eq 0 ] || return "$rc"
    echo "something is where the rule registry goes, and a copy of the skill is at $correct."
    echo "nothing was read or run: whether it is a registry that holds is what \`bstack rules verify\` says."
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
    # A copy named with BSTACK_CORRECT that is not there is the first thing wrong.
    [ "$rc" -ne 2 ] || return 2
    # A registry named on the command line is the skill's to find; otherwise it is looked
    # for here first, so "no registry" is told apart from "could not run".
    if [ "$named" -eq 0 ] && ! registry_present; then
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
    case "$rc" in
        0|1) note_unseen_sources ;;
        # The skill's `check` ends with 0, 1 or 2. 3, 4 and 6 mean something in this script only.
        3|4|6) echo "rules-registry: the skill ended with status $rc, which its check does not use" >&2; rc=2 ;;
    esac
    return "$rc"
}

command="${1:-}"
[ $# -eq 0 ] || shift
case "$command" in
    locate)       locate ;;
    status)       cmd_status "$@" ;;
    init)         cmd_init "$@" ;;
    verify)       cmd_verify "$@" ;;
    check)        cmd_check "$@" ;;
    -h|--help|help) usage ;;
    *)            usage >&2; exit 2 ;;
esac
