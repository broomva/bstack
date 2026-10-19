#!/usr/bin/env bash
# tests/upgrade-trap-cleanup.test.sh — the vendored upgrade's EXIT trap must
# clean its temp dir without tripping `set -u` (BRO-3200).
#
# bstack_upgrade_vendored made `tmp` a function local and registered
# `trap 'rm -rf "$tmp"' EXIT`. The trap runs when the script exits, after the
# function has returned and `tmp` is gone, so every upgrade, dry run or failed
# download ended with "tmp: unbound variable" and left the temp dir behind.
#
# Offline: a fake vendored install whose update check reports an upgrade, and a
# tarball URL that cannot be fetched, so the function sets the trap and fails.
# Run from the repo root:
#   bash tests/upgrade-trap-cleanup.test.sh

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FAIL=0
ok()  { echo "  [ok] $1"; PASS=$((PASS + 1)); }
bad() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); }

H="$(mktemp -d)"
trap 'rm -rf "$H"' EXIT
INST="$H/install"
mkdir -p "$INST/bin" "$H/tmp" "$H/home"
cp "$BSTACK_REPO/bin/bstack" "$INST/bin/bstack"
echo "0.1.0" > "$INST/VERSION"
printf '#!/bin/sh\necho "UPGRADE_AVAILABLE 0.1.0 0.2.0"\n' > "$INST/bin/bstack-update-check"
chmod +x "$INST/bin/"*

# macOS `mktemp -d` ignores TMPDIR, so a TMPDIR check would pass vacuously.
# A shim on PATH routes bare `mktemp -d` into a directory this test inspects.
mkdir -p "$H/shim"
printf '#!/bin/sh\nif [ "$#" -eq 1 ] && [ "$1" = "-d" ]; then exec /usr/bin/mktemp -d "%s/tmp.XXXXXX"; fi\nexec /usr/bin/mktemp "$@"\n' "$H/tmp" > "$H/shim/mktemp"
chmod +x "$H/shim/mktemp"

echo "== vendored upgrade, download fails after the trap is set =="
OUT="$(HOME="$H/home" PATH="$H/shim:$PATH" \
  BSTACK_RELEASE_TARBALL_URL="file://$H/does-not-exist" \
  bash "$INST/bin/bstack" upgrade </dev/null 2>&1)"

case "$OUT" in
  *"unbound variable"*) bad "EXIT trap trips set -u: $(printf '%s' "$OUT" | grep 'unbound variable' | head -1)" ;;
  *) ok "no 'unbound variable' on exit" ;;
esac

case "$OUT" in
  *"Failed to download"*) ok "reached the download step (the trap was set)" ;;
  *) bad "did not reach the download step; output: $(printf '%s' "$OUT" | tail -2)" ;;
esac

LEFT="$(find "$H/tmp" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')"
[ "$LEFT" -eq 0 ] && ok "temp dir removed on exit" || bad "$LEFT temp entries left in TMPDIR"

echo "== plugin.json version matches VERSION =="
PV="$(python3 -I -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$BSTACK_REPO/.claude-plugin/plugin.json")"
V="$(tr -d '[:space:]' < "$BSTACK_REPO/VERSION")"
[ "$PV" = "$V" ] && ok "plugin.json $PV == VERSION $V" || bad "plugin.json $PV != VERSION $V"

echo
echo "upgrade-trap-cleanup: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
