#!/usr/bin/env bash
# tests/hook-python-isolation-mutation.test.sh — every -I is load-bearing (BRO-2652).
#
# For EACH isolated python site a hook can reach (tests/hook_python_sites.py), copy
# the plugin tree, remove `-I` from exactly that one site, and run
# tests/hook-python-isolation.test.sh against the copy with the static check OFF and
# the PYTHONPATH vector OFF. It must go RED: the cwd / script-dir plants alone have to
# kill the mutant through a real hook event. The static check must ALSO flag the
# mutant on its own, so each site is covered twice, independently.
#
# Plus the two supporting changes, which -I alone would make regress:
#   sibling   test_lock.py back to `from git_env_policy import ...`: under -I the
#             hook would crash on import and stop blocking.
#   usersite  a sensor stops re-adding the user site: under -I a `pip install
#             --user` PyYAML vanishes and the policy degrades. Skipped where PyYAML
#             is importable under -I anyway (then this mutant is not observable).
#
# Mutants run 4 at a time; each suite run owns its own temp tree.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUITE="$REPO/tests/hook-python-isolation.test.sh"
SITES_PY="$REPO/tests/hook_python_sites.py"

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/neutral" "$T/m" "$T/log"
hpy() { ( cd "$T/neutral" && python3 -I "$@" ); }

pass=0; fail=0
ok()  { echo "  [pass] $1"; pass=$((pass + 1)); }
bad() { echo "  [FAIL] $1"; fail=$((fail + 1)); }

hpy "$SITES_PY" "$REPO" sites | awk -F'\t' '$3 == 1 { print $1 "\t" $2 }' > "$T/sites.tsv"
N="$(wc -l < "$T/sites.tsv" | tr -d ' ')"
echo "hook python isolation — mutation proof over $N isolated sites"
[ "$N" -ge 12 ] || bad "only $N isolated sites enumerated (expected >= 12)"

cat > "$T/mutate.py" <<'PY'
import sys
root, rel, line, mode = sys.argv[1], sys.argv[2], int(sys.argv[3] or 0), sys.argv[4]
p = f"{root}/{rel}"
text = open(p).read()
if mode == "site":
    lines = text.split("\n")
    before = lines[line - 1]
    after = before.replace("python3 -I ", "python3 ", 1)
    if after == before:
        sys.exit(f"mutate: no 'python3 -I ' on {rel}:{line}")
    lines[line - 1] = after
    text = "\n".join(lines)
elif mode == "sibling":
    start = text.index("if __package__:  # imported as scripts.test_lock")
    end = text.index("    git_var_allowed = _gep.git_var_allowed\n") + len("    git_var_allowed = _gep.git_var_allowed\n")
    text = text[:start] + (
        "try:\n    from git_env_policy import git_var_allowed\n"
        "except ImportError:\n    from scripts.git_env_policy import git_var_allowed\n") + text[end:]
elif mode == "usersite":
    old = "            sys.path.append(user_site)\n"
    if text.count(old) != 1:
        sys.exit(f"mutate: user-site append not found in {rel}")
    text = text.replace(old, "            pass\n")
else:
    sys.exit(f"mutate: unknown mode {mode}")
open(p, "w").write(text)
PY

# make_mutant ID REL LINE MODE — a copy of the plugin tree with one change
make_mutant() {
    local d="$T/m/$1"
    mkdir -p "$d"
    cp -R "$REPO/scripts" "$REPO/hooks" "$REPO/bin" "$d/"
    hpy "$T/mutate.py" "$d" "$2" "$3" "$4" || return 1
    local changed
    changed="$(diff -r "$REPO/scripts" "$d/scripts"; diff -r "$REPO/hooks" "$d/hooks")"
    [ -n "$changed" ] || { echo "mutant $1 is identical to the source" >&2; return 1; }
}

# run_mutant ID — the suite against the mutant; record exit + the failing lines
run_mutant() {
    ( HOOK_ISO_PLUGIN_SRC="$T/m/$1" HOOK_ISO_SKIP_STATIC=1 HOOK_ISO_NO_PYTHONPATH=1 \
        bash "$SUITE" > "$T/log/$1.out" 2>&1; echo "$?" > "$T/log/$1.rc" )
}

IDS=""
i=0
while IFS="$(printf '\t')" read -r rel line; do
    i=$((i + 1)); id="site$i"
    printf '%s\t%s:%s\n' "$id" "$rel" "$line" >> "$T/labels.tsv"
    make_mutant "$id" "$rel" "$line" site || { bad "could not build mutant for $rel:$line"; continue; }
    IDS="$IDS $id"
done < "$T/sites.tsv"

make_mutant sibling scripts/test_lock.py 0 sibling && IDS="$IDS sibling" \
    && printf 'sibling\tscripts/test_lock.py: sibling import back on sys.path\n' >> "$T/labels.tsv"
if ( cd "$T/neutral" && python3 -I -c 'import yaml' >/dev/null 2>&1 ); then
    echo "  [skip] usersite mutants: PyYAML imports under -I without the user site here, so dropping the re-add is not observable"
elif ! ( cd "$T/neutral" && python3 -c 'import yaml' >/dev/null 2>&1 ); then
    echo "  [skip] usersite mutants: PyYAML is not installed for this python3"
else
    for s in leverage-sensor leverage-ship-sensor; do
        make_mutant "usersite-$s" "scripts/$s.py" 0 usersite && IDS="$IDS usersite-$s" \
            && printf 'usersite-%s\tscripts/%s.py: user site not re-added\n' "$s" "$s" >> "$T/labels.tsv"
    done
fi

# 4 at a time (bash 3.2 has no `wait -n`).
batch=""
for id in $IDS; do
    run_mutant "$id" &
    batch="$batch $!"
    if [ "$(echo $batch | wc -w)" -ge 4 ]; then wait $batch; batch=""; fi
done
[ -n "$batch" ] && wait $batch

echo "  mutant                          dynamic (suite)          static (hook_python_sites check)"
for id in $IDS; do
    label="$(awk -F'\t' -v k="$id" '$1 == k { print $2 }' "$T/labels.tsv")"
    rc="$(cat "$T/log/$id.rc" 2>/dev/null || echo missing)"
    killed="$(grep -E '^[[:space:]]+\[FAIL\]' "$T/log/$id.out" | sed -E 's/^[[:space:]]+\[FAIL\] //; s/: output changed with plants present//; s/ —.*//' | cut -c1-60 | tr '\n' ';' | sed 's/;$//')"
    static="n/a"
    case "$id" in
        site*)
            if hpy "$SITES_PY" "$T/m/$id" check >/dev/null 2>&1; then static="SURVIVED"; else static="RED"; fi ;;
    esac
    if [ "$rc" != 0 ] && [ "$rc" != missing ]; then
        dyn="RED"
    else
        dyn="SURVIVED"
    fi
    echo "  - $label"
    echo "      dynamic: $dyn (exit $rc) — $killed"
    echo "      static:  $static"
    if [ "$dyn" = RED ] && [ "$static" != SURVIVED ]; then
        ok "$label: killed"
    else
        bad "$label: mutant survived (dynamic=$dyn static=$static)"
        sed 's/^/        /' "$T/log/$id.out" | tail -20
    fi
done

echo ""
echo "hook-python-isolation-mutation: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
