#!/usr/bin/env python3
"""Baseline and audit scheduled or hooked commands that run a file from a git working tree.

A LaunchAgent or a Claude Code hook that executes a file tracked in a working
tree runs whatever that checkout holds: an edit takes effect on the next run,
without a commit, a review or a merge. Two surfaces are read:

  - every com.broomva.*.plist in the LaunchAgents folder: Program and
    ProgramArguments, read as the argument vector launchd runs;
  - every hook `command` in the personal Claude Code settings.json, read as a
    shell command line.

A word is a script the command executes only by its POSITION, as the shell
would run it; how the word looks does not matter. The line is split into simple
commands at `;`, `&&`, `||`, `|`, `&` and newlines, quotes are removed, the target
of a redirection is dropped, and leading `VAR=value` assignments and the wrappers
`env`, `exec`, `nohup`, `time`, `command`, `sudo` and `nice` are skipped. Then:

  1. the command word counts when it contains a `/` (`./x.sh`, `scripts/y.sh`,
     `"$CLAUDE_PROJECT_DIR"/x.sh`, an absolute path); a word without one is a
     program looked up on PATH, whatever its extension;
  2. when the command word is an interpreter (`bash`, `sh`, `zsh`, `dash`,
     `python`, `python3`, `python3.14`, `node`, `bun`, `deno`, `ruby`, `perl`,
     `osascript`), its first operand after the options counts; `-c`/`-m`/`-e`
     name no file, and the string of `bash -c` / `sh -lc` is a command line
     scanned by these same rules (INTERPRETERS lists the options modelled);
  3. the word after `source` or `.` counts.

Every other word is an argument and is never reported: `echo report.sh`,
`cat notes.py`, `rm -f ./old.sh`. The bodies of `$(...)` and backquotes are
scanned as command lines of their own. An operand that is a bare variable
(`bash "$T"`) is a value made at run time and is not looked at.

A counted word is flagged when it names an existing file that the nearest
repository above its resolved path tracks (`git ls-files --error-unmatch`). A
command that writes `git show origin/main:<path>` to a temp file, or runs a
released copy outside any working tree, names no tracked file and passes. A
relative word is resolved only against a `cd <absolute dir>` earlier in the same
line or the plist's WorkingDirectory.

A hook has no working directory the scanner knows, and a LaunchAgent may have
none. A counted relative word with nothing to resolve it against
(`./hooks/x.sh`, `python3 scripts/y.py`, `cd scripts && ./doctor.sh`,
`"$CLAUDE_PROJECT_DIR"/x.sh`) is reported as `unresolved`, with its command, and
ratchets like a flagged entry: recorded in the baseline, info while
grandfathered, a failure when new.

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
import shlex
import re
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
SCRIPT_EXT = ('.sh', '.bash', '.zsh', '.py', '.js', '.mjs', '.cjs', '.ts', '.rb', '.pl')
HOME_PREFIXES = ('~/', '$HOME/', '${HOME}/')
ASSIGNMENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*(\[[^]]*\])?\+?=')
# Words that may stand before a command without being one, and words that open a
# clause whose next words are not a command (`for x in a b`, `case x in`).
PREFIX_WORDS = {'!', '{', '}', 'if', 'then', 'else', 'elif', 'fi', 'while', 'until', 'do', 'done', 'esac'}
CLAUSE_WORDS = {'for', 'select', 'case', 'function', 'in'}
# Wrappers that run their operand as a command: (short options that take a value,
# long options that take the next word as a value). `command -v` and `command -V`
# print instead of running. Other wrappers (`timeout`, `xargs`, `caffeinate`) are
# not modelled: the command they run is an argument here and is not reported.
WRAPPERS = {
    'env': ('uCS', {'--unset', '--chdir', '--split-string'}),
    'exec': ('a', set()),
    'nohup': ('', set()),
    'time': ('', set()),
    'command': ('', set()),
    'sudo': ('ughpCDrtTU', {'--user', '--group', '--host', '--prompt', '--close-from', '--chdir',
                            '--role', '--type', '--command-timeout', '--other-user'}),
    'nice': ('n', {'--adjustment'}),
}
# Interpreters whose first operand is a script file. For each: short options that
# take a value (the rest of the cluster, or the next word), long options that take
# the next word, short and long options after which no script file is named (code
# from a string, a module, stdin), and the shell option whose value is itself a
# command line. Only these options are modelled: an unknown short option is a flag,
# an unknown long option takes no separate value. So python's `-I` and `-u` are
# flags, `-W x`/`-X x` take one word, `-c`/`-m` name no file; `bash -lc '<line>'`
# scans `<line>` by these same rules.
INTERPRETERS = {
    'shell': ('oO', {'--rcfile', '--init-file'}, 's', set(), 'c'),
    'python': ('WX', {'--check-hash-based-pycs'}, 'cm', set(), ''),
    'node': ('rC', {'--require', '--import', '--loader', '--experimental-loader', '--conditions',
                    '--input-type', '--title'}, 'ep', {'--eval', '--print'}, ''),
    'ruby': ('IrCE', set(), 'e', set(), ''),
    'perl': ('IMm', set(), 'eE', set(), ''),
    'osascript': ('ls', set(), 'e', set(), ''),
    # bun and deno: after an optional `run`, the operand is a file only when it is
    # written as one (a `/` or a script extension); `bun run dev`, `bun test` and
    # `deno task build` name a package script or a subcommand.
    'bun': ('rc', {'--cwd', '--preload', '--config', '--env-file'}, 'ep', {'--eval', '--print'}, ''),
    'deno': ('cL', {'--config', '--import-map', '--log-level', '--location'}, '', set(), ''),
}


def expand(word, home):
    for prefix in HOME_PREFIXES:
        if word.startswith(prefix):
            return str(home / word[len(prefix):])
    return word


def interpreter(name):
    if name in ('bash', 'sh', 'zsh', 'dash'):
        return 'shell'
    if re.fullmatch(r'python[0-9.]*', name):
        return 'python'
    return name if name in INTERPRETERS else None


def closing(text, start, close):
    """Index of the ``close`` that ends the group opened just before ``start``."""
    depth, i = 1, start
    while i < len(text):
        c = text[i]
        if c == '\\':
            i += 2
            continue
        if c == "'" and close == ')':
            j = text.find("'", i + 1)
            i = len(text) if j < 0 else j + 1
            continue
        if c == close and close == '`':
            return i
        if c == '(' and close == ')' or c == '{' and close == '}':
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(text)


def split_line(text):
    """A shell command line as ('word', value, substitutions), ('op', text) and
    ('redirect', text) items. Quotes are removed from a word's value; the bodies of
    `$(...)` and backquotes in it are returned as command lines of their own.
    Heredoc bodies are not modelled."""
    items, word, subs, in_word = [], [], [], False
    i, n = 0, len(text)

    def end_word():
        nonlocal word, subs, in_word
        if in_word:
            items.append(('word', ''.join(word), subs))
        word, subs, in_word = [], [], False

    def substitution(i):
        """Consume `$(`, `${` or a backquote at ``i``; return the index after it."""
        if text.startswith('$(', i):
            j = closing(text, i + 2, ')')
            subs.append(text[i + 2:j])
        elif text.startswith('${', i):
            j = closing(text, i + 2, '}')
        else:
            j = closing(text, i + 1, '`')
            subs.append(text[i + 1:j])
        word.append(text[i:j + 1])
        return j + 1

    while i < n:
        c = text[i]
        if c in ' \t':
            end_word()
            i += 1
        elif c == '\\':
            if text[i + 1:i + 2] != '\n':
                word.append(text[i + 1:i + 2])
                in_word = True
            i += 2
        elif c == '#' and not in_word:
            j = text.find('\n', i)
            i = n if j < 0 else j
        elif c == "'":
            j = text.find("'", i + 1)
            j = n if j < 0 else j
            word.append(text[i + 1:j])
            in_word, i = True, j + 1
        elif c == '"':
            in_word, i = True, i + 1
            while i < n and text[i] != '"':
                if text[i] == '\\' and i + 1 < n and text[i + 1] in '"\\$`\n':
                    word.append(text[i + 1])
                    i += 2
                elif text.startswith(('$(', '${'), i) or text[i] == '`':
                    i = substitution(i)
                else:
                    word.append(text[i])
                    i += 1
            i += 1
        elif text.startswith(('$(', '${'), i) or c == '`':
            in_word = True
            i = substitution(i)
        elif c in '<>' or text.startswith('&>', i):
            # An fd number written against the operator belongs to it (`2>&1`).
            if in_word and not subs and ''.join(word).isdigit():
                word, in_word = [], False
            end_word()
            j = i + 1
            while j < n and text[j] in '<>&|-':
                j += 1
            items.append(('redirect', text[i:j], []))
            i = j
        elif c in ';|&()\n':
            end_word()
            op = text[i:i + 2] if text[i:i + 2] in ('&&', '||', ';;', '|&') else c
            items.append(('op', op, []))
            i += len(op)
        else:
            word.append(c)
            in_word, i = True, i + 1
    end_word()
    return items


def skip_options(argv, i, short_value, long_value):
    """Index of the first word at or after ``i`` that is not an option or its value."""
    while i < len(argv):
        word = argv[i]
        if word == '--':
            return i + 1
        if word.startswith('--'):
            i += 2 if word in long_value else 1
        elif len(word) > 1 and word[0] == '-':
            takes = next((k for k, ch in enumerate(word[1:], 1) if ch in short_value), None)
            i += 2 if takes == len(word) - 1 else 1
        else:
            return i
    return i


def operand(argv, i, spec):
    """('script' | 'command' | None, index) for an interpreter's arguments from ``i``."""
    short_value, long_value, short_stop, long_stop, short_command = spec
    command = False
    while i < len(argv):
        word = argv[i]
        if word == '--':
            i += 1
            break
        if word.startswith('--'):
            if word.split('=', 1)[0] in long_stop:
                return None, i
            i += 2 if word in long_value else 1
        elif len(word) > 1 and (word[0] == '-' or short_command and word[0] == '+'):
            takes = False
            for k, ch in enumerate(word[1:], 1):
                if ch in short_stop:
                    return None, i
                if ch in short_command:
                    command = True
                elif ch in short_value:
                    takes = k == len(word) - 1
                    break
            i += 2 if takes else 1
        else:
            break
    if i >= len(argv):
        return None, i
    return ('command' if command else 'script'), i


