#!/usr/bin/env python3
"""Ratchet scheduled jobs and hooks: a new or changed entry must be acknowledged.

A LaunchAgent or a hook runs whatever its command names, and if that is a file in
a git working tree, an edit there takes effect on the next run without a
commit, a review or a merge. Deciding from shell text whether an entry reaches
such a file cannot be made sound on a real file system, so the ratchet does not
depend on it.

THE RATCHET is on the identity of each entry, which no reading of its text
changes:
  - a LaunchAgent: the plist file name, and the SHA-256 of the canonical JSON
    serialisation (keys sorted, values exactly as stored) of its
    ProgramArguments, Program, WorkingDirectory and EnvironmentVariables;
  - a hook: the hooks file, the event and matcher, and the SHA-256 of the exact
    command string.
Whitespace is significant; nothing is normalised. The baseline is the set of
identities. Doctor FAILS when the machine has an entry whose identity is not in
the baseline (new, or its text changed), whatever the annotation says. A
removed entry passes and is reported. Nothing passes because of its class.
Acknowledging is recording the baseline again (`bstack doctor
--baseline-entries --force` after a review); recording prints, and with --json
returns, every new or changed entry with its annotation and every removed one.

SOURCES read: every com.broomva.*.plist in ~/Library/LaunchAgents; the hooks of
the user's Claude Code settings (~/.claude/settings.json); the user's Codex
hooks file (~/.codex/hooks.json), which has the same shape. NOT read: other
LaunchAgent labels, LaunchDaemons, project-level settings files, `prompt`-type
hooks, cron and other schedulers. A source that exists but cannot be read or
parsed is a failure that names it, never a pass and never an empty list.

THE ANNOTATION is best effort and never decides whether doctor passes. For
each entry it says `flagged` (a tracked file it names, and its repository),
`unresolved` (why the text could not be read), or `no working-tree file
found`. It can miss a file reached through a symlinked directory and `..`, a
file in a directory that can be searched but not listed, a file on a file
system mounted twice, and content fetched at run time. The rules below are the
annotation's.

ANNOTATION, READABLE TEXT. The annotation reads only entries it can read
completely; an entry containing any shell construct outside a small plain
grammar is annotated unresolved.

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

A fully readable entry with nothing flagged and nothing unresolved is annotated
`no working-tree file found`.

  doctor-worktree-entries.py                    check against the baseline (doctor section 33)
  doctor-worktree-entries.py --report           list every entry with its annotation; reads and writes no baseline
  doctor-worktree-entries.py --baseline-entries [--force]   record (acknowledge) the baseline
  options: --json, --launch-agents DIR, --settings FILE, --codex-hooks FILE

Exit codes, with or without --json: 0 no new or changed entry (always, for
--report and a successful recording), 1 a new or changed entry, a source that
cannot be read, a broken baseline, or a baseline that could not be recorded, 42
no baseline recorded, 2 a usage error.

With --json every mode prints exactly one JSON object, errors included:
{"schema": 2, "mode": "check"|"report"|"baseline", "baseline_recorded_at",
"new": [entry], "removed": [{"source", "entry", "sha256"}], "unchanged":
<count>, "error"}, where an entry is {"source", "entry", "sha256", "command",
"annotation": {"class", "files": [{"file", "repository"}], "reasons"}}.
--report adds "entries" (every entry; "new", "removed", "unchanged" are null).
Recording adds "action" ("recorded", or null when nothing was written) and
"baseline" (the file's path); its "new" and "removed" are what it acknowledged.
On an error "new", "removed" and "unchanged" are null. A usage error prints mode
null.
"""
import argparse
import datetime
import hashlib
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


LAUNCHD_KEYS = ('ProgramArguments', 'Program', 'WorkingDirectory', 'EnvironmentVariables')


def digest(text):
    """SHA-256 of the exact text: whitespace is significant, nothing is normalised."""
    return hashlib.sha256(text.encode('utf-8', 'surrogatepass')).hexdigest()


