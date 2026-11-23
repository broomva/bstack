#!/bin/bash
# tests/doctor-worktree-entries.test.sh — BRO-4084. The working-tree entry
# ratchet (scripts/doctor-worktree-entries.py, doctor section 33) against
# fixtures only: HOME is a decoy that holds an entry the check would flag, so a
# run that read HOME instead of its fixture home or its options fails here.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT
HOME_FIXTURE="$TMP/home"
TREE="$TMP/tree"
AGENTS="$HOME_FIXTURE/Library/LaunchAgents"
SETTINGS="$HOME_FIXTURE/.claude/settings.json"
BASELINE="$HOME_FIXTURE/.broomva/state/doctor-worktree-entries.json"
mkdir -p "$AGENTS" "$HOME_FIXTURE/.claude" "$HOME_FIXTURE/bin" "$TMP/workspace" "$TMP/decoy-home/Library/LaunchAgents" "$TMP/path-bin"
# A program found on PATH that ends in a script extension is a program name, not a path.
printf '#!/bin/sh\n' > "$TMP/path-bin/onpath.sh"
chmod +x "$TMP/path-bin/onpath.sh"
export PATH="$TMP/path-bin:$PATH"
export HOME="$TMP/decoy-home"
export BSTACK_DOCTOR_HOME="$HOME_FIXTURE" BROOMVA_WORKSPACE="$TMP/workspace"
unset BROOMVA_HOME
git init -q "$TMP/workspace"
DOCTOR="$REPO/scripts/doctor.sh"
ENTRIES="$REPO/scripts/doctor-worktree-entries.py"
run_doctor() { bash "$DOCTOR" --quiet >"$TMP/out" 2>&1; }
run_doctor_verbose() { bash "$DOCTOR" "$@" >"$TMP/out" 2>&1; }
plist() {  # plist DIR NAME ARG... — a LaunchAgent whose ProgramArguments are ARG...
    python3 - "$@" <<'PY_PLIST'
import plistlib, sys
with open(f'{sys.argv[1]}/{sys.argv[2]}.plist', 'wb') as stream:
    plistlib.dump({'Label': sys.argv[2], 'ProgramArguments': sys.argv[3:]}, stream)
PY_PLIST
}

# ── fixtures: a working tree, a linked worktree, a released copy, a plain folder ──
git init -q "$TREE"
mkdir -p "$TREE/sub" "$TMP/plain" "$HOME_FIXTURE/.broomva/releases/tool"
for f in tracked.sh sub/inner.sh sub/rel.py hook.py 'with space.sh' untracked.sh; do echo '#' > "$TREE/$f"; done
git -C "$TREE" add tracked.sh sub/inner.sh sub/rel.py hook.py 'with space.sh'
git -C "$TREE" -c user.name=t -c user.email=t@example.invalid commit -q -m fixture
git -C "$TREE" worktree add -q "$TMP/linked" -b linked >/dev/null 2>&1
[ -f "$TMP/linked/.git" ] || { echo 'fixture: the linked worktree has no .git file' >&2; exit 1; }
echo '#' > "$TMP/plain/released.sh"
echo '#' > "$HOME_FIXTURE/.broomva/releases/tool/run.sh"
ln -s "$TREE/tracked.sh" "$HOME_FIXTURE/bin/linked.sh"
plist "$TMP/decoy-home/Library/LaunchAgents" com.broomva.decoy /bin/bash "$TREE/tracked.sh"

