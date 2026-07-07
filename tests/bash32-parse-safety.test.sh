#!/bin/bash
# bash32-parse-safety.test.sh — regression guard for BRO-1718.
#
# v0.34.0 shipped doctor.sh §25 with a literal backtick inside the script_path()
# operator-blacklist tuple, inside a quoted <<'PYEOF' heredoc nested in $( ... ).
# bash 3.2's command-substitution pre-parser chokes on backticks there EVEN WHEN
# the heredoc is quoted (classic 3.2 parser bug):
#
#   doctor.sh: line 1354: unexpected EOF while looking for matching ``'
#
# Every verification layer missed it because every layer resolves bash from a
# modern PATH (dev wrapper `exec bash`, ubuntu CI, tests invoking `bash "$t"`)
# — a shared "bash >= 4" premise. The one path nobody exercised is the delivery
# interface: direct execution via the `#!/bin/bash` shebang, which on stock
# macOS is bash 3.2. See research entity pattern/shared-assumption-drift.
#
# Guards (deterministic on every platform, genuinely 3.2 on macOS):
#   1. No literal backtick inside any quoted-heredoc body born inside $( / <(
#      in scripts/, tests/, bin/ — the static class guard; catches the bug on
#      ubuntu CI too.
#   2. /bin/bash -n parses every scripts/ + tests/ *.sh and bash-shebang bin/*
#      — full-file parse under the SYSTEM bash (3.2 on macOS: the real
#      regression check; >=4 elsewhere: still a parse gate). bash -n parses the
#      whole file regardless of runtime control flow, so no scratch workspace
#      is needed.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0

fail() { echo "  ✗ $1"; FAILS=$((FAILS+1)); }
pass() { echo "  ✓ $1"; }

echo "[bash32-parse-safety] repo: $REPO"
echo "[bash32-parse-safety] system bash: $(/bin/bash --version | head -1)"

# ── Assertion 1: no backtick inside $()-nested quoted-heredoc bodies ──────────
# The hazard is SPECIFIC: bash 3.2 pre-parses the body of a heredoc that is
# opened inside $( ... ) command substitution and dies on backticks there, even
# when the heredoc delimiter is quoted. Standalone heredocs (a bare
# `python3 - <<'PY'` command) are parse-safe in 3.2 and deliberately NOT
# flagged — compute-arc-status.sh et al. carry backticks in standalone heredoc
# bodies today and /bin/bash 3.2 parses them fine.
# Detection: heredoc opener line carries an unclosed $( or <( (open-paren
# balance > 0 on that line). Known residual: a heredoc opened on a LATER line
# than its enclosing $( is not caught here — Assertion 2 backstops that under a
# real 3.2 (macOS). If the character is ever needed inside such a body, use
# chr(96) in python — as this scanner itself does: its own heredoc body is
# exactly the hazard form, so tests/ is inside its own scan scope (P20 round 1
# caught the first version of this file carrying a literal backtick at the
# comparison line — the guard failing its own invariant).
HITS=$(python3 - "$REPO" <<'PYEOF'
import glob, os, re, sys
repo = sys.argv[1]
# NB: this body lives inside $( ... ); bash 3.2's pre-parser scans the RAW
# heredoc text and dies on backticks, unbalanced quotes, and dollar-paren
# openers. So every such character below is built via chr(): 96 backtick,
# 39 single quote, 34 double quote, 36 dollar, 60 less-than, 40 open-paren.
SQ, DQ, BT = chr(39), chr(34), chr(96)
CMD_OPEN, PROC_OPEN = chr(36) + chr(40), chr(60) + chr(40)
opener = re.compile("<<-?\\s*([%s%s])([A-Za-z_][A-Za-z0-9_]*)\\1" % (SQ, DQ))
hits = []
files = sorted(glob.glob(os.path.join(repo, "scripts", "**", "*.sh"), recursive=True))
files += sorted(glob.glob(os.path.join(repo, "tests", "**", "*.sh"), recursive=True))
files += sorted(p for p in glob.glob(os.path.join(repo, "bin", "*")) if os.path.isfile(p))
for path in files:
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        continue
    terminator = None
    for i, line in enumerate(lines, 1):
        if terminator is not None:
            if line.strip() == terminator:
                terminator = None
            elif BT in line:
                hits.append("%s:%d" % (os.path.relpath(path, repo), i))
        else:
            m = opener.search(line)
            if m and (CMD_OPEN in line or PROC_OPEN in line) \
                 and line.count(chr(40)) > line.count(chr(41)):
                terminator = m.group(2)   # heredoc born inside unclosed cmd-subst
for h in hits:
    print(h)
PYEOF
)
RC=$?
if [ "$RC" -ne 0 ]; then
    fail "heredoc scanner crashed (exit $RC) — cannot certify assertion 1"
elif [ -n "$HITS" ]; then
    fail "literal backtick inside \$()-nested quoted-heredoc body (bash-3.2 parse hazard):"
    echo "$HITS" | sed 's/^/      /'
else
    pass "no backticks inside \$()-nested quoted-heredoc bodies (scripts/ + tests/ + bin/)"
fi

# ── Assertion 2: system /bin/bash parses every script ─────────────────────────
# On macOS /bin/bash IS 3.2 — this is the real regression check for BRO-1718.
# Elsewhere it is still a full-file parse gate through the delivery interface.
PARSE_FAILS=""
while IFS= read -r f; do
    # bin/ may one day carry non-bash executables — only parse bash shebangs.
    case "$f" in
        */bin/*) head -1 "$f" | grep -q bash || continue ;;
    esac
    if ! ERR=$(/bin/bash -n "$f" 2>&1); then
        # NB: rel computed on its own line — bash 3.2 cannot parse nested
        # quotes like ${f#"$REPO"/} inside a larger double-quoted string.
        rel=${f#"$REPO"/}
        PARSE_FAILS="${PARSE_FAILS}${rel}: ${ERR}"$'\n'
    fi
done < <(find "$REPO/scripts" "$REPO/tests" -name '*.sh' -type f; find "$REPO/bin" -maxdepth 1 -type f)
if [ -n "$PARSE_FAILS" ]; then
    fail "/bin/bash -n parse failures:"
    printf '%s' "$PARSE_FAILS" | sed 's/^/      /'
else
    pass "/bin/bash -n parses all scripts/ + tests/ + bash-shebang bin/ files"
fi

# ── Verdict ───────────────────────────────────────────────────────────────────
if [ "$FAILS" -gt 0 ]; then
    echo "[bash32-parse-safety] FAIL ($FAILS)"
    exit 1
fi
echo "[bash32-parse-safety] PASS"
