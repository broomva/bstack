#!/bin/bash
# tests/doctor-worktree-entries.test.sh — BRO-4084. The working-tree entry
# ratchet (scripts/doctor-worktree-entries.py, doctor section 33) against
# fixtures only: HOME is a decoy that holds an entry the check would flag, so a
# run that read HOME instead of its fixture home or its options fails here.
#
# The check reads raw text and never interprets shell. The word table pins the
# class of each word in the command shapes three review rounds of the earlier,
# shell-parsing version got wrong; the property test generates wrappers and
# quotings around tracked files and requires every one to be flagged, so the
# class "a shape hides the path" is tested, not single cases.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT
HOME_FIXTURE="$TMP/home"
TREE="$TMP/tree"
AGENTS="$HOME_FIXTURE/Library/LaunchAgents"
SETTINGS="$HOME_FIXTURE/.claude/settings.json"
BASELINE="$HOME_FIXTURE/.broomva/state/doctor-worktree-entries.json"
mkdir -p "$AGENTS" "$HOME_FIXTURE/.claude" "$HOME_FIXTURE/bin" "$TMP/workspace" "$TMP/decoy-home/Library/LaunchAgents"
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
mkdir -p "$TREE/sub" "$TREE/my dir" "$TMP/plain" "$HOME_FIXTURE/.broomva/releases/tool"
for f in tracked.sh sub/inner.sh sub/rel.py hook.py x.py notes.md 'with space.sh' 'my dir/x.sh' untracked.sh; do
    echo '#' > "$TREE/$f"
done
git -C "$TREE" add tracked.sh sub/inner.sh sub/rel.py hook.py x.py notes.md 'with space.sh' 'my dir/x.sh'
git -C "$TREE" -c user.name=t -c user.email=t@example.invalid commit -q -m fixture
git -C "$TREE" worktree add -q "$TMP/linked" -b linked >/dev/null 2>&1
[ -f "$TMP/linked/.git" ] || { echo 'fixture: the linked worktree has no .git file' >&2; exit 1; }
echo '#' > "$TMP/plain/released.sh"
echo '#' > "$HOME_FIXTURE/.broomva/releases/tool/run.sh"
ln -s "$TREE/tracked.sh" "$HOME_FIXTURE/bin/linked.sh"
plist "$TMP/decoy-home/Library/LaunchAgents" com.broomva.decoy /bin/bash "$TREE/tracked.sh"

# ── word table and property test (module functions, fixture home only) ──
python3 - "$ENTRIES" "$TREE" "$TMP/plain" "$HOME_FIXTURE" "$TMP/linked" <<'PY_TABLE'
import importlib.util, itertools, os, sys
from pathlib import Path
entries, tree, plain, home, linked = sys.argv[1:]
sys.path.insert(0, os.path.dirname(entries))
spec = importlib.util.spec_from_file_location('entries', entries)
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
HOME = Path(home)
T, S, D = f'{tree}/tracked.sh', f'{tree}/with space.sh', f'{tree}/my dir/x.sh'
L = f'{home}/bin/linked.sh'


def classify(command):
    found, missed = d.read_entry([command], HOME)
    return [p for p in found if d.repository(p) is not None], missed  # as scan() keeps them


class U(list):
    """Expected: the whole entry is unresolved, the reason naming this construct."""
    def __init__(self, construct):
        super().__init__()
        self.construct = construct

    def matches(self, missed):
        return len(missed) == 1 and missed[0].startswith(f'unreadable {self.construct} at column ')

    def __repr__(self):
        return f'U({self.construct})'


def agrees(got, want_files, want_missed):
    if isinstance(want_missed, U):
        return got[0] == want_files and want_missed.matches(got[1])
    return got == (want_files, want_missed)


