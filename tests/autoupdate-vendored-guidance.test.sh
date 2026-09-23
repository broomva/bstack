#!/usr/bin/env bash
# tests/autoupdate-vendored-guidance.test.sh — upgrade guidance for a vendored
# install must never recommend `npx skills add`, which can leave an install
# holding only SKILL.md (no bin/, scripts/ or hooks). BRO-2536.
#
# Covers the SessionStart nudge (scripts/bstack-autoupdate-hook.sh) and the
# fallbacks `bstack upgrade` prints (bin/bstack). Run from the repo root:
#   bash tests/autoupdate-vendored-guidance.test.sh

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$BSTACK_REPO/scripts/bstack-autoupdate-hook.sh"
BIN="$BSTACK_REPO/bin/bstack"
PASS=0
FAIL=0
ok()  { echo "  [ok] $1"; PASS=$((PASS + 1)); }
bad() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); }

H="$(mktemp -d)"
trap 'rm -rf "$H"' EXIT
mkdir -p "$H/.claude/skills/bstack/bin"
printf '#!/bin/sh\necho "UPGRADE_AVAILABLE 0.1.0 0.2.0"\n' > "$H/.claude/skills/bstack/bin/bstack-update-check"
printf '#!/bin/sh\necho ""\n' > "$H/.claude/skills/bstack/bin/bstack-config"
chmod +x "$H/.claude/skills/bstack/bin/"*

echo "== SessionStart nudge, vendored install (no .git) =="
OUT="$(HOME="$H" BSTACK_AUTO_UPGRADE=1 bash "$HOOK" </dev/null 2>&1)"
RC=$?
[ "$RC" -eq 0 ] && ok "hook exits 0" || bad "hook exited $RC"
case "$OUT" in
    *"v0.2.0 available"*) ok "nudge names the new version" ;;
    *) bad "nudge missing or wrong: $OUT" ;;
esac
case "$OUT" in
    *"bstack upgrade"*"bstack-upgrade/SKILL.md"*) ok "nudge names bstack upgrade and the manual flow" ;;
    *) bad "nudge does not name a working upgrade path: $OUT" ;;
esac
if printf '%s' "$OUT" | grep -q 'npx skills add -g'; then
    bad "nudge recommends npx skills add -g"
else
    ok "nudge does not recommend npx skills add -g"
fi

echo "== bin/bstack vendored fallbacks =="
if grep -nE 'echo .*(Falling back|fallback).*npx skills add' "$BIN" >/dev/null; then
    bad "bin/bstack still falls back to npx skills add"
else
    ok "no bin/bstack fallback recommends npx skills add"
fi
N="$(grep -c 'bstack-upgrade/SKILL.md (git clone --depth 1' "$BIN")"
[ "$N" -eq 3 ] && ok "all three fallbacks point at the manual clone ($N)" || bad "expected 3 manual-clone fallbacks, found $N"

echo "== bin/bstack upgrade at runtime: download fails on a vendored install =="
# A source grep cannot see a fallback that crashes before printing (an unset
# variable under set -u), so run the real path: a vendored copy of the repo, an
# update-check stub that reports an upgrade, and a tarball URL that cannot resolve.
INST="$H/install/bstack"
mkdir -p "$INST"
(cd "$BSTACK_REPO" && tar -cf - --exclude .git --exclude tests .) | tar -xf - -C "$INST"
[ -x "$INST/bin/bstack" ] || { bad "could not stage a vendored copy at $INST"; echo "  passed: $PASS  failed: $FAIL"; exit 1; }
printf '#!/bin/sh\necho "UPGRADE_AVAILABLE 0.1.0 0.2.0"\n' > "$INST/bin/bstack-update-check"
chmod +x "$INST/bin/"*
UP="$(HOME="$H" BSTACK_RELEASE_TARBALL_URL="file:///nonexistent-bstack-release" bash "$INST/bin/bstack" upgrade 2>&1)"
case "$UP" in
    *"$INST/bstack-upgrade/SKILL.md (git clone --depth 1"*) ok "fallback prints, with the install's own path" ;;
    *) bad "fallback missing or path unexpanded: $(printf '%s' "$UP" | head -5)" ;;
esac
if printf '%s' "$UP" | grep -q 'npx skills add'; then
    bad "runtime fallback mentions npx skills add"
else
    ok "runtime fallback does not mention npx skills add"
fi

echo ""
echo "  passed: $PASS  failed: $FAIL"
[ "$FAIL" -eq 0 ]
