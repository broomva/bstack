#!/usr/bin/env bash
# autonomous-arc.sh — session-scoped arc-state helper (BRO-1700 loop-stall
# rejection). One JSON file per session at
#   $BROOMVA_AUTONOMOUS_HOME/<session_id>.arc
# holds the "autonomous arc" posture the loop-stall hooks read. This one file IS
# the shared substrate; every hook keys off the session_id it receives on stdin,
# so posture never leaks across sessions.
#
# Consumers:
#   autonomous-posture-hook.sh   (UserPromptSubmit) — sets the arc on /autonomous,
#                                 re-stamps sticky posture while it is active
#   arc-continuation-hook.sh     (Stop) — blocks a no-op mid-arc terminal
#   limit-stall-resume-hook.sh   (Stop) — logs a rate-limit stall candidate
#
# Subcommands (session_id is always the first positional after the verb):
#   set    <sid> <slug> [milestone ...]   create/refresh an ACTIVE arc (resets counters)
#   next   <sid>                          first milestone whose status != done ("" if none)
#   complete <sid>                        mark arc inactive — THE complete-sentinel
#   status <sid>                          "active <slug>" | "inactive"
#   active <sid>                          exit 0 if an active arc exists, else exit 1
#   get    <sid> <field>                  print a scalar field (e.g. reconcile_count)
#   bump   <sid> <resume_count|reconcile_count>   increment + print the new value
#
# Env:
#   BROOMVA_AUTONOMOUS_HOME   arc-file dir (default ~/.config/broomva/autonomous)
set -uo pipefail

HOME_DIR="${BROOMVA_AUTONOMOUS_HOME:-$HOME/.config/broomva/autonomous}"
VERB="${1:-}"
SID="${2:-}"

command -v python3 >/dev/null 2>&1 || { echo "autonomous-arc: python3 required" >&2; exit 3; }
[ -n "$VERB" ] || { echo "usage: autonomous-arc.sh set|next|complete|status|active|get|bump <session_id> ..." >&2; exit 2; }
[ -n "$SID" ]  || { echo "autonomous-arc: session_id required" >&2; exit 2; }

# sanitize session_id → filename (only word chars, dot, dash)
SAFE_SID="$(printf '%s' "$SID" | tr -c 'A-Za-z0-9._-' '_')"
ARC="$HOME_DIR/$SAFE_SID.arc"

shift 2 2>/dev/null || true

python3 - "$VERB" "$ARC" "$@" <<'PY'
import sys, json, os, datetime

verb, arc_path, rest = sys.argv[1], sys.argv[2], sys.argv[3:]

def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()

def load():
    try:
        with open(arc_path) as f:
            return json.load(f)
    except Exception:
        return {}

def save(d):
    os.makedirs(os.path.dirname(arc_path), exist_ok=True)
    tmp = arc_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, arc_path)

if verb == "set":
    slug = rest[0] if rest else "arc"
    milestones = [{"slice": m, "status": "todo"} for m in rest[1:]]
    save({
        "active": True,
        "slug": slug,
        "invoked_at": now(),
        "milestones": milestones,
        "resume_count": 0,
        "reconcile_count": 0,
        "last_reconcile": None,
    })
    print(f"active {slug}")

elif verb == "complete":
    d = load()
    d["active"] = False
    d["completed_at"] = now()
    save(d)
    print("inactive")

elif verb == "status":
    d = load()
    print(f"active {d.get('slug','')}" if d.get("active") else "inactive")

elif verb == "active":
    sys.exit(0 if load().get("active") else 1)

elif verb == "next":
    for m in load().get("milestones", []):
        if m.get("status") != "done":
            print(m.get("slice", ""))
            break
    else:
        print("")

elif verb == "get":
    field = rest[0] if rest else ""
    print(load().get(field, ""))

elif verb == "bump":
    counter = rest[0] if rest and rest[0] in ("resume_count", "reconcile_count") else "reconcile_count"
    d = load()
    d[counter] = int(d.get(counter, 0)) + 1
    if counter == "reconcile_count":
        d["last_reconcile"] = now()
    save(d)
    print(d[counter])

else:
    print(f"autonomous-arc: unknown verb {verb!r}", file=sys.stderr)
    sys.exit(2)
PY