# (command, flagged files, unresolved words). Neither: the command is ignored.
rows = [
    # the three review rounds' shapes
    ("bash -c 'cd scripts && ./doctor.sh'", [], ['./doctor.sh']),
    ('echo report.sh', [], []),
    ('(cd /tmp); ./scripts/x.py', [], U('`(`')),            # round 2: `(` is outside the plain grammar
    (f'cat {tree}/notes.md', [f'{tree}/notes.md'], []),
    (f'/bin/bash {T}', [T], []),
    (f'python3 -I {tree}/x.py arg', [f'{tree}/x.py'], []),
    (f'exec {T}', [T], []),
    (f'timeout 5 {T}', [T], []),
    (f'xargs {T}', [T], []),
    ('git show origin/main:scripts/x.sh > "$T"', [], U('`$T`')),  # round 2: the recommended form costs a baseline line
    ('curl https://example.com/x.sh', [], []),
    # a path with a space: handled, quoted or not
    (f'"{S}" --flag', [S], []),
    (f"bash '{S}'", [S], []),
    (f'cat {S}', [S], []),
    (f'cat {tree}/with\\ space.sh', [], U('`\\`')),        # round 2: a backslash is not plain text
    (f'"{D}"', [D], []),              # `dir/x.sh` is used up by the join, not reported
    ('"./with space.sh" --flag', [], ['./with']),
    # not a working tree, not a file, not tracked
    (f'{home}/.broomva/releases/tool/run.sh sweep', [], []),
    (f'bash {plain}/released.sh', [], []),
    (f'{tree}/untracked.sh', [], []),
    (f'{tree}/gone.sh', [], []),
    (f'ls {tree}/sub', [], []),
    # a symlink into a working tree, spelled four ways
    (L, [L], []),
    ('~/bin/linked.sh', [L], []),
    ('$HOME/bin/linked.sh', [L], []),
    ('"${HOME}"/bin/linked.sh', [L], []),
    # never considered: no `/`, an option, a URL, a git object, a standard stream
    ('git status', [], []),
    ('bun run x', [], []),
    ('bash "$T"', [], U('`$T`')),                           # round 2: a variable other than HOME
    (f'cmd -o{T}', [], []),
    ('cmd 2>/dev/null </dev/stdin >/dev/stdout 2>/dev/stderr', [], []),
    ('git show HEAD:a/b.sh', [], []),
    ('wget ftp://host/a/b.sh', [], []),
    ('curl https://example.com/get?file=a/b.sh', [], U('`?`')),  # round 2: `?`
    # NAME=value and --option=value are read as their value
    (f'FOO={T} cmd', [T], []),
    (f'cmd --config={T}', [T], []),
    ('PYTHONPATH=lib/x cmd', [], ['lib/x']),
    # unresolved: relative, a variable other than HOME, the rest of a substitution
    ('"$CLAUDE_PROJECT_DIR"/x.sh', [], U('`$C`')),
    ('${DIR}/x.sh', [], U('`${`')),
    ('"$(dirname "$0")"/x.sh', [], U('`$(`')),
    (f'x=$(pwd){T}', [], U('`$(`')),                        # round 2: a substitution, whatever follows
    ('python3 x.py', [], []),                               # a bare word: no WorkingDirectory here
    (f'cd {tree} && ./tracked.sh', [], ['./tracked.sh']),   # a cd is not followed
    # a directory word paired with every bare word of the text (no cd tracking, no order)
    (f'cd {tree} && python3 x.py', [f'{tree}/x.py'], []),
    (f"bash -lc 'cd {tree} && exec python3 -I x.py arg'", [f'{tree}/x.py'], []),
    (f'cd {tree}; ./x.py', [], ['./x.py']),                  # a path word: still unresolved
    (f'git -C {tree} show origin/main:scripts/x.sh > "$T"', [], U('`$T`')),
    (f'git -C {tree} fetch origin && git -C {tree} show HEAD:x.py', [], []),  # `fetch`, `show`, `origin` are not files there
    (f'git -C {tree} log notes.md', [f'{tree}/notes.md'], []),  # conservative: named, though not run or read
    (f'python3 x.py; ls {tree}/sub', [], []),                # x.py is not in sub
    (f'ls {tree}', [], []),                                  # a directory alone is never reported
    ('git diff origin/main', [], ['origin/main']),          # not a file, still unresolved
    (f'a && ./b.sh | cat {T}', [T], ['./b.sh']),
    # round 1 review of #155: raw text a shell reads differently. Since round 2 none of it is
    # decoded: it is outside the plain grammar, so the whole entry is unresolved.
    (f'bash {tree}/x.\\\npy', [], U('`\\`')),              # round 1 reviewer: a line continuation
    (f'bash {tree}/x.\\\r\npy', [], U('`\\`')),           # the same with CRLF
    (f'bash "{tree}/tra\\\ncked.sh"', [], U('`\\`')),
    (f"bash '{tree}/x.\\\npy'", [], U('`\\`')),
    (f'bash {tree}/tracked\\.sh', [], U('`\\`')),
    ("bash $'" + T.replace('/', '\\x2f') + "'", [], U("`$'`")),
    ("bash $'" + T.replace('/', '\\057') + "'", [], U("`$'`")),
    (f'bash $"{T}"', [], U('`$"`')),
    (f'bash {T}\0ignored', [], U('U+0000')),
    (f'bash {tree}/trac\x01ked.sh', [], U('U+0001')),
    (f'bash {tree}/*.sh', [], U('`*`')),
    (f'bash {tree}/{{tracked,x}}.sh', [], U('`{`')),
    (f'cd {tree}\npython3 x.py', [], U('U+000A')),          # a newline: not plain text
    (f'cd\t{tree}\t&&\tpython3\tx.py', [f'{tree}/x.py'], []),  # tabs are plain text: still flagged
    # round 2 review of #155, the reviewer's strings
    (f'bash $(echo {tree}/x).py', [], U('`$(`')),           # MAJOR: a substitution hid x.py
    (f'bash {tree}/x.py$EMPTY', [], U('`$E`')),             # MAJOR: an empty variable suffix hid x.py
    (f'bash {tree}/x\u00a0.py', [], U('U+00A0')),            # MINOR: a non-breaking space
    (f'bash {tree}/x\u200b.py', [], U('U+200B')),            # MINOR: a zero-width space
    (f'bash `echo {tree}`/x.py', [], U('```')),
    (f'bash ${{T_VAR}}/x.py', [], U('`${`')),
    (f'git -C {tree} show origin/main:x.py > "$OUT" && bash "$OUT"', [], U('`$O`')),  # an honest entry: unresolved, by design
    # the exact HOME forms and a leading ~ stay plain text
    ('"$HOME"/bin/linked.sh', [L], []),
    ('echo $HOMEX/bin/linked.sh', [], U('`$H`')),            # not $HOME: another variable
    ('a~/bin/linked.sh', [], U('`~`')),                       # ~ inside a word
    (f'cmd 2>&1 | tee -a log; {T} >> out & wait', [T], []),   # every operator in the grammar
]
failed = []
for command, want_files, want_missed in rows:
    got = classify(command)
    if not agrees(got, want_files, want_missed):
        failed.append(f'  {command!r}: want {want_files} {want_missed}, got {got[0]} {got[1]}')