# ── classification, read through the two options (--report: no baseline at all) ──
OPT_AGENTS="$TMP/agents"
OPT_SETTINGS="$TMP/settings.json"
mkdir -p "$OPT_AGENTS"
plist "$OPT_AGENTS" com.broomva.direct /bin/bash "$TREE/tracked.sh" compile
plist "$OPT_AGENTS" com.broomva.shell /bin/bash -lc "export X=1; /bin/bash \"$TREE/sub/inner.sh\" --apply 2>&1 | tee -a log"
plist "$OPT_AGENTS" com.broomva.cd /bin/bash -c "cd $TREE/sub && echo start && /usr/bin/python3 rel.py harvest --days 45"
plist "$OPT_AGENTS" com.broomva.home-link /bin/bash -c "exec \$HOME/bin/linked.sh serve"
plist "$OPT_AGENTS" com.broomva.space "$TREE/with space.sh"
plist "$OPT_AGENTS" com.broomva.linked-worktree /bin/bash "$TMP/linked/tracked.sh"
# These pass: main's copy into a temp file (two spellings), a released copy, a
# plain folder, an untracked file in the tree, a missing file, a directory.
plist "$OPT_AGENTS" com.broomva.gitshow /bin/bash -lc "T=\$(mktemp); git -C \"$TREE\" show origin/main:tracked.sh > \"\$T\" || exit 0; bash \"\$T\"; rm -f \"\$T\""
plist "$OPT_AGENTS" com.broomva.gitshow-cd /bin/bash -lc "cd $TREE && git show origin/main:tracked.sh > /tmp/x.\$\$ && bash /tmp/x.\$\$"
plist "$OPT_AGENTS" com.broomva.released /bin/bash "$HOME_FIXTURE/.broomva/releases/tool/run.sh"
plist "$OPT_AGENTS" com.broomva.plain /bin/bash "$TMP/plain/released.sh"
plist "$OPT_AGENTS" com.broomva.untracked /bin/bash "$TREE/untracked.sh"
plist "$OPT_AGENTS" com.broomva.missing /bin/bash "$TREE/gone.sh"
plist "$OPT_AGENTS" com.broomva.directory /usr/bin/env ls "$TREE/sub"
# Relative script paths: with no directory they are unresolved; a relative `cd`
# from a known WorkingDirectory still resolves.
plist "$OPT_AGENTS" com.broomva.relative /bin/bash -c "./run.sh nightly"
# Not a com.broomva.* agent: out of scope.
plist "$OPT_AGENTS" org.example.other /bin/bash "$TREE/tracked.sh"
python3 - "$OPT_AGENTS" "$OPT_SETTINGS" "$TREE" "$HOME_FIXTURE" <<'PY_FIXTURE'
import json, plistlib, sys
agents, settings, tree, home = sys.argv[1:]
with open(f'{agents}/com.broomva.program.plist', 'wb') as stream:
    plistlib.dump({'Label': 'program', 'Program': f'{tree}/tracked.sh'}, stream)
with open(f'{agents}/com.broomva.cwd.plist', 'wb') as stream:
    plistlib.dump({'Label': 'cwd', 'WorkingDirectory': tree, 'ProgramArguments': ['/bin/sh', 'tracked.sh', 'run']}, stream)
with open(f'{agents}/com.broomva.cwd-shell.plist', 'wb') as stream:
    plistlib.dump({'Label': 'cwd-shell', 'WorkingDirectory': tree,
                   'ProgramArguments': ['/bin/bash', '-c', 'exec ./sub/inner.sh --apply']}, stream)
with open(f'{agents}/com.broomva.cwd-cd.plist', 'wb') as stream:
    plistlib.dump({'Label': 'cwd-cd', 'WorkingDirectory': tree,
                   'ProgramArguments': ['/bin/bash', '-c', 'cd sub && ./inner.sh']}, stream)
