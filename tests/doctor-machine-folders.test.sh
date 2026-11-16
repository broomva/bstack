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
run_doctor
grep -q 'grandfathered folder: ~/.config/broomva/existing' "$TMP/out"
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
echo 'machine folder ratchet: passed' 
