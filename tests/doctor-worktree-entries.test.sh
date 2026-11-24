#!/bin/bash
# tests/doctor-worktree-entries.test.sh — BRO-4084. The working-tree entry
# ratchet (scripts/doctor-worktree-entries.py, doctor section 33) against
# fixtures only: HOME is a decoy that holds an entry the check would flag, so a
# run that read HOME instead of its fixture home or its options fails here.
#
# The ratchet is on the identity of each entry: the generated mutation test
# changes every recorded entry one character at a time and requires each change
# to fail. The classifier is an annotation only; the word table and the earlier
# generated tests pin what the annotation says, never whether doctor passes.
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
# Round 3: tracked names holding each readable punctuation character, a space, and combinations.
mkdir -p "$TREE/punct"
for f in 'p=q.sh' 'p:q.sh' 'p,q.sh' 'p+q.sh' 'p@q.sh' 'p%q.sh' 'p-q.sh' 'p_q.sh' 'p.q.sh' 'p q.sh' \
         'a=b.sh' 'a:b.sh' '--x=y.sh' 'A=1 B.sh' 'x,y.sh'; do
    echo '#' > "$TREE/punct/$f"
done
echo '#' > "$TREE/a=b.sh"
git -C "$TREE" add punct 'a=b.sh'
# Round 4: x.sh (the reviewer's file) and a name with an accented letter, written in NFC.
echo '#' > "$TREE/x.sh"
python3 -c 'import sys; open(sys.argv[1] + "/caf\u00e9.sh", "w").write("#\n")' "$TREE"
git -C "$TREE" add x.sh "$(printf 'caf\303\251.sh')"
git -C "$TREE" -c user.name=t -c user.email=t@example.invalid commit -q -m fixture
git -C "$TREE" worktree add -q "$TMP/linked" -b linked >/dev/null 2>&1
[ -f "$TMP/linked/.git" ] || { echo 'fixture: the linked worktree has no .git file' >&2; exit 1; }
echo '#' > "$TMP/plain/released.sh"
echo '#' > "$HOME_FIXTURE/.broomva/releases/tool/run.sh"
ln -s "$TREE/tracked.sh" "$HOME_FIXTURE/bin/linked.sh"
# A hard link outside any working tree: the same file as tracked notes.md under an untracked name.
ln "$TREE/notes.md" "$TMP/plain/notes-link.md"
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
    flagged = []
    for path in found:  # as scan() judges them: by identity
        verdict = d.judge(path)
        if verdict and verdict[0] == 'tracked':
            flagged.append(path)
        elif verdict:
            missed = missed + [f'hard link {path}']
    return flagged, missed


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
    (f'cmd -o{T}', [T], []),                                 # round 3: an absolute path inside an option is found
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
    (f'cd {tree} && ./tracked.sh', [T], ['./tracked.sh']),  # round 3: a directory x a token adds the file; still unresolved
    # a directory word paired with every bare word of the text (no cd tracking, no order)
    (f'cd {tree} && python3 x.py', [f'{tree}/x.py'], []),
    (f"bash -lc 'cd {tree} && exec python3 -I x.py arg'", [f'{tree}/x.py'], []),
    (f'cd {tree}; ./x.py', [f'{tree}/x.py'], ['./x.py']),     # round 3: flagged too; the relative word stays unresolved
    (f'git -C {tree} show origin/main:scripts/x.sh > "$T"', [], U('`$T`')),
    (f'git -C {tree} fetch origin && git -C {tree} show HEAD:x.py', [f'{tree}/x.py'], []),  # round 3: the piece after `:` names a tracked file
    (f'git -C {tree} log notes.md', [f'{tree}/notes.md'], []),  # conservative: named, though not run or read
    (f'python3 x.py; ls {tree}/sub', [f'{tree}/x.py'], []),  # round 3: every tracked directory on the way pairs, here the tree
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
    # round 3 review of #155, the reviewer's string: `=` inside a tracked absolute file name
    (f'bash {tree}/a=b.sh', [f'{tree}/a=b.sh'], []),
    # round 4: a hard link outside any working tree is unresolved: another name of it may be tracked
    (f'cat {plain}/notes-link.md', [], [f'hard link {plain}/notes-link.md']),
    # round 6 (the Linux CI failure): a file reached only from INSIDE a word is a suffix of another
    # path (there: `/bin/link` inside `/home/bin/linked.sh`); it adds an annotation only when tracked
    (f'cat /zz{plain}/notes-link.md', [], []),
    (f'cat "$HOME"{plain}/notes-link.md', [], []),    # after a closing quote is still inside the word
    (f'cat "{plain}/notes-link.md"', [], [f'hard link {plain}/notes-link.md']),  # after an opening quote starts it
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
                    failed.append(f'  inserted {c!r}: {entry!r} was annotated "no working-tree file found"')
if set(d.READABLE_CHARS) != GRAMMAR:
    failed.append(f'  the module grammar differs from the test grammar: {sorted(set(d.READABLE_CHARS) ^ GRAMMAR)}')
