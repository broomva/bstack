#!/usr/bin/env python3
"""Baseline and audit scheduled or hooked commands that name a file in a git working tree.

A LaunchAgent or a Claude Code hook that names a file tracked in a working tree
uses whatever that checkout holds: an edit takes effect on the next run, without
a commit, a review or a merge. That is as true of a file the entry only reads
(`cat`, a config, a prompt) as of one it executes, so this check does not ask
which: a hook that reads a file from a working tree feeds it unreviewed content
just as one that executes it does. Two surfaces are read, as raw text:

  - every com.broomva.*.plist in the LaunchAgents folder: `Program` and each
    `ProgramArguments` string, each scanned on its own (so the string of a
    `bash -c` is covered without being parsed);
  - every hook `command` in the personal Claude Code settings.json.

THE CONTRACT. This check certifies only entries it can read completely; an
entry containing any shell construct outside a small plain grammar is reported
as unresolved and must be acknowledged in the baseline.

  The plain grammar (READABLE_CHARS and its neighbours below, written down
  once): ASCII letters and digits; `_ - . / : = , + @ %`; space and tab; single
  and double quotes; the operator characters `& | ; < >` (so `&&`, `||`, `;`,
  `|`, `>`, `>>`, `<`, `2>&1`, `&`); `~` at the start of a word; and exactly
  `$HOME` and `${HOME}`. Anything else anywhere in an entry (any other `$`, a
  backquote, `( ) { } * ? [ ] ! #`, a backslash, a `~` inside a word, a newline, any
  control character, any non-ASCII character including non-breaking and
  zero-width spaces) makes the WHOLE entry unresolved, with one reason naming
  the first such construct and its column. Nothing in it is interpreted: no
  line continuation, escape, substitution, expansion or quoting form is
  decoded. For a LaunchAgent the entry is the Program and every
  ProgramArguments string together.

  What that costs. An entry that uses a variable or a substitution is
  unresolved, including the recommended form `T=$(mktemp); git -C /t show
  origin/main:<path> > "$T" && bash "$T"`: it needs a baseline line. That is
  accepted because scheduled jobs and hooks are added rarely, and a new one
  should be looked at once; a reviewed baseline line is that look.

Only a fully readable entry is scanned. The principle of the scan: a reading may
only ADD candidates; the raw text is always a candidate too. No rule replaces the
text it interprets (a word by its value after `=`, a word joined with the next),
so there is no word boundary to get wrong. The check never splits commands,
follows a `cd`, or models a wrapper or an interpreter's options:

  ABSOLUTE paths are found by asking the file system. From every place in the
  text where an absolute path can start (a `/`, a `~/` at the start of a word,
  `$HOME`, `${HOME}`), every end position to the right is tried, on the text as
  written and with quote characters removed. A candidate that is an existing
  file (following symlinks) tracked by a git working tree flags the entry
  (walk up for `.git`; `git ls-files` is read once per repository; a linked
  worktree counts). So `=`, `:`, `,`, `+`, `@`, `%`, spaces and quotes in a
  file name do not matter, and a file is flagged wherever it appears: as the
  command, as an interpreter's script, inside an option (`-o/t/x`), as a
  `NAME=/t/x` value, or as an argument that is only read. A shorter prefix that
  names a tracked file also counts (`/t/x.sh.bak` flags `/t/x.sh` if that is
  tracked): the conservative side. A missing, untracked or out-of-tree file is
  not reported: a released copy outside any working tree passes.

  FILES ARE COMPARED BY IDENTITY, never by path string. A candidate exists if
  os.stat succeeds; whether it is a file or a directory comes from that stat;
  its identity is (device, inode). The working tree it sits in is found by
  walking up from its real path asking the file system for `.git`. Each tree's
  tracked set is built once: `git ls-files`, then the identity of each listed
  path. A candidate is tracked when its identity is in that set, so letter case
  on a case-insensitive volume, `//`, `/./`, `..`, symlinks to the file or its
  directory, and a hard link inside the tree all name the tracked file. The scan
  skips text that cannot be a directory entry by a folded spelling (Unicode
  decomposed, case folded), which only ever merges names; it never decides.

  A HARD LINK that is not tracked where it sits (or sits in no working tree)
  makes the entry unresolved: the file has more than one name, and this check
  cannot tell whether another name is tracked. That is the conservative side:
  an outside copy made with `ln` instead of `cp` costs a baseline line, or a
  real copy. A file on a read-only file system (macOS's system volume, where
  `/usr/bin/python3` and `/usr/bin/git` have dozens of names) is exempt, since
  no name of it can be edited.

  DIRECTORIES met by the same scan that lie in a git working tree, and a
  LaunchAgent's WorkingDirectory, are joined with every place a relative name
  can start (the start of a token, after a quote, after `=`, `:` or `,`), again
  trying every end position. When that names a tracked file the entry is
  flagged with it, in any order and with no `cd` tracking: `cd /t && python3
  x.py`, `cd /t && bash 'A=1 B.sh'`, `git -C /t show HEAD:x.py` and
  `python3 x.py; ls /t/sub` (the tree itself is on the way to `/t/sub`) are all
  flagged. A directory by itself is never reported. A false flag costs one
  baseline line or an absolute rewrite; a missed entry runs unreviewed code.

  A RELATIVE path word is UNRESOLVED: this check cannot tell what it names, and
  the remedy is an absolute path. Tokens end at whitespace and operators, quotes
  are removed; a token with no `=`, `:` or `,` is judged whole, otherwise each
  piece between those characters is judged, so every `/` in it is seen. A piece
  holding a `/` is unresolved unless it starts an absolute path, starts with
  `-`, is one of /dev/null, /dev/stdin, /dev/stdout, /dev/stderr, or lies inside
  an absolute path the scan found (`dir/x.sh` in `"/t/my dir/x.sh"`). Never
  judged: a URL (`scheme://`) and a git object name `<ref>:<path>`. In a
  LaunchAgent with a WorkingDirectory, relative pieces resolve against it
  instead. Words that are not file paths at all (`origin/main`, `text/plain`)
  are unresolved too. `cd /t && ./x.sh` is flagged (directory x token) and
  `./x.sh` is still unresolved.

A fully readable entry with nothing flagged and nothing unresolved is clean.
Flagged and unresolved entries both ratchet: recorded in the baseline, printed
as info while grandfathered, and a new one fails.

What this check does not see: content fetched at run time by a program the
entry names (a script that reads another file, a URL it downloads).

  doctor-worktree-entries.py                    audit against the baseline (doctor section 33)
  doctor-worktree-entries.py --report           list every flagged and unresolved entry; reads and writes no baseline
  doctor-worktree-entries.py --baseline-entries [--force]   record the baseline
  options: --json, --launch-agents DIR, --settings FILE

Exit codes, with or without --json: 0 no new flagged or unresolved entry
(always, for --report), 1 a new flagged or unresolved entry, a broken baseline
or an input that could not be read (or a baseline that could not be recorded),
42 no baseline recorded, 2 a usage error.

With --json every mode prints exactly one JSON object, errors included. It is
{"schema": 1, "mode": "check"|"report"|"baseline", "baseline_recorded_at",
"flagged": [{"entry", "file", "repository"}], "unresolved": [{"entry",
"command", "word"}], "grandfathered": <count>, "error"}. "flagged" and
"unresolved" hold the entries that are not in the baseline (every one, for
--report, where "grandfathered" is null); "grandfathered" counts both kinds. On
an error "flagged", "unresolved" and "grandfathered" are null: "could not look"
is never an empty list. Mode "baseline" (--baseline-entries) adds "action"
("recorded", or null when nothing was written) and "baseline" (the baseline
file's path); "flagged" and "unresolved" are everything recorded and
"grandfathered" is null. A usage error prints mode null with the message in
"error".
"""
import argparse
import datetime
import json
import os
import plistlib
import re
import shlex
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path

