#!/usr/bin/env bash
# tests/bg-task-stop-guard.test.sh — the Paseo background-task Stop guard (BRO-2815).
#
# Every case runs the command hooks/hooks.json registers for the guard (located by
# name under Stop, with ${CLAUDE_PLUGIN_ROOT} pointed at the tree under test), on
# real Claude Code 2.1.295 Stop inputs from tests/fixtures/bg-task-stop-guard/.
# State and the decision log go to a scratch BROOMVA_AUTONOMOUS_HOME per case.
#
# Then the mutation proof: each mutant below is applied to a copy of the tree and
# this suite is re-run against the copy. Every mutant must turn it red.
#
# Env: BG_GUARD_ROOT   tree under test (default: this repo)
#      BG_GUARD_NO_MUTATE=1  skip the mutation phase (set for the inner runs)
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${BG_GUARD_ROOT:-$REPO}"
FX="$REPO/tests/fixtures/bg-task-stop-guard"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
QUIET="${BG_GUARD_NO_MUTATE:-}"

PASS=0; FAIL=0
ok()  { [ -n "$QUIET" ] || echo "  [ok] $1"; PASS=$((PASS + 1)); }
bad() { [ -n "$QUIET" ] || echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); }

[ -n "$QUIET" ] || echo "── bg-task-stop-guard (BRO-2815) ──────────────"

CMD="$(python3 -I - "$ROOT/hooks/hooks.json" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
for m in d.get("hooks", {}).get("Stop", []):
    for h in m.get("hooks", []):
        if "bg-task-stop-guard" in h.get("command", ""):
            print(h["command"])
            sys.exit(0)
PY
)"
if [ -n "$CMD" ]; then ok "registered under Stop in hooks/hooks.json"; else bad "not registered under Stop in hooks/hooks.json"; fi

N=0
# run <payload-file> [env assignments...] -> sets RC, ERR; fresh state unless KEEP=1
run() {
    local payload="$1"; shift
    if [ -z "${KEEP:-}" ]; then N=$((N + 1)); STATE="$T/state$N"; fi
    mkdir -p "$STATE"
    ERR="$(env -u PASEO_AGENT_ID -u BSTACK_BG_TASK_GUARD \
        CLAUDE_PLUGIN_ROOT="$ROOT" BROOMVA_AUTONOMOUS_HOME="$STATE" "$@" \
        bash -c "${CMD:-exit 0}" < "$payload" 2>&1 >/dev/null)"
    RC=$?
}
# with_prompt <src> <prompt_id> -> path of a copy with that prompt_id
with_prompt() {
    local out="$T/p-$2.json"
    python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); d["prompt_id"]=sys.argv[2]; json.dump(d,open(sys.argv[3],"w"))' "$1" "$2" "$out"
    echo "$out"
}
# with_tasks <src> <json-list> -> path of a copy with that background_tasks value
with_tasks() {
    local out="$T/t-$RANDOM$RANDOM.json"
    python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); v=json.loads(sys.argv[2]); d.pop("background_tasks",None) if v is None else d.__setitem__("background_tasks",v); json.dump(d,open(sys.argv[3],"w"))' "$1" "$2" "$out"
    echo "$out"
}
PASEO=(PASEO_AGENT_ID=test-agent)

# ── blocks on a planted pending task, under Paseo ─────────────────────────────
run "$FX/subagent-pending.json" "${PASEO[@]}"
if [ "$RC" = 2 ] && grep -q "FOREGROUND" <<<"$ERR" && grep -q "subagent a269ed5d7cff7bbc3" <<<"$ERR"; then
    ok "pending subagent under Paseo: exit 2, foreground re-prompt naming the task"
else bad "pending subagent under Paseo: rc=$RC"; fi
if grep -q '"verdict": "BLOCK"' "$STATE/bg-task-guard.jsonl" 2>/dev/null; then ok "BLOCK logged"; else bad "BLOCK not logged"; fi

run "$FX/shell-pending.json" "${PASEO[@]}"
if [ "$RC" = 2 ] && grep -q "sleep 90; echo done" <<<"$ERR"; then ok "pending shell (or Monitor) under Paseo: exit 2, names the command"; else bad "pending shell under Paseo: rc=$RC"; fi