print(f'inserted: {never_clean} of {inserted} generated commands flagged or unresolved '
      f'({len(OUTSIDE_ASCII)} ASCII + {len(NON_ASCII)} non-ASCII characters x 3 positions x {len(BASES)} commands x 2)')

# Round 3, the class: a reading may only ADD candidates. A tracked file whose name holds
# readable punctuation or a space is flagged in every command shape, none clean.
PUNCT = ['p=q.sh', 'p:q.sh', 'p,q.sh', 'p+q.sh', 'p@q.sh', 'p%q.sh', 'p-q.sh', 'p_q.sh', 'p.q.sh', 'p q.sh',
         'a=b.sh', 'a:b.sh', '--x=y.sh', 'A=1 B.sh', 'x,y.sh']
SHAPES = ['{f}', 'bash {f}', 'python3 -I {f} arg', 'env K=V bash {f}', 'cd {d} && bash {n}', 'K={f} bash -c run',
          "bash '{f}'", 'bash "{f}"', "cd {d} && bash '{n}'", 'cd {d} && bash "{n}"', 'exec {f}', 'cat {f}']
named = named_flagged = 0
for name in PUNCT:
    target = f'{tree}/punct/{name}'
    real = os.path.realpath(target)
    for shape in SHAPES:
        text = shape.format(f=target, d=f'{tree}/punct', n=name)
        named += 1
        assert d.unreadable(text) is None, text
        if any(os.path.realpath(path) == real for path in classify(text)[0]):
            named_flagged += 1
        else:
            failed.append(f'  punctuation: {text!r} does not flag {target}')
print(f'punctuation: {named_flagged} of {named} generated commands flagged ({len(PUNCT)} names x {len(SHAPES)} shapes)')

# Round 4, the class: files are compared by identity, never by path string. Every other
# spelling of a tracked file that the file system resolves to it must be flagged.
import unicodedata
aliases = os.path.join(os.path.dirname(tree), 'aliases')
os.makedirs(aliases, exist_ok=True)
case_insensitive = os.path.exists(os.path.join(tree, 'TRACKED.SH'))
print(f'identity: this file system is case-{"in" if case_insensitive else ""}sensitive (probe: TRACKED.SH)')


def other_case(name):
    return name.swapcase() if name.swapcase() != name else name


IDENTITY_FILES = [T, f'{tree}/x.py', f'{tree}/notes.md', f'{tree}/sub/inner.sh', f'{linked}/tracked.sh']
spelled = spelled_flagged = skipped = 0
for k, target in enumerate(IDENTITY_FILES):
    folder, name = os.path.split(target)
    root = linked if target.startswith(linked + '/') else tree
    rel = os.path.relpath(target, root)
    os.symlink(target, f'{aliases}/file-{k}')
    os.symlink(folder, f'{aliases}/dir-{k}')
    os.makedirs(f'{root}/hardlinks', exist_ok=True)
    os.link(target, f'{root}/hardlinks/{k}-{name}')       # untracked name, same file, same tree
    spellings = {
        'letter case': (f'{folder}/{other_case(name)}', case_insensitive),
        'doubled slash': (f'{folder}//{name}', True),
        '/./': (f'{folder}/./{name}', True),
        'sub/../': (f'{root}/sub/../{rel}', True),
        'symlink to the file': (f'{aliases}/file-{k}', True),
        'symlink to its directory': (f'{aliases}/dir-{k}/{name}', True),
        'hard link in the same tree': (f'{root}/hardlinks/{k}-{name}', True),
    }
    real = os.stat(target)
    for label, (spelling, applies) in spellings.items():
        for shape in ('bash {s}', 'cat {s}'):
            text = shape.format(s=spelling)
            if not applies:
                skipped += 1
                print(f'  skipped ({label}): this file system is case-sensitive, so {text!r} names no file')
                continue
            spelled += 1
            found, missed = classify(text)
            if any(os.path.samestat(os.stat(path), real) for path in found):
                spelled_flagged += 1
            else:
                failed.append(f'  identity ({label}): {text!r} does not flag {target}, got {found} {missed}')
# The reviewer's string: `T/X.SH` names the tracked `T/x.sh` on a case-insensitive file system.
if case_insensitive:
    spelled += 1
    if classify(f'bash {tree}/X.SH')[0] == [f'{tree}/X.SH']:
        spelled_flagged += 1
    else:
        failed.append(f'  identity (round 4 reviewer): bash T/X.SH is not flagged: {classify(f"bash {tree}/X.SH")}')
else:
    skipped += 1
    print('  skipped (round 4 reviewer, bash T/X.SH): this file system is case-sensitive')
print(f'identity: {spelled_flagged} of {spelled} generated spellings flagged, {skipped} skipped')
# NFC and NFD: a name with an accented letter is non-ASCII text, so the allowlist makes the whole
# entry unresolved before any file is compared. Both spellings, on any file system.
nfd_same = os.path.exists(os.path.join(tree, unicodedata.normalize('NFD', 'caf\u00e9.sh')))
print(f'identity: NFD spelling {"resolves" if nfd_same else "does not resolve"} to the NFC file here; '
      'either way the text is not plain, so it is unresolved')
