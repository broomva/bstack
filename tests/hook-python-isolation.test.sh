#!/usr/bin/env bash
# tests/hook-python-isolation.test.sh — no hook imports a module from the repo it runs in (BRO-2652).
#
# The bstack hooks fire with cwd = the session's repository. `python3 -` and
# `python3 -c` put that directory FIRST on sys.path, and `python3 FILE` puts FILE's
# directory there. So a json.py (or re.py, subprocess.py, yaml.py, ...) merged into
# the repo replaced the standard library module, ran as code, and could erase the
# hook's decision while the hook still exited 0. Every hook-reachable site now runs
# `python3 -I`: no cwd, no script dir, no PYTHONPATH, no user site on sys.path.
#
# Method, against the REAL hook scripts, driven through hooks/hooks.json:
#   1. Copy scripts/ hooks/ bin/ into a temp plugin root (CLAUDE_PLUGIN_ROOT).
#   2. Pass A: drive realistic events with no plants. Record stdout/stderr/exit and
#      the state each hook leaves. Each baseline must SHOW its decision (a block
#      blocks, a brief renders, PyYAML loads), or the comparison would be vacuous.
#   3. Pass B: plant <module>.py for every candidate module in the cwd, in the
#      plugin's scripts/ and hooks/ (the script dirs), and on PYTHONPATH. Each plant
#      writes a marker file and raises SystemExit(0): the worst-case attacker, one
#      that erases a decision silently. Drive the same events.
#   4. Assert pass B == pass A for every event, and that NO marker exists.
#   5. Positive controls: without -I, a planted json.py MUST run (cwd, script dir
#      and PYTHONPATH vectors), else the test proves nothing and fails.
#   6. Static completeness: tests/hook_python_sites.py lists every python call site
#      a hook can reach; any site without -I (and not on its reasoned allowlist)
#      fails, so a future un-isolated site fails CI.
#
# tests/hook-python-isolation-mutation.test.sh removes -I from one site at a time
# and requires this test to go RED for each.
#
# Env (used by the mutation driver):
#   HOOK_ISO_PLUGIN_SRC   tree to copy the plugin from (default: this repo)
#   HOOK_ISO_SKIP_STATIC  1 = skip step 6 (so a mutant must be killed dynamically)
#   HOOK_ISO_NO_PYTHONPATH 1 = plant in cwd and the script dirs only (see pass B)
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${HOOK_ISO_PLUGIN_SRC:-$REPO}"

pass=0; fail=0
ok()  { echo "  [pass] $1"; pass=$((pass + 1)); }
bad() { echo "  [FAIL] $1"; fail=$((fail + 1)); }

command -v python3 >/dev/null 2>&1 || { echo "python3 required"; exit 1; }
command -v git >/dev/null 2>&1 || { echo "git required"; exit 1; }

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
T="$(cd "$T" && pwd -P)"
NEUTRAL="$T/neutral"; PLUG="$T/plugin"; WS="$T/ws"; MARK="$T/marks"; H="$T/h"
mkdir -p "$NEUTRAL" "$PLUG" "$WS" "$MARK" "$H" "$T/pp" "$T/fakebin" "$T/tr" "$T/arc-tr" "$T/out/A" "$T/out/B"
cp -R "$SRC/scripts" "$SRC/hooks" "$SRC/bin" "$PLUG/"

# The ambient session must not steer workspace resolution.
unset BSTACK_WORKSPACE BROOMVA_WORKSPACE CLAUDE_PROJECT_DIR PYTHONPATH PYTHONHOME PYTHONSTARTUP

# `gh` must exist for knowledge-wakeup to spawn the ship sensor; this stub never
# answers, and the setpoints below name no repos, so nothing is fetched.
printf '#!/bin/sh\nexit 1\n' > "$T/fakebin/gh"; chmod +x "$T/fakebin/gh"

# ── harness helpers (always python3 -I, always from a neutral cwd) ───────────
cat > "$H/hookcmd.py" <<'PY'
import json, sys
hooks = json.load(open(sys.argv[1]))["hooks"]
hits = [h["command"] for b in hooks.get(sys.argv[2], []) for h in b["hooks"]
        if sys.argv[3] in h["command"]]