print(f'word table: {len(rows) - len(failed)} of {len(rows)} rows as expected')

# A LaunchAgent's WorkingDirectory resolves a relative word; nothing else does.
for command, want, want_missed in [
        ('./tracked.sh', [T], []), ('sub/inner.sh', [f'{tree}/sub/inner.sh'], []),
        ('python3 x.py', [f'{tree}/x.py'], []), ('origin/main', [], []), ('echo nothing-here', [], []),
        ('"$DIR"/tracked.sh', [], U('`$D`')), ('~other/tracked.sh', [], ['~other/tracked.sh'])]:
    found, missed = d.read_entry([command], HOME, cwd=tree)
    if not agrees((found, missed), want, want_missed):
        failed.append(f'  WorkingDirectory {command!r}: want {want} {want_missed}, got {found} {missed}')

# Property: every wrapper around every spelling of every tracked file is flagged.
files = {
    T: [T, f'"{T}"', f"'{T}'"],
    f'{tree}/sub/inner.sh': [f'{tree}/sub/inner.sh', f'"{tree}"/sub/inner.sh'],
    f'{tree}/notes.md': [f'{tree}/notes.md'],
    S: [S, f'"{S}"', f"'{S}'", f'{tree}/with\\ space.sh'],
    D: [D, f'"{D}"', f'{tree}/my\\ dir/x.sh'],
    f'{linked}/tracked.sh': [f'{linked}/tracked.sh'],
    L: [L, '~/bin/linked.sh', '$HOME/bin/linked.sh', '${HOME}/bin/linked.sh', '"$HOME"/bin/linked.sh',
        '"${HOME}/bin/linked.sh"'],
}
wrappers = [
    '{p}', '{p} arg', '/bin/bash {p}', 'bash -x {p}', 'sh -e {p} --flag', 'python3 -I {p} arg',
    'python3 -W ignore -X dev {p}', 'exec {p}', 'exec -a name {p}', 'timeout 5 {p}', 'timeout -s KILL 5 {p}',
    'xargs {p}', 'xargs -n1 {p}', 'nohup {p} &', 'env A=1 {p}', 'env -i PATH=/bin {p}', 'sudo -u nobody {p}',
    'nice -n 5 {p}', 'caffeinate -i {p}', 'command {p}', 'time {p}', 'flock /tmp/l {p}', 'cat {p} | sh',
    'sh < {p}', '<{p} sh', 'source {p}', '. {p}', 'x=$({p})', 'x=`{p}`', '({p})', '{{ {p}; }}',
    'true && {p}', 'false || {p}', 'a; {p}; b', 'if [ -f {p} ]; then {p}; fi', 'cmd >{p}', 'cmd 2>>{p}',
    'cp {p} /tmp/x', 'cmd --file={p}', 'FOO={p} cmd', 'cd /tmp && {p}', '(cd /tmp); {p}', 'bash -c "{p}"',
    "bash -c '{p} --x'", "sh -lc 'cd scripts && {p}'", 'osascript {p}', 'node --require x {p}', 'bun {p}',
    'deno run {p}', 'ruby {p}', 'perl -I lib {p}', 'cat <<EOF\n{p}\nEOF', 'git -C /tmp status; {p}',
    'echo start; {p} 2>&1 | tee -a log', 'while read x; do {p} "$x"; done < list',
]