def launchd_entries(folder, home):
    """(entry, command, files, missed, sha256) for each com.broomva.*.plist in ``folder``.

    The identity hashes the canonical JSON serialisation of the plist's
    ProgramArguments, Program, WorkingDirectory and EnvironmentVariables (keys
    sorted, values exactly as stored). Any change to any of them is a new identity.
    """
    try:
        with os.scandir(folder) as listing:
            names = sorted(item.name for item in listing
                           if item.name.startswith('com.broomva.') and item.name.endswith('.plist'))
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ValueError(f'cannot read {folder}: {exc}') from exc
    for plist in (folder / name for name in names):
        try:
            with plist.open('rb') as stream:
                data = plistlib.load(stream)
        except Exception as exc:  # plistlib raises expat errors that are not ValueError
            raise ValueError(f'cannot read {plist}: {exc}') from exc
        if not isinstance(data, dict):
            raise ValueError(f'cannot read {plist}: not a dictionary')
        canonical = json.dumps({key: data.get(key) for key in LAUNCHD_KEYS}, sort_keys=True,
                               ensure_ascii=False, separators=(',', ':'), default=repr)
        cwd = data.get('WorkingDirectory')
        cwd = expand(cwd, home) if isinstance(cwd, str) and cwd else None
        cwd = cwd if cwd is not None and os.path.isabs(cwd) else None
        arguments = data.get('ProgramArguments')
        argv = [item for item in arguments if isinstance(item, str)] if isinstance(arguments, list) else []
        program = data.get('Program')
        strings = ([program] if isinstance(program, str) and program else []) + argv
        files, missed = read_entry(strings, home, cwd)
        yield plist.name, shlex.join(strings), files, missed, digest(canonical)


def hook_entries(settings, home):
    """(entry, command, files, missed, sha256) for each hook command in a hooks file.

    Claude Code's settings.json and Codex's hooks.json share the shape
    {"hooks": {event: [{"matcher", "hooks": [{"type": "command", "command"}]}]}}.
    The entry is the event and matcher; the identity hashes the exact command.
    A file that exists but cannot be read or parsed is an error naming it.
    """
    try:
        data = json.loads(settings.read_text(encoding='utf-8'))
    except FileNotFoundError:
        # A dangling symlink is a file that could not be read, not an absent one.
        if os.path.lexists(settings):
            raise ValueError(f'cannot read {settings}: dangling symlink')
        return
    except (OSError, ValueError) as exc:
        raise ValueError(f'cannot read {settings}: {exc}') from exc
    if not isinstance(data, dict):
        raise ValueError(f'cannot read {settings}: not a JSON object')
    hooks = data.get('hooks')
    if hooks is None:
        return
    if not isinstance(hooks, dict):
        raise ValueError(f'cannot read {settings}: "hooks" is not an object')
    for event, groups in hooks.items():
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict):
                continue
            matcher = group.get('matcher')
            label = f'{event}' + (f'[{matcher}]' if isinstance(matcher, str) and matcher else '')
            commands = group.get('hooks')
            for hook in commands if isinstance(commands, list) else []:
                command = hook.get('command') if isinstance(hook, dict) else None
                if isinstance(command, str):
                    named, missed = read_entry([command], home)
                    yield label, command, named, missed, digest(command)


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


NOT_FOUND = 'no working-tree file found'
IDENTITY_FIELDS = ('source', 'entry', 'sha256')


def collect(sources, home):
    """Every entry of every source: {source, entry, sha256, command, annotation}.

    ``sources`` is [(kind, path)] with kind 'launchd' or 'hooks'. The annotation
    is the classifier's best-effort reading (flagged, unresolved, or no
    working-tree file found); it never decides whether the ratchet passes.
    """
    homes = sorted({str(home), os.path.realpath(home)}, key=len, reverse=True)

    def display(path):
        path = str(path)
        for prefix in homes:
            if path == prefix or path.startswith(prefix + os.sep):
                return '~' + path[len(prefix):]
        return path

    def display_text(text):
        for prefix in homes:
            text = text.replace(prefix + os.sep, '~' + os.sep)
        return text

    found = []
    for kind, path in sources:
        rows = launchd_entries(path, home) if kind == 'launchd' else hook_entries(path, home)
        for entry, command, files, missed, sha in rows:
            tracked, reasons = [], list(missed)
            for file in files:
                verdict = judge(file)
                if verdict and verdict[0] == 'tracked':
                    tracked.append({'file': display(file), 'repository': display(verdict[1])})
                elif verdict:
                    reasons.append(f'hard link {display(file)} {HARD_LINK}')
            label = 'flagged' if tracked else 'unresolved' if reasons else NOT_FOUND
            found.append({'source': display(path), 'entry': entry, 'sha256': sha,
                          'command': display_text(command),
                          'annotation': {'class': label, 'files': tracked, 'reasons': reasons}})
    return found


