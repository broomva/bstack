#!/usr/bin/env bash
# fresh-install-portability.test.sh — BRO-1715.
#
# bstack's DISCIPLINE harness (governance files, P1/P2/P6/P7 hooks) transplants to
# a fresh non-~/broomva workspace, but its SELF-IMPROVING / AUTONOMY layer was
# silently ~/broomva-hosted. Four packaging bugs made a fresh `git clone bstack &&
# bstack bootstrap` install look healthy while the loop was dead:
#
#   Bug 1  5 skill-dir hooks (bstack-autoupdate, knowledge-wakeup, auth-preflight,
#          autonomous-posture, arc-continuation) were wired at
#          ${BROOMVA_HOME}/.claude/skills/bstack/scripts/ — a dir no install step
#          creates (bstack installs by git-clone, NOT `npx skills add`, so it is
#          never a global skill). Every one dangled → the loop actuation wire +
#          the ship-signal that rides it were dead on arrival.
#   Bug 2  .control/leverage-setpoints.yaml (the reference signal r0) was never
#          seeded → leverage-sensor.py ran referenceless (empty metrics, no r).
#   Bug 3  doctor §7 false-RED: it checked workspace-relative skills/<n>/scripts/*.py
#          but P6/P9/P12 install globally (-g into ~/.claude|.agents/skills).
#   Bug 4  doctor false-GREEN off-broomva: WORKSPACE defaulted to $HOME/broomva, so
#          a bare `bstack doctor` from an unrelated dir audited the healthy ~/broomva.
#
# THE ONLY HONEST TEST: bootstrap into an EMPTY scratch workspace (never ~/broomva,
# which self-hosts the monorepo and passes vacuously). RED on current main proves
# the bugs; GREEN after the fix proves portability. Hermetic — no network, no gh,
# no real $HOME (BSTACK_SKIP_SKILLS=1 + a temp HOME).
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0; FAIL=0; FAILED=()
pass() { echo "  [ok] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); FAILED+=("$1"); }

echo "── fresh-install-portability (BRO-1715) ───────────────"

# ── Fresh scratch workspace + isolated HOME (never ~/broomva) ────────────────
TW=$(mktemp -d)   # the workspace under test
TH=$(mktemp -d)   # isolated HOME — global skills / config never touch the real home
(
  cd "$TW" && git init -q
  git -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
)
# Bootstrap the discipline + loop wiring WITHOUT the network fan-out or RCS
# sub-installer. The 5 Bug-1 hooks live in the MAIN settings snippet (Phase 3),
# so they are wired regardless of BSTACK_SKIP_RCS.
HOME="$TH" BROOMVA_WORKSPACE="$TW" BSTACK_SKIP_SKILLS=1 BSTACK_SKIP_RCS=1 \
  bash "$REPO/scripts/bootstrap.sh" >/dev/null 2>&1
SETTINGS="$TW/.claude/settings.json"

# ── Assertion 1 (Bug 1): every bstack-SHIPPED hook command resolves ──────────
# A hook under $HOME/.{claude,agents}/skills/<name>/ for a companion skill we
# deliberately did NOT install (name != bstack) is tolerated. Everything else —
# including anything hiding under .../skills/bstack/ (bstack is NOT a companion
# skill) — MUST resolve to a real file.
if [ ! -f "$SETTINGS" ]; then
  fail "bootstrap produced no .claude/settings.json"
elif python3 - "$SETTINGS" "$TH" <<'PY'
import json, os, sys
settings, home = sys.argv[1], sys.argv[2]
roots = (os.path.join(home, ".claude", "skills") + os.sep,
         os.path.join(home, ".agents", "skills") + os.sep)
bad = []
for event, blocks in json.load(open(settings)).get("hooks", {}).items():
    for b in blocks:
        for h in b.get("hooks", []):
            cmd = h.get("command", "")
            # First absolute-path token (interpreter-aware, matches doctor §25).
            path = next((t for t in cmd.split() if t.startswith("/")), "")
            if not path:
                continue
            tolerated = False
            for r in roots:
                if path.startswith(r):
                    name = path[len(r):].split(os.sep)[0]
                    if name != "bstack":   # a companion skill we skipped — OK absent
                        tolerated = True
            if not tolerated and not os.path.isfile(path):
                bad.append(f"{event}: {path}")
if bad:
    sys.stderr.write("dangling hook command(s):\n" + "\n".join("  " + x for x in bad) + "\n")
    sys.exit(1)
PY
then
  pass "Bug 1: every bstack-shipped hook command resolves to a real file"
else
  fail "Bug 1: one or more wired hook commands dangle (see above)"
fi

# ── Assertion 2 (Bug 2): leverage-setpoints.yaml seeded with non-empty metrics ─
SP="$TW/.control/leverage-setpoints.yaml"
if [ -f "$SP" ] && grep -qE '^[[:space:]]*-[[:space:]]*id:[[:space:]]*m[0-9]' "$SP"; then
  pass "Bug 2: .control/leverage-setpoints.yaml seeded with non-empty metrics (r0 present)"
else
  fail "Bug 2: .control/leverage-setpoints.yaml missing or has no metrics — loop runs referenceless"
fi

# ── Assertion 3 (Bug 3): doctor OKs P6/P9/P12 for GLOBALLY-installed skills ───
# Stand up the skill scripts where `npx skills add -g` actually puts them, then
# assert doctor §7 does not false-RED them as workspace-relative-missing.
mkdir -p "$TH/.claude/skills/bookkeeping/scripts" \
         "$TH/.claude/skills/p9/scripts" \
         "$TH/.claude/skills/persist/scripts"
: > "$TH/.claude/skills/bookkeeping/scripts/bookkeeping.py"
: > "$TH/.claude/skills/p9/scripts/p9.py"
: > "$TH/.claude/skills/persist/scripts/persist.py"
DOC_OUT=$(HOME="$TH" BROOMVA_WORKSPACE="$TW" bash "$REPO/scripts/doctor.sh" 2>&1)
if echo "$DOC_OUT" | grep -qE 'P(6|9|12) mechanism missing'; then
  fail "Bug 3: doctor false-RED — P6/P9/P12 GAP despite the skills being installed globally"
else
  pass "Bug 3: doctor OKs P6/P9/P12 from the global skill dirs (no workspace-relative false-RED)"
fi

# ── Assertion 4 (Bug 4): doctor FAILS LOUD off-broomva (no ~/broomva default) ─
# From an unrelated cwd, env unset, isolated HOME + empty config: doctor must
# resolve the workspace (→ the cwd, which is NOT a workspace) and error out
# loudly — never silently fall back to a hardcoded ~/broomva and audit it.
UNREL=$(mktemp -d); TH2=$(mktemp -d)
set +e
D4_OUT=$(cd "$UNREL" && env -u BROOMVA_WORKSPACE HOME="$TH2" BSTACK_STATE_DIR="$TH2/.bstack" \
  bash "$REPO/scripts/doctor.sh" 2>&1)
D4_CODE=$?
set -e
if [ "$D4_CODE" -ne 0 ] && echo "$D4_OUT" | grep -q "workspace not found" \
   && echo "$D4_OUT" | grep -q "$UNREL"; then
  pass "Bug 4: doctor fails loud off-broomva (exit $D4_CODE; resolved cwd, no ~/broomva default)"
else
  fail "Bug 4: doctor did not fail loud (exit $D4_CODE) — silently defaulted instead of erroring on no workspace"
fi

# ── Assertion 6 (§25 robustness): exotic hook forms don't false-gap ──────────
# script_path() is deliberately conservative — it must NOT spuriously HARD-gap a
# `sh -c` composite or an interpreter-prefixed command that resolves (P20 gate
# finding: a false HARD gap would break `doctor --strict` on a real hook).
REAL_HOOK="$TW/scripts/real-hook.sh"; : > "$REAL_HOOK"
python3 - "$SETTINGS" "$REAL_HOOK" <<'PY'
import json, sys
p, real = sys.argv[1], sys.argv[2]
d = json.load(open(p))
ss = d.setdefault("hooks", {}).setdefault("SessionStart", [])
ss.append({"hooks": [{"type": "command", "command": "sh -c 'echo hi'", "_bstack_primitive": "t1"}]})
ss.append({"hooks": [{"type": "command", "command": "python3 %s --flag" % real, "_bstack_primitive": "t2"}]})
json.dump(d, open(p, "w"), indent=2)
PY
D6_OUT=$(HOME="$TH" BROOMVA_WORKSPACE="$TW" bash "$REPO/scripts/doctor.sh" 2>&1)
if echo "$D6_OUT" | grep -qE 'dangles.*(echo hi|real-hook)'; then
  fail "§25 false-positive: sh -c composite or interpreter-prefixed resolvable hook wrongly flagged"
else
  pass "§25 robustness: sh -c composite + interpreter-prefixed resolvable hook not false-gapped"
fi

# ── Assertion 5 (Bug 1, doctor half): §25 hard-gaps a dangling wired hook ─────
# The silent-death class itself: a hook can be WIRED (name-grep passes, §24-style)
# while its command path resolves to nothing. Inject one and assert doctor §25
# reports it as a gap rather than staying green.
python3 - "$SETTINGS" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d.setdefault("hooks", {}).setdefault("SessionStart", []).append(
    {"hooks": [{"type": "command",
                "command": "/nonexistent/bstack/scripts/ghost-hook.sh",
                "_bstack_primitive": "test"}]})
json.dump(d, open(p, "w"), indent=2)
PY
D5_OUT=$(HOME="$TH" BROOMVA_WORKSPACE="$TW" bash "$REPO/scripts/doctor.sh" 2>&1)
if echo "$D5_OUT" | grep -q "wired hook command dangles: /nonexistent/bstack/scripts/ghost-hook.sh"; then
  pass "Bug 1 (doctor §25): a dangling wired hook is hard-gapped, not silently green"
else
  fail "Bug 1 (doctor §25): dangling wired hook not caught by doctor"
fi

rm -rf "$TW" "$TH" "$UNREL" "$TH2"

echo "─────────────────────────────────────"
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  for t in "${FAILED[@]}"; do echo "    - $t"; done
  exit 1
fi
echo "  fresh-install-portability passed."