for form in ('NFC', 'NFD'):
    text = f'bash {tree}/' + unicodedata.normalize(form, 'caf\u00e9.sh')
    found, missed = classify(text)
    if found or not (len(missed) == 1 and missed[0].startswith('unreadable U+')):
        failed.append(f'  identity ({form}): {text!r} is not unresolved as a whole: {found} {missed}')
# A file with many names on a read-only file system (macOS's system volume) is not a hard-link
# risk: no name of it can be edited. Shown when this machine has one.
for probe in ('/usr/bin/python3', '/usr/bin/git'):
    if os.path.exists(probe) and os.stat(probe).st_nlink > 1 and d.read_only(probe):
        if classify(f'{probe} -V') != ([], []):
            failed.append(f'  identity: {probe} (read-only, {os.stat(probe).st_nlink} names) is annotated')
        else:
            print(f'identity: {probe} has {os.stat(probe).st_nlink} names on a read-only volume: no hard-link annotation')
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
# Codex user hooks share the settings.json shape: a third source.
OPT_CODEX="$TMP/codex-hooks.json"
python3 - "$OPT_CODEX" "$TREE" <<'PY_CODEX'
import json, sys
json.dump({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': f'python3 {sys.argv[2]}/hook.py codex-stop', 'timeout': 5}]}]}},
          open(sys.argv[1], 'w'))
PY_CODEX
# Not read: a project-level settings file inside the tree (a fixture that would be flagged if it were).
mkdir -p "$TREE/.claude"
printf '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "%s/tracked.sh"}]}]}}' "$TREE" > "$TREE/.claude/settings.json"
python3 -I "$ENTRIES" --report --json --launch-agents "$OPT_AGENTS" --settings "$OPT_SETTINGS" --codex-hooks "$OPT_CODEX" >"$TMP/json"
python3 - "$TMP/json" "$TREE" "$TMP/linked" "$OPT_AGENTS" "$OPT_SETTINGS" "$OPT_CODEX" <<'PY_EXPECT'
import json, sys
d = json.load(open(sys.argv[1]))
tree, linked, agents, settings, codex = sys.argv[2:]
assert sorted(d) == ['baseline_recorded_at', 'entries', 'error', 'mode', 'new', 'removed', 'schema', 'unchanged'], sorted(d)
assert (d['schema'], d['mode'], d['error'], d['new'], d['removed'], d['unchanged']) == (3, 'report', None, None, None, None), d


def label(item):  # the earlier names of the fixture entries, so the expectations below read as before
    if item['source'] == agents:
        return 'launchd:' + item['entry'][:-len('.plist')]
    return ('codex-hook:' if item['source'] == codex else 'claude-hook:') + item['entry']


entries = d['entries']
# Scope: com.broomva.* agents, every hook object of the user's Claude settings and of the Codex user
# hooks file (a prompt-type hook is an entry like any other). Not read: other labels, a project-level
# settings file, LaunchDaemons, cron.
assert {item['source'] for item in entries} == {agents, settings, codex}, {item['source'] for item in entries}
assert not any('org.example' in item['entry'] for item in entries)
assert len([i for i in entries if i['source'] == settings]) == 12, 'a prompt-type hook is an entry'
assert any(i['entry'] == 'SessionStart' and '"type":"prompt"' in i['command'] for i in entries)
for item in entries:
    assert len(item['sha256']) == 64 and item['annotation']['class'] in ('flagged', 'unresolved', 'no working-tree file found'), item
want = sorted([
    ('claude-hook:PostToolUse', f'{tree}/notes.md', tree),
    ('claude-hook:PreToolUse[Bash]', f'{tree}/hook.py', tree),
    ('claude-hook:Stop', '~/bin/linked.sh', tree),
    ('codex-hook:Stop', f'{tree}/hook.py', tree),
    ('launchd:com.broomva.cd', f'{tree}/sub/rel.py', tree),
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
got = sorted((label(i), f['file'], f['repository']) for i in entries for f in i['annotation']['files'])
assert got == want, '\n' + '\n'.join(map(str, sorted(set(got) ^ set(want))))
want_reasons = sorted([
    ('claude-hook:SessionStart', '"$CLAUDE_PROJECT_DIR"/tracked.sh', 'unreadable `$C` at column 2'),
    ('launchd:com.broomva.gitshow', "/bin/bash -lc 'T=$(mktemp); git -C \"" + tree + "\" show origin/main:tracked.sh > \"$T\" "
     "|| exit 0; bash \"$T\"; rm -f \"$T\"'", 'unreadable `$(` at column 3 of argument 2'),
    ('claude-hook:UserPromptSubmit', "bash -c 'cd scripts && ./doctor.sh'", './doctor.sh'),
    ('claude-hook:UserPromptSubmit', 'python3 scripts/y.py --fast', 'scripts/y.py'),
    ('launchd:com.broomva.cd', f"/bin/bash -c 'cd {tree}/sub && /usr/bin/python3 ./rel.py harvest'", './rel.py'),
    ('launchd:com.broomva.relative', "/bin/bash -c './run.sh nightly'", './run.sh'),
])
got_reasons = sorted((label(i), i['command'], r) for i in entries for r in i['annotation']['reasons'])
assert got_reasons == want_reasons, '\n' + '\n'.join(map(str, sorted(set(got_reasons) ^ set(want_reasons))))
counts = {}
for item in entries:
    counts[item['annotation']['class']] = counts.get(item['annotation']['class'], 0) + 1
assert counts == {'flagged': 14, 'unresolved': 5, 'no working-tree file found': 11}, counts
PY_EXPECT
python3 -I "$ENTRIES" --report --launch-agents "$OPT_AGENTS" --settings "$OPT_SETTINGS" --codex-hooks "$OPT_CODEX" >"$TMP/out"
grep -Fq "[info] $OPT_AGENTS com.broomva.direct.plist: /bin/bash $TREE/tracked.sh compile [annotation, best effort: flagged: $TREE/tracked.sh (repository $TREE)]" "$TMP/out"
grep -Fxq '[info] 30 entries (report only: no baseline read or written); annotation, best effort: 14 flagged, 11 no working-tree file found, 5 unresolved' "$TMP/out"
if grep -q 'clean' "$TMP/out"; then echo 'the report calls an entry clean' >&2; exit 1; fi
# Without the options the fixture home is read (empty so far), never HOME's decoy.
python3 -I "$ENTRIES" --report >"$TMP/out"
grep -Fq '[info] 0 entries (report only: no baseline read or written)' "$TMP/out"
if python3 -I "$ENTRIES" --report --baseline-entries >"$TMP/out" 2>&1; then echo '--report accepted a baseline write' >&2; exit 1; fi
[ ! -e "$HOME_FIXTURE/.broomva/state" ] || { echo '--report wrote under the machine state path' >&2; exit 1; }
[ ! -e "$HOME/.broomva" ] || { echo 'the check wrote under HOME' >&2; exit 1; }

# ── the ratchet: on the identity of each entry; the annotation never decides ──
CODEX="$HOME_FIXTURE/.codex/hooks.json"
mkdir -p "$HOME_FIXTURE/.codex"
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
python3 - "$SETTINGS" "$CODEX" "$TREE" <<'PY_SETTINGS'
import json, sys
json.dump({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': f'python3 -I {sys.argv[3]}/hook.py stop', 'timeout': 30},
                                         {'type': 'command', 'command': 'echo report.sh'}]}]}}, open(sys.argv[1], 'w'))