# Isolated Python (-I) omits the script directory from sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import broomva_paths as paths

BASELINE_NAME = 'doctor-worktree-entries.json'
GIT_TIMEOUT = 10
FIELDS = ('entry', 'file', 'repository')
UNRESOLVED_FIELDS = ('entry', 'command', 'word')
HOME_PREFIXES = ('~/', '$HOME/', '${HOME}/')  # expanded in a LaunchAgent's WorkingDirectory
# ── The readable text of an entry. Written down once: anything else in an entry
# makes the whole entry unresolved (see read_entry). ──
READABLE_CHARS = frozenset(
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'  # names: ASCII letters and digits
    + '_-./:=,+@%'                                                     # path and argument punctuation
    + ' \t'                                                            # word separators: space and tab
    + '\'"'                                                            # single and double quotes
    + '&|;<>'                                                          # operators: && || ; | > >> < 2>&1 &
)
HOME_FORMS = ('${HOME}', '$HOME')  # the only `$` forms: exactly these, `$HOME` not followed by a name character
TILDE_FOLLOWS = frozenset(' \t&|;<>=')  # `~` is readable only at the start of a word: after these or at the start
NAME_CHARS = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_')
QUOTES = frozenset('\'"')
DELIMITERS = frozenset(' \t;|&<>')  # end a token for the unresolved reading
# A relative reading starts at the start of the text or after one of these: a
# token boundary, a quote, or the `=`, `:`, `,` that separate pieces of a token.
RELATIVE_STARTS_AFTER = frozenset(' \t;|&<>\'"=:,')
STANDARD_STREAMS = {'/dev/null', '/dev/stdin', '/dev/stdout', '/dev/stderr'}
URL = re.compile(r'[A-Za-z][A-Za-z0-9+.-]*://')


