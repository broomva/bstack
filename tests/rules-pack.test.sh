#!/usr/bin/env bash
# tests/rules-pack.test.sh — BRO-4147. `bstack bootstrap` ends with a rule registry that
# replays the control gate it wired, and each row bstack ships (references/rules-pack.yaml)
# fails when the gate stops refusing what the row replays.
#
# The replay is done by the `correct` skill (broomva/skills). This test needs a copy that
# has `init --pack`: $BSTACK_TEST_CORRECT names its correct.py (CI pins one), otherwise the
# installed skill is used. There is no skip path: without the skill the test fails.
#
# Every case bootstraps an empty scratch repository under a scratch HOME, so nothing is
# written outside the test's own directory and the machine's installed plugin and skills
# do not decide the result. bootstrap wires hooks two ways (BRO-1929): by hand, or six of
# them through the bstack plugin when one is installed. Both are run here: an earlier
# version of this test ran only the way the author's machine took and passed while the
# other failed.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_ASSERTIONS=81
PASS=0; FAIL=0; FAILED=()
pass() { echo "  [ok] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); FAILED+=("$1"); }
# is <label> <command...>: one assertion, passed when the command succeeds.
is() { local label="$1"; shift; if "$@"; then pass "$label"; else fail "$label"; fi; }
has() { grep -qF -- "$2" "$1"; }
lacks() { ! grep -qF -- "$2" "$1"; }
# row <file> <status> <id>: the replay printed that status for that row.
row() { grep -qE "^ *$2 +[a-z]+ +$3( |\$)" "$1"; }

echo "── rules-pack (BRO-4147) ──────────────────────────────"

# Git reads no configuration from outside the test: a global hooksPath or template on the
# machine must not decide whether a scratch repository gets its first commit.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
# Nor does a bstack switch the caller happens to have set (BSTACK_NO_PLUGIN=1 turned the
# plugin case into the other one): every BSTACK_* and BROOMVA_* variable is dropped, bar
# the one that names the skill for this test. Each case sets what it needs.
for name in $(compgen -v | grep -E '^(BSTACK_|BROOMVA_)' || true); do
    [ "$name" = BSTACK_TEST_CORRECT ] || unset "$name"
done
REAL_SETTINGS="${HOME:-/nonexistent}/.claude/settings.json"
real_settings_sum() { if [ -f "$REAL_SETTINGS" ]; then cksum <"$REAL_SETTINGS"; else echo none; fi; }
REAL_SETTINGS_BEFORE="$(real_settings_sum)"

CORRECT="${BSTACK_TEST_CORRECT:-}"
if [ -z "$CORRECT" ]; then
    for candidate in "${HOME:-/nonexistent}/.claude/skills/correct/scripts/correct.py" \
                     "${HOME:-/nonexistent}/.agents/skills/correct/scripts/correct.py"; do
        if [ -f "$candidate" ] && grep -q -- '--pack' "$candidate"; then CORRECT="$candidate"; break; fi
    done
fi
if [ -z "$CORRECT" ] || [ ! -f "$CORRECT" ] || ! grep -q -- '--pack' "$CORRECT"; then
    echo "  [FAIL] no copy of the correct skill with \`init --pack\` (BSTACK_TEST_CORRECT='${BSTACK_TEST_CORRECT:-}')" >&2
    echo "         install it: npx skills add broomva/skills --skill correct -g" >&2
    exit 1
fi
echo "  using $CORRECT"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
OUT="$TMP/out"
PACK="$REPO/references/rules-pack.yaml"
SCRIPT="$REPO/scripts/rules-registry.sh"

# shellcheck source=scripts/lib/broomva-paths.sh
. "$REPO/scripts/lib/broomva-paths.sh"
policy_of() { broomva_repo_path policy "$1"; }
registry_of() { echo "$(dirname "$(policy_of "$1")")/rules.yaml"; }

new_ws() {
    local ws="$TMP/$1"
    mkdir -p "$ws"
    (
        cd "$ws" && git init -q
        git -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
        git checkout -q -b work
    ) >/dev/null 2>&1
    # A fixture without a first commit on a work branch is not the case under test.
    if [ "$(git -C "$ws" symbolic-ref --short HEAD 2>/dev/null)" != work ] || ! git -C "$ws" rev-parse --verify -q HEAD >/dev/null; then
        echo "  [FAIL] could not build the scratch repository $ws" >&2
        exit 1
    fi
    echo "$ws"
}
# new_home <name>: an empty HOME. The skill's runner imports PyYAML and looks for it in the
# user site, which moves with HOME; when that is where this machine keeps PyYAML, the new
# home's user site is a link to the real one. That directory of installed Python packages
# is the one thing of the real home a case can reach, and through the link it is the real
# one, not a copy. No settings, skills or plugins of the real home are visible.
new_home() {
    local home="$TMP/$1" real_site new_site
    mkdir -p "$home"
    if ! HOME="$home" python3 -I -c 'import site, sys; sys.path.append(site.getusersitepackages()); import yaml' 2>/dev/null; then
        real_site="$(python3 -c 'import site; print(site.getusersitepackages())')"
        new_site="$(HOME="$home" python3 -c 'import site; print(site.getusersitepackages())')"
        mkdir -p "$(dirname "$new_site")" && ln -s "$real_site" "$new_site"
    fi
    echo "$home"
}
# The home every case runs under unless it names another: no plugin, no skills.
HOME_PLAIN="$(new_home home)"
if ! HOME="$HOME_PLAIN" python3 -I -c 'import site, sys; sys.path.append(site.getusersitepackages()); import yaml' 2>/dev/null; then
    echo "  [FAIL] PyYAML is not importable under a scratch HOME, so the replay cannot run" >&2
    exit 1
fi
# boot <workspace> [VAR=value ...]: bootstrap it in full (hooks and the control loop; only
# the skill downloads are skipped); the log is <workspace>.log.
boot() {
    local ws="$1"; shift
    env HOME="$HOME_PLAIN" BSTACK_CORRECT="$CORRECT" BROOMVA_WORKSPACE="$ws" BSTACK_SKIP_SKILLS=1 "$@" \
        bash "$REPO/scripts/bootstrap.sh" >"$ws.log" 2>&1
}
# replay <workspace>: the skill's own `check` on it; output in $OUT, status in $RC. The
# test asks the skill directly; bstack has no command of its own for this.
replay() {
    RC=0
    env HOME="$HOME_PLAIN" python3 -I "$CORRECT" check --root "$1" >"$OUT" 2>&1 || RC=$?
}
# run_script <workspace> [VAR=value ...]: scripts/rules-registry.sh; output in $OUT, status in $RC.
run_script() {
    local ws="$1"; shift
    RC=0
    env HOME="$HOME_PLAIN" BSTACK_CORRECT="$CORRECT" BROOMVA_WORKSPACE="$ws" "$@" bash "$SCRIPT" >"$OUT" 2>&1 || RC=$?
}
tree_of() { (cd "$1" && find . -path ./.git -prune -o -print | sort); }
commit_all() {
    git -C "$1" add -A
    git -C "$1" -c user.email=t@t -c user.name=t commit -q -m "$2" >/dev/null 2>&1
}
wired() { grep -qF -- "$2" "$1/.claude/settings.json"; }
# gate_matchers <workspace>: the matchers of the blocks that run the control gate, sorted.
gate_matchers() {
    python3 - "$1/.claude/settings.json" <<'PY'
import json, sys
blocks = json.load(open(sys.argv[1])).get("hooks", {}).get("PreToolUse", [])
print(" ".join(sorted(b.get("matcher", "") for b in blocks
                      if any("control-gate-hook.sh" in h.get("command", "") for h in b.get("hooks", [])))))
PY
}
# The ids of the pack's rows at a level ("hook" or "gap"), read from the pack itself, so a
# row added to the pack is asserted here without an edit.
pack_ids() {
    python3 - "$PACK" "$1" <<'PY'
import sys, yaml
for rule in yaml.safe_load(open(sys.argv[1]))["rules"]:
    if rule["level"] == sys.argv[2]:
        print(rule["id"])
PY
}
HOOK_ROWS="$(pack_ids hook)"
GAP_ROWS="$(pack_ids gap)"

# ── 0. Each rule quotes the pattern the gate enforces, character for character ──
# A rule that says more than its pattern does is the defect two review rounds found in
# this pack. The rules now quote the patterns; this holds the quotes to the gate's source.
echo "0. the rules quote the gate's patterns"
# quotes_match <gate script> <pack> <expected number of gate patterns>: 0 when every
# pattern of the gate is quoted by a hook rule of the pack and every quote is a pattern of
# the gate; else 1 with the differences on stdout.
quotes_match() {
    python3 - "$1" "$2" "$3" <<'PY'
import re, sys, yaml
gate, pack, expected = open(sys.argv[1], encoding="utf-8").read(), yaml.safe_load(open(sys.argv[2], encoding="utf-8")), int(sys.argv[3])
patterns = re.findall(r'^\s+\(r"(.+)",\s+"G\d: [^"]+"\),?\s*$', gate, re.M)
quoted = [q for rule in pack["rules"] if rule["level"] == "hook" for q in re.findall(r"`([^`]+)`", rule["rule"])]
problems = []
if len(patterns) != expected:
    problems.append(f"the gate has {len(patterns)} pattern(s) this test can read, {expected} expected")
problems += [f"the gate enforces {p!r} and no rule quotes it" for p in patterns if p not in quoted]
problems += [f"a rule quotes {q!r}, which is not a pattern of the gate" for q in quoted if q not in patterns]
print("\n".join(problems))
sys.exit(1 if problems else 0)
PY
}
quotes_match "$REPO/scripts/control-gate-hook.sh" "$PACK" 9 >"$OUT" 2>&1; RC=$?
[ "$RC" -eq 0 ] || cat "$OUT"
is "all nine patterns of the gate are quoted by the rules, and nothing else is" test "$RC" -eq 0
# The check can fail: a gate whose pattern changed, and a pack that quotes a pattern the gate lacks.
python3 - "$REPO/scripts/control-gate-hook.sh" "$TMP/gate-changed.sh" "$PACK" "$TMP/pack-changed.yaml" <<'PY'
import sys
gate = open(sys.argv[1], encoding="utf-8").read()
assert gate.count(r'r"\.pem$"') == 1
open(sys.argv[2], "w", encoding="utf-8").write(gate.replace(r'r"\.pem$"', r'r"\.pem"'))
pack = open(sys.argv[3], encoding="utf-8").read()
assert pack.count("`credentials`, `(^|/)id_rsa") == 1
open(sys.argv[4], "w", encoding="utf-8").write(pack.replace("`credentials`, `(^|/)id_rsa", "`credentials\\.json$`, `(^|/)id_rsa"))
PY
quotes_match "$TMP/gate-changed.sh" "$PACK" 9 >"$OUT" 2>&1; RC=$?
is "a pattern changed in the gate and not in the pack fails this check" test "$RC" -eq 1 -a "$(grep -c 'no rule quotes it' "$OUT")" = 1
quotes_match "$REPO/scripts/control-gate-hook.sh" "$TMP/pack-changed.yaml" 9 >"$OUT" 2>&1; RC=$?
is "a rule that quotes a pattern the gate does not have fails this check" test "$RC" -eq 1 -a "$(grep -c 'is not a pattern of the gate' "$OUT")" = 1

# ── 1. A fresh bootstrap ends with a registry whose every row replays ────────
echo "1. fresh bootstrap, every hook wired by hand (no plugin)"
WS="$(new_ws fresh)"
is "bootstrap exits 0" boot "$WS"
unwired=""
for hook in control-gate-hook.sh role-x-intake-hook.sh conversation-bridge-hook.sh autonomous-posture-hook.sh \
            arc-continuation-hook.sh leverage-sensor.py l3-stability-pretool-hook.sh; do
    wired "$WS" "$hook" || unwired="$unwired $hook"
done
is "all seven of bstack's blocking-event hooks are in the settings file (missing:${unwired:- none})" test -z "$unwired"
is "the git pre-commit hook is installed" test -f "$WS/.githooks/pre-commit"
is "bootstrap says the registry replays" has "$WS.log" "rule registry: replayed, and no row failed."
is "bootstrap shows the skill's notice that edits on main and master are refused" has "$WS.log" "an edit on main or master in this repository is refused"
is "without the plugin, bootstrap adds no note about hooks the replay could not see" lacks "$WS.log" "BRO-3811"
is "the registry was written beside the policy" test -f "$(registry_of "$WS")"
replay "$WS"
is "the skill's own check of that workspace exits 0" test "$RC" -eq 0
is "five hook rows and two gap rows come from the pack" test "$(echo "$HOOK_ROWS" | wc -l | tr -d ' ')/$(echo "$GAP_ROWS" | wc -l | tr -d ' ')" = "5/2"
missing=""
for id in $HOOK_ROWS; do row "$OUT" PASS "$id" || missing="$missing $id"; done
is "every hook row of the pack is replayed and passes (missing:${missing:- none})" test -z "$missing"
missing=""
for id in $GAP_ROWS; do row "$OUT" GAP "$id" || missing="$missing $id"; done
is "each gap row is reported as a gap, not as a pass (missing:${missing:- none})" test -z "$missing"
is "the starter row replays too" row "$OUT" PASS branch-first
is "every blocking hook and git hook is accounted for" row "$OUT" PASS registry-completeness
is "nothing failed" lacks "$OUT" "FAIL"
is "the control gate is registered for Bash, Edit, MultiEdit and Write" test "$(gate_matchers "$WS")" = "Bash Edit MultiEdit Write"
is "the policy carries gates.hard" grep -q '^  hard: \[\]$' "$(policy_of "$WS")"
is "known misses are counted in the replay, not hidden" grep -qE 'no-catastrophic-delete +5 known miss' "$OUT"
is "the continuation-line push is replayed as a known miss" grep -qE 'no-force-push +2 known miss' "$OUT"

# Everything below removes something and expects a row to fail. That means nothing unless
# the starting point is green.
if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo "rules-pack: the fresh bootstrap is not green ($FAIL failed), so the cases that break it were not run" >&2
    printf '  failed: %s\n' "${FAILED[@]}" >&2
    exit 1
fi

# ── 2. A second bootstrap writes nothing new ─────────────────────────────────
# The tree committed here already holds scripts/__pycache__, which the skill's starter hook
# leaves when it is replayed (BRO-4185); "unchanged" is measured against that tree.
echo "2. second bootstrap"
commit_all "$WS" bootstrapped
is "bootstrap exits 0 again" boot "$WS"
is "the tree is unchanged" test -z "$(git -C "$WS" status --porcelain)"
is "it says the registry is kept" has "$WS.log" "already has a registry"
is "and still replays it" has "$WS.log" "rule registry: replayed, and no row failed."
is "the registry holds the whole pack, and bootstrap shows the skill saying so" has "$WS.log" "pack   bstack: all 7 row(s) are in this registry"

# ── 3. Each row fails when its hook stops refusing ───────────────────────────
echo "3. a row fails when its hook stops refusing"
GATE="$WS/scripts/control-gate-hook.sh"
cp "$GATE" "$TMP/gate.orig"
# drop <reason>: remove the one pattern line of the gate that carries that reason.
drop() {
    python3 - "$GATE" "$1" <<'PY'
import sys
path, reason = sys.argv[1], sys.argv[2]
lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
kept = [line for line in lines if reason not in line]
assert len(lines) - len(kept) == 1, f"{reason!r} is on {len(lines) - len(kept)} line(s) of the gate"
open(path, "w", encoding="utf-8").write("".join(kept))
PY
}
# One line per pattern of the gate: its reason, then the row that replays it.
while IFS='|' read -r reason id; do
    cp "$TMP/gate.orig" "$GATE"
    if drop "$reason"; then
        replay "$WS"
        # Exactly one row fails, of every row in the registry (the starter's, the pack's
        # and the completeness check), and it is the one that replays this pattern.
        failing="$(grep -E '^ *FAIL +[a-z]+ +' "$OUT" | awk '{print $3}' | tr '\n' ' ')"
        if [ "$RC" -eq 1 ] && [ "$failing" = "$id " ]; then
            pass "without \"$reason\" only $id fails"
        else
            fail "without \"$reason\": exit $RC, failing rows: ${failing:-none} (expected only $id)"
        fi
    else
        fail "the gate has no single line for \"$reason\""
    fi
done <<'EOF'
G1: force-push|no-force-push
G3: catastrophic recursive delete|no-catastrophic-delete
G3: hard reset to remote|no-hard-reset-to-remote
G2: bypassing pre-commit hooks|no-hook-bypass
G2: writing a .env secret file|no-secret-file-write
G2: writing a credentials file|no-secret-file-write
G2: writing a private SSH key|no-secret-file-write
G2: writing a .pem key|no-secret-file-write
G2: writing a .key file|no-secret-file-write
EOF
printf '#!/usr/bin/env bash\nexit 0\n' >"$GATE"
replay "$WS"
failed=""
for id in $HOOK_ROWS; do row "$OUT" FAIL "$id" || failed="$failed $id"; done
is "a gate that allows everything fails every hook row (not failing:${failed:- none})" test "$RC" -eq 1 -a -z "$failed"
# bootstrap reports what the skill returned: with that gate the phase says a row failed.
is "bootstrap still exits 0 with a gate that allows everything" boot "$WS"
is "and reports the skill's exit status, 1" has "$WS.log" "rule registry: exit 1"
cp "$TMP/gate.orig" "$GATE"

cp "$WS/.claude/settings.json" "$TMP/settings.orig"
python3 - "$WS/.claude/settings.json" <<'PY'
import json, sys
path = sys.argv[1]
doc = json.load(open(path))
blocks = doc["hooks"]["PreToolUse"]
kept = [b for b in blocks if not (b.get("matcher") == "MultiEdit" and "control-gate-hook.sh" in json.dumps(b))]
assert len(blocks) - len(kept) == 1, "expected one MultiEdit control-gate block"
doc["hooks"]["PreToolUse"] = kept
json.dump(doc, open(path, "w"), indent=2)
PY
replay "$WS"
is "a gate that is not registered for MultiEdit fails the secrets row" test "$RC" -eq 1 -a "$(row "$OUT" FAIL no-secret-file-write; echo $?)" = 0
cp "$TMP/settings.orig" "$WS/.claude/settings.json"
replay "$WS"
is "restored, the replay exits 0 again" test "$RC" -eq 0

# ── 4. The gate and the registry read the same policy file ───────────────────
# With one in each governance folder, a pattern in the one both prefer is refused by the
# gate AND reported by the registry as a gate with no row; a pattern only in the other one
# is neither.
echo "4. one policy file for the gate and the registry"
BOTH="$TMP/both"; cp -R "$WS" "$BOTH"
mkdir -p "$BOTH/$BROOMVA_REPO_DIR"
printf 'version: 1\ngates:\n  hard:\n    - id: G-NEW\n      pattern: marker-in-the-new-folder\n' >"$BOTH/$BROOMVA_REPO_DIR/policy.yaml"
printf 'version: 1\ngates:\n  hard:\n    - id: G-OLD\n      pattern: marker-in-the-old-folder\n' >"$BOTH/$BROOMVA_REPO_LEGACY_DIR/policy.yaml"
# gate_says <workspace> <command text>: the gate's exit status for that Bash command.
gate_says() {
    printf '{"tool_name": "Bash", "tool_input": {"command": "%s"}}' "$2" \
        | CLAUDE_PROJECT_DIR="$1" bash "$1/scripts/control-gate-hook.sh" >/dev/null 2>&1
    echo $?
}
is "the gate enforces the pattern in the policy file the registry reads" test "$(gate_says "$BOTH" "echo marker-in-the-new-folder")" = 2
is "and not the pattern in the file the registry does not read" test "$(gate_says "$BOTH" "echo marker-in-the-old-folder")" = 0
replay "$BOTH"
is "the registry reports that same gate as one with no row" test "$RC" -eq 1 -a "$(grep -c 'policy gate G-NEW has a pattern but no row' "$OUT")" = 1
is "and says nothing of the gate in the file neither reads" lacks "$OUT" "G-OLD"

# ── 5. A workspace bootstrapped before 0.53.0 ────────────────────────────────
# Its policy file has no gates.hard list. bstack does not edit the policy file: bootstrap
# says which line to add, and the replay passes once the operator has added it.
echo "5. a workspace from before 0.53.0"
OLD="$(new_ws old)"
is "BSTACK_SKIP_RULES=1 bootstraps without a registry" boot "$OLD" BSTACK_SKIP_RULES=1
is "and writes none" test ! -e "$(registry_of "$OLD")"
# Make it what 0.52 left: no gates.hard in the policy, no MultiEdit block for the gate.
python3 - "$(policy_of "$OLD")" "$OLD/.claude/settings.json" <<'PY'
import json, sys, yaml
policy, settings = sys.argv[1], sys.argv[2]
text = open(policy, encoding="utf-8").read()
start = text.index("gates:\n") + len("gates:\n")
end = text.index("  hard: []\n") + len("  hard: []\n")
text = text[:start] + text[end:].lstrip("\n")
assert "hard" not in yaml.safe_load(text)["gates"]
open(policy, "w", encoding="utf-8").write(text)
doc = json.load(open(settings))
doc["hooks"]["PreToolUse"] = [b for b in doc["hooks"]["PreToolUse"] if b.get("matcher") != "MultiEdit"]
json.dump(doc, open(settings, "w"), indent=2)
PY
cp "$(policy_of "$OLD")" "$TMP/old-policy.before"
is "bootstrap exits 0" boot "$OLD"
is "it leaves the policy file byte for byte" cmp -s "$(policy_of "$OLD")" "$TMP/old-policy.before"
is "the replay says it cannot read the hard gates" has "$OLD.log" "cannot read the hard gates"
is "and bootstrap says which line to add" has "$OLD.log" "Add the line \`hard: []\` under \`gates:\` in the policy file"
is "it registered the gate for MultiEdit" test "$(gate_matchers "$OLD")" = "Bash Edit MultiEdit Write"
# The operator adds the line.
python3 - "$(policy_of "$OLD")" <<'PY'
import sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
assert text.count("gates:\n") == 1
open(path, "w", encoding="utf-8").write(text.replace("gates:\n", "gates:\n  hard: []\n"))
PY
is "with the line added by hand, bootstrap exits 0" boot "$OLD"
is "and the registry replays" has "$OLD.log" "rule registry: replayed, and no row failed."

# ── 6. A registry that was there before, or that an older pack wrote ─────────
echo "6. a registry that differs from the pack"
STALE="$TMP/stale"; cp -R "$WS" "$STALE"
python3 - "$(registry_of "$STALE")" <<'PY'
import sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
assert text.count("Never recursively delete") == 1, text.count("Never recursively delete")
open(path, "w", encoding="utf-8").write(text.replace("Never recursively delete", "Do not recursively delete"))
PY
cp "$(registry_of "$STALE")" "$TMP/stale-registry.before"
run_script "$STALE"
is "the script passes on the skill's exit 3: the replay passed and the registry lacks part of the pack" test "$RC" -eq 3
is "the skill names the row" has "$OUT" "row(s) that differ from the pack: no-catastrophic-delete"
is "and the registry is left byte for byte" cmp -s "$(registry_of "$STALE")" "$TMP/stale-registry.before"
printf '# a line the workspace added\n' >>"$STALE/scripts/control-gate-hook.sh"
is "bootstrap says the registry lacks part of bstack's pack" test "$(boot "$STALE"; echo $?)$(has "$STALE.log" "lacks part of bstack's pack"; echo $?)" = 00
is "bootstrap keeps a gate script the workspace changed, and says it differs" test "$(has "$STALE.log" "[keep] scripts/control-gate-hook.sh (existing — preserved; it differs from this bstack version's copy)"; echo $?)$(tail -1 "$STALE/scripts/control-gate-hook.sh")" = "0# a line the workspace added"

# ── 7. With the bstack plugin installed, the plugin's hooks are not wired by hand ──
echo "7. fresh bootstrap, plugin preferred"
HOME_PLUGIN="$(new_home home-plugin)"
mkdir -p "$HOME_PLUGIN/.agents/skills/bstack/.claude-plugin"
printf '{"name":"bstack"}\n' >"$HOME_PLUGIN/.agents/skills/bstack/.claude-plugin/plugin.json"
PLUG="$(new_ws plugin)"
is "bootstrap exits 0" boot "$PLUG" HOME="$HOME_PLUGIN"
is "it took the plugin path" has "$PLUG.log" "preferring bstack@skills-dir"
is "the plugin's hooks are not in the settings file" test "$(wired "$PLUG" arc-continuation-hook.sh; echo $?)$(wired "$PLUG" l3-stability-pretool-hook.sh; echo $?)" = 11
is "the control gate still is" wired "$PLUG" control-gate-hook.sh
is "bootstrap says the registry replays" has "$PLUG.log" "rule registry: replayed, and no row failed."
is "and says what the replay could not see on this path" has "$PLUG.log" "the replay did not see them (BRO-3811)"
RC=0
env HOME="$HOME_PLUGIN" python3 -I "$CORRECT" check --root "$PLUG" >"$OUT" 2>&1 || RC=$?
is "the skill's own check exits 0 on this path too" test "$RC" -eq 0

# ── 8. Without a usable copy of the skill nothing is written and nothing aborts ──
echo "8. without the correct skill"
BARE="$(new_ws bare)"
bare_before="$(tree_of "$BARE")"
run_script "$BARE" BSTACK_CORRECT=""
is "the script exits 127 and says how to install the skill" test "$RC" -eq 127 -a "$(grep -c 'npx skills add broomva/skills --skill correct -g' "$OUT")" = 1
is "and writes nothing" test "$(tree_of "$BARE")" = "$bare_before"
RC=0
env -u HOME -u BSTACK_CORRECT BROOMVA_WORKSPACE="$BARE" bash "$SCRIPT" >"$OUT" 2>&1 || RC=$?
is "with no HOME at all the answer is the same, not a shell error" test "$RC" -eq 127 -a "$(grep -c 'unbound variable' "$OUT")" = 0
run_script "$BARE" BSTACK_CORRECT="$TMP/nowhere/correct.py"
is "a named copy that is not there is not replaced by another: exit 127, and it is named" test "$RC" -eq 127 -a "$(grep -cF "BSTACK_CORRECT names $TMP/nowhere/correct.py" "$OUT")" = 1
printf '#!/usr/bin/env python3\nprint("an older copy")\n' >"$TMP/old-correct.py"
run_script "$BARE" BSTACK_CORRECT="$TMP/old-correct.py"
is "a copy without the pack option is not run" test "$RC" -eq 127 -a "$(grep -c 'an older copy' "$OUT")" = 0
is "none of that wrote to the workspace" test "$(tree_of "$BARE")" = "$bare_before"
is "bootstrap still exits 0 without the skill" boot "$BARE" BSTACK_CORRECT=""
is "it says how to install the skill" has "$BARE.log" "npx skills add broomva/skills --skill correct -g"
is "it writes no registry" test ! -e "$(registry_of "$BARE")"
# Two installed copies: the one that has the pack option is the one run, wherever it is.
HOME_TWO="$(new_home home-two)"
mkdir -p "$HOME_TWO/.claude/skills/correct/scripts" "$HOME_TWO/.agents/skills/correct/scripts"
printf '#!/usr/bin/env python3\nprint("the older copy ran")\n' >"$HOME_TWO/.claude/skills/correct/scripts/correct.py"
printf '#!/usr/bin/env python3\n# has the --pack option\nprint("the copy with the option ran")\n' >"$HOME_TWO/.agents/skills/correct/scripts/correct.py"
run_script "$BARE" HOME="$HOME_TWO" BSTACK_CORRECT=""
is "of two installed copies the one with the pack option is run" test "$RC" -eq 0 -a "$(grep -c 'the copy with the option ran' "$OUT")" = 1 -a "$(grep -c 'the older copy ran' "$OUT")" = 0
RC=0
env HOME="$HOME_PLAIN" BSTACK_CORRECT="$CORRECT" BROOMVA_WORKSPACE="$BARE" bash "$SCRIPT" --dry-run >"$OUT" 2>&1 || RC=$?
is "the script takes no argument: exit 2, and nothing is written" test "$RC" -eq 2 -a ! -e "$(registry_of "$BARE")"

# ── 9. A failure in this phase does not stop bootstrap, and its output is live ──
echo "9. the phase cannot fail bootstrap"
# A copy of the skill that crashes: the phase reports the exit status and bootstrap goes on.
printf '#!/usr/bin/env python3\n# has the --pack option, and crashes\nraise SystemExit(77)\n' >"$TMP/crashing-correct.py"
CRASH="$(new_ws crash)"
is "bootstrap exits 0 with a skill that crashes" boot "$CRASH" BSTACK_CORRECT="$TMP/crashing-correct.py"
is "the phase reports the skill's exit status" has "$CRASH.log" "rule registry: exit 77"
# The skill says that the starter hook is installed BEFORE it replays, and bootstrap shows
# the phase's output as it comes. A bootstrap stopped during the replay has therefore shown
# it. (An earlier version captured the output and printed it when the phase returned: stop
# it in between and the hook was active with nothing said.) The stand-in prints the line,
# records its pid and waits; the line must be in bootstrap's output while it still waits.
printf '#!/usr/bin/env python3\n# has the --pack option; says the hook is installed, then waits\nimport os, time\nprint("the starter hook is installed (stand-in)", flush=True)\nopen(os.environ["PIDFILE"], "w").write(str(os.getpid()))\ntime.sleep(300)\n' >"$TMP/blocking-correct.py"
LIVE="$(new_ws live)"; rm -f "$TMP/blocking.pid"
( exec env HOME="$HOME_PLAIN" PIDFILE="$TMP/blocking.pid" BSTACK_CORRECT="$TMP/blocking-correct.py" BROOMVA_WORKSPACE="$LIVE" \
    BSTACK_SKIP_SKILLS=1 bash "$REPO/scripts/bootstrap.sh" >"$LIVE.log" 2>&1 ) &
boot_pid=$!
for _ in $(seq 1 240); do
    [ -s "$TMP/blocking.pid" ] && break
    kill -0 "$boot_pid" 2>/dev/null || break   # bootstrap ended without starting the stand-in
    python3 -c 'import time; time.sleep(0.5)'
done
blocked_pid="$(cat "$TMP/blocking.pid" 2>/dev/null || echo 0)"
case "$blocked_pid" in ''|*[!0-9]*) blocked_pid=0 ;; esac
is "bootstrap shows what the skill says while the replay is still running" test "$blocked_pid" -gt 0 -a "$(grep -c 'the starter hook is installed (stand-in)' "$LIVE.log")" = 1
# pid 0 is "every process in this group": never signal it.
if [ "$blocked_pid" -gt 0 ]; then kill -KILL "$blocked_pid" 2>/dev/null || true; fi
wait "$boot_pid"; RC=$?
is "and when the skill is stopped, bootstrap says so and still exits 0" test "$RC" -eq 0 -a "$(grep -c 'rule registry: exit' "$LIVE.log")" = 1

is "the machine's own settings file was not touched" test "$(real_settings_sum)" = "$REAL_SETTINGS_BEFORE"

echo ""
echo "rules-pack: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
    printf '  failed: %s\n' "${FAILED[@]}"
    exit 1
fi
if [ "$PASS" -ne "$EXPECTED_ASSERTIONS" ]; then
    echo "rules-pack: $PASS assertions ran, $EXPECTED_ASSERTIONS expected (a case stopped asserting, or one was added without the count)" >&2
    exit 1
fi
