#!/usr/bin/env python3
"""Baseline and audit scheduled or hooked commands that run a file from a git working tree.

A LaunchAgent or a Claude Code hook that executes a file tracked in a working
tree runs whatever that checkout holds: an edit takes effect on the next run,
without a commit, a review or a merge. Two surfaces are read:

  - every com.broomva.*.plist in the LaunchAgents folder: Program and each
    ProgramArguments entry, including the words of a `bash -c` string;
  - every hook `command` in the personal Claude Code settings.json.

A file named there is flagged when it exists and the nearest repository above
its resolved path tracks it (`git ls-files --error-unmatch`). A command that
writes `git show origin/main:<path>` to a temp file, or runs a released copy
outside any working tree, names no tracked file and passes. A relative word is
resolved only against a `cd <absolute dir>` earlier in the same string or the
plist's WorkingDirectory. A path containing spaces inside a shell string is not
recognised (one that is a whole ProgramArguments entry is).

A hook has no working directory the scanner knows, and a LaunchAgent may have
none. A relative SCRIPT PATH with nothing to resolve it against
(`./hooks/x.sh`, `python3 scripts/y.py`, `cd scripts && ./doctor.sh`,
`"$CLAUDE_PROJECT_DIR"/x.sh`) is reported as `unresolved`, with its command, and
ratchets like a flagged entry: recorded in the baseline, info while
grandfathered, a failure when new. A word counts as a script path when it starts
with `./` or `../`, starts with a variable other than HOME and continues with
`/`, or ends in a script extension and is not a program found on PATH. Bare
program names (`bun`, `python3`, `git`) and arguments without those marks
(`stop`, `log`, `config.json`) are not paths to run and are never reported.

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
import shutil
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
# `"$CLAUDE_PROJECT_DIR"/x.sh` is one word to the shell; drop the quotes around a
# leading variable so the separators below do not cut the variable from its path.
QUOTED_VAR = re.compile(r'''["'](\$\{?[A-Za-z_][A-Za-z0-9_]*\}?)["'](?=/)''')
# Whitespace, quotes and shell punctuation end a word. A colon does not: it keeps
# `origin/main:scripts/x.sh` one relative word instead of a path under a `cd`.
SEPARATORS = re.compile(r'''[\s"'`;|&()<>=]+''')
HOME_PREFIXES = ('~/', '$HOME/', '${HOME}/')


def expand(word, home):
    for prefix in HOME_PREFIXES:
        if word.startswith(prefix):
            return str(home / word[len(prefix):])
    return word


def is_script_path(word):
    """Whether a relative word names a script to run, rather than a program or an argument.

    A program on PATH (`bun`, `python3`, `git`) has no `/`, so the shell finds it on
    PATH; it is never a working-tree file the scanner could miss. An argument
    (`stop`, `log`, `config.json`, `--apply`) does not name a script. What remains
    is a word written as a path to run: it starts with `./` or `../`, or with a
    variable and a `/` (`$CLAUDE_PROJECT_DIR/x.sh`), or it ends in a script
    extension and is not itself a program found on PATH.
    """
    if word.startswith('-') or ':' in word:
        return False  # an option, or a git `rev:path` / URL, never a file to run
    if word.startswith(('./', '../')):
        return True
    if word.startswith('$'):
        return '/' in word
    if not word.endswith(SCRIPT_EXT):
        return False
    return '/' in word or shutil.which(word) is None


def named_files(text, home, cwd=None):
    """(existing files a shell string names, relative script paths it could not resolve)."""
    found, unresolved = [], []
    words = [word for word in SEPARATORS.split(QUOTED_VAR.sub(r'\1', text)) if word]
    for index, word in enumerate(words):
        path = expand(word, home)
        if index and words[index - 1] == 'cd':
            # A relative `cd` moves a known directory; from an unknown one, or to a
            # variable, the directory becomes unknown.
            if os.path.isabs(path):
                cwd = path
            elif cwd is not None and not word.startswith(('$', '-')):
                cwd = os.path.normpath(os.path.join(cwd, path))
            else:
                cwd = None
            continue
        if not os.path.isabs(path):
            if not is_script_path(word):
                continue
            if cwd is None or word.startswith('$'):
                unresolved.append(word)
                continue
            path = os.path.normpath(os.path.join(cwd, path))
        if os.path.isfile(path):
            found.append(path)
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
        arguments = data.get('ProgramArguments')
        files, unresolved = [], []
        for argument in [data.get('Program')] + (arguments if isinstance(arguments, list) else []):
            if not isinstance(argument, str) or not argument:
                continue
            # launchd passes an argument as one word, spaces included.
            whole = expand(argument, home)
            if cwd and not os.path.isabs(whole):
                whole = os.path.normpath(os.path.join(cwd, whole))
            if os.path.isabs(whole) and os.path.isfile(whole):
                files.append(whole)
            else:
                named, missed = named_files(argument, home, cwd)
                files.extend(named)
                unresolved.extend((argument, word) for word in missed)
        yield f'launchd:{plist.name[:-len(".plist")]}', files, unresolved


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