if len(hits) != 1:
    sys.exit(f"hookcmd: {len(hits)} {sys.argv[2]} commands contain {sys.argv[3]!r}")
print(hits[0])
PY
cat > "$H/state.py" <<'PY'
import json, sys
from datetime import datetime, timezone
json.dump({"measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "window_days": 7, "sessions_analyzed": 1,
           "metrics": {"m2_tool_error_rate": 1.0}}, open(sys.argv[1], "w"))
PY
cat > "$H/probe.py" <<'PY'
import json
PY
hpy() { ( cd "$NEUTRAL" && python3 -I "$@" ); }

# ── candidate modules: everything the hook sites import, plus their stdlib deps ──
CANDIDATES="__future__ argparse ast base64 binascii bisect calendar codecs collections
contextlib contextvars copy copyreg dataclasses datetime decimal dis enum errno fcntl
fnmatch fractions functools genericpath gettext glob hashlib heapq importlib inspect
io json keyword linecache locale math numbers opcode operator os pathlib pickle
platform posixpath pprint random re reprlib select selectors shlex shutil signal
site sitecustomize stat string struct subprocess sys tempfile textwrap threading
time token tokenize tomllib traceback types typing unicodedata usercustomize uuid
warnings weakref yaml zlib _strptime zoneinfo"
BUILTINS=" $(hpy -c 'import sys; print(" ".join(sys.builtin_module_names))') "
MODS=""
for m in $CANDIDATES; do
    case "$BUILTINS" in *" $m "*) continue ;; esac   # compiled in: cannot be shadowed
    MODS="$MODS $m"
done

plant() { # dir module markerdir
    printf 'open(%s, "w").close()\nraise SystemExit(0)\n' "'$3/$2'" > "$1/$2.py"
}

echo "hook python isolation — positive controls (without -I the plants MUST run)"
LIVE=""
for m in $MODS; do
    mkdir -p "$T/pc/$m" "$T/pcmark"
    plant "$T/pc/$m" "$m" "$T/pcmark"
    ( cd "$T/pc/$m" && python3 -c "import $m" >/dev/null 2>&1 )
    [ -e "$T/pcmark/$m" ] && LIVE="$LIVE $m"
done
case " $LIVE " in
    *" json "*) ok "cwd vector: python3 -c 'import json' runs a planted json.py (live: $(echo $LIVE | wc -w | tr -d ' ') modules)" ;;
    *) bad "cwd vector: a planted json.py did not run without -I — the test would be vacuous" ;;
esac
echo "    shadowable without -I:$LIVE"
mkdir -p "$T/pcs" "$T/pcsmark" "$T/pcp" "$T/pcpmark"
cp "$H/probe.py" "$T/pcs/probe.py"; plant "$T/pcs" json "$T/pcsmark"
( cd "$NEUTRAL" && python3 "$T/pcs/probe.py" >/dev/null 2>&1 )
[ -e "$T/pcsmark/json" ] && ok "script-dir vector: python3 FILE runs a json.py beside FILE" \
    || bad "script-dir vector: a json.py beside the script did not run — vacuous"
plant "$T/pcp" json "$T/pcpmark"
( cd "$NEUTRAL" && PYTHONPATH="$T/pcp" python3 -c 'import json' >/dev/null 2>&1 )
[ -e "$T/pcpmark/json" ] && ok "PYTHONPATH vector: a json.py on PYTHONPATH runs" \
    || bad "PYTHONPATH vector: a json.py on PYTHONPATH did not run — vacuous"

YAML_OK=0
( cd "$NEUTRAL" && python3 -c 'import yaml' >/dev/null 2>&1 ) && YAML_OK=1