def key(item):
    return tuple(item[field] for field in IDENTITY_FIELDS)


def describe(item):
    note = item['annotation']
    if note['class'] == 'flagged':
        what = 'flagged: ' + ', '.join(f"{f['file']} (repository {f['repository']})" for f in note['files'])
    elif note['class'] == 'unresolved':
        what = 'unresolved: ' + '; '.join(note['reasons'])
    else:
        what = NOT_FOUND
    return f"{item['source']} {item['entry']}: {item['command']} [annotation, best effort: {what}]"


def compare(current, saved):
    """(new or changed entries, removed identities, unchanged count)."""
    saved_keys = {key(item) for item in saved}
    current_keys = {key(item) for item in current}
    new = [item for item in current if key(item) not in saved_keys]
    removed = [dict(zip(IDENTITY_FIELDS, k)) for k in sorted(saved_keys - current_keys)]
    return new, removed, len(current_keys & saved_keys)


def change_kind(item, saved):
    named = {(s['source'], s['entry']) for s in saved}
    return 'changed' if (item['source'], item['entry']) in named else 'new'


def report(mode, recorded_at, error, **fields):
    print(json.dumps({'schema': 2, 'mode': mode, 'baseline_recorded_at': recorded_at,
                      'new': None, 'removed': None, 'unchanged': None, 'error': error, **fields}))


class JsonArgumentParser(argparse.ArgumentParser):
    """With --json, a usage error is one JSON object too (exit 2)."""
    def error(self, message):
        if '--json' in sys.argv[1:]:
            report(None, None, f'usage: {message}')
            sys.exit(2)
        super().error(message)


def arguments(argv):
    parser = JsonArgumentParser(description=__doc__.split('\n\n')[0], allow_abbrev=False)
    parser.add_argument('--launch-agents', metavar='DIR', help='default: ~/Library/LaunchAgents')
    parser.add_argument('--settings', metavar='FILE', help='default: ~/.claude/settings.json')
    parser.add_argument('--codex-hooks', metavar='FILE', help='default: ~/.codex/hooks.json')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--report', action='store_true')
    parser.add_argument('--baseline-entries', action='store_true')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args(argv)
    if args.report and args.baseline_entries:
        parser.error('--report never writes a baseline; drop --baseline-entries')
    return args


ACKNOWLEDGE = ('review it, then acknowledge it with bstack doctor --baseline-entries --force. '
               'The annotation is best effort and never decides: it can miss a file reached through a '
               'symlinked directory and `..`, an unlistable directory, a file system mounted twice, or '
               'content fetched at run time')
BASELINE_PATH = None  # set by main(); named in a --baseline-entries --json error


def load_baseline(baseline):
    with baseline.open(encoding='utf-8') as stream:
        data = json.load(stream)
    rows = data.get('entries') if isinstance(data, dict) else None
    if (not isinstance(data, dict) or data.get('schema') != 2 or not isinstance(data.get('recorded_at'), str)
            or not isinstance(rows, list)
            or not all(isinstance(r, dict) and all(isinstance(r.get(f), str) for f in IDENTITY_FIELDS)
                       for r in rows)):
        raise ValueError(f'invalid baseline format in {baseline} (re-record with bstack doctor '
                         '--baseline-entries --force)')
    return data['recorded_at'], [{f: r[f] for f in IDENTITY_FIELDS} for r in rows]