for label in workflow monitor "MCP task" "cloud session"; do
    P="$(with_tasks "$FX/shell-pending.json" "[{\"id\":\"x1\",\"type\":\"$label\",\"status\":\"running\",\"description\":\"d\"}]")"
    run "$P" "${PASEO[@]}"
    if [ "$RC" = 2 ]; then ok "counted type '$label' blocks"; else bad "counted type '$label' did not block: rc=$RC"; fi
done
P="$(with_tasks "$FX/shell-pending.json" '[{"id":"x1","type":"subagent","status":"pending","description":"d"}]')"
run "$P" "${PASEO[@]}"
if [ "$RC" = 2 ]; then ok "status 'pending' blocks"; else bad "status 'pending' did not block: rc=$RC"; fi

# ── passes when nothing of the session's own is in flight ─────────────────────
run "$FX/none-pending.json" "${PASEO[@]}"
if [ "$RC" = 0 ] && [ -z "$ERR" ]; then ok "no background task: allowed, silent"; else bad "no background task: rc=$RC"; fi
P="$(with_tasks "$FX/shell-pending.json" null)"
run "$P" "${PASEO[@]}"
if [ "$RC" = 0 ]; then ok "background_tasks absent (older Claude Code): allowed"; else bad "background_tasks absent: rc=$RC"; fi
for label in teammate dream "auto-mode scan" "memory import" some_future_type; do
    P="$(with_tasks "$FX/shell-pending.json" "[{\"id\":\"x1\",\"type\":\"$label\",\"status\":\"running\",\"description\":\"d\"}]")"
    run "$P" "${PASEO[@]}"
    if [ "$RC" = 0 ]; then ok "harness-internal/unknown type '$label' allowed"; else bad "type '$label' blocked: rc=$RC"; fi
done
for st in completed failed killed stopped; do
    P="$(with_tasks "$FX/shell-pending.json" "[{\"id\":\"x1\",\"type\":\"shell\",\"status\":\"$st\",\"description\":\"d\"}]")"
    run "$P" "${PASEO[@]}"
    if [ "$RC" = 0 ]; then ok "terminal status '$st' allowed"; else bad "terminal status '$st' blocked: rc=$RC"; fi
done

# ── never outside Paseo ───────────────────────────────────────────────────────
for f in subagent-pending shell-pending; do
    run "$FX/$f.json"
    if [ "$RC" = 0 ] && [ -z "$ERR" ]; then ok "$f outside Paseo (PASEO_AGENT_ID unset): allowed"; else bad "$f outside Paseo: rc=$RC"; fi
    run "$FX/$f.json" PASEO_AGENT_ID=
    if [ "$RC" = 0 ]; then ok "$f with PASEO_AGENT_ID empty: allowed"; else bad "$f with PASEO_AGENT_ID empty: rc=$RC"; fi
done
run "$FX/subagent-pending.json" "${PASEO[@]}" BSTACK_BG_TASK_GUARD=0
if [ "$RC" = 0 ]; then ok "BSTACK_BG_TASK_GUARD=0 opts out"; else bad "BSTACK_BG_TASK_GUARD=0 still blocked: rc=$RC"; fi

# ── caps: it can never loop ───────────────────────────────────────────────────
# Loop safety is the guard's own per-task record, not stop_hook_active (which any
# Stop hook's block sets). The real live-run pair: the block on subagent a269...,
# then the continuation's stop (stop_hook_active: true) with a shell the subagent
# left behind. That shell is a task the guard never asked about, so it blocks once.
run "$FX/subagent-pending.json" "${PASEO[@]}"
KEEP=1 run "$FX/shell-pending-hook-active.json" "${PASEO[@]}"
if [ "$RC" = 2 ] && grep -q "buvv3m82b" <<<"$ERR"; then
    ok "the real post-block stop with a NEW leftover shell: blocked once"
else bad "leftover shell in the continuation was allowed: rc=$RC"; fi
KEEP=1 run "$FX/shell-pending-hook-active.json" "${PASEO[@]}"
if [ "$RC" = 0 ] && grep -q '"verdict": "CAP"' "$STATE/bg-task-guard.jsonl"; then
    ok "the same tasks again: allowed and logged CAP (never asks twice about a task)"
else bad "same tasks blocked twice: rc=$RC"; fi

run "$FX/shell-pending-hook-active.json" "${PASEO[@]}"
if [ "$RC" = 2 ]; then ok "stop_hook_active from another hook's block does not disarm the guard"; else bad "stop_hook_active disarmed the guard: rc=$RC"; fi

# A standing monitor kept on purpose: asked about once, then accepted across wakes
# (new prompt_ids), and it does not spend the lifetime budget a real strand needs.
MON='{"id":"bmon1","type":"shell","status":"running","description":"standing monitor","command":"tail -f log"}'
run "$(with_prompt "$(with_tasks "$FX/shell-pending.json" "[$MON]")" w1)" "${PASEO[@]}"
mblocks=0; [ "$RC" = 2 ] && mblocks=1
for i in 2 3 4 5 6 7; do
    KEEP=1 run "$(with_prompt "$(with_tasks "$FX/shell-pending.json" "[$MON]")" "w$i")" "${PASEO[@]}"
    [ "$RC" = 2 ] && mblocks=$((mblocks + 1))
done
KEEP=1 run "$(with_prompt "$(with_tasks "$FX/shell-pending.json" "[$MON,{\"id\":\"arev\",\"type\":\"subagent\",\"status\":\"running\",\"description\":\"P20 reviewer\"}]")" real)" "${PASEO[@]}"
if [ "$mblocks" = 1 ] && [ "$RC" = 2 ]; then
    ok "standing monitor: 1 block over 7 wakes, and a later real strand still blocks"
else bad "standing monitor: $mblocks blocks over 7 wakes, real strand rc=$RC"; fi
caps="$(grep -c '"verdict": "CAP"' "$STATE/bg-task-guard.jsonl")"
if [ "$caps" = 6 ]; then ok "CAP is logged once per prompt (6 wakes, 6 lines)"; else bad "CAP lines: $caps (want 6)"; fi
KEEP=1 run "$(with_prompt "$(with_tasks "$FX/shell-pending.json" "[$MON]")" w7)" "${PASEO[@]}"
if [ "$(grep -c '"verdict": "CAP"' "$STATE/bg-task-guard.jsonl")" = 6 ]; then ok "a repeat stop in the same prompt adds no CAP line"; else bad "CAP log grows within one prompt"; fi

# Consecutive: one prompt whose continuations keep launching new tasks -> 2 blocks.
run "$(with_tasks "$FX/shell-pending.json" '[{"id":"c1","type":"shell","status":"running","description":"d"}]')" "${PASEO[@]}"
cblocks=0; [ "$RC" = 2 ] && cblocks=1
for i in 2 3 4 5; do
    KEEP=1 run "$(with_tasks "$FX/shell-pending.json" "[{\"id\":\"c$i\",\"type\":\"shell\",\"status\":\"running\",\"description\":\"d\"}]")" "${PASEO[@]}"
    [ "$RC" = 2 ] && cblocks=$((cblocks + 1))
done
if [ "$cblocks" = 2 ]; then ok "per-prompt cap: 5 new tasks in one prompt, exactly 2 blocked"; else bad "per-prompt cap: $cblocks of 5 blocked (want 2)"; fi

# Lifetime: 23 prompts each with a new task -> exactly 20 blocked; never resets.
blocks=0; first=1
for i in $(seq 1 23); do
    P="$(with_prompt "$(with_tasks "$FX/shell-pending.json" "[{\"id\":\"t$i\",\"type\":\"shell\",\"status\":\"running\",\"description\":\"d\"}]")" "lp$i")"
    if [ "$first" = 1 ]; then run "$P" "${PASEO[@]}"; first=0; else KEEP=1 run "$P" "${PASEO[@]}"; fi
    [ "$RC" = 2 ] && blocks=$((blocks + 1))
done
if [ "$blocks" = 20 ]; then ok "lifetime cap: 23 prompts with new tasks, exactly 20 blocked"; else bad "lifetime cap: $blocks of 23 blocked (want 20)"; fi

# A task with no id is keyed by type + description: asked about once.
NOID='[{"type":"subagent","status":"running","description":"P20 reviewer"}]'
run "$(with_prompt "$(with_tasks "$FX/shell-pending.json" "$NOID")" n1)" "${PASEO[@]}"
r1=$RC
KEEP=1 run "$(with_prompt "$(with_tasks "$FX/shell-pending.json" "$NOID")" n2)" "${PASEO[@]}"
if [ "$r1" = 2 ] && [ "$RC" = 0 ]; then ok "a task with no id: blocked once, then accepted"; else bad "no-id task: rc $r1 then $RC (want 2 then 0)"; fi

# An empty prompt_id never grows the CAP log.
run "$(with_prompt "$FX/shell-pending.json" "")" "${PASEO[@]}"
for i in 1 2 3 4 5; do KEEP=1 run "$(with_prompt "$FX/shell-pending.json" "")" "${PASEO[@]}"; done
if [ "$(grep -c '"verdict": "CAP"' "$STATE/bg-task-guard.jsonl" 2>/dev/null)" = 0 ]; then ok "empty prompt_id: no CAP log growth"; else bad "empty prompt_id grows the CAP log"; fi

# A hand-corrupted state file (blocked_tasks a string) fails safe.
run "$FX/shell-pending.json" "${PASEO[@]}"
mkdir -p "$STATE/bg-task-guard"
printf '{"blocked_tasks":"bnssbndta","total_blocks":"x"}' > "$STATE/bg-task-guard/a4db1711-2742-4637-be34-d67775aa5397.json"
KEEP=1 run "$(with_prompt "$FX/shell-pending.json" fresh)" "${PASEO[@]}"
if [ "$RC" = 0 ]; then ok "corrupt state (non-int total): fails open"; else bad "corrupt state: rc=$RC"; fi

# ── fail-open ─────────────────────────────────────────────────────────────────
printf 'not json' > "$T/bad.json"
run "$T/bad.json" "${PASEO[@]}"
if [ "$RC" = 0 ]; then ok "malformed input: fails open"; else bad "malformed input: rc=$RC"; fi
printf '[1,2]' > "$T/list.json"
run "$T/list.json" "${PASEO[@]}"
if [ "$RC" = 0 ]; then ok "non-object input: fails open"; else bad "non-object input: rc=$RC"; fi
mkdir -p "$T/ro" && touch "$T/ro/bg-task-guard" && chmod 500 "$T/ro"
ERR="$(env CLAUDE_PLUGIN_ROOT="$ROOT" BROOMVA_AUTONOMOUS_HOME="$T/ro" PASEO_AGENT_ID=x bash -c "${CMD:-exit 0}" < "$FX/shell-pending.json" 2>&1 >/dev/null)"; RC=$?
chmod 700 "$T/ro"
if [ "$RC" = 0 ]; then ok "unwritable state dir: fails open"; else bad "unwritable state dir: rc=$RC"; fi

# ── mutation proof ────────────────────────────────────────────────────────────
if [ -z "$QUIET" ]; then
    echo "  mutation proof:"
    G="hooks/bg-task-stop-guard.py"
    MUTANTS=(
      "paseo-gate|$G|if not env.get(\"PASEO_AGENT_ID\"):|if False:"
      "never-blocks|$G|        return 2|        return 0"
      "type-filter|$G|if t.get(\"type\") not in COUNTED:|if False:"
      "never-in-flight|$G|        out.append(t)|        pass"
      "terminal-filter|$G|if str(t.get(\"status\", \"\")).lower() in TERMINAL:|if False:"
      "task-dedupe|$G|fresh = [t for t in tasks if _task_key(t) not in asked]|fresh = tasks"
      "never-fresh|$G|fresh = [t for t in tasks if _task_key(t) not in asked]|fresh = []"
      "obeys-other-hooks|$G|    sid = re.sub(|    if payload.get(\"stop_hook_active\"):
        return \"ALLOW\", None
    sid = re.sub("
      "cap-log-spam|$G|if pid and state.get(\"last_cap_prompt\") != pid:|if True:"
      "lifetime-cap|$G|LIFE_MAX = 20|LIFE_MAX = 99"
      "prompt-cap|$G|PROMPT_MAX = 2|PROMPT_MAX = 99"
      "id-key|$G|return str(t.get(\"id\") or|return str(__import__(\"time\").time_ns()) or str(t.get(\"id\") or"
      "opt-out|$G|== \"0\":|== \"never\":"
      "fail-closed|$G|    except Exception:
        return 0|    except Exception:
        return 2"
      "unregistered|hooks/hooks.json|bg-task-stop-guard.py|bg-task-stop-guard-gone.py"
    )
    killed=0
    for m in "${MUTANTS[@]}"; do
        IFS='|' read -r name file from to <<<"$m"
        # a two-line mutant: read keeps only the first line; take the rest verbatim
        from="${m#*|*|}"; from="${from%%|*}"; to="${m##*|}"
        MT="$T/mut-$name"; mkdir -p "$MT/hooks"
        cp "$ROOT/hooks/hooks.json" "$ROOT/$G" "$MT/hooks/"
        if ! python3 -I - "$MT/$file" "$from" "$to" <<'PY'
import sys
p, a, b = sys.argv[1:4]
s = open(p).read()
if s.count(a) < 1:
    sys.exit(1)
open(p, "w").write(s.replace(a, b))
PY
        then bad "mutant $name: pattern not found (stale mutant)"; continue; fi
        if BG_GUARD_ROOT="$MT" BG_GUARD_NO_MUTATE=1 bash "$0" >/dev/null 2>&1; then
            bad "mutant $name survived"
        else
            echo "    [killed] $name"; killed=$((killed + 1))
        fi
    done
    if [ "$killed" = "${#MUTANTS[@]}" ]; then ok "mutation: ${killed}/${#MUTANTS[@]} mutants killed"; else bad "mutation: ${killed}/${#MUTANTS[@]} killed"; fi
fi

[ -n "$QUIET" ] || echo "bg-task-stop-guard: $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