def unreadable(text):
    """(construct, index) of the first thing outside the readable text, or None."""
    i, n = 0, len(text)
    while i < n:
        form = next((f for f in HOME_FORMS if text.startswith(f, i)), None)
        if form and (form == '${HOME}' or i + len(form) >= n or text[i + len(form)] not in NAME_CHARS):
            i += len(form)
            continue
        c = text[i]
        if c in READABLE_CHARS or (c == '~' and (i == 0 or text[i - 1] in TILDE_FOLLOWS)):
            i += 1
            continue
        if c == '$' and i + 1 < n and 0x20 < ord(text[i + 1]) < 0x7f:
            return f'`{text[i:i + 2]}`', i
        if 0x20 < ord(c) < 0x7f:
            return f'`{c}`', i
        return f'U+{ord(c):04X}', i
    return None


def expand(word, home):
    for prefix in HOME_PREFIXES:
        if word.startswith(prefix):
            return str(home / word[len(prefix):])
    return word


def without_quotes(text):
    """(text with quote characters removed, index in ``text`` of each kept character)."""
    kept = [i for i, c in enumerate(text) if c not in QUOTES]
    return ''.join(text[i] for i in kept), kept


def identity(st):
    """A file's identity: the same file has one, however its path is spelled."""
    return st.st_dev, st.st_ino


def stat_or_none(path):
    try:
        return os.stat(path)
    except (OSError, ValueError):
        return None


def fold(name):
    """A spelling key that only ever MERGES names: Unicode decomposed, case folded.

    Used solely to skip text that cannot be a name in a directory. Two names a
    file system treats as one (letter case on a case-insensitive volume, NFC and
    NFD on macOS) always fold alike, so the filter never skips a real match;
    whether a candidate exists, and which file it is, is decided by os.stat.
    """
    return unicodedata.normalize('NFD', name).casefold()