def scan_argv(argv, home, state, found, unresolved):
    """Record the script one simple command (its words, quotes removed) executes."""

    def count(word):
        path = expand(word, home)
        if not os.path.isabs(path):
            if path.startswith('$') and '/' not in path:
                return  # a value made at run time, such as a temp file: nothing to look at
            if state['cwd'] is None or path.startswith('$'):
                unresolved.append(word)
                return
            path = os.path.normpath(os.path.join(state['cwd'], path))
        if os.path.isfile(path):
            found.append(path)

    i = 0
    while i < len(argv):
        word = argv[i]
        if word in CLAUSE_WORDS:
            return
        if word in PREFIX_WORDS or ASSIGNMENT.match(word):
            i += 1
            continue
        name = os.path.basename(word)
        if name not in WRAPPERS:
            break
        if '/' in word:
            count(word)
        if name == 'command' and any(w in ('-v', '-V') for w in argv[i + 1:i + 2]):
            return
        i = skip_options(argv, i + 1, *WRAPPERS[name])
    if i >= len(argv):
        return
    word = argv[i]
    if word == 'cd':
        j = skip_options(argv, i + 1, '', set())
        target = expand(argv[j], home) if j < len(argv) else '-'
        if os.path.isabs(target):
            state['cwd'] = target
        elif state['cwd'] is not None and not target.startswith(('$', '-')):
            state['cwd'] = os.path.normpath(os.path.join(state['cwd'], target))
        else:
            state['cwd'] = None
        return
    if word in ('source', '.'):
        if i + 1 < len(argv):
            count(argv[i + 1])
        return
    # A word with no `/` in command position is a program looked up on PATH.
    if '/' in word:
        count(word)
    kind = interpreter(os.path.basename(word))
    if kind is None:
        return
    mode, j = operand(argv, i + 1, INTERPRETERS[kind])
    if mode == 'command':
        scan_line(argv[j], home, dict(state), found, unresolved)
        return
    if mode is None or argv[j] == '-':
        return
    if kind in ('bun', 'deno'):
        if argv[j] == 'run':
            mode, j = operand(argv, j + 1, INTERPRETERS[kind])
            if mode is None:
                return
        if '/' not in argv[j] and not argv[j].endswith(SCRIPT_EXT):
            return
    count(argv[j])