# ── the workspace the hooks run in ───────────────────────────────────────────
# A private HOME: the user's global git config (hooksPath, signing) stays out.
mkdir -p "$T/home-git"
(
    cd "$WS" || exit 1
    export HOME="$T/home-git"
    git init -q . && git config user.email t@t && git config user.name t
    mkdir -p src tests .control
    printf 'x = 1\n' > src/app.py
    printf 'def test_x():\n    assert 1\n' > tests/test_locked.py
    printf '# gov\n' > CLAUDE.md
    git add -A && git commit -q -m seed
) || { echo "workspace setup failed"; exit 1; }
# Setup, before any plant exists: plain python3 so this also runs on an unfixed tree.
( cd "$WS" && HOME="$T/home-git" python3 "$PLUG/scripts/test_lock.py" commit tests/test_locked.py -m "lock the repro" >/dev/null ) \
    || { echo "test-lock commit failed"; exit 1; }
cat > "$WS/.control/leverage-setpoints.yaml" <<'YAML'
authored_by: hook-isolation-test
window_days: 3
metrics:
  - id: m2
    name: tool-error-rate
    level: L0
    target: 0.05
    alert: 0.2
    direction: lower_is_better
    actuator: read the failing tool output before retrying
YAML
cat > "$T/tr/s1.jsonl" <<'JSONL'
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"Edit","input":{"file_path":"/x/apps/a.ts"}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","is_error":true,"content":"boom"}]}}
JSONL
printf '%s\n' '{"type":"assistant","uuid":"a1","message":{"role":"assistant","content":[{"type":"text","text":"No response requested."}]}}' > "$T/arc-tr/noop.jsonl"
printf '%s\n' '{"type":"assistant","uuid":"a2","message":{"role":"assistant","content":[{"type":"text","text":"Stopping here. This one is your call."}]}}' > "$T/arc-tr/handback.jsonl"
printf '%s\n' '{"type":"assistant","uuid":"a3","message":{"role":"assistant","content":[{"type":"tool_use","id":"t9","name":"Bash","input":{"command":"ls"}}]}}' > "$T/arc-tr/productive.jsonl"

# A second workspace that ships its own bridge script: the bridge hook then runs
# `python3 -I "$BRIDGE"`, whose sys.path[0] would otherwise be THIS scripts/ dir.
WS2="$T/ws2"; mkdir -p "$WS2/scripts"
cat > "$WS2/scripts/conversation-history.py" <<'PYSTUB'
import json, os
with open(os.environ["BRIDGE_PROBE_OUT"], "w") as f:
    json.dump({"ran": True}, f)
PYSTUB

HJ="$PLUG/hooks/hooks.json"
C_POSTURE="$(hpy "$H/hookcmd.py" "$HJ" UserPromptSubmit autonomous-posture-hook.sh)" || exit 1
C_ARC="$(hpy "$H/hookcmd.py" "$HJ" Stop arc-continuation-hook.sh)" || exit 1
C_SENSOR="$(hpy "$H/hookcmd.py" "$HJ" Stop leverage-sensor.py)" || exit 1
C_WAKEUP="$(hpy "$H/hookcmd.py" "$HJ" SessionStart knowledge-wakeup-hook.sh)" || exit 1
C_UPDATE="$(hpy "$H/hookcmd.py" "$HJ" SessionStart bstack-autoupdate-hook.sh)" || exit 1
C_L3="$(hpy "$H/hookcmd.py" "$HJ" PreToolUse l3-stability-pretool-hook.sh)" || exit 1
C_LOCK="$(hpy "$H/hookcmd.py" "$HJ" PreToolUse test-lock-hook.sh)" || exit 1
C_GATE="bash \"\${CLAUDE_PLUGIN_ROOT}/scripts/control-gate-hook.sh\""          # bootstrap-deployed
C_BRIDGE="bash \"\${CLAUDE_PLUGIN_ROOT}/scripts/conversation-bridge-hook.sh\""  # bootstrap-deployed

PASSNAME=A
EXTRA_PYTHONPATH=""
norm() { sed -E 's/[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]+)?(Z|[+-][0-9]{2}:?[0-9]{2})?/<TS>/g'; }

