#!/bin/bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
HOME_FIXTURE="$TMP/home"
mkdir -p "$HOME_FIXTURE/.config/broomva/existing" "$TMP/workspace/.control"
export BSTACK_DOCTOR_HOME="$HOME_FIXTURE" BROOMVA_WORKSPACE="$TMP/workspace"
DOCTOR="$REPO/scripts/doctor.sh"
run_doctor() { bash "$DOCTOR" --quiet >"$TMP/out" 2>&1; }
if run_doctor; then echo 'missing baseline passed' >&2; exit 1; fi
grep -q 'bstack doctor --baseline-roots' "$TMP/out"
bash "$DOCTOR" --baseline-roots >"$TMP/out"
grep -q '.config/broomva/existing' "$TMP/out"
run_doctor
grep -q 'grandfathered folder: ~/.config/broomva/existing' "$TMP/out"
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
echo 'machine folder ratchet: passed' 