def tree_root(path):
    """The top of the git working tree ``path`` sits in, or None (asked of the file system)."""
    top = os.path.dirname(os.path.realpath(path)) if not os.path.isdir(path) else os.path.realpath(path)
    while not os.path.exists(os.path.join(top, '.git')):
        parent = os.path.dirname(top)
        if parent == top:
            return None
        top = parent
    return top


class FileSystem:
    """Directory listings (folded) and stat answers, cached for one entry."""

    def __init__(self):
        self.listings = {}

    def folded_names(self, folder):
        if folder not in self.listings:
            try:
                names = os.listdir(folder)
            except OSError:
                names = []
            self.listings[folder] = frozenset(fold(name) for name in names)
        return self.listings[folder]

    def walk(self, base, rest):
        """Every existing file or directory named by a prefix of ``rest`` read from ``base``.

        Yields (path, stat result, length) for each prefix of ``rest`` (length is
        its number of characters) that os.stat finds, trying EVERY end position: a
        path may hold spaces, `=`, `:`, quotes or any other readable character. A
        component stops growing only when its folded spelling starts no folded
        name in its directory (see fold), so the walk costs a few listings.
        """
        folder, component = base, ''
        for length, c in enumerate(rest, 1):
            if c == '/':
                if component:
                    nested = os.path.join(folder, component)
                    st = stat_or_none(nested)
                    if st is None or not stat.S_ISDIR(st.st_mode):
                        return
                    folder, component = nested, ''
                continue
            component += c
            special = component in ('.', '..')
            key = fold(component)
            names = self.folded_names(folder)
            if not special and not any(name.startswith(key) for name in names):
                return
            if special or key in names:
                joined = os.path.join(folder, component)
                st = stat_or_none(joined)
                if st is not None:
                    yield os.path.normpath(joined), st, length


def absolute_starts(text, home):
    """(index, base directory, offset of the rest) for each place an absolute path can start."""
    for i, c in enumerate(text):
        if c == '/':
            yield i, '/', i
        elif c == '~' and text.startswith('~/', i) and (i == 0 or text[i - 1] in TILDE_FOLLOWS):
            yield i, str(home), i + 1
        elif text.startswith('${HOME}', i):
            yield i, str(home), i + len('${HOME}')
        elif text.startswith('$HOME', i) and (i + 5 >= len(text) or text[i + 5] not in NAME_CHARS):
            yield i, str(home), i + len('$HOME')


def relative_readings(text):
    """(start index, piece) for every piece of the text a relative path could be.

    Tokens end at whitespace and operators; quotes are removed. A token with no
    `=`, `:` or `,` is one piece. Otherwise its pieces are what lies between
    those characters, so every `/` of the token is in some piece and is judged.
    A git object name (`<ref>:<path>`) and a URL are skipped as a whole.
    """
    tokens, start = [], None
    for i, c in enumerate(text + ' '):
        if c in DELIMITERS:
            if start is not None:
                tokens.append((start, text[start:i]))
                start = None
        elif start is None:
            start = i
    for start, token in tokens:
        plain, kept = without_quotes(token)
        if not plain or URL.match(plain):
            continue
        offset = 0
        for value in plain.split('='):
            if ':' in value and not value.startswith(('/', '~', '$', '.')):
                offset += len(value) + 1
                continue  # a git object name, <ref>:<path>
            for piece in re.split('([:,])', value):
                if piece and piece not in ':,':
                    yield start + kept[offset], piece
                offset += len(piece)
            offset += 1