def raw_spellings(p):
    """The same absolute path, spelled with raw text a shell reads differently."""
    return [p[:-2] + '\\\n' + p[-2:], p[:-2] + '\\\r\n' + p[-2:], f'"{p[:-2]}\\\n{p[-2:]}"',
            p[:-1] + '\\' + p[-1], "$'" + p.replace('/', '\\x2f') + "'", "$'" + p.replace('/', '\\057') + "'",
            p + '\0', p[:-2] + '\\\n' + p[-2:] + '\0']


for target in files:
    files[target] = files[target] + raw_spellings(files[target][0])


def reads_as_expected(text, real):
    """Plain text must flag the target; anything else must leave the whole entry unresolved."""
    found, missed = classify(text)
    if d.unreadable(text) is None:
        return any(os.path.realpath(path) == real for path in found)
    return found == [] and len(missed) == 1 and missed[0].startswith('unreadable ')


generated = flagged = 0
for target, spellings in files.items():
    real = os.path.realpath(target)
    for wrapper, spelling in itertools.product(wrappers, spellings):
        command = wrapper.format(p=spelling)
        for text in (command, f"bash -c '{command}'"):
            generated += 1
            if reads_as_expected(text, real):
                flagged += 1
            else:
                failed.append(f'  property: {text!r} neither flags {target} nor is unresolved as a whole')
# The `cd D && <interpreter> W` shape: a directory word and a bare word, in any spelling.
for target in (T, f'{tree}/x.py', f'{tree}/sub/inner.sh', f'{tree}/notes.md', f'{linked}/tracked.sh'):
    folder, name = os.path.split(target)
    real = os.path.realpath(target)
    for folder_spelling in (folder, f'"{folder}"', f"'{folder}'"):
        for shape in ('cd {d} && {w}', 'cd {d} && python3 -I {w} arg', 'cd {d}; bash {w}', 'cd {d} && exec ./x; sh {w}',
                      '(cd {d} && nohup timeout 5 node {w} &)', 'pushd {d} >/dev/null; ruby {w}; popd',
                      'cd {d} || exit 1; cat {w} | tee -a log', 'env -C {d} perl {w}', '{w} --in {d}',
                      'cd {d}\npython3 {w}', 'cd\t{d}\t&&\tpython3\t{w}', 'cd {d} \\\n && python3 {w}',
                      'cd {d}\r\n{w}', 'cd {d}\0{w}'):
            for text in (shape.format(d=folder_spelling, w=name), "bash -lc '" + shape.format(d=folder_spelling, w=name) + "'"):
                generated += 1
                if reads_as_expected(text, real):
                    flagged += 1
                else:
                    failed.append(f'  property: {text!r} neither flags {target} nor is unresolved as a whole')
print(f'property: {flagged} of {generated} generated commands flagged, or unresolved as a whole when not plain text')
# Property: a path the check cannot read comes out unresolved, never ignored.
unread = missed_count = 0
for target, spellings in files.items():
    p = spellings[0]
    for unreadable in (p[:-2] + '\x01' + p[-2:], p[:-2] + '\x1b' + p[-2:], p[:-2] + '\x7f' + p[-2:],
                       os.path.dirname(p) + '/*' + p[-3:], os.path.dirname(p) + '/[a-z]*', p[:-1] + '?'):
        for wrapper in wrappers:
            command = wrapper.format(p=unreadable)
            for text in (command, f"bash -c '{command}'"):
                unread += 1
                if classify(text)[1]:
                    missed_count += 1
                else:
                    failed.append(f'  unreadable: {text!r} was ignored')
print(f'unreadable: {missed_count} of {unread} generated commands unresolved')

# Round 2, the class: every character outside the plain grammar, at the start, inside
# the path and at the end of a flagged command, comes out flagged or unresolved, never clean.
# The grammar is spelled here independently of the module, so a change to the module's set
# is caught by this generator rather than silently shrinking it.
GRAMMAR = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789' + '_-./:=,+@%' + ' \t' + '\'"' + '&|;<>')
OUTSIDE_ASCII = [chr(c) for c in range(128) if chr(c) not in GRAMMAR]
NON_ASCII = ['\u00a0', '\u200b', '\u200d', '\u200f', '\u00e9', '\uff0f']  # NBSP, ZWSP, ZWJ, RLM, e-acute, full-width slash
BASES = [(f'bash {T}', T), (f'cat {tree}/notes.md', f'{tree}/notes.md'), (f'cd {tree} && python3 x.py', f'{tree}/x.py')]
inserted = never_clean = 0
for base, target in BASES:
    real = os.path.realpath(target)
    assert reads_as_expected(base, real), base
    cut = base.rindex('/') + 3  # inside the file name (for the bare-word shape, inside the directory)
    for c in OUTSIDE_ASCII + NON_ASCII:
        for text in (c + base, base[:cut] + c + base[cut:], base + c):
            for entry in (text, f"bash -c '{text}'"):
                inserted += 1
                found, missed = classify(entry)
                if any(os.path.realpath(path) == real for path in found) or missed:
                    never_clean += 1
                else:
                    failed.append(f'  inserted {c!r}: {entry!r} came out clean')