# run NAME CMD PAYLOAD [VAR=value ...] — one hook event, cwd = the workspace
run() {
    local name="$1" cmd="$2" payload="$3"; shift 3
    local o="$T/out/$PASSNAME/$name"
    printf '%s' "$payload" > "$o.in"
    ( cd "$WS" && env CLAUDE_PLUGIN_ROOT="$PLUG" BROOMVA_AUTONOMOUS_HOME="$T/arc" ARC_DRAIN_MS=50 \
        CLAUDE_TRANSCRIPTS="$T/tr/*.jsonl" PATH="$T/fakebin:$PATH" \
        ${EXTRA_PYTHONPATH:+PYTHONPATH="$EXTRA_PYTHONPATH"} "$@" \
        bash -c "$cmd" < "$o.in" > "$o.stdout" 2> "$o.stderr" )
    echo "$?" > "$o.rc"
}
note() { printf '%s\n' "$2" >> "$T/out/$PASSNAME/$1.stdout"; }   # append observed state
arc() { ( cd "$NEUTRAL" && BROOMVA_AUTONOMOUS_HOME="$T/arc" bash "$PLUG/scripts/autonomous-arc.sh" "$@" ); }
arc_state() { note "$1" "state: rc=$(arc get "$2" reconcile_count) tb=$(arc get "$2" total_blocks) hb=$(arc get "$2" handback_count) status=$(arc status "$2")"; }
stop_payload() { printf '{"session_id":"%s","transcript_path":"%s","stop_hook_active":false}' "$1" "$2"; }

drive() {
    # UserPromptSubmit — /autonomous bootstraps an arc and stamps posture; a plain
    # prompt with no arc says nothing.
    rm -rf "$T/arc"
    run posture_bootstrap "$C_POSTURE" '{"session_id":"S-post","prompt":"/autonomous ship it"}'
    arc_state posture_bootstrap S-post
    run posture_quiet "$C_POSTURE" '{"session_id":"S-none","prompt":"hello there"}'

    # Stop — a no-op terminal mid-arc is blocked; a handback with no ask is blocked;
    # a tool call is productive (allowed) and resets the consecutive counter.
    rm -rf "$T/arc"; arc set S-arc demo slice-1 >/dev/null
    run arc_block "$C_ARC" "$(stop_payload S-arc "$T/arc-tr/noop.jsonl")"
    arc_state arc_block S-arc
    rm -rf "$T/arc"; arc set S-hb demo slice-1 >/dev/null
    run arc_handback "$C_ARC" "$(stop_payload S-hb "$T/arc-tr/handback.jsonl")"
    arc_state arc_handback S-hb
    rm -rf "$T/arc"; arc set S-ok demo slice-1 >/dev/null; arc try-block S-ok 2 5 >/dev/null
    run arc_productive "$C_ARC" "$(stop_payload S-ok "$T/arc-tr/productive.jsonl")"
    arc_state arc_productive S-ok

    # PreToolUse — test-lock blocks an edit (and a Bash write) to the locked test,
    # allows an edit elsewhere; the L3 hook warns on a governance file.
    run lock_edit "$C_LOCK" "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$WS/tests/test_locked.py\"},\"cwd\":\"$WS\"}"
    run lock_bash "$C_LOCK" "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo x > tests/test_locked.py\"},\"cwd\":\"$WS\"}"
    run lock_allow "$C_LOCK" "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$WS/src/app.py\"},\"cwd\":\"$WS\"}"
    run l3_warn "$C_L3" "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$WS/CLAUDE.md\"}}"

    # Stop — the leverage sensor (through bstack-hook-guard.sh) computes and stores.
    rm -f "$WS/.control/leverage-state.json" "$WS/.control/leverage-metrics.jsonl" "$WS/.control/leverage-ship-state.json"
    run sensor_stop "$C_SENSOR" '{"session_id":"S-sensor"}'
    if [ -f "$WS/.control/leverage-state.json" ]; then
        note sensor_stop "state: $(hpy -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["sessions_analyzed"], d["worst"] and d["worst"]["key"], d["policy_warnings"])' "$WS/.control/leverage-state.json")"
    else
        note sensor_stop "state: none written"
    fi

    # SessionStart — knowledge-wakeup renders the cached brief (regraded against the
    # YAML policy) and spawns the ship sensor in the background; wait for it.
    rm -f "$WS/.control/leverage-ship-state.json"; hpy "$H/state.py" "$WS/.control/leverage-state.json"
    run wakeup "$C_WAKEUP" '{"session_id":"S-wake"}'
    local i=0
    while [ "$i" -lt 150 ] && [ ! -f "$WS/.control/leverage-ship-state.json" ] && [ -z "$(ls -A "$MARK")" ]; do
        sleep 0.1; i=$((i + 1))
    done
    sleep 0.2
    if [ -f "$WS/.control/leverage-ship-state.json" ]; then
        note wakeup "ship-state: $(hpy -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["gh_ok"], d["m6s_meta_work_ship_ratio"], "window=%s" % d["window_days"])' "$WS/.control/leverage-ship-state.json")"
    else
        note wakeup "ship-state: none written"
    fi
    run autoupdate "$C_UPDATE" '{"session_id":"S-up"}' HOME="$T/home-update"

    # Bootstrap-deployed workspace hooks. control-gate is the P2 PreToolUse gate.
    run gate_block "$C_GATE" '{"tool_name":"Bash","tool_input":{"command":"git push --force origin main"}}' CLAUDE_PROJECT_DIR="$WS"
    run gate_allow "$C_GATE" '{"tool_name":"Bash","tool_input":{"command":"ls -la"}}' CLAUDE_PROJECT_DIR="$WS"
    rm -rf "$T/home-bridge" "$WS/docs"
    run bridge "$C_BRIDGE" '{"session_id":"S-bridge","hook_event_name":"Stop"}' CLAUDE_PROJECT_DIR="$WS" HOME="$T/home-bridge"
    local log="$WS/docs/conversations/Conversations.md"
    if [ -f "$log" ]; then note bridge "log: $(norm < "$log")"; else note bridge "log: none"; fi
    # The workspace-bridge path runs in the background; wait (bounded) for its probe.
    # A fresh HOME per pass: the hook's cooldown stamp lives under it.
    rm -rf "$T/bridge-probe.json" "$T/home-bridge2-$PASSNAME"
    run bridge_ws "$C_BRIDGE" '{"session_id":"S-bridge2","hook_event_name":"Stop"}' \
        CLAUDE_PROJECT_DIR="$WS2" HOME="$T/home-bridge2-$PASSNAME" BRIDGE_PROBE_OUT="$T/bridge-probe.json"
    local _i
    for _i in $(seq 1 100); do [ -f "$T/bridge-probe.json" ] && break; sleep 0.1; done
    note bridge_ws "probe: $(cat "$T/bridge-probe.json" 2>/dev/null || echo none)"
}