command = lambda text: {'type': 'command', 'command': text}
json.dump({'hooks': {
    'PreToolUse': [{'matcher': 'Bash', 'hooks': [command(f'python3 -I {tree}/hook.py pretool --harness=claude')]}],
    'Stop': [{'hooks': [command('CTX_PYTHON=/usr/bin/python3 /bin/sh ~/bin/linked.sh stop 2>/dev/null || true'),
                        command(f'{home}/.broomva/releases/tool/run.sh sweep')]}],
    'SessionStart': [{'hooks': [command('"$CLAUDE_PROJECT_DIR"/tracked.sh'),
                                command('python3 -I /nonexistent/hook.py session-start'),
                                {'type': 'prompt', 'prompt': f'{tree}/tracked.sh'}]}],
    # A hook has no working directory the scanner knows: each relative script path is
    # unresolved (the first is the review's exact string). The second group are a
    # program on PATH, bare program names and arguments: never reported.
    'UserPromptSubmit': [{'hooks': [command("bash -c 'cd scripts && ./doctor.sh'"),
                                    command('./hooks/x.sh'),
                                    command('python3 scripts/y.py --fast')]}],
    'PostToolUse': [{'hooks': [command('onpath.sh stop'), command('bun run dev'),
                               command('git status 2>&1 | tee -a log'), command('python3 -m tool config.json')]}],
}, 'permissions': {'allow': [f'Bash({tree}/tracked.sh)']}}, open(settings, 'w'))
PY_FIXTURE
python3 -I "$ENTRIES" --report --json --launch-agents "$OPT_AGENTS" --settings "$OPT_SETTINGS" >"$TMP/json"
python3 - "$TMP/json" "$TREE" "$TMP/linked" <<'PY_EXPECT'
import json, sys
d = json.load(open(sys.argv[1]))
tree, linked = sys.argv[2:]
want = sorted([
    ('claude-hook:PreToolUse[Bash]', f'{tree}/hook.py', tree),
    ('claude-hook:Stop', '~/bin/linked.sh', tree),
    ('launchd:com.broomva.cd', f'{tree}/sub/rel.py', tree),
    ('launchd:com.broomva.cwd', f'{tree}/tracked.sh', tree),
    ('launchd:com.broomva.cwd-cd', f'{tree}/sub/inner.sh', tree),
    ('launchd:com.broomva.cwd-shell', f'{tree}/sub/inner.sh', tree),
    ('launchd:com.broomva.direct', f'{tree}/tracked.sh', tree),
    ('launchd:com.broomva.home-link', '~/bin/linked.sh', tree),
    ('launchd:com.broomva.linked-worktree', f'{linked}/tracked.sh', linked),
    ('launchd:com.broomva.program', f'{tree}/tracked.sh', tree),
    ('launchd:com.broomva.shell', f'{tree}/sub/inner.sh', tree),
    ('launchd:com.broomva.space', f'{tree}/with space.sh', tree),
])
got = [(item['entry'], item['file'], item['repository']) for item in d['flagged']]
assert got == want, '\n' + '\n'.join(map(str, sorted(set(got) ^ set(want))))
want_unresolved = sorted([
    ('claude-hook:SessionStart', '"$CLAUDE_PROJECT_DIR"/tracked.sh', '$CLAUDE_PROJECT_DIR/tracked.sh'),
    ('claude-hook:UserPromptSubmit', "bash -c 'cd scripts && ./doctor.sh'", './doctor.sh'),
    ('claude-hook:UserPromptSubmit', './hooks/x.sh', './hooks/x.sh'),
    ('claude-hook:UserPromptSubmit', 'python3 scripts/y.py --fast', 'scripts/y.py'),
    ('launchd:com.broomva.relative', './run.sh nightly', './run.sh'),
])
got_unresolved = [(item['entry'], item['command'], item['word']) for item in d['unresolved']]
assert got_unresolved == want_unresolved, '\n' + '\n'.join(map(str, sorted(set(got_unresolved) ^ set(want_unresolved))))
assert d == {'schema': 1, 'mode': 'report', 'baseline_recorded_at': None, 'flagged': d['flagged'],
             'unresolved': d['unresolved'], 'grandfathered': None, 'error': None}, d