if set(d.READABLE_CHARS) != GRAMMAR:
    failed.append(f'  the module grammar differs from the test grammar: {sorted(set(d.READABLE_CHARS) ^ GRAMMAR)}')
print(f'inserted: {never_clean} of {inserted} generated commands flagged or unresolved '
      f'({len(OUTSIDE_ASCII)} ASCII + {len(NON_ASCII)} non-ASCII characters x 3 positions x {len(BASES)} commands x 2)')
if failed:
    print('\n'.join(failed[:40]))
    sys.exit(1)
PY_TABLE

# ── classification, read through the two options (--report: no baseline at all) ──
OPT_AGENTS="$TMP/agents"
OPT_SETTINGS="$TMP/settings.json"
mkdir -p "$OPT_AGENTS"
plist "$OPT_AGENTS" com.broomva.direct /bin/bash "$TREE/tracked.sh" compile
plist "$OPT_AGENTS" com.broomva.shell /bin/bash -lc "export X=1; /bin/bash \"$TREE/sub/inner.sh\" --apply 2>&1 | tee -a log"
plist "$OPT_AGENTS" com.broomva.read /bin/bash -c "cat $TREE/notes.md | wc -l"
plist "$OPT_AGENTS" com.broomva.home-link /bin/bash -c "exec \$HOME/bin/linked.sh serve"
plist "$OPT_AGENTS" com.broomva.space "$TREE/with space.sh"
plist "$OPT_AGENTS" com.broomva.linked-worktree /bin/bash "$TMP/linked/tracked.sh"
# These pass: a released copy, a plain folder, an untracked file in the tree, a
# missing file, a directory. Main's copy into a temp file is unresolved (not plain text).
plist "$OPT_AGENTS" com.broomva.gitshow /bin/bash -lc "T=\$(mktemp); git -C \"$TREE\" show origin/main:tracked.sh > \"\$T\" || exit 0; bash \"\$T\"; rm -f \"\$T\""
plist "$OPT_AGENTS" com.broomva.released /bin/bash "$HOME_FIXTURE/.broomva/releases/tool/run.sh"
plist "$OPT_AGENTS" com.broomva.plain /bin/bash "$TMP/plain/released.sh"
plist "$OPT_AGENTS" com.broomva.untracked /bin/bash "$TREE/untracked.sh"
plist "$OPT_AGENTS" com.broomva.missing /bin/bash "$TREE/gone.sh"
plist "$OPT_AGENTS" com.broomva.directory /usr/bin/env ls "$TREE/sub"
# A cd is not followed: the relative word after it is unresolved.
plist "$OPT_AGENTS" com.broomva.cd /bin/bash -c "cd $TREE/sub && /usr/bin/python3 ./rel.py harvest"
plist "$OPT_AGENTS" com.broomva.relative /bin/bash -c "./run.sh nightly"
# Not a com.broomva.* agent: out of scope.
plist "$OPT_AGENTS" org.example.other /bin/bash "$TREE/tracked.sh"
python3 - "$OPT_AGENTS" "$OPT_SETTINGS" "$TREE" "$HOME_FIXTURE" <<'PY_FIXTURE'
import json, plistlib, sys
agents, settings, tree, home = sys.argv[1:]
with open(f'{agents}/com.broomva.program.plist', 'wb') as stream:
    plistlib.dump({'Label': 'program', 'Program': f'{tree}/tracked.sh'}, stream)
# WorkingDirectory resolves relative words in every argument, a `-c` string included.
with open(f'{agents}/com.broomva.cwd.plist', 'wb') as stream:
    plistlib.dump({'Label': 'cwd', 'WorkingDirectory': tree, 'ProgramArguments': ['/bin/sh', 'tracked.sh', 'run']}, stream)
with open(f'{agents}/com.broomva.cwd-shell.plist', 'wb') as stream:
    plistlib.dump({'Label': 'cwd-shell', 'WorkingDirectory': tree,
                   'ProgramArguments': ['/bin/bash', '-c', 'exec ./sub/inner.sh --apply']}, stream)
