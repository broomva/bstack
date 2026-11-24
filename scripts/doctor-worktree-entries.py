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

The check does not interpret shell. It never splits commands, follows a `cd`,
models a wrapper (`timeout`, `xargs`, `exec`) or an interpreter's options, or
tracks quotes beyond what is written below. It looks at words:

  A PATH WORD is a maximal run of characters other than whitespace and the
  shell operator characters ; | & ( ) < > and the backquote, that contains a
  `/`. A quote character is dropped without ending the word, so
  `"$CLAUDE_PROJECT_DIR"/x.sh` is the one word `$CLAUDE_PROJECT_DIR/x.sh`; a
  backslash keeps the next character in the word (`with\\ space.sh`). A word of
  the form NAME=value or --option=value is read as its value. Never
  considered: a word with no `/` (`echo report.sh`, `git status`, `bun run x`),
  a word starting with `-`, a URL (`scheme://`), a git object name
  `<ref>:<path>` (`git show origin/main:scripts/x.sh`), and /dev/null,
  /dev/stdin, /dev/stdout, /dev/stderr.

  An ABSOLUTE word (after expanding a leading `~/`, `$HOME/` or `${HOME}/`) is
  FLAGGED when it resolves, following symlinks, to an existing file that a git
  working tree tracks (walk up for `.git`, then `git ls-files --error-unmatch`;
  a linked worktree counts). It is flagged wherever it appears: as the command,
  as an interpreter's script, or as an argument that is only read. An absolute
  word that does not exist, is a directory, or is not tracked is not reported
  itself (a directory is used only for the pairing below): a released copy
  outside any working tree passes, and so does a command that writes
  `git show origin/main:<path>` to a temp file.

  A path with a space. An absolute word followed by more words, separated only
  by spaces or tabs, is also tried joined with them (up to eight): when such a
  join names an existing file, that file is the word. So `"/t/with space.sh"`,
  `'/t/with space.sh'` and `/t/with\\ space.sh` are all found, quoted or not;
  words used up by such a join are not reported again.

  A RELATIVE word, or one that starts with a variable other than HOME
  (`"$CLAUDE_PROJECT_DIR"/x.sh`), is UNRESOLVED: this check cannot tell what it
  names, and the remedy is an absolute path. So is a word that starts right
  after a `)` or a backquote and names no existing file (`$(dirname "$0")/x.sh`,
  the rest of a command substitution's output). The one exception: in a
  LaunchAgent with a `WorkingDirectory`, a relative word is resolved against it
  and then treated as absolute. A bare word is never unresolved. A `cd` in the
  text is not followed, so `cd /t && ./x.sh` leaves `./x.sh` unresolved.

  A BARE word paired with a DIRECTORY. Every absolute word that resolves
  (following symlinks) to a directory, and a LaunchAgent's WorkingDirectory, is
  paired with every bare word (no `/`) of the same text, in any order and with
  no `cd` tracking: when directory/bare-word is an existing tracked file, the
  entry is flagged with that file. So `cd /t && python3 x.py` and
  `/bin/sh run.sh` under a WorkingDirectory are flagged. A directory word by
  itself is never reported, so `git -C /t show origin/main:<path>` stays clean
  unless one of its bare words happens to name a tracked file in /t. That is
  the conservative side, and deliberate: `git -C /t log README.md` is flagged
  although nothing runs or reads README.md. A false flag costs one baseline
  line or an absolute rewrite; a missed entry runs unreviewed code. Words that are not file paths at all
  (`origin/main`, `text/plain`) are unresolved too; they ratchet like the rest.

Flagged and unresolved entries both ratchet: recorded in the baseline, printed
as info while grandfathered, and a new one fails.

What this check does not see: a path built at run time with no `/` written in
the text (`bash "$SCRIPT"`, a glob such as `/t/*.sh`, a name read from a file);
the file behind a relative path it cannot resolve (reported as unresolved, never
ignored); and content fetched by a program it names (a script that reads another
file, a URL it downloads).

  doctor-worktree-entries.py                    audit against the baseline (doctor section 33)
  doctor-worktree-entries.py --report           list every flagged and unresolved entry; reads and writes no baseline
  doctor-worktree-entries.py --baseline-entries [--force]   record the baseline
  options: --json, --launch-agents DIR, --settings FILE

Exit codes, with or without --json: 0 no new flagged or unresolved entry
(always, for --report), 1 a new flagged or unresolved entry, a broken baseline
or an input that could not be read, 42 no baseline recorded.

--json prints {"schema": 1, "mode": "check"|"report", "baseline_recorded_at",
"flagged": [{"entry", "file", "repository"}], "unresolved": [{"entry",
"command", "word"}], "grandfathered": <count>, "error"}. "flagged" and
"unresolved" hold the entries that are not in the baseline (every one, for
--report, where "grandfathered" is null); "grandfathered" counts both kinds. On
an error "flagged", "unresolved" and "grandfathered" are null: "could not look"
is never an empty list.
"""
import argparse
import datetime
import json
import os
import plistlib
import re
import shlex
import subprocess
import sys
from pathlib import Path

# Isolated Python (-I) omits the script directory from sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import broomva_paths as paths

BASELINE_NAME = 'doctor-worktree-entries.json'
GIT_TIMEOUT = 10
FIELDS = ('entry', 'file', 'repository')
UNRESOLVED_FIELDS = ('entry', 'command', 'word')
HOME_PREFIXES = ('~/', '$HOME/', '${HOME}/')
QUOTES = set('\'"')
DELIMITERS = set(' \t\r\n`;|&()<>')
STANDARD_STREAMS = {'/dev/null', '/dev/stdin', '/dev/stdout', '/dev/stderr'}
URL = re.compile(r'[A-Za-z][A-Za-z0-9+.-]*://')
MAX_JOIN = 8


def words(text):
    """[(value, joinable, glued)] for each word of the text.

    A word is a maximal run of characters other than whitespace, the shell
    operator characters and the backquote. Quote characters are dropped from a
    word without ending it, so `"$DIR"/x.sh` is the one word `$DIR/x.sh`. A
    backslash keeps the next character in the word. ``joinable``: only spaces or
    tabs separate the word from the next one. ``glued``: the word starts right
    after a `)` or backquote, so it continues a command substitution's output.
    """
    found, value, i, n = [], [], 0, len(text)
    after_substitution = glued = False
    while i < n:
        c = text[i]
        if c == '\\' and i + 1 < n:
            glued = glued or (not value and after_substitution)
            value.append(text[i + 1])
            i += 2
            continue
        if c in QUOTES:
            i += 1
            continue
        if c in DELIMITERS:
            if value:
                j = i
                while j < n and text[j] in ' \t':
                    j += 1
                joinable = j > i and j < n and text[j] not in DELIMITERS
                found.append((''.join(value), joinable, glued))
                value, glued = [], False
            after_substitution = c in ')`'
            i += 1
            continue
        if not value:
            glued = after_substitution
        value.append(c)
        i += 1
    if value:
        found.append((''.join(value), False, glued))
    return found


def path_word(word):
    """The path a word names, or None when the word is never considered."""
    if URL.match(word):
        return None
    if '=' in word:
        word = word.split('=', 1)[1]
    if '/' not in word or word.startswith('-') or word in STANDARD_STREAMS:
        return None
    if ':' in word and not word.startswith(('/', '~', '$', '.')):
        return None  # a git object name, <ref>:<path>
    return word


def expand(word, home):
    for prefix in HOME_PREFIXES:
        if word.startswith(prefix):
            return str(home / word[len(prefix):])
    return word


def absolute(word, home, cwd):
    """The absolute path a word names, or None when the check cannot resolve it."""
    path = expand(word, home)
    if os.path.isabs(path):
        return path
    if cwd is None or path.startswith(('$', '~')):
        return None
    return os.path.normpath(os.path.join(cwd, path))


def named_paths(text, home, cwd=None):
    """(existing files the text names by absolute path, words it cannot resolve).

    ``cwd`` is a LaunchAgent's WorkingDirectory: relative words resolve against
    it. Every absolute directory word is paired with every bare word of the same
    text (no order, no `cd`): a pair that names an existing file counts as that
    file. Nothing else in the text changes how a word resolves.
    """
    items = words(text)
    files, unresolved, folders, bare_words, i = [], [], [], [], 0
    if cwd is not None:
        folders.append(cwd)
    while i < len(items):
        value, _, glued = items[i]
        word = path_word(value)
        if word is None:
            bare = value.split('=', 1)[-1]
            if bare and '/' not in bare and not bare.startswith(('-', '$', '~')) and bare not in ('.', '..'):
                bare_words.append(bare)
            i += 1
            continue
        path = absolute(word, home, cwd)
        if path is None:
            unresolved.append(word)
            i += 1
            continue
        # A path with a space: try the next words, joined by one space each.
        used, k, joined = 1, i, path
        while items[k][1] and k + 1 < len(items) and k - i < MAX_JOIN:
            k += 1
            joined = f'{joined} {items[k][0]}'
            if os.path.isfile(joined):
                path, used = joined, k - i + 1
        if os.path.isfile(path):
            files.append(path)
        elif os.path.isdir(path):
            folders.append(path)  # never reported by itself; see the pairing below
        elif glued:
            # Right after `)` or a backquote, a word that names no file may continue
            # a substitution's output (`$(dirname "$0")/x.sh`): it is not absolute.
            unresolved.append(word)
        i += used
    # A bare file name used from a directory the text names (`cd /t && python3 x.py`).
    for folder in dict.fromkeys(os.path.realpath(f) for f in folders):
        for bare in dict.fromkeys(bare_words):
            candidate = os.path.join(folder, bare)
            if os.path.isfile(candidate) and candidate not in files:
                files.append(candidate)
    return files, unresolved


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
        files, missed = [], []
        for text in strings:
            named, unresolved = named_paths(text, home, cwd)
            files.extend(named)
            missed.extend(unresolved)
        command = shlex.join(strings)
        yield f'launchd:{plist.name[:-len(".plist")]}', files, [(command, word) for word in missed]


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
                    named, missed = named_paths(command, home)
                    yield label, named, [(command, word) for word in missed]


def repository(path):
    """The top level of the working tree that tracks ``path``, or None."""
    real = os.path.realpath(path)
    top = os.path.dirname(real)
    while not os.path.lexists(os.path.join(top, '.git')):
        parent = os.path.dirname(top)
        if parent == top:
            return None
        top = parent
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    try:
        out = subprocess.run(
            ['git', '-C', top, 'ls-files', '--error-unmatch', '--', os.path.relpath(real, top)],
            capture_output=True, text=True, timeout=GIT_TIMEOUT, env=env)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f'git ls-files timed out in {top}') from exc
    if out.returncode == 0:
        return top
    if out.returncode == 1:
        return None
    # Any other status means git could not answer; unknown is not "untracked".
    raise ValueError(f'git ls-files failed in {top}: {out.stderr.strip()}')


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
    for entry, files, missed in entries:
        for path in files:
            top = repository(path)
            if top is not None:
                flagged.add((entry, display(path), display(top)))
        for command, word in missed:
            unresolved.add((entry, display_text(command), word))
    return flagged, unresolved


def line(item):
    return f'{item[0]} names {item[1]} (repository {item[2]})'


def unresolved_line(item):
    return f'{item[0]} names `{item[2]}`, a path this check cannot resolve, in: {item[1]}'


def report(mode, recorded_at, flagged, unresolved, grandfathered, error):
    print(json.dumps({
        'schema': 1, 'mode': mode, 'baseline_recorded_at': recorded_at,
        'flagged': None if flagged is None else [dict(zip(FIELDS, item)) for item in sorted(flagged)],
        'unresolved': None if unresolved is None else [dict(zip(UNRESOLVED_FIELDS, item))
                                                       for item in sorted(unresolved)],
        'grandfathered': grandfathered, 'error': error}))


def arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], allow_abbrev=False)
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
                'reads it. Use a released copy, or `git show origin/main:<path>` into a temp file')
UNRESOLVED_NOTE = 'this check cannot tell what it names; write it as an absolute path'


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
    baseline = paths.machine_path('state') / BASELINE_NAME
    if args.baseline_entries:
        flagged, unresolved = scan(launch_agents, settings, home)
        os.umask(0o077)
        baseline.parent.mkdir(parents=True, exist_ok=True)
        with baseline.open('w' if args.force else 'x', encoding='utf-8') as stream:
            json.dump({'recorded_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       'entries': [dict(zip(FIELDS, item)) for item in sorted(flagged)],
                       'unresolved': [dict(zip(UNRESOLVED_FIELDS, item)) for item in sorted(unresolved)]},
                      stream, indent=2)
            stream.write('\n')
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
        if ARGS.json and not ARGS.baseline_entries:
            report('report' if ARGS.report else 'check', None, None, None, None, f'working-tree entry check: {exc}')
            sys.exit(1)
        print(f'[failure] working-tree entry check: {exc}', file=sys.stderr)
        sys.exit(1)