def named_paths(text, home, cwd=None):
    """(existing files a READABLE text names, path words it cannot resolve).

    Called only through read_entry. Readings may only ADD candidates; the raw text
    is always a candidate too:
      - absolute: from every place an absolute path can start (`/`, `~/` at a word
        start, `$HOME`, `${HOME}`), every end position to the right is asked of
        the file system, on the text as written and with quotes removed;
      - directories met on the way that lie in a git working tree, and a
        LaunchAgent's WorkingDirectory, are joined with every relative reading
        start (a word start, after a quote, after `=`, `:` or `,`), again with
        every end position;
      - a relative piece holding a `/` is unresolved (unless a WorkingDirectory
        resolves it), except where it lies inside an absolute path found above.
    """
    fs = FileSystem()
    files, seen, folders, covered = [], set(), {}, []

    def add(path, st):
        if identity(st) not in seen:
            seen.add(identity(st))
            files.append(path)

    def add_folder(path, st):
        folders.setdefault(identity(st), os.path.realpath(path))

    if cwd is not None and stat_or_none(cwd) is not None:
        add_folder(cwd, os.stat(cwd))
    for index, base, rest_at in absolute_starts(text, home):
        rest = text[rest_at:]
        plain, kept = without_quotes(rest)
        for variant, position in ((rest, None), (plain, kept)):
            for path, st, length in fs.walk(base, variant):
                end = rest_at + (length if position is None else position[length - 1] + 1)
                covered.append((index, end))
                if stat.S_ISREG(st.st_mode):
                    add(path, st)
                elif stat.S_ISDIR(st.st_mode) and tree_root(path) is not None:
                    add_folder(path, st)
    starts = [i for i in range(len(text))
              if (i == 0 or text[i - 1] in RELATIVE_STARTS_AFTER)
              and text[i] not in DELIMITERS and text[i] not in '/~$']
    for folder in folders.values():
        for i in starts:
            rest = text[i:]
            for variant in (rest, without_quotes(rest)[0]):
                for path, st, _ in fs.walk(folder, variant):
                    if stat.S_ISREG(st.st_mode):
                        add(path, st)
    unresolved = []
    for start, piece in relative_readings(text):
        if '/' not in piece or piece.startswith(('/', '~/', '$HOME/', '${HOME}/', '-')) or piece in STANDARD_STREAMS:
            continue
        if cwd is not None and not piece.startswith(('~', '$')):
            continue  # resolved against the WorkingDirectory above
        if any(a <= start < b for a, b in covered):
            continue  # part of an absolute path that names something (`/t/my dir/x.sh`)
        if piece not in unresolved:
            unresolved.append(piece)
    return files, unresolved


def read_entry(texts, home, cwd=None):
    """(files, unresolved) for one entry: the single place raw text enters the scan.

    ``texts`` are the entry's strings (a hook's command; a LaunchAgent's Program
    and each ProgramArguments string). If any of them holds anything outside the
    readable text, the WHOLE entry is unresolved, with one reason naming the
    first such construct and its column, and nothing in it is interpreted.
    Otherwise each string is scanned for path words.
    """
    for k, text in enumerate(texts):
        found = unreadable(text)
        if found is not None:
            construct, index = found
            where = f'column {index + 1}' + (f' of argument {k}' if len(texts) > 1 else '')
            return [], [f'unreadable {construct} at {where}']
    files, missed = [], []
    for text in texts:
        named, unresolved = named_paths(text, home, cwd)
        files.extend(named)
        missed.extend(unresolved)
    return files, missed


def launchd_entries(folder, home):
    try:
        with os.scandir(folder) as listing:
            names = sorted(item.name for item in listing
                           if item.name.startswith('com.broomva.') and item.name.endswith('.plist'))
    except FileNotFoundError:
        return
    for plist in (folder / name for name in names):
        try:
            with plist.open('rb') as stream:
                data = plistlib.load(stream)
        except Exception as exc:  # plistlib raises expat errors that are not ValueError
            raise ValueError(f'cannot read {plist}: {exc}') from exc
        if not isinstance(data, dict):
            raise ValueError(f'cannot read {plist}: not a dictionary')
        cwd = data.get('WorkingDirectory')
        cwd = expand(cwd, home) if isinstance(cwd, str) and cwd else None
        cwd = cwd if cwd is not None and os.path.isabs(cwd) else None
        arguments = data.get('ProgramArguments')
        argv = [item for item in arguments if isinstance(item, str)] if isinstance(arguments, list) else []
        program = data.get('Program')
        strings = ([program] if isinstance(program, str) and program else []) + argv
        files, missed = read_entry(strings, home, cwd)
        command = shlex.join(strings)
        yield f'launchd:{plist.name[:-len(".plist")]}', command, files, missed