command = lambda text: {'type': 'command', 'command': text}
json.dump({'hooks': {
    'PreToolUse': [{'matcher': 'Bash', 'hooks': [command(f'python3 -I {tree}/hook.py pretool --harness=claude')]}],
    'Stop': [{'hooks': [command('CTX_PYTHON=/usr/bin/python3 /bin/sh ~/bin/linked.sh stop 2>/dev/null || true'),
                        command(f'{home}/.broomva/releases/tool/run.sh sweep')]}],
    'SessionStart': [{'hooks': [command('"$CLAUDE_PROJECT_DIR"/tracked.sh'),
                                command('python3 -I /nonexistent/hook.py session-start'),
                                {'type': 'prompt', 'prompt': f'{tree}/tracked.sh'}]}],
    'UserPromptSubmit': [{'hooks': [command("bash -c 'cd scripts && ./doctor.sh'"),
                                    command('python3 scripts/y.py --fast')]}],
    # Reading a tracked file is flagged like running it; bare words never count.
    'PostToolUse': [{'hooks': [command('bun run dev'), command('git status 2>&1 | tee -a log'),
                               command('echo report.sh'), command(f'cat {tree}/notes.md')]}],
}, 'permissions': {'allow': [f'Bash({tree}/tracked.sh)']}}, open(settings, 'w'))
PY_FIXTURE
python3 -I "$ENTRIES" --report --json --launch-agents "$OPT_AGENTS" --settings "$OPT_SETTINGS" >"$TMP/json"
python3 - "$TMP/json" "$TREE" "$TMP/linked" <<'PY_EXPECT'
import json, sys
d = json.load(open(sys.argv[1]))
tree, linked = sys.argv[2:]
want = sorted([
    ('claude-hook:PostToolUse', f'{tree}/notes.md', tree),
    ('claude-hook:PreToolUse[Bash]', f'{tree}/hook.py', tree),
    ('claude-hook:Stop', '~/bin/linked.sh', tree),
    ('launchd:com.broomva.cwd', f'{tree}/tracked.sh', tree),
    ('launchd:com.broomva.cwd-shell', f'{tree}/sub/inner.sh', tree),
    ('launchd:com.broomva.direct', f'{tree}/tracked.sh', tree),
    ('launchd:com.broomva.home-link', '~/bin/linked.sh', tree),
    ('launchd:com.broomva.linked-worktree', f'{linked}/tracked.sh', linked),
    ('launchd:com.broomva.program', f'{tree}/tracked.sh', tree),
    ('launchd:com.broomva.read', f'{tree}/notes.md', tree),
    ('launchd:com.broomva.shell', f'{tree}/sub/inner.sh', tree),
    ('launchd:com.broomva.space', f'{tree}/with space.sh', tree),
])
got = [(item['entry'], item['file'], item['repository']) for item in d['flagged']]
assert got == want, '\n' + '\n'.join(map(str, sorted(set(got) ^ set(want))))
want_unresolved = sorted([
    ('claude-hook:SessionStart', '"$CLAUDE_PROJECT_DIR"/tracked.sh', 'unreadable `$C` at column 2'),
    # main's copy into a temp file: not plain text, so unresolved as a whole and baselined once
    ('launchd:com.broomva.gitshow', "/bin/bash -lc 'T=$(mktemp); git -C \"" + tree + "\" show origin/main:tracked.sh > \"$T\" "
     "|| exit 0; bash \"$T\"; rm -f \"$T\"'", 'unreadable `$(` at column 3 of argument 2'),
    ('claude-hook:UserPromptSubmit', "bash -c 'cd scripts && ./doctor.sh'", './doctor.sh'),
    ('claude-hook:UserPromptSubmit', 'python3 scripts/y.py --fast', 'scripts/y.py'),
    ('launchd:com.broomva.cd', f"/bin/bash -c 'cd {tree}/sub && /usr/bin/python3 ./rel.py harvest'", './rel.py'),
    ('launchd:com.broomva.relative', "/bin/bash -c './run.sh nightly'", './run.sh'),
])
got_unresolved = [(item['entry'], item['command'], item['word']) for item in d['unresolved']]
assert got_unresolved == want_unresolved, '\n' + '\n'.join(map(str, sorted(set(got_unresolved) ^ set(want_unresolved))))
assert d == {'schema': 1, 'mode': 'report', 'baseline_recorded_at': None, 'flagged': d['flagged'],
             'unresolved': d['unresolved'], 'grandfathered': None, 'error': None}, d
