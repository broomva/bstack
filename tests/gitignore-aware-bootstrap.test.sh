#!/usr/bin/env bash
# gitignore-aware-bootstrap — covers the v0.23.0 safety fixes (issue #67):
#   1. install-l3-stability.sh never clobbers a TRACKED .githooks/pre-commit
#   2. install-l3-stability.sh still preserves an UNTRACKED pre-commit as .local
#   3. bootstrap.sh adds machine-local audit-log glob to .gitignore (real git repo)
#   4. bootstrap.sh warns when committable substrate is gitignored (coverage gap)

set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0; FAIL=0; FAILED=()
pass() { echo "  [ok] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); FAILED+=("$1"); }

echo "── gitignore-aware-bootstrap ──────────────────────────"

# ── 1. TRACKED pre-commit is preserved (not clobbered) ──────────────────────
TW=$(mktemp -d)
(
  cd "$TW" && git init -q
  mkdir -p .githooks
  printf '#!/bin/bash\n# repo multimedia validator\n' > .githooks/pre-commit
  chmod +x .githooks/pre-commit
  git add .githooks/pre-commit && git -c user.email=t@t -c user.name=t commit -q -m "hook"
)
BROOMVA_WORKSPACE="$TW" bash "$REPO/scripts/install-l3-stability.sh" >/dev/null 2>&1
if grep -q "multimedia validator" "$TW/.githooks/pre-commit"; then
  pass "tracked .githooks/pre-commit preserved (not clobbered)"
else
  fail "tracked .githooks/pre-commit was clobbered"
fi
if [ ! -f "$TW/.githooks/pre-commit.local" ]; then
  pass "no .pre-commit.local created for tracked hook (default)"
else
  fail ".pre-commit.local was created for a tracked hook (default)"
fi

# ── 1b. --force on a TRACKED hook DOES preserve it as .pre-commit.local ──────
BROOMVA_WORKSPACE="$TW" bash "$REPO/scripts/install-l3-stability.sh" --force >/dev/null 2>&1
if grep -q "L3 rate gate" "$TW/.githooks/pre-commit" 2>/dev/null \
   && grep -q "multimedia validator" "$TW/.githooks/pre-commit.local" 2>/dev/null; then
  pass "--force preserves tracked hook as .pre-commit.local, then installs L3 hook"
else
  fail "--force did NOT create the promised .pre-commit.local sidecar"
fi
rm -rf "$TW"

# ── 2. UNTRACKED pre-commit IS preserved as sidecar, ours installed ─────────
TW=$(mktemp -d)
(
  cd "$TW" && git init -q
  mkdir -p .githooks
  printf '#!/bin/bash\n# untracked local hook\n' > .githooks/pre-commit
  chmod +x .githooks/pre-commit
)
BROOMVA_WORKSPACE="$TW" bash "$REPO/scripts/install-l3-stability.sh" >/dev/null 2>&1
if grep -q "L3 rate gate" "$TW/.githooks/pre-commit" 2>/dev/null \
   && grep -q "untracked local hook" "$TW/.githooks/pre-commit.local" 2>/dev/null; then
  pass "untracked hook moved to .pre-commit.local, L3 hook installed"
else
  fail "untracked-hook preservation regressed"
fi
rm -rf "$TW"

# ── 3 + 4. bootstrap gitignore reconciliation on a real git repo ────────────
TW=$(mktemp -d); TH=$(mktemp -d)
( cd "$TW" && git init -q )
HOME="$TH" BROOMVA_WORKSPACE="$TW" BSTACK_SKIP_SKILLS=1 BSTACK_SKIP_RCS=1 \
  BROOMVA_STATE_DIR="$TH/.cfg" bash "$REPO/scripts/bootstrap.sh" >/dev/null 2>&1
if grep -q "control/audit/\*.jsonl" "$TW/.gitignore" 2>/dev/null; then
  pass "bootstrap added machine-local audit-log glob to .gitignore"
else
  fail "bootstrap did not add audit-log glob to .gitignore"
fi
# committable-ignored warning
echo ".control/arcs.yaml" >> "$TW/.gitignore"
out=$(HOME="$TH" BROOMVA_WORKSPACE="$TW" BSTACK_SKIP_SKILLS=1 BSTACK_SKIP_RCS=1 \
  BROOMVA_STATE_DIR="$TH/.cfg" bash "$REPO/scripts/bootstrap.sh" 2>&1)
if echo "$out" | grep -q "arcs.yaml is gitignored but the loop needs it"; then
  pass "bootstrap warns when committable substrate is gitignored"
else
  fail "no warning for gitignored committable substrate"
fi
# idempotency: the audit-log glob appears exactly once after repeated runs
n=$(grep -c "^\.control/audit/\*\.jsonl$" "$TW/.gitignore" 2>/dev/null || echo 0)
if [ "$n" -eq 1 ]; then
  pass "audit-log glob is idempotent (single .gitignore entry after 2 runs)"
else
  fail "audit-log glob duplicated in .gitignore ($n entries)"
fi
rm -rf "$TW" "$TH"

echo "─────────────────────────────────────"
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  for t in "${FAILED[@]}"; do echo "    - $t"; done
  exit 1
fi
echo "  gitignore-aware-bootstrap passed."