def hook_entries(settings, home):
    try:
        data = json.loads(settings.read_text(encoding='utf-8'))
    except FileNotFoundError:
        # A dangling settings symlink is a file that could not be read, not an absent one.
        if os.path.lexists(settings):
            raise
        return
    except ValueError as exc:
        raise ValueError(f'cannot read {settings}: {exc}') from exc
    hooks = data.get('hooks') if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return
    for event, groups in hooks.items():
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict):
                continue
            matcher = group.get('matcher')
            label = f'claude-hook:{event}' + (f'[{matcher}]' if isinstance(matcher, str) and matcher else '')
            commands = group.get('hooks')
            for hook in commands if isinstance(commands, list) else []:
                command = hook.get('command') if isinstance(hook, dict) else None
                if isinstance(command, str):
                    named, missed = read_entry([command], home)
                    yield label, command, named, missed


TRACKED = {}  # work-tree top -> identities of the files `git ls-files` lists there, built once per process
HARD_LINK = ('has more than one name; this check cannot tell whether another name is tracked')


def tracked_identities(top):
    if top not in TRACKED:
        env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        try:
            out = subprocess.run(['git', '-C', top, 'ls-files', '-z'],
                                 capture_output=True, timeout=GIT_TIMEOUT, env=env)
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f'git ls-files timed out in {top}') from exc
        if out.returncode != 0:
            # git could not answer; unknown is not "untracked".
            raise ValueError(f'git ls-files failed in {top}: {out.stderr.decode(errors="replace").strip()}')
        found = set()
        for name in out.stdout.split(b'\0'):
            if name:
                try:
                    found.add(identity(os.lstat(os.path.join(top, os.fsdecode(name)))))
                except OSError:
                    pass  # listed but gone from the checkout
        TRACKED[top] = found
    return TRACKED[top]


def judge(path):
    """('tracked', top) when ``path`` is a tracked file, ('hard link', None) when it is a
    file with more than one name that is not tracked where it sits, else None.
    Compared by identity (device, inode), never by path string."""
    st = stat_or_none(path)
    if st is None or not stat.S_ISREG(st.st_mode):
        return None
    top = tree_root(path)
    if top is not None and identity(st) in tracked_identities(top):
        return 'tracked', top
    if st.st_nlink > 1 and not read_only(path):
        return 'hard link', None
    return None


def read_only(path):
    """True when the file system holding ``path`` is mounted read-only (as macOS's
    system volume is): no name of a file there can be edited, so its other names
    cannot carry an unreviewed edit."""
    try:
        return bool(os.statvfs(path).f_flag & os.ST_RDONLY)
    except OSError:
        return False


def repository(path):
    """The top level of the working tree that tracks ``path``, or None."""
    verdict = judge(path)
    return verdict[1] if verdict and verdict[0] == 'tracked' else None


def scan(launch_agents, settings, home):
    """(flagged, unresolved): every (entry, file, repository) where a scheduled or hooked
    command names a tracked file, and every (entry, command, word) it cannot resolve."""
    homes = sorted({str(home), os.path.realpath(home)}, key=len, reverse=True)

    def display(path):
        for prefix in homes:
            if path == prefix or path.startswith(prefix + os.sep):
                return '~' + path[len(prefix):]
        return path

    def display_text(text):
        for prefix in homes:
            text = text.replace(prefix + os.sep, '~' + os.sep)
        return text

    flagged, unresolved = set(), set()
    entries = list(launchd_entries(launch_agents, home)) + list(hook_entries(settings, home))
    for entry, command, files, missed in entries:
        for path in files:
            verdict = judge(path)
            if verdict and verdict[0] == 'tracked':
                flagged.add((entry, display(path), display(verdict[1])))
            elif verdict:
                unresolved.add((entry, display_text(command), f'hard link {display(path)} {HARD_LINK}'))
        for word in missed:
            unresolved.add((entry, display_text(command), word))
    return flagged, unresolved