PY_EXPECT
python3 -I "$ENTRIES" --report --launch-agents "$OPT_AGENTS" --settings "$OPT_SETTINGS" >"$TMP/out"
grep -Fxq "[info] launchd:com.broomva.direct names $TREE/tracked.sh (repository $TREE)" "$TMP/out"
grep -Fxq '[info] 12 entries name a file in a git working tree, run or read (report only: no baseline read or written)' "$TMP/out"
grep -Fxq '[info] 6 path words could not be resolved' "$TMP/out"
grep -Fxq "[info] unresolved: claude-hook:UserPromptSubmit names \`./doctor.sh\`, a path this check cannot resolve, in: bash -c 'cd scripts && ./doctor.sh'" "$TMP/out"
# Without the options the fixture home is read (empty so far), never HOME's decoy.
python3 -I "$ENTRIES" --report >"$TMP/out"
grep -Fxq '[info] 0 entries name a file in a git working tree, run or read (report only: no baseline read or written)' "$TMP/out"
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
grep -Fq "launchd:com.broomva.existing names $TREE/tracked.sh (repository $TREE)" "$TMP/out"
grep -Fq "claude-hook:Stop names $TREE/hook.py (repository $TREE)" "$TMP/out"
if grep -q 'decoy' "$TMP/out" "$BASELINE"; then echo 'the baseline read HOME, not the fixture home' >&2; exit 1; fi
run_doctor_verbose
grep -Fq "[info] grandfathered entry: launchd:com.broomva.existing names $TREE/tracked.sh (repository $TREE)" "$TMP/out"
# A passing ratchet prints nothing under --quiet.
run_doctor
if grep -q 'grandfathered\|working-tree entry' "$TMP/out"; then echo 'quiet mode listed grandfathered entries' >&2; exit 1; fi
if PATH="$TMP/no-python:$PATH" run_doctor; then echo 'enabled ratchet without python passed' >&2; exit 1; fi
# A new entry fails doctor and names the entry, the file and the repository.
plist "$AGENTS" com.broomva.new /bin/bash -lc "timeout 60 $TREE/sub/inner.sh --apply"
if run_doctor; then echo 'new launchd entry passed' >&2; exit 1; fi
grep -Fq "[failure] new entry names a file in a git working tree: launchd:com.broomva.new names $TREE/sub/inner.sh (repository $TREE)" "$TMP/out"
grep -Fq 'whether the entry runs the file or only reads it' "$TMP/out"
grep -Fq '[gap] working-tree entry ratchet failed' "$TMP/out"
if grep -Fq '[failure] new entry names a file in a git working tree: launchd:com.broomva.existing' "$TMP/out"; then echo 'a grandfathered entry failed' >&2; exit 1; fi
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
# Repointed at main's copy it is not plain text: unresolved as a whole, so it fails
# until acknowledged in the baseline. Repointed at a released copy, it passes.
plist "$AGENTS" com.broomva.new /bin/bash -lc "T=\$(mktemp); git -C \"$TREE\" show origin/main:sub/inner.sh > \"\$T\" && bash \"\$T\""
if run_doctor; then echo 'an entry that is not plain text passed' >&2; exit 1; fi
grep -Fq '[failure] new entry names a path this check cannot resolve: launchd:com.broomva.new is not plain text (unreadable `$(` at column 3 of argument 2)' "$TMP/out"
grep -Fq 'this check certifies only entries it can read completely' "$TMP/out"
plist "$AGENTS" com.broomva.new /bin/bash "$HOME_FIXTURE/.broomva/releases/tool/run.sh"
run_doctor
rm "$AGENTS/com.broomva.new.plist"
# A new hook that only reads a tracked file fails the same way.
python3 - "$SETTINGS" "$TREE" <<'PY_SETTINGS'
import json, sys
d = json.load(open(sys.argv[1]))
d['hooks']['PreToolUse'] = [{'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': f'cat {sys.argv[2]}/notes.md'}]}]
json.dump(d, open(sys.argv[1], 'w'))
PY_SETTINGS
if run_doctor; then echo 'new hook entry passed' >&2; exit 1; fi
grep -Fq "claude-hook:PreToolUse[Bash] names $TREE/notes.md (repository $TREE)" "$TMP/out"
# A grandfathered entry that leaves is information; the baseline can be re-recorded.
rm "$AGENTS/com.broomva.existing.plist"
if run_doctor; then echo 'new hook entry passed after a removal' >&2; exit 1; fi
grep -Fq '[info] baselined entry no longer names a file in a working tree: launchd:com.broomva.existing' "$TMP/out"
bash "$DOCTOR" --baseline-entries --force >"$TMP/out"
run_doctor
# A grandfathered entry repointed at another tracked file is a new entry.
python3 - "$SETTINGS" "$TREE" <<'PY_SETTINGS'
import json, sys
d = json.load(open(sys.argv[1]))
d['hooks']['PreToolUse'][0]['hooks'][0]['command'] = f'cat {sys.argv[2]}/sub/inner.sh'
json.dump(d, open(sys.argv[1], 'w'))
PY_SETTINGS
if run_doctor; then echo 'a repointed grandfathered entry passed' >&2; exit 1; fi
grep -Fq "claude-hook:PreToolUse[Bash] names $TREE/sub/inner.sh (repository $TREE)" "$TMP/out"
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
# An unresolved path word ratchets like a flagged entry: new fails, recorded is
# grandfathered (info), and one that leaves is information.
python3 - "$SETTINGS" <<'PY_SETTINGS'
import json, sys
d = json.load(open(sys.argv[1]))
d['hooks']['Stop'][0]['hooks'].append({'type': 'command', 'command': "bash -c 'cd scripts && ./doctor.sh'"})
json.dump(d, open(sys.argv[1], 'w'))
PY_SETTINGS
json_run 1 "d['error'] is None and d['flagged'] == [] and d['grandfathered'] == 2 and d['unresolved'] == [{'entry': 'claude-hook:Stop', 'command': \"bash -c 'cd scripts && ./doctor.sh'\", 'word': './doctor.sh'}]"
if run_doctor; then echo 'a new unresolved hook passed' >&2; exit 1; fi
grep -Fq '[failure] new entry names a path this check cannot resolve: claude-hook:Stop names `./doctor.sh`' "$TMP/out"
grep -Fq 'this check certifies only entries it can read completely; write plain absolute paths' "$TMP/out"
# The negative control: without the unresolved comparison the same run passes.
python3 - "$TMP/copy/scripts/doctor-worktree-entries.py" "$ENTRIES" <<'PY_MUTATE'
import sys
s = open(sys.argv[2]).read()
assert s.count('unresolved - saved_unresolved') == 1
open(sys.argv[1], 'w').write(s.replace('unresolved - saved_unresolved', 'unresolved - unresolved'))
PY_MUTATE
python3 -I "$TMP/copy/scripts/doctor-worktree-entries.py" >"$TMP/out"
bash "$DOCTOR" --baseline-entries --force >"$TMP/out"
grep -Fq 'unresolved: claude-hook:Stop names `./doctor.sh`' "$TMP/out"
json_run 0 "d['error'] is None and d['flagged'] == [] and d['unresolved'] == [] and d['grandfathered'] == 3"
run_doctor_verbose
grep -Fq '[info] grandfathered unresolved entry: claude-hook:Stop names `./doctor.sh`' "$TMP/out"
cp "$TMP/settings.good" "$SETTINGS"
run_doctor_verbose
grep -Fq '[info] baselined unresolved entry is gone: claude-hook:Stop names `./doctor.sh`' "$TMP/out"
bash "$DOCTOR" --baseline-entries --force >"$TMP/out"
# --json in every mode: recording a baseline, refusing to overwrite one, a usage error.
json_mode() {  # json_mode EXPECTED_EXIT PYTHON_ASSERTION ARG...
    want="$1" assertion="$2"; shift 2
    set +e
    python3 -I "$ENTRIES" "$@" >"$TMP/json" 2>"$TMP/json.err"
    rc=$?
    set -e
    [ "$rc" = "$want" ] || { echo "$* exited $rc, wanted $want" >&2; cat "$TMP/json" "$TMP/json.err" >&2; exit 1; }
    [ ! -s "$TMP/json.err" ] || { echo "$* wrote to stderr" >&2; cat "$TMP/json.err" >&2; exit 1; }
    [ "$(wc -l <"$TMP/json" | tr -d ' ')" = "1" ] || { echo "$* did not print exactly one line" >&2; cat "$TMP/json" >&2; exit 1; }
    python3 - "$TMP/json" "$assertion" "$BASELINE" "$TREE" <<'PY_MODE'
import json, sys
d = json.load(open(sys.argv[1]))
baseline, tree = sys.argv[3:]
assert d['schema'] == 1, d
assert eval(sys.argv[2]), (sys.argv[2], d)
PY_MODE
}
json_mode 0 "d['mode'] == 'baseline' and d['action'] == 'recorded' and d['baseline'] == baseline and d['error'] is None and d['baseline_recorded_at'] == json.load(open(baseline))['recorded_at'] and d['flagged'] == [dict(entry=e['entry'], file=e['file'], repository=e['repository']) for e in json.load(open(baseline))['entries']] and d['unresolved'] == [] and d['grandfathered'] is None" --baseline-entries --force --json
json_mode 1 "d['mode'] == 'baseline' and d['action'] is None and d['baseline'] == baseline and 'File exists' in d['error'] and d['flagged'] is None and d['unresolved'] is None" --baseline-entries --json
json_mode 2 "d['mode'] is None and d['error'].startswith('usage: ') and d['flagged'] is None" --report --baseline-entries --json
json_mode 2 "d['mode'] is None and 'unrecognized arguments' in d['error']" --json --no-such-option
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
printf '{bad json' > "$BASELINE"
json_run 1 "$unreadable and 'working-tree entry check' in d['error']"
if run_doctor; then echo 'malformed baseline passed' >&2; exit 1; fi
grep -q 'working-tree entry check' "$TMP/out"
printf '{"recorded_at": "x", "entries": [{"entry": "e"}], "unresolved": []}' > "$BASELINE"
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
