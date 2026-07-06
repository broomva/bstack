#!/usr/bin/env bash
# autonomous-arc.sh — session-scoped arc-state helper (BRO-1700 loop-stall
# rejection). One JSON file per session at
#   $BROOMVA_AUTONOMOUS_HOME/<session_id>.arc
# holds the "autonomous arc" posture the loop-stall hooks read. This one file IS
# the shared substrate; every hook keys off the session_id it receives on stdin,
# so posture never leaks across sessions.
#
# Consumers:
#   autonomous-posture-hook.sh   (UserPromptSubmit) — sets the arc on /autonomous
#                                 (only if none is active), re-stamps sticky posture
#   arc-continuation-hook.sh     (Stop) — blocks a no-op mid-arc terminal
#
# Subcommands (session_id is always the first positional after the verb):
#   set    <sid> <slug> [milestone ...]   create/refresh an ACTIVE arc (counters=0)
#   next   <sid>                          first milestone whose status != done ("" if none)
#   complete <sid>                        mark arc inactive — THE complete-sentinel
#   status <sid>                          "active <slug>" | "inactive"
#   active <sid>                          exit 0 if an active, non-stale arc exists, else 1
#   get    <sid> <field>                  print a scalar field (e.g. reconcile_count)
#   bump   <sid> reconcile_count          increment + print the new value
#   reset  <sid> reconcile_count          set to 0 (called on a productive turn so the
#                                         consecutive cap bounds CONSECUTIVE stalls)
#   try-block <sid> <consec_max> <life_max>   atomic runaway guard: prints BLOCK and
#                                         increments reconcile_count + total_blocks iff
#                                         reconcile_count<consec_max AND total_blocks<life_max;
#                                         else prints CAP. total_blocks NEVER resets — a
#                                         lifetime ceiling so an interleaved-tool loop
#                                         (which resets reconcile_count) still terminates.
#
# Concurrency: writes are serialized with an flock on <arc>.lock and land via an
# atomic mkstemp+os.replace, so a Stop-hook bump and a UserPromptSubmit set can
# never interleave into a corrupt/lost-update arc.
#
# Env:
#   BROOMVA_AUTONOMOUS_HOME     arc-file dir (default ~/.config/broomva/autonomous)
#   BROOMVA_ARC_STALE_SECONDS   auto-expiry window (default 28800 = 8h); an arc older
#                               than this reads as inactive (backstop so a never-completed
#                               arc cannot fight the user forever)
set -uo pipefail

HOME_DIR="${BROOMVA_AUTONOMOUS_HOME:-$HOME/.config/broomva/autonomous}"
VERB="${1:-}"
SID="${2:-}"

command -v python3 >/dev/null 2>&1 || { echo "autonomous-arc: python3 required" >&2; exit 3; }
[ -n "$VERB" ] || { echo "usage: autonomous-arc.sh set|next|complete|status|active|get|bump|reset <session_id> ..." >&2; exit 2; }
[ -n "$SID" ]  || { echo "autonomous-arc: session_id required" >&2; exit 2; }

# sanitize session_id → filename (only word chars, dot, dash)
SAFE_SID="$(printf '%s' "$SID" | tr -c 'A-Za-z0-9._-' '_')"
ARC="$HOME_DIR/$SAFE_SID.arc"

shift 2 2>/dev/null || true

STALE_SECONDS="${BROOMVA_ARC_STALE_SECONDS:-28800}" python3 - "$VERB" "$ARC" "$@" <<'PY'
import sys, json, os, time, datetime, tempfile, fcntl

verb, arc_path, rest = sys.argv[1], sys.argv[2], sys.argv[3:]
STALE = int(os.environ.get("STALE_SECONDS", "28800") or 28800)

def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()

def load():
    try:
        with open(arc_path) as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        # a corrupt/half-written file: do NOT fabricate state. Callers that mutate
        # run under flock (so this should not happen); a reader treats it as "no arc".
        return {}

def is_stale(d):
    ts = d.get("invoked_at")
    if not ts:
        return False
    try:
        t = datetime.datetime.fromisoformat(ts)
        age = time.time() - t.timestamp()
        return age > STALE
    except Exception:
        return False

def is_active(d):
    return bool(d.get("active")) and not is_stale(d)

def save_atomic(d):
    os.makedirs(os.path.dirname(arc_path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(arc_path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, arc_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def with_lock(mutate):
    """Serialize read-modify-write across concurrent hooks via flock on a sidecar."""
    os.makedirs(os.path.dirname(arc_path), exist_ok=True)
    lock_path = arc_path + ".lock"
    with open(lock_path, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        d = load()
        out = mutate(d)
        save_atomic(d)
        return out

if verb == "set":
    slug = rest[0] if rest else "arc"
    milestones = [{"slice": m, "status": "todo"} for m in rest[1:]]
    def _set(d):
        d.clear()
        d.update({
            "active": True, "slug": slug, "invoked_at": now_iso(),
            "milestones": milestones, "reconcile_count": 0, "total_blocks": 0,
            "last_reconcile": None,
        })
        return f"active {slug}"
    print(with_lock(_set))

elif verb == "complete":
    def _c(d):
        d["active"] = False
        d["completed_at"] = now_iso()
        return "inactive"
    print(with_lock(_c))

elif verb == "status":
    d = load()
    print(f"active {d.get('slug','')}" if is_active(d) else "inactive")

elif verb == "active":
    sys.exit(0 if is_active(load()) else 1)

elif verb == "next":
    for m in load().get("milestones", []):
        if m.get("status") != "done":
            print(m.get("slice", "")); break
    else:
        print("")

elif verb == "get":
    print(load().get(rest[0], "") if rest else "")

elif verb == "bump":
    def _b(d):
        d["reconcile_count"] = int(d.get("reconcile_count", 0)) + 1
        d["last_reconcile"] = now_iso()
        return d["reconcile_count"]
    print(with_lock(_b))

elif verb == "reset":
    def _r(d):
        d["reconcile_count"] = 0
        return 0
    print(with_lock(_r))

elif verb == "try-block":
    consec_max = int(rest[0]) if rest and rest[0].isdigit() else 2
    life_max = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else 5
    def _tb(d):
        rc = int(d.get("reconcile_count", 0))
        tb = int(d.get("total_blocks", 0))
        if rc < consec_max and tb < life_max:
            d["reconcile_count"] = rc + 1
            d["total_blocks"] = tb + 1
            d["last_reconcile"] = now_iso()
            return "BLOCK"
        return "CAP"
    print(with_lock(_tb))

else:
    print(f"autonomous-arc: unknown verb {verb!r}", file=sys.stderr)
    sys.exit(2)
PY
