#!/bin/bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
HOME_FIXTURE="$TMP/home"
mkdir -p "$HOME_FIXTURE" "$TMP/workspace/.control"
export BSTACK_DOCTOR_HOME="$HOME_FIXTURE" BROOMVA_WORKSPACE="$TMP/workspace"
DOCTOR="$REPO/scripts/doctor.sh"
run_doctor() { bash "$DOCTOR" --quiet >"$TMP/out" 2>&1; }
run_doctor_verbose() { bash "$DOCTOR" "$@" >"$TMP/out" 2>&1; }
# Without a baseline the ratchet is off: one [info] line, never a gap, so the gap
# total and --strict are what they were before this section existed.
assert_not_enabled() {
    grep -Fq '[info] machine folder ratchet is not enabled on this machine' "$TMP/out"
    if grep -Fq '[gap] machine folder' "$TMP/out"; then echo "missing baseline counted as a gap ($1)" >&2; exit 1; fi
    if grep -Fq '[failure]' "$TMP/out"; then echo "missing baseline reported a failure ($1)" >&2; exit 1; fi
    listed="$(grep -c '^  \[gap\]' "$TMP/out" || true)"
    total="$(sed -n 's/.* \([0-9][0-9]*\) gap(s).*/\1/p' "$TMP/out")"
    [ "${total:-0}" = "$listed" ] || { echo "gap total $total differs from $listed listed gaps ($1)" >&2; exit 1; }
}
run_doctor_verbose
assert_not_enabled 'empty home'
run_doctor
if grep -q 'machine folder' "$TMP/out"; then echo 'quiet mode mentioned a ratchet that is off' >&2; exit 1; fi
mkdir -p "$TMP/no-python"
printf '#!/bin/sh\nexit 127\n' > "$TMP/no-python/python3"
chmod +x "$TMP/no-python/python3"
PATH="$TMP/no-python:$PATH" run_doctor_verbose
assert_not_enabled 'no python'
mkdir -p "$HOME_FIXTURE/.config/broomva/existing"
run_doctor_verbose
assert_not_enabled 'folders present'
run_doctor_verbose --strict || true
assert_not_enabled 'strict'
bash "$DOCTOR" --baseline-roots >"$TMP/out"
grep -q '.config/broomva/existing' "$TMP/out"
run_doctor_verbose
grep -q 'grandfathered folder: ~/.config/broomva/existing' "$TMP/out"
# A passing ratchet prints nothing under --quiet: a real machine has dozens of
# grandfathered folders, and they are information, not findings.
run_doctor
if grep -q 'grandfathered\|machine folder' "$TMP/out"; then echo 'quiet mode listed grandfathered folders' >&2; exit 1; fi
if PATH="$TMP/no-python:$PATH" run_doctor; then echo 'enabled ratchet without python passed' >&2; exit 1; fi
mkdir -p "$HOME_FIXTURE/.local/share/broomva/new-folder"
if run_doctor; then echo 'new folder passed' >&2; exit 1; fi
grep -q 'new-folder' "$TMP/out"
if bash "$DOCTOR" --baseline-roots >"$TMP/out" 2>&1; then echo 'baseline overwrite passed' >&2; exit 1; fi
if run_doctor; then echo 'baseline was widened' >&2; exit 1; fi
# The negative control removes the comparison in an isolated copy.
mkdir -p "$TMP/copy/scripts"
cp "$REPO/scripts/doctor-machine-folders.py" "$REPO/scripts/broomva_paths.py" "$TMP/copy/scripts/"
python3 - "$TMP/copy/scripts/doctor-machine-folders.py" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
assert 'folders - saved - known' in s
open(p, 'w').write(s.replace('folders - saved - known', 'folders - folders - known'))
PY
python3 -I "$TMP/copy/scripts/doctor-machine-folders.py" >"$TMP/out"
# A registry legacy entry is accepted without being added to the baseline.
cp "$REPO/scripts/doctor-machine-folders.py" "$TMP/copy/scripts/doctor-machine-folders.py"
python3 - "$TMP/copy/scripts/broomva_paths.py" <<'PY_REGISTRY'
import sys
p = sys.argv[1]
s = open(p).read()
assert "'~/maestro'" in s
open(p, 'w').write(s.replace("'~/maestro'", "'~/.local/share/broomva/new-folder'", 1))
PY_REGISTRY
python3 -I "$TMP/copy/scripts/doctor-machine-folders.py" >"$TMP/out"
rm -r "$HOME_FIXTURE/.config/broomva/existing"
if run_doctor; then echo 'new folder passed after removal' >&2; exit 1; fi
grep -q 'baselined folder no longer exists' "$TMP/out"
bash "$DOCTOR" --baseline-roots --force >"$TMP/out"
run_doctor
# All five roots participate, including a root that was missing at baseline.
for root in .config/broomva .local/share/broomva .local/state/broomva .cache/broomva .control; do
    mkdir -p "$HOME_FIXTURE/$root/probe"
    if run_doctor; then echo "new folder under $root passed" >&2; exit 1; fi
    grep -Fq "~/$root/probe" "$TMP/out"
    rmdir "$HOME_FIXTURE/$root/probe"