PY_EXPECT
python3 -I "$ENTRIES" --report --launch-agents "$OPT_AGENTS" --settings "$OPT_SETTINGS" >"$TMP/out"
grep -Fxq "[info] launchd:com.broomva.direct runs $TREE/tracked.sh (repository $TREE)" "$TMP/out"
grep -Fxq '[info] 12 entries run a file from a git working tree (report only: no baseline read or written)' "$TMP/out"
grep -Fxq '[info] 5 script paths could not be resolved' "$TMP/out"
grep -Fxq "[info] unresolved: claude-hook:UserPromptSubmit runs \`./doctor.sh\` with no directory to resolve it against, in: bash -c 'cd scripts && ./doctor.sh'" "$TMP/out"
# Without the options the fixture home is read (empty so far), never HOME's decoy.
python3 -I "$ENTRIES" --report >"$TMP/out"
grep -Fxq '[info] 0 entries run a file from a git working tree (report only: no baseline read or written)' "$TMP/out"
if python3 -I "$ENTRIES" --report --baseline-entries >"$TMP/out" 2>&1; then echo '--report accepted a baseline write' >&2; exit 1; fi
[ ! -e "$HOME_FIXTURE/.broomva/state" ] || { echo '--report wrote under the machine state path' >&2; exit 1; }
[ ! -e "$HOME/.broomva" ] || { echo 'the check wrote under HOME' >&2; exit 1; }