SCENARIOS="posture_bootstrap posture_quiet arc_block arc_handback arc_productive lock_edit lock_bash lock_allow l3_warn sensor_stop wakeup autoupdate gate_block gate_allow bridge bridge_ws"

# ── pass A: no plants ────────────────────────────────────────────────────────
PASSNAME=A; drive
rm -rf "$WS/docs"   # the bridge's log is not part of the repo under test

echo "hook python isolation — baselines show their decisions (non-vacuous)"
has()   { grep -qF -- "$3" "$T/out/A/$1.$2"; }
rcis()  { [ "$(cat "$T/out/A/$1.rc")" = "$2" ]; }
expect() { if "${@:2}"; then ok "$1"; else bad "$1"; fi; }
expect "posture: /autonomous stamps the sticky posture"      has posture_bootstrap stdout "sticky posture"
expect "posture: no arc, no output"                          test ! -s "$T/out/A/posture_quiet.stdout"
expect "arc-continuation: a no-op mid-arc turn is blocked"    has arc_block stdout '"decision": "block"'
expect "arc-continuation: the block spent the stall budget"   has arc_block stdout "state: rc=1 tb=1"
expect "arc-continuation: a handback with no ask is blocked"  has arc_handback stdout "Blocked on you"
expect "arc-continuation: a tool call resets the counter"     has arc_productive stdout "state: rc=0 tb=1"
expect "test-lock: an Edit to the locked test exits 2"        rcis lock_edit 2
expect "test-lock: the block names the lock"                  has lock_edit stderr "BLOCKED (test-lock)"
expect "test-lock: a Bash write to the locked test exits 2"   rcis lock_bash 2
expect "test-lock: an unrelated Edit exits 0"                 rcis lock_allow 0
expect "l3-stability: governance edit warns"                  has l3_warn stdout "L3 governance mutation"
expect "leverage-sensor (Stop): stored its state"             has sensor_stop stdout "state: 1 m2_tool_error_rate"
expect "knowledge-wakeup: the brief renders the worst gap"    has wakeup stdout "Worst gap [ALERT] tool-error-rate"
# window=3 comes from the YAML policy (the default is 7): proof the ship sensor read it.
expect "knowledge-wakeup: the ship sensor ran and read the policy" has wakeup stdout "ship-state: False None window=3"
expect "control-gate: force-push is blocked (exit 2)"         rcis gate_block 2
expect "control-gate: ls is allowed (exit 0)"                 rcis gate_allow 0
expect "conversation-bridge: the session stamp was written"   has bridge stdout "session S-bridge"
expect "conversation-bridge: the workspace bridge script ran"  has bridge_ws stdout '"ran": true'
if [ "$YAML_OK" = 1 ]; then
    expect "sensors: PyYAML loads under -I (policy not degraded)" \
        test -z "$(cat "$T/out/A/sensor_stop.stdout" "$T/out/A/wakeup.stdout" | grep -F 'could not load setpoints')"