json.dump({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': "bash -c 'cd scripts && ./doctor.sh'", 'timeout': 5}]}]}},
          open(sys.argv[2], 'w'))
PY_SETTINGS
run_doctor_verbose
assert_not_enabled 'entries present'
run_doctor_verbose --strict || true
assert_not_enabled 'strict'
# Recording acknowledges, and says what it acknowledges.
bash "$DOCTOR" --baseline-entries >"$TMP/out"
grep -Fq 'acknowledged 4 new or changed, 0 removed, 0 unchanged:' "$TMP/out"
grep -Fq "  new (0 -> 1): ~/Library/LaunchAgents com.broomva.existing.plist: /bin/bash $TREE/tracked.sh [annotation, best effort: flagged: $TREE/tracked.sh (repository $TREE)]" "$TMP/out"
grep -Fq '  new (0 -> 1): ~/.claude/settings.json Stop: echo report.sh [annotation, best effort: no working-tree file found]' "$TMP/out"
grep -Fq "  new (0 -> 1): ~/.codex/hooks.json Stop: bash -c 'cd scripts && ./doctor.sh' [annotation, best effort: unresolved: ./doctor.sh]" "$TMP/out"
if grep -q 'decoy' "$TMP/out" "$BASELINE"; then echo 'the baseline read HOME, not the fixture home' >&2; exit 1; fi
run_doctor_verbose
grep -Fq '[info] 4 acknowledged entries unchanged' "$TMP/out"
run_doctor
if grep -q 'working-tree entry\|acknowledged' "$TMP/out"; then echo 'quiet mode printed a passing ratchet' >&2; exit 1; fi
if PATH="$TMP/no-python:$PATH" run_doctor; then echo 'enabled ratchet without python passed' >&2; exit 1; fi
if bash "$DOCTOR" --baseline-entries >"$TMP/out" 2>&1; then echo 'baseline overwrite passed' >&2; exit 1; fi
# A NEW entry fails whatever its annotation; this one names no working-tree file at all.
plist "$AGENTS" com.broomva.new /bin/echo hello
if run_doctor; then echo 'a new entry passed' >&2; exit 1; fi
grep -Fq '[failure] new entry since the baseline (0 -> 1): ~/Library/LaunchAgents com.broomva.new.plist: /bin/echo hello [annotation, best effort: no working-tree file found]' "$TMP/out"
grep -Fq 'review it, then acknowledge it with bstack doctor --baseline-entries --force' "$TMP/out"
grep -Fq 'it never decides' "$TMP/out" || grep -Fq 'never decides' "$TMP/out"
grep -Fq '[gap] working-tree entry ratchet failed' "$TMP/out"
rm "$AGENTS/com.broomva.new.plist"
run_doctor
# A CHANGED entry fails: one trailing space in a hook command.
cp "$SETTINGS" "$TMP/settings.good"
python3 - "$SETTINGS" <<'PY_CHANGE'
import json, sys
d = json.load(open(sys.argv[1]))
d['hooks']['Stop'][0]['hooks'][1]['command'] += ' '
json.dump(d, open(sys.argv[1], 'w'))
PY_CHANGE
if run_doctor; then echo 'a changed entry passed' >&2; exit 1; fi
grep -Fq '[failure] changed entry since the baseline (0 -> 1): ~/.claude/settings.json Stop: echo report.sh  [annotation, best effort: no working-tree file found]' "$TMP/out"
cp "$TMP/settings.good" "$SETTINGS"
run_doctor
# One more COPY of an acknowledged hook fails: entries are counted, not collapsed.
python3 - "$SETTINGS" <<'PY_COPY'
import json, sys
d = json.load(open(sys.argv[1]))
d['hooks']['Stop'][0]['hooks'].append(dict(d['hooks']['Stop'][0]['hooks'][1]))
json.dump(d, open(sys.argv[1], 'w'))
PY_COPY
if run_doctor; then echo 'a second identical hook passed' >&2; exit 1; fi
grep -Fq '[failure] one more copy of an entry since the baseline (1 -> 2): ~/.claude/settings.json Stop: echo report.sh' "$TMP/out"
cp "$TMP/settings.good" "$SETTINGS"
run_doctor
# A REMOVED entry passes and is reported; re-recording says it is acknowledged as removed.
mv "$AGENTS/com.broomva.existing.plist" "$TMP/existing.plist"
run_doctor_verbose
grep -Fq '[info] acknowledged entry is gone: ~/Library/LaunchAgents com.broomva.existing.plist (1 -> 0;' "$TMP/out"
bash "$DOCTOR" --baseline-entries --force >"$TMP/out"
grep -Fq 'acknowledged 0 new or changed, 1 removed, 3 unchanged:' "$TMP/out"
grep -Fq '  removed: ~/Library/LaunchAgents com.broomva.existing.plist (' "$TMP/out"
mv "$TMP/existing.plist" "$AGENTS/com.broomva.existing.plist"
bash "$DOCTOR" --baseline-entries --force >"$TMP/out"
grep -Fq 'acknowledged 1 new or changed, 0 removed, 3 unchanged:' "$TMP/out"
run_doctor

# ── the property: for a recorded baseline, ANY change to any entry fails and names it ──
python3 - "$ENTRIES" "$AGENTS" "$SETTINGS" "$CODEX" "$TREE" <<'PY_MUTATE'
import contextlib, datetime, importlib.util, io, json, os, plistlib, shutil, sys
entries_path, agents, settings, codex, tree = sys.argv[1:]
sys.path.insert(0, os.path.dirname(entries_path))
spec = importlib.util.spec_from_file_location('entries', entries_path)
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
# One more agent that uses every hashed key.
with open(f'{agents}/com.broomva.env.plist', 'wb') as stream:
    plistlib.dump({'Label': 'env', 'Program': '/bin/echo', 'ProgramArguments': ['/bin/echo', 'hello  world'],
                   'WorkingDirectory': tree, 'EnvironmentVariables': {'A': '1', 'PATH': '/usr/bin:/bin'},
                   'RunAtLoad': True, 'XDate': datetime.datetime(2026, 1, 2, 3, 4, 5), 'XData': b'\x00\xff'}, stream)
# The canonical form, defined once: sorted keys, no added whitespace, dates as ISO text, data as hex.
assert d.canonical({'b': b'\x00\xff', 'a': datetime.datetime(2026, 1, 2, 3, 4, 5), 's': ' x '}, 'test') == \
    '{"a":{"$date":"2026-01-02T03:04:05"},"b":{"$data":"00ff"},"s":" x "}'


def run(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = d.main(d.arguments([*args, '--json']))
    return code, json.loads(out.getvalue())


code, rec = run('--baseline-entries', '--force')
assert code == 0 and rec['action'] == 'recorded', rec
code, base = run()
assert code == 0 and base['new'] == [] and base['unchanged'] == 5, base
saved = {path: open(path, 'rb').read() for path in (settings, codex, *(f'{agents}/{n}' for n in os.listdir(agents)))}


def restore():
    for path, data in saved.items():
        open(path, 'wb').write(data)
    for name in os.listdir(agents):
        if f'{agents}/{name}' not in saved:
            os.remove(f'{agents}/{name}')


def mutations(text):
    """Insert, delete and replace one character at several positions, whitespace included."""
    spots = sorted({0, len(text) // 2, len(text)} | ({text.index(' ')} if ' ' in text else set()))
    for at in spots:
        for c in (' ', '\t', 'x'):
            yield text[:at] + c + text[at:]
        if at < len(text):
            yield text[:at] + text[at + 1:]
            yield text[:at] + ('x' if text[at] != 'x' else 'y') + text[at + 1:]
            yield text[:at] + (' ' if text[at] != ' ' else '_') + text[at + 1:]


# Every text of every fixture entry: (file, how to rewrite it with a new text, original text, entry name).
targets = []
for name in sorted(os.listdir(agents)):
    data = plistlib.load(open(f'{agents}/{name}', 'rb'))
    for k, arg in enumerate(data.get('ProgramArguments', [])):
        targets.append((f'{agents}/{name}', name, ('ProgramArguments', k), arg))
    for field in ('Program', 'WorkingDirectory'):
        if field in data:
            targets.append((f'{agents}/{name}', name, (field, None), data[field]))
    for var, value in data.get('EnvironmentVariables', {}).items():
        targets.append((f'{agents}/{name}', name, ('EnvironmentVariables', var), value))
for path in (settings, codex):
    data = json.load(open(path))
    for event, groups in data['hooks'].items():
        for g, group in enumerate(groups):
            for h, hook in enumerate(group['hooks']):
                targets.append((path, event, (event, g, h), hook['command']))


def write(path, where, value):
    if path.endswith('.plist'):
        data = plistlib.load(open(path, 'rb'))
        field, index = where
        if index is None:
            data[field] = value
        else:
            data[field][index] = value
        plistlib.dump(data, open(path, 'wb'))
    else:
        data = json.load(open(path))
        event, g, h = where
        data['hooks'][event][g]['hooks'][h]['command'] = value
        json.dump(data, open(path, 'w'))


failed, total = [], 0
for path, entry, where, original in targets:
    for text in dict.fromkeys(mutations(original)):
        if text == original:
            continue
        total += 1
        write(path, where, text)
        code, out = run()
        if code != 1 or not any(item['entry'] == entry for item in out['new']):
            failed.append(f'  mutation of {entry} {where}: {text!r} -> exit {code}, new {[i["entry"] for i in out["new"]]}')
        restore()

def expect_fail(label, entry, change):
    global total
    total += 1
    change()
    code, out = run()
    if code != 1 or not any(item['entry'] == entry for item in out['new']):
        failed.append(f'  {label} of {entry}: exit {code}, new {[(i["entry"], i["count"]) for i in out["new"]]}')
    restore()


def expect_pass(label, change):
    global passes, total_pass
    total_pass += 1
    change()
    code, out = run()
    if code == 0 and out['new'] == [] and out['removed'] == []:
        passes += 1
    else:
        failed.append(f'  {label}: exit {code}, new {[i["entry"] for i in out["new"]]}, removed {out["removed"]}')
    restore()


def edit_json(path, fn):
    def change():
        data = json.load(open(path))
        fn(data)
        json.dump(data, open(path, 'w'))
    return change


def edit_plist(path, fn, sort_keys=True):
    def change():
        data = plistlib.load(open(path, 'rb'))
        data = fn(data) or data
        plistlib.dump(data, open(path, 'wb'), sort_keys=sort_keys)
    return change


hooks = [(path, event, g, h) for path in (settings, codex) for event, groups in json.load(open(path))['hooks'].items()
         for g, group in enumerate(groups) for h, _ in enumerate(group['hooks'])]
plists = sorted(n for n in os.listdir(agents) if n.endswith('.plist'))
# (a) one more copy of each entry
for path, event, g, h in hooks:
    expect_fail('a copy', event, edit_json(path, lambda d, e=event, g=g, h=h: d['hooks'][e][g]['hooks'].append(dict(d['hooks'][e][g]['hooks'][h]))))
for name in plists:
    copy = name.replace('com.broomva.', 'com.broomva.copy-')  # a plist's file name is part of its identity
    expect_fail('a copy', copy, lambda n=name, c=copy: shutil.copy(f'{agents}/{n}', f'{agents}/{c}'))
# (b) every non-command field of a hook, and plist keys outside the earlier four
for path, event, g, h in hooks:
    def setter(field, value):
        return lambda d, e=event, g=g, h=h: d['hooks'][e][g]['hooks'][h].__setitem__(field, value)
    expect_fail('type', event, edit_json(path, setter('type', 'commandx')))
    expect_fail('timeout', event, edit_json(path, lambda d, e=event, g=g, h=h: d['hooks'][e][g]['hooks'][h].__setitem__(
        'timeout', d['hooks'][e][g]['hooks'][h].get('timeout', 0) + 1)))
    expect_fail('an extra key', event, edit_json(path, setter('x', 1)))
for name in plists:
    for field, value in (('StartInterval', 60), ('StandardOutPath', '/tmp/out.log'), ('Label', 'other'),
                         ('XDate', datetime.datetime(2027, 1, 1)), ('XData', b'\x01')):
        expect_fail(field, name, edit_plist(f'{agents}/{name}', lambda d, f=field, v=value: d.__setitem__(f, v)))
    expect_fail('RunAtLoad', name, edit_plist(f'{agents}/{name}', lambda d: d.__setitem__('RunAtLoad', not d.get('RunAtLoad', False))))
# (c) reordering keys inside an entry changes nothing; (d) nor does reordering entries in a list,
# because the baseline is a multiset of identities: the same entries, counted, in any order.
passes = total_pass = 0
for path, event, g, h in hooks:
    expect_pass(f'key order of {event}', edit_json(path, lambda d, e=event, g=g, h=h: d['hooks'][e][g]['hooks'].__setitem__(
        h, dict(reversed(list(d['hooks'][e][g]['hooks'][h].items()))))))
for name in plists:
    expect_pass(f'key order of {name}', edit_plist(f'{agents}/{name}', lambda d: dict(reversed(list(d.items()))), sort_keys=False))
expect_pass('two hooks swapped in a list', edit_json(settings, lambda d: d['hooks']['Stop'][0]['hooks'].reverse()))
# Added entries: a new agent, a new hook in each hooks file.
# Added entries: a new agent, a new hook in each hooks file.
additions = [
    lambda: plistlib.dump({'Label': 'added', 'ProgramArguments': ['/bin/echo', 'added']}, open(f'{agents}/com.broomva.added.plist', 'wb')),
    lambda: write_hook(settings), lambda: write_hook(codex)]


def write_hook(path):
    data = json.load(open(path))
    data['hooks'].setdefault('SessionEnd', []).append({'hooks': [{'type': 'command', 'command': 'true'}]})
    json.dump(data, open(path, 'w'))


for k, add in enumerate(additions):
    total += 1
    add()
    code, out = run()
    if code != 1 or len(out['new']) != 1:
        failed.append(f'  addition {k}: exit {code}, new {out["new"]}')
    restore()
code, out = run()
if code != 0 or out['new'] != []:
    failed.append(f'  the unchanged machine does not pass: {code} {out}')
os.rename(f'{agents}/com.broomva.env.plist', f'{agents}/env.removed')
code, out = run()
if code != 0 or [r['entry'] for r in out['removed']] != ['com.broomva.env.plist']:
    failed.append(f'  a removed entry does not pass: {code} {out}')
os.rename(f'{agents}/env.removed', f'{agents}/com.broomva.env.plist')
print(f'mutation: {total - (len(failed) - (total_pass - passes))} of {total} changes to recorded entries fail and name the entry '
      f'(text edits of {len(targets)} texts, copies, non-command fields, plist keys, additions); '
      f'{passes} of {total_pass} reorderings pass')
if failed:
    print('\n'.join(failed[:20]))
    sys.exit(1)
os.remove(f'{agents}/com.broomva.env.plist')
code, rec = run('--baseline-entries', '--force')
assert code == 0 and [r['entry'] for r in rec['removed']] == ['com.broomva.env.plist'], rec
PY_MUTATE

# ── --json: one object in every mode, with the exit code of the plain run ──
json_mode() {  # json_mode EXPECTED_EXIT PYTHON_ASSERTION ARG...
    want="$1" assertion="$2"; shift 2
    set +e
    python3 -I "$ENTRIES" "$@" >"$TMP/json" 2>"$TMP/json.err"
    rc=$?
    set -e
    [ "$rc" = "$want" ] || { echo "$* exited $rc, wanted $want" >&2; cat "$TMP/json" "$TMP/json.err" >&2; exit 1; }
    [ ! -s "$TMP/json.err" ] || { echo "$* wrote to stderr" >&2; cat "$TMP/json.err" >&2; exit 1; }
    [ "$(wc -l <"$TMP/json" | tr -d ' ')" = "1" ] || { echo "$* did not print exactly one line" >&2; cat "$TMP/json" >&2; exit 1; }
    python3 - "$TMP/json" "$assertion" "$BASELINE" <<'PY_MODE'
import json, sys
d = json.load(open(sys.argv[1]))
baseline = sys.argv[3]
assert d['schema'] == 3 and 'error' in d, d
assert eval(sys.argv[2]), (sys.argv[2], d)
PY_MODE
}
unreadable="d['error'] and d['new'] is None and d['removed'] is None and d['unchanged'] is None"
json_mode 0 "d['mode'] == 'check' and d['new'] == [] and d['removed'] == [] and d['unchanged'] == 4" --json
json_mode 0 "d['mode'] == 'report' and len(d['entries']) == 4" --report --json
plist "$AGENTS" com.broomva.new /bin/bash "$TREE/tracked.sh"
json_mode 1 "[(i['entry'], i['annotation']['class']) for i in d['new']] == [('com.broomva.new.plist', 'flagged')]" --json
json_mode 1 "d['mode'] == 'baseline' and d['action'] is None and d['baseline'] == baseline and 'File exists' in d['error']" --baseline-entries --json
json_mode 0 "d['mode'] == 'baseline' and d['action'] == 'recorded' and [i['entry'] for i in d['new']] == ['com.broomva.new.plist'] and d['unchanged'] == 4" --baseline-entries --force --json
rm "$AGENTS/com.broomva.new.plist"
json_mode 0 "[r['entry'] for r in d['removed']] == ['com.broomva.new.plist']" --json
bash "$DOCTOR" --baseline-entries --force >"$TMP/out"
json_mode 2 "d['mode'] is None and d['error'].startswith('usage: ')" --report --baseline-entries --json
json_mode 2 "'unrecognized arguments' in d['error']" --json --no-such-option
# A source that exists but cannot be read or parsed fails and names it, never a pass or an empty list.
source_fails() {  # source_fails NAME — the current inputs must fail, naming NAME, plain and --json
    json_mode 1 "$unreadable and '$1' in d['error']" --json
    if run_doctor; then echo "an unreadable source passed ($1)" >&2; exit 1; fi
    grep -Fq "$1" "$TMP/out" || { echo "the failure does not name $1" >&2; exit 1; }
}
printf 'not a plist' > "$AGENTS/com.broomva.broken.plist"
source_fails com.broomva.broken.plist
rm "$AGENTS/com.broomva.broken.plist"
cp "$AGENTS/com.broomva.existing.plist" "$AGENTS/com.broomva.locked.plist"
chmod 000 "$AGENTS/com.broomva.locked.plist"
if [ -r "$AGENTS/com.broomva.locked.plist" ]; then
    echo '  skipped (unreadable plist): this user can read a mode-000 file (root)'
else
    source_fails com.broomva.locked.plist
fi
chmod 600 "$AGENTS/com.broomva.locked.plist"
rm "$AGENTS/com.broomva.locked.plist"
printf '{bad json' > "$SETTINGS"
source_fails settings.json
mv "$SETTINGS" "$TMP/settings.moved"
mkdir "$SETTINGS"
source_fails settings.json
rmdir "$SETTINGS"
mv "$TMP/settings.moved" "$SETTINGS"
cp "$TMP/settings.good" "$SETTINGS"
cp "$CODEX" "$TMP/codex.good"
# Every unexpected shape in a hooks file fails and names the file and the JSON path.
while IFS='|' read -r body path; do
    printf '%s' "$body" > "$CODEX"
    source_fails "hooks.json: $path"
done <<'SHAPES'
[1]|$ is not an object
{"hooks": [1, 2]}|hooks is not an object
{"hooks": {"Stop": {}}}|hooks.Stop is not a list
{"hooks": {"Stop": null}}|hooks.Stop is not a list
{"hooks": {"Stop": ["echo hi"]}}|hooks.Stop[0] is not an object
{"hooks": {"Stop": [{"matcher": "x"}]}}|hooks.Stop[0].hooks is missing or not a list
{"hooks": {"Stop": [{"hooks": null}]}}|hooks.Stop[0].hooks is missing or not a list
{"hooks": {"Stop": [{"hooks": "echo"}]}}|hooks.Stop[0].hooks is missing or not a list
{"hooks": {"Stop": [{"hooks": [], "when": "always"}]}}|hooks.Stop[0] is an object with unexpected keys
{"hooks": {"Stop": [{"hooks": ["echo hi"]}]}}|hooks.Stop[0].hooks[0] is not an object
{"hooks": {"Stop": [{"hooks": [null]}]}}|hooks.Stop[0].hooks[0] is not an object
SHAPES
cp "$TMP/codex.good" "$CODEX"
python3 -c 'import plistlib, sys; plistlib.dump([1, 2], open(sys.argv[1], "wb"))' "$AGENTS/com.broomva.list.plist"
source_fails 'com.broomva.list.plist: not a dictionary'
rm "$AGENTS/com.broomva.list.plist"
# git that cannot answer for a file is a failure, never "untracked".
mkdir -p "$TMP/broken-tree"
printf 'not a git dir\n' > "$TMP/broken-tree/.git"
echo '#' > "$TMP/broken-tree/run.sh"
plist "$AGENTS" com.broomva.unknown /bin/bash "$TMP/broken-tree/run.sh"
json_mode 1 "$unreadable and 'git ls-files failed' in d['error']" --json
rm "$AGENTS/com.broomva.unknown.plist"
run_doctor
# A missing or broken baseline.
printf '{bad json' > "$BASELINE"
json_mode 1 "$unreadable and 'working-tree entry check' in d['error']" --json
if run_doctor; then echo 'malformed baseline passed' >&2; exit 1; fi
printf '{"schema": 2, "recorded_at": "x", "entries": []}' > "$BASELINE"
json_mode 1 "$unreadable and 'invalid baseline format' in d['error']" --json
rm "$BASELINE"
ln -s "$TMP/missing-baseline" "$BASELINE"
json_mode 1 "$unreadable and 'working-tree entry check' in d['error']" --json
if run_doctor; then echo 'dangling baseline passed' >&2; exit 1; fi
rm "$BASELINE"
json_mode 42 "$unreadable and 'no baseline recorded' in d['error']" --json
[ ! -e "$HOME/.broomva" ] || { echo 'the check wrote under HOME' >&2; exit 1; }
echo 'working-tree entry ratchet: passed'
