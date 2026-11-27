#!/usr/bin/env bash
# rules-registry.sh — find the `correct` skill and have it give the workspace its rule
# registry, with bstack's rows (BRO-4147). `bstack bootstrap` runs this in phase 3.7.
#
# bstack installs hooks. The `correct` skill (broomva/skills) keeps a rule registry in the
# workspace's governance folder and replays each rule's proofs and controls through the
# hook entry the harness would run. bstack ships the rows for its own hooks
# (references/rules-pack.yaml) and does not carry a copy of the runner. This script does
# two things and judges nothing: it finds the skill, and it runs
#
#     correct.py init --root <workspace> --pack references/rules-pack.yaml
#
# Everything else is the skill's: where the registry is, what counts as one, what `init`
# writes (nothing, when a registry exists), and the replay. To replay or inspect a registry
# later, call the skill's `correct.py check` or `correct.py init` directly.
#
# The workspace is $BROOMVA_WORKSPACE, else the current directory. It takes no argument.
#
# The skill: $BSTACK_CORRECT names its correct.py outright (CI, tests, a checkout, a copy
# installed some other way). Otherwise it is looked for in ~/.claude/skills/correct and
# ~/.agents/skills/correct. A copy counts when its text holds `--pack`: that is a search of
# the file, not a run of it, and it is how a copy from before the option is told apart. Of
# two copies the first that counts is used.
#
# Exit status. When the skill ran, the skill's own, unchanged: 0 the replay passed; 1 a row
# did not hold; 2 it could not run; 3 the workspace already had a registry, the replay
# passed, and the registry lacks part of bstack's pack. When it did not run: 2 this script
# was given an argument; 127 no copy of the skill with `--pack` was found (it says how to
# install one), or python3 is not on PATH (it says so).
set -uo pipefail
# With CDPATH set, `cd` prints the directory it chose, and the two lines below would take
# that for part of the path.
unset CDPATH

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BSTACK_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE="${BROOMVA_WORKSPACE:-$PWD}"
PACK="$BSTACK_REPO/references/rules-pack.yaml"
INSTALL_HINT="npx skills add broomva/skills --skill correct -g"

if [ $# -gt 0 ]; then
    echo "rules-registry: this script takes no argument, got: $1" >&2
    exit 2
fi

has_pack_option() { grep -q -- '--pack' "$1" 2>/dev/null; }

# The path of a correct.py that has `init --pack`, on stdout; 1 when there is none.
find_skill() {
    local candidate
    if [ -n "${BSTACK_CORRECT:-}" ]; then
        # A named copy is the only one considered: never a reason to fall back to another.
        candidate="$BSTACK_CORRECT"
        [ -f "$candidate" ] && [ -r "$candidate" ] && has_pack_option "$candidate" || return 1
        printf '%s\n' "$candidate"
        return 0
    fi
    # No HOME (a bare service environment) means no installed skill to find.
    [ -n "${HOME:-}" ] || return 1
    # Two harnesses keep their own copy of a skill and one can lag: the first copy that has
    # the option is the one used.
    for candidate in "$HOME/.claude/skills/correct/scripts/correct.py" \
                     "$HOME/.agents/skills/correct/scripts/correct.py"; do
        if [ -f "$candidate" ] && [ -r "$candidate" ] && has_pack_option "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

if ! correct="$(find_skill)"; then
    if [ -n "${BSTACK_CORRECT:-}" ]; then
        echo "BSTACK_CORRECT names $BSTACK_CORRECT, which is not a readable copy of the correct skill with \`init --pack\`."
    else
        echo "no copy of the correct skill with \`init --pack\` was found, so no rule registry was written."
    fi
    echo "install or update it, then run bootstrap again: $INSTALL_HINT"
    exit 127
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is not on PATH, so the correct skill cannot run and no rule registry was written."
    exit 127
fi

# -u: the skill's output is shown as it is written, whichever copy it is.
exec python3 -I -u "$correct" init --root "$WORKSPACE" --pack "$PACK"