else
    echo "  [skip] PyYAML not importable by this python3 at all — the -I load check needs it"
fi

# ── pass B: plants in cwd, in the script dirs, and on PYTHONPATH ─────────────
for m in $MODS; do
    plant "$WS" "$m" "$MARK"
    plant "$T/pp" "$m" "$MARK"
    for d in "$PLUG/scripts" "$PLUG/hooks" "$WS2/scripts"; do
        [ -e "$d/$m.py" ] || plant "$d" "$m" "$MARK"
    done
done
# The PYTHONPATH vector is off in mutation runs: a planted site.py there kills
# every non-isolated interpreter at startup, which would mask whether the cwd and
# script-dir plants (the actual threat) kill each mutant on their own.
PASSNAME=B
[ "${HOOK_ISO_NO_PYTHONPATH:-0}" = 1 ] || EXTRA_PYTHONPATH="$T/pp"
drive

echo "hook python isolation — planted modules change nothing"
for s in $SCENARIOS; do
    same=1
    for part in stdout stderr rc; do
        if ! cmp -s <(norm < "$T/out/A/$s.$part") <(norm < "$T/out/B/$s.$part"); then
            same=0
            echo "    --- $s.$part differs (A then B):"
            diff <(norm < "$T/out/A/$s.$part") <(norm < "$T/out/B/$s.$part") | head -8 | sed 's/^/      /'
        fi
    done
    [ "$same" = 1 ] && ok "$s: identical with plants present" || bad "$s: output changed with plants present"
done
if [ -z "$(ls -A "$MARK")" ]; then
    ok "no planted module was imported ($(echo $MODS | wc -w | tr -d ' ') modules x cwd, script dirs${EXTRA_PYTHONPATH:+, PYTHONPATH})"
else
    bad "planted modules were imported: $(ls "$MARK" | tr '\n' ' ')"
fi

# ── static completeness ──────────────────────────────────────────────────────
if [ "${HOOK_ISO_SKIP_STATIC:-0}" != 1 ]; then
    echo "hook python isolation — every hook-reachable python site says -I"
    if out="$(hpy "$REPO/tests/hook_python_sites.py" "$SRC" check 2>&1)"; then
        ok "static: no un-isolated python site is reachable from a hook"
    else
        bad "static: un-isolated python site(s) reachable from a hook"
    fi
    printf '%s\n' "$out" | sed 's/^/  /'
    n="$(hpy "$REPO/tests/hook_python_sites.py" "$SRC" sites | awk -F'\t' '$3==1' | wc -l | tr -d ' ')"
    [ "$n" -ge 12 ] && ok "static: $n isolated sites enumerated (>= the 12 known)" \
        || bad "static: only $n isolated sites enumerated — the enumeration shrank"
fi

echo ""
echo "hook-python-isolation: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