done
run_doctor
BASELINE="$HOME_FIXTURE/.broomva/state/doctor-machine-folders.json"
printf '{bad json' > "$BASELINE"
if run_doctor; then echo 'malformed baseline passed' >&2; exit 1; fi
grep -q 'machine folder check' "$TMP/out"
rm "$BASELINE"
ln -s "$TMP/missing-baseline" "$BASELINE"
if run_doctor; then echo 'dangling baseline passed' >&2; exit 1; fi
grep -q 'machine folder check' "$TMP/out"
# --json (BRO-4083): one JSON object on stdout, nothing on stderr, and the exit
# code of the human run. A baseline it could not read is an error with null
# lists, never an empty list a consumer would read as "no new folder".
FOLDERS="$REPO/scripts/doctor-machine-folders.py"
json_run() {  # json_run EXPECTED_EXIT PYTHON_ASSERTION
    set +e
    python3 -I "$FOLDERS" --json >"$TMP/json" 2>"$TMP/json.err"
    json_rc=$?
    python3 -I "$FOLDERS" >/dev/null 2>&1
    human_rc=$?
    set -e
    [ "$json_rc" = "$1" ] || { echo "--json exited $json_rc, wanted $1 ($2)" >&2; exit 1; }
    [ "$json_rc" = "$human_rc" ] || { echo "--json exited $json_rc, the human run $human_rc ($2)" >&2; exit 1; }
    [ ! -s "$TMP/json.err" ] || { echo "--json wrote to stderr ($2)" >&2; cat "$TMP/json.err" >&2; exit 1; }
    [ "$(wc -l <"$TMP/json" | tr -d ' ')" = "1" ] || { echo "--json did not print exactly one line ($2)" >&2; exit 1; }
    python3 - "$TMP/json" "$2" "$BASELINE" <<'PY_JSON'
import json, sys
d = json.load(open(sys.argv[1]))
baseline = sys.argv[3]
assert sorted(d) == ['baseline_recorded_at', 'error', 'grandfathered', 'schema', 'unregistered'], sorted(d)
assert d['schema'] == 1, d
assert eval(sys.argv[2]), (sys.argv[2], d)
PY_JSON
}
unreadable="d['error'] and d['unregistered'] is None and d['grandfathered'] is None and d['baseline_recorded_at'] is None"
json_run 1 "$unreadable and 'machine folder check' in d['error']"        # dangling symlink
rm "$BASELINE"
printf '{bad json' > "$BASELINE"
json_run 1 "$unreadable and 'machine folder check' in d['error']"        # malformed
printf '{"recorded_at": "x", "folders": "not-a-list"}' > "$BASELINE"
json_run 1 "$unreadable and 'invalid baseline format' in d['error']"     # wrong shape
rm "$BASELINE"
json_run 42 "$unreadable and 'no baseline recorded' in d['error']"       # not opted in
# "grandfathered" counts the baselined folders still present, not the baseline's size.
mkdir -p "$HOME_FIXTURE/.config/broomva/json-gone"
bash "$DOCTOR" --baseline-roots >"$TMP/out"
rmdir "$HOME_FIXTURE/.config/broomva/json-gone"
json_run 0 "d['error'] is None and d['unregistered'] == [] and d['grandfathered'] == 1 and d['baseline_recorded_at'] == json.load(open(baseline))['recorded_at']"
mkdir -p "$HOME_FIXTURE/.cache/broomva/json-probe" "$HOME_FIXTURE/.control/json-probe"
json_run 1 "d['error'] is None and d['grandfathered'] == 1 and d['unregistered'] == [{'path': '~/.cache/broomva/json-probe', 'suggested': '~/.broomva/cache/json-probe'}, {'path': '~/.control/json-probe', 'suggested': '~/.broomva/state/json-probe'}]"
# The human output for the same state is what it was before --json existed.
python3 -I "$FOLDERS" >"$TMP/out" 2>&1 || true
grep -Fxq '[failure] unregistered stack folder: ~/.cache/broomva/json-probe; place it under ~/.broomva/cache/json-probe' "$TMP/out"
grep -Fxq '[info] grandfathered folder: ~/.local/share/broomva/new-folder' "$TMP/out"
if grep -q '"schema"' "$TMP/out"; then echo 'the human run printed JSON' >&2; exit 1; fi
# Negative control: with the comparison removed in a copy, --json reports nothing new.
cp "$REPO/scripts/broomva_paths.py" "$TMP/copy/scripts/broomva_paths.py"
python3 - "$REPO/scripts/doctor-machine-folders.py" "$TMP/copy/scripts/doctor-machine-folders.py" <<'PY_MUTANT'
import sys
s = open(sys.argv[1]).read()
assert s.count('folders - saved - known') == 1
open(sys.argv[2], 'w').write(s.replace('folders - saved - known', 'folders - folders - known'))
PY_MUTANT
python3 -I "$TMP/copy/scripts/doctor-machine-folders.py" --json >"$TMP/json"
grep -Fq '"unregistered": []' "$TMP/json"
rmdir "$HOME_FIXTURE/.cache/broomva/json-probe" "$HOME_FIXTURE/.control/json-probe"
echo 'machine folder ratchet: passed' 