# ── the ratchet through doctor, on the fixture home's own two paths ──
# Without a baseline the ratchet is off: one [info] line, never a gap.
assert_not_enabled() {
    grep -Fq '[info] working-tree entry ratchet is not enabled on this machine' "$TMP/out"
    if grep -Fq '[gap] working-tree entry' "$TMP/out"; then echo "missing baseline counted as a gap ($1)" >&2; exit 1; fi
    if grep -Fq '[failure]' "$TMP/out"; then echo "missing baseline reported a failure ($1)" >&2; exit 1; fi
    listed="$(grep -c '^  \[gap\]' "$TMP/out" || true)"
    total="$(sed -n 's/.* \([0-9][0-9]*\) gap(s).*/\1/p' "$TMP/out")"
    [ "${total:-0}" = "$listed" ] || { echo "gap total $total differs from $listed listed gaps ($1)" >&2; exit 1; }
}
run_doctor_verbose
assert_not_enabled 'empty home'
run_doctor
if grep -q 'working-tree entry' "$TMP/out"; then echo 'quiet mode mentioned a ratchet that is off' >&2; exit 1; fi
mkdir -p "$TMP/no-python"
printf '#!/bin/sh\nexit 127\n' > "$TMP/no-python/python3"
chmod +x "$TMP/no-python/python3"
PATH="$TMP/no-python:$PATH" run_doctor_verbose
assert_not_enabled 'no python'
plist "$AGENTS" com.broomva.existing /bin/bash "$TREE/tracked.sh"
python3 - "$SETTINGS" "$TREE" <<'PY_SETTINGS'
import json, sys
json.dump({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': f'python3 -I {sys.argv[2]}/hook.py stop'}]}]}},
          open(sys.argv[1], 'w'))
PY_SETTINGS
run_doctor_verbose
assert_not_enabled 'entries present'
run_doctor_verbose --strict || true
assert_not_enabled 'strict'
bash "$DOCTOR" --baseline-entries >"$TMP/out"
grep -Fq "launchd:com.broomva.existing runs $TREE/tracked.sh (repository $TREE)" "$TMP/out"
grep -Fq "claude-hook:Stop runs $TREE/hook.py (repository $TREE)" "$TMP/out"
if grep -q 'decoy' "$TMP/out" "$BASELINE"; then echo 'the baseline read HOME, not the fixture home' >&2; exit 1; fi
run_doctor_verbose
grep -Fq "[info] grandfathered entry: launchd:com.broomva.existing runs $TREE/tracked.sh (repository $TREE)" "$TMP/out"
# A passing ratchet prints nothing under --quiet.
run_doctor
if grep -q 'grandfathered\|working-tree entry' "$TMP/out"; then echo 'quiet mode listed grandfathered entries' >&2; exit 1; fi
if PATH="$TMP/no-python:$PATH" run_doctor; then echo 'enabled ratchet without python passed' >&2; exit 1; fi
# A new entry fails doctor and names the entry, the file and the repository.
plist "$AGENTS" com.broomva.new /bin/bash -lc "/bin/bash $TREE/sub/inner.sh --apply"
if run_doctor; then echo 'new launchd entry passed' >&2; exit 1; fi
grep -Fq "[failure] new entry runs a file from a git working tree: launchd:com.broomva.new runs $TREE/sub/inner.sh (repository $TREE)" "$TMP/out"
grep -Fq '[gap] working-tree entry ratchet failed' "$TMP/out"
if grep -Fq '[failure] new entry runs a file from a git working tree: launchd:com.broomva.existing' "$TMP/out"; then echo 'a grandfathered entry failed' >&2; exit 1; fi
if bash "$DOCTOR" --baseline-entries >"$TMP/out" 2>&1; then echo 'baseline overwrite passed' >&2; exit 1; fi
if run_doctor; then echo 'baseline was widened' >&2; exit 1; fi
# The negative control removes the comparison in an isolated copy.
mkdir -p "$TMP/copy/scripts"
cp "$ENTRIES" "$REPO/scripts/broomva_paths.py" "$TMP/copy/scripts/"
python3 - "$TMP/copy/scripts/doctor-worktree-entries.py" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
assert s.count('flagged - saved') == 1
open(p, 'w').write(s.replace('flagged - saved', 'flagged - flagged'))
PY
python3 -I "$TMP/copy/scripts/doctor-worktree-entries.py" >"$TMP/out"
# Repointed at main's copy, then at a released copy, the same entry passes.
plist "$AGENTS" com.broomva.new /bin/bash -lc "T=\$(mktemp); git -C \"$TREE\" show origin/main:sub/inner.sh > \"\$T\" && bash \"\$T\""
run_doctor
plist "$AGENTS" com.broomva.new /bin/bash "$HOME_FIXTURE/.broomva/releases/tool/run.sh"
run_doctor
rm "$AGENTS/com.broomva.new.plist"
# A new hook fails the same way.
python3 - "$SETTINGS" "$TREE" <<'PY_SETTINGS'
import json, sys
d = json.load(open(sys.argv[1]))
d['hooks']['PreToolUse'] = [{'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': f'{sys.argv[2]}/tracked.sh gate'}]}]
json.dump(d, open(sys.argv[1], 'w'))
PY_SETTINGS
if run_doctor; then echo 'new hook entry passed' >&2; exit 1; fi
grep -Fq "claude-hook:PreToolUse[Bash] runs $TREE/tracked.sh (repository $TREE)" "$TMP/out"
# A grandfathered entry that leaves is information; the baseline can be re-recorded.
rm "$AGENTS/com.broomva.existing.plist"
if run_doctor; then echo 'new hook entry passed after a removal' >&2; exit 1; fi
grep -Fq '[info] baselined entry no longer runs from a working tree: launchd:com.broomva.existing' "$TMP/out"
bash "$DOCTOR" --baseline-entries --force >"$TMP/out"
run_doctor
# A grandfathered entry repointed at another tracked file is a new entry.
python3 - "$SETTINGS" "$TREE" <<'PY_SETTINGS'
import json, sys
d = json.load(open(sys.argv[1]))
d['hooks']['PreToolUse'][0]['hooks'][0]['command'] = f'{sys.argv[2]}/sub/inner.sh gate'
json.dump(d, open(sys.argv[1], 'w'))
PY_SETTINGS
if run_doctor; then echo 'a repointed grandfathered entry passed' >&2; exit 1; fi
grep -Fq "claude-hook:PreToolUse[Bash] runs $TREE/sub/inner.sh (repository $TREE)" "$TMP/out"
bash "$DOCTOR" --baseline-entries --force >"$TMP/out"
run_doctor