def main(args):
    global BASELINE_PATH
    home = Path(os.environ.get('BSTACK_DOCTOR_HOME', str(Path.home()))).absolute()
    if 'BSTACK_DOCTOR_HOME' in os.environ:
        os.environ['HOME'] = str(home)
        os.environ['BROOMVA_HOME'] = str(home / '.broomva')
    sources = [
        ('launchd', Path(args.launch_agents).absolute() if args.launch_agents else home / 'Library/LaunchAgents'),
        ('hooks', Path(args.settings).absolute() if args.settings else home / '.claude/settings.json'),
        ('hooks', Path(args.codex_hooks).absolute() if args.codex_hooks else home / '.codex/hooks.json'),
    ]
    baseline = paths.machine_path('state') / BASELINE_NAME
    BASELINE_PATH = str(baseline)
    if args.report:
        current = collect(sources, home)
        if args.json:
            report('report', None, None, entries=current)
            return 0
        for item in current:
            print(f'[info] {describe(item)}')
        counts = {}
        for item in current:
            counts[item['annotation']['class']] = counts.get(item['annotation']['class'], 0) + 1
        print(f'[info] {len(current)} entries (report only: no baseline read or written); annotation, best effort: '
              + ', '.join(f'{n} {label}' for label, n in sorted(counts.items())))
        return 0
    if args.baseline_entries:
        current = collect(sources, home)
        previous = []
        if baseline.exists() and args.force:
            try:
                previous = load_baseline(baseline)[1]
            except (OSError, ValueError):
                previous = []  # a broken baseline is replaced; everything is acknowledged anew
        new, removed, unchanged = compare(current, previous)
        os.umask(0o077)
        baseline.parent.mkdir(parents=True, exist_ok=True)
        recorded_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with baseline.open('w' if args.force else 'x', encoding='utf-8') as stream:
            json.dump({'schema': 2, 'recorded_at': recorded_at,
                       'entries': [{f: item[f] for f in IDENTITY_FIELDS} for item in current]}, stream, indent=2)
            stream.write('\n')
        if args.json:
            report('baseline', recorded_at, None, new=new, removed=removed, unchanged=unchanged,
                   action='recorded', baseline=str(baseline))
            return 0
        print(f'[info] baseline recorded at {baseline}; acknowledged {len(new)} new or changed, '
              f'{len(removed)} removed, {unchanged} unchanged:')
        for item in new:
            print(f'  {change_kind(item, previous)}: {describe(item)}')
        for item in removed:
            print(f"  removed: {item['source']} {item['entry']} ({item['sha256'][:12]})")
        return 0
    try:
        recorded_at, saved = load_baseline(baseline)
    except FileNotFoundError:
        # A dangling baseline symlink is a broken opt-in, not an absent one.
        if os.path.lexists(baseline):
            raise
        if args.json:
            report('check', None, f'no baseline recorded at {baseline}: run bstack doctor --baseline-entries')
        return 42  # No baseline: the ratchet is off on this machine.
    current = collect(sources, home)
    new, removed, unchanged = compare(current, saved)
    failed = int(bool(new))
    if args.json:
        report('check', recorded_at, None, new=new, removed=removed, unchanged=unchanged)
        return failed
    for item in removed:
        print(f"[info] acknowledged entry is gone: {item['source']} {item['entry']} (baseline can be re-recorded)")
    print(f'[info] {unchanged} acknowledged entr{"y" if unchanged == 1 else "ies"} unchanged')
    for item in new:
        print(f'[failure] {change_kind(item, saved)} entry since the baseline: {describe(item)}; {ACKNOWLEDGE}')
    return failed


if __name__ == '__main__':
    ARGS = arguments(sys.argv[1:])
    try:
        sys.exit(main(ARGS))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        if ARGS.json:
            mode = 'report' if ARGS.report else 'baseline' if ARGS.baseline_entries else 'check'
            extra = {'action': None, 'baseline': BASELINE_PATH} if ARGS.baseline_entries else {}
            report(mode, None, f'working-tree entry check: {exc}', **extra)
            sys.exit(1)
        print(f'[failure] working-tree entry check: {exc}', file=sys.stderr)
        sys.exit(1)