def line(item):
    return f'{item[0]} names {item[1]} (repository {item[2]})'


def unresolved_line(item):
    if item[2].startswith('hard link '):
        return f'{item[0]} names a {item[2]}, in: {item[1]}'
    if item[2].startswith('unreadable '):
        return f'{item[0]} is not plain text ({item[2]}), in: {item[1]}'
    return f'{item[0]} names `{item[2]}`, a path this check cannot resolve, in: {item[1]}'


def report(mode, recorded_at, flagged, unresolved, grandfathered, error, **extra):
    print(json.dumps({
        'schema': 1, 'mode': mode, 'baseline_recorded_at': recorded_at,
        'flagged': None if flagged is None else [dict(zip(FIELDS, item)) for item in sorted(flagged)],
        'unresolved': None if unresolved is None else [dict(zip(UNRESOLVED_FIELDS, item))
                                                       for item in sorted(unresolved)],
        'grandfathered': grandfathered, 'error': error, **extra}))


class JsonArgumentParser(argparse.ArgumentParser):
    """With --json, a usage error is one JSON object too (exit 2)."""
    def error(self, message):
        if '--json' in sys.argv[1:]:
            report(None, None, None, None, None, f'usage: {message}')
            sys.exit(2)
        super().error(message)


def arguments(argv):
    parser = JsonArgumentParser(description=__doc__.split('\n\n')[0], allow_abbrev=False)
    parser.add_argument('--launch-agents', metavar='DIR', help='default: ~/Library/LaunchAgents')
    parser.add_argument('--settings', metavar='FILE', help='default: ~/.claude/settings.json')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--report', action='store_true')
    parser.add_argument('--baseline-entries', action='store_true')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args(argv)
    if args.report and args.baseline_entries:
        parser.error('--report never writes a baseline; drop --baseline-entries')
    return args


FLAGGED_NOTE = ('an edit there reaches the entry without review, whether the entry runs the file or only '
                'reads it. Use a released copy outside any working tree, or `git show origin/main:<path>` into a temp '
                'file (that form is not plain text, so it is acknowledged once in the baseline)')
UNRESOLVED_NOTE = ('this check certifies only entries it can read completely; write plain absolute paths, '
                   'or acknowledge the entry with bstack doctor --baseline-entries --force after reviewing it')


BASELINE_PATH = None  # set by main(); named in a --baseline-entries --json error