# ── --json: one object, the exit code of the human run, null lists on an error ──
json_run() {  # json_run EXPECTED_EXIT PYTHON_ASSERTION
    set +e
    python3 -I "$ENTRIES" --json >"$TMP/json" 2>"$TMP/json.err"
    json_rc=$?
    python3 -I "$ENTRIES" >/dev/null 2>&1
    human_rc=$?
    set -e
    [ "$json_rc" = "$1" ] || { echo "--json exited $json_rc, wanted $1 ($2)" >&2; exit 1; }
    [ "$json_rc" = "$human_rc" ] || { echo "--json exited $json_rc, the human run $human_rc ($2)" >&2; exit 1; }
    [ ! -s "$TMP/json.err" ] || { echo "--json wrote to stderr ($2)" >&2; cat "$TMP/json.err" >&2; exit 1; }
    [ "$(wc -l <"$TMP/json" | tr -d ' ')" = "1" ] || { echo "--json did not print exactly one line ($2)" >&2; exit 1; }
    python3 - "$TMP/json" "$2" "$BASELINE" "$TREE" <<'PY_JSON'
import json, sys
d = json.load(open(sys.argv[1]))
baseline, tree = sys.argv[3:]
assert sorted(d) == ['baseline_recorded_at', 'error', 'flagged', 'grandfathered', 'mode', 'schema', 'unresolved'], sorted(d)
assert d['schema'] == 1 and d['mode'] == 'check', d
assert eval(sys.argv[2]), (sys.argv[2], d)
PY_JSON
}
unreadable="d['error'] and d['flagged'] is None and d['unresolved'] is None and d['grandfathered'] is None and d['baseline_recorded_at'] is None"
json_run 0 "d['error'] is None and d['flagged'] == [] and d['unresolved'] == [] and d['grandfathered'] == 2 and d['baseline_recorded_at'] == json.load(open(baseline))['recorded_at']"
plist "$AGENTS" com.broomva.new /bin/bash "$TREE/tracked.sh"
json_run 1 "d['error'] is None and d['grandfathered'] == 2 and d['flagged'] == [{'entry': 'launchd:com.broomva.new', 'file': tree + '/tracked.sh', 'repository': tree}]"
rm "$AGENTS/com.broomva.new.plist"
# "grandfathered" counts the baselined entries still flagged, not the baseline's size.
cp "$SETTINGS" "$TMP/settings.good"
python3 - "$SETTINGS" <<'PY_SETTINGS'
import json, sys
d = json.load(open(sys.argv[1]))
del d['hooks']['PreToolUse']
json.dump(d, open(sys.argv[1], 'w'))
PY_SETTINGS
json_run 0 "d['error'] is None and d['flagged'] == [] and d['grandfathered'] == 1 and len(json.load(open(baseline))['entries']) == 2"
cp "$TMP/settings.good" "$SETTINGS"
# An unresolved script path ratchets like a flagged entry: new fails, recorded is
# grandfathered (info), and one that leaves is information.
python3 - "$SETTINGS" <<'PY_SETTINGS'
import json, sys
d = json.load(open(sys.argv[1]))
d['hooks']['Stop'][0]['hooks'].append({'type': 'command', 'command': "bash -c 'cd scripts && ./doctor.sh'"})
json.dump(d, open(sys.argv[1], 'w'))
PY_SETTINGS
json_run 1 "d['error'] is None and d['flagged'] == [] and d['grandfathered'] == 2 and d['unresolved'] == [{'entry': 'claude-hook:Stop', 'command': \"bash -c 'cd scripts && ./doctor.sh'\", 'word': './doctor.sh'}]"
if run_doctor; then echo 'a new unresolved hook passed' >&2; exit 1; fi
grep -Fq '[failure] new entry runs a script path the check cannot resolve: claude-hook:Stop runs `./doctor.sh`' "$TMP/out"
# The negative control: without the unresolved comparison the same run passes.
python3 - "$TMP/copy/scripts/doctor-worktree-entries.py" "$ENTRIES" <<'PY_MUTATE'
import sys
s = open(sys.argv[2]).read()
assert s.count('unresolved - saved_unresolved') == 1
open(sys.argv[1], 'w').write(s.replace('unresolved - saved_unresolved', 'unresolved - unresolved'))
PY_MUTATE
python3 -I "$TMP/copy/scripts/doctor-worktree-entries.py" >"$TMP/out"
bash "$DOCTOR" --baseline-entries --force >"$TMP/out"
grep -Fq 'unresolved: claude-hook:Stop runs `./doctor.sh`' "$TMP/out"
json_run 0 "d['error'] is None and d['flagged'] == [] and d['unresolved'] == [] and d['grandfathered'] == 3"
run_doctor_verbose
grep -Fq '[info] grandfathered unresolved entry: claude-hook:Stop runs `./doctor.sh`' "$TMP/out"
cp "$TMP/settings.good" "$SETTINGS"
run_doctor_verbose
grep -Fq '[info] baselined unresolved entry is gone: claude-hook:Stop runs `./doctor.sh`' "$TMP/out"
bash "$DOCTOR" --baseline-entries --force >"$TMP/out"
# An input that cannot be read is a failure, never "nothing flagged".
printf 'not a plist' > "$AGENTS/com.broomva.broken.plist"
json_run 1 "$unreadable and 'com.broomva.broken.plist' in d['error']"
if run_doctor; then echo 'unreadable plist passed' >&2; exit 1; fi
grep -q 'working-tree entry check: cannot read' "$TMP/out"
rm "$AGENTS/com.broomva.broken.plist"
printf '{bad json' > "$SETTINGS"
json_run 1 "$unreadable and 'settings.json' in d['error']"
if run_doctor; then echo 'unreadable settings passed' >&2; exit 1; fi
cp "$TMP/settings.good" "$SETTINGS"
# git that cannot answer for a file is a failure, never "untracked".
mkdir -p "$TMP/broken-tree"
printf 'not a git dir\n' > "$TMP/broken-tree/.git"
echo '#' > "$TMP/broken-tree/run.sh"
plist "$AGENTS" com.broomva.unknown /bin/bash "$TMP/broken-tree/run.sh"
json_run 1 "$unreadable and 'git ls-files failed' in d['error']"
if run_doctor; then echo 'a file git could not classify passed' >&2; exit 1; fi
rm "$AGENTS/com.broomva.unknown.plist"
run_doctor
# A missing or broken baseline.
cp "$BASELINE" "$TMP/baseline.good"
printf '{bad json' > "$BASELINE"
json_run 1 "$unreadable and 'working-tree entry check' in d['error']"
if run_doctor; then echo 'malformed baseline passed' >&2; exit 1; fi
grep -q 'working-tree entry check' "$TMP/out"
printf '{"recorded_at": "x", "entries": [{"entry": "e"}]}' > "$BASELINE"
json_run 1 "$unreadable and 'invalid baseline format' in d['error']"
rm "$BASELINE"
ln -s "$TMP/missing-baseline" "$BASELINE"
json_run 1 "$unreadable and 'working-tree entry check' in d['error']"
if run_doctor; then echo 'dangling baseline passed' >&2; exit 1; fi
grep -q 'working-tree entry check' "$TMP/out"
rm "$BASELINE"
json_run 42 "$unreadable and 'no baseline recorded' in d['error']"
[ ! -e "$HOME/.broomva" ] || { echo 'the check wrote under HOME' >&2; exit 1; }
echo 'working-tree entry ratchet: passed'