def scan_line(text, home, state, found, unresolved):
    """Scan every simple command of a shell command line, in order; a `cd` carries."""
    words, target = [], False
    for kind, value, subs in split_line(text):
        for body in subs:
            scan_line(body, home, dict(state), found, unresolved)
        if kind == 'op':
            scan_argv(words, home, state, found, unresolved)
            words = []
        elif kind == 'redirect':
            target = True
        elif target:
            target = False  # the file a redirection names is never a command word
        else:
            words.append(value)
    scan_argv(words, home, state, found, unresolved)


def named_files(text, home, cwd=None):
    """(existing files a shell command line executes, relative script paths it could not resolve).

    A word is a script the line executes only by its position: a command word that
    contains a `/`, the script operand of a known interpreter, or the word after
    `source` or `.`. Every other word is an argument and is never reported.
    """
    found, unresolved = [], []
    scan_line(text, home, {'cwd': cwd}, found, unresolved)
    return found, unresolved


def named_by_argv(argv, home, cwd=None):
    """The same for an argument vector that no shell splits (a LaunchAgent's)."""
    found, unresolved = [], []
    scan_argv(argv, home, {'cwd': cwd}, found, unresolved)
    return found, unresolved


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
        # launchd runs Program, or else ProgramArguments[0], with ProgramArguments as
        # its argument vector: each entry is one word, spaces included, no shell.
        arguments = data.get('ProgramArguments')
        argv = [item for item in arguments if isinstance(item, str)] if isinstance(arguments, list) else []
        program = data.get('Program')
        if isinstance(program, str) and program:
            argv = [program] + argv[1:]
        files, missed = named_by_argv(argv, home, cwd)
        command = shlex.join(argv)
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
                    named, missed = named_files(command, home)
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
    command runs a tracked file, and every (entry, command, word) whose script path
    has no directory to resolve against."""
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
    return f'{item[0]} runs {item[1]} (repository {item[2]})'


def unresolved_line(item):
    return f'{item[0]} runs `{item[2]}` with no directory to resolve it against, in: {item[1]}'


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
        print(f'[info] {len(flagged)} entr{"y runs" if len(flagged) == 1 else "ies run"} a file from a git '
              'working tree (report only: no baseline read or written)')
        print(f'[info] {len(unresolved)} script path{"" if len(unresolved) == 1 else "s"} could not be resolved')
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
        # A baseline recorded before `unresolved` existed has no such key: it
        # grandfathers none, so every unresolved entry is new until re-recorded.
        if (not isinstance(data, dict) or not isinstance(data.get('recorded_at'), str)
                or not rows(data.get('entries'), FIELDS)
                or not rows(data.get('unresolved', []), UNRESOLVED_FIELDS)):
            raise ValueError('invalid baseline format')
        saved = {tuple(item[field] for field in FIELDS) for item in data['entries']}
        saved_unresolved = {tuple(item[field] for field in UNRESOLVED_FIELDS)
                            for item in data.get('unresolved', [])}
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
        print(f'[info] baselined entry no longer runs from a working tree: {line(item)} (baseline can be tightened)')
    for item in sorted(saved_unresolved - unresolved):
        print(f'[info] baselined unresolved entry is gone: {unresolved_line(item)} (baseline can be tightened)')
    for item in sorted(flagged & saved):
        print(f'[info] grandfathered entry: {line(item)}')
    for item in sorted(unresolved & saved_unresolved):
        print(f'[info] grandfathered unresolved entry: {unresolved_line(item)}')
    for item in sorted(new):
        print(f'[failure] new entry runs a file from a git working tree: {line(item)}; an edit there takes '
              'effect without review. Run a released copy, or `git show origin/main:<path>` into a temp file')
    for item in sorted(new_unresolved):
        print(f'[failure] new entry runs a script path the check cannot resolve: {unresolved_line(item)}; '
              'name the script by its absolute path so the check can tell whether it runs from a working tree')
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