def main(args):
    home = Path(os.environ.get('BSTACK_DOCTOR_HOME', str(Path.home()))).absolute()
    if 'BSTACK_DOCTOR_HOME' in os.environ:
        os.environ['HOME'] = str(home)
        os.environ['BROOMVA_HOME'] = str(home / '.broomva')
    launch_agents = Path(args.launch_agents).absolute() if args.launch_agents else home / 'Library/LaunchAgents'
    settings = Path(args.settings).absolute() if args.settings else home / '.claude/settings.json'
    if args.report:
        flagged, unresolved = scan(launch_agents, settings, home)
        if args.json:
            report('report', None, flagged, unresolved, None, None)
            return 0
        for item in sorted(flagged):
            print(f'[info] {line(item)}')
        for item in sorted(unresolved):
            print(f'[info] unresolved: {unresolved_line(item)}')
        print(f'[info] {len(flagged)} entr{"y names" if len(flagged) == 1 else "ies name"} a file in a git '
              'working tree, run or read (report only: no baseline read or written)')
        print(f'[info] {len(unresolved)} path word{"" if len(unresolved) == 1 else "s"} could not be resolved')
        return 0
    global BASELINE_PATH
    baseline = paths.machine_path('state') / BASELINE_NAME
    BASELINE_PATH = str(baseline)
    if args.baseline_entries:
        flagged, unresolved = scan(launch_agents, settings, home)
        os.umask(0o077)
        baseline.parent.mkdir(parents=True, exist_ok=True)
        recorded_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with baseline.open('w' if args.force else 'x', encoding='utf-8') as stream:
            json.dump({'recorded_at': recorded_at,
                       'entries': [dict(zip(FIELDS, item)) for item in sorted(flagged)],
                       'unresolved': [dict(zip(UNRESOLVED_FIELDS, item)) for item in sorted(unresolved)]},
                      stream, indent=2)
            stream.write('\n')
        if args.json:
            report('baseline', recorded_at, flagged, unresolved, None, None,
                   action='recorded', baseline=str(baseline))
            return 0
        print(f'[info] baseline recorded at {baseline}:')
        for item in sorted(flagged):
            print(f'  {line(item)}')
        for item in sorted(unresolved):
            print(f'  unresolved: {unresolved_line(item)}')
        return 0
    try:
        with baseline.open(encoding='utf-8') as stream:
            data = json.load(stream)

        def rows(items, fields):
            return isinstance(items, list) and all(
                isinstance(item, dict) and all(isinstance(item.get(field), str) for field in fields)
                for item in items)
        if (not isinstance(data, dict) or not isinstance(data.get('recorded_at'), str)
                or not rows(data.get('entries'), FIELDS)
                or not rows(data.get('unresolved'), UNRESOLVED_FIELDS)):
            raise ValueError('invalid baseline format')
        saved = {tuple(item[field] for field in FIELDS) for item in data['entries']}
        saved_unresolved = {tuple(item[field] for field in UNRESOLVED_FIELDS) for item in data['unresolved']}
    except FileNotFoundError:
        # A dangling baseline symlink is a broken opt-in, not an absent one.
        if os.path.lexists(baseline):
            raise
        if args.json:
            report('check', None, None, None, None,
                   f'no baseline recorded at {baseline}: run bstack doctor --baseline-entries')
        return 42  # No baseline: the ratchet is off on this machine.
    flagged, unresolved = scan(launch_agents, settings, home)
    new = flagged - saved
    new_unresolved = unresolved - saved_unresolved
    failed = int(bool(new or new_unresolved))
    if args.json:
        report('check', data['recorded_at'], new, new_unresolved,
               len(flagged & saved) + len(unresolved & saved_unresolved), None)
        return failed
    for item in sorted(saved - flagged):
        print(f'[info] baselined entry no longer names a file in a working tree: {line(item)} '
              '(baseline can be tightened)')
    for item in sorted(saved_unresolved - unresolved):
        print(f'[info] baselined unresolved entry is gone: {unresolved_line(item)} (baseline can be tightened)')
    for item in sorted(flagged & saved):
        print(f'[info] grandfathered entry: {line(item)}')
    for item in sorted(unresolved & saved_unresolved):
        print(f'[info] grandfathered unresolved entry: {unresolved_line(item)}')
    for item in sorted(new):
        print(f'[failure] new entry names a file in a git working tree: {line(item)}; {FLAGGED_NOTE}')
    for item in sorted(new_unresolved):
        print(f'[failure] new entry names a path this check cannot resolve: {unresolved_line(item)}; '
              f'{UNRESOLVED_NOTE}')
    return failed


if __name__ == '__main__':
    ARGS = arguments(sys.argv[1:])
    try:
        sys.exit(main(ARGS))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        if ARGS.json:
            if ARGS.baseline_entries:
                report('baseline', None, None, None, None, f'working-tree entry check: {exc}',
                       action=None, baseline=BASELINE_PATH)
            else:
                report('report' if ARGS.report else 'check', None, None, None, None,
                       f'working-tree entry check: {exc}')
            sys.exit(1)
        print(f'[failure] working-tree entry check: {exc}', file=sys.stderr)
        sys.exit(1)
