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

  doctor-worktree-entries.py                    audit against the baseline (doctor section 33)
  doctor-worktree-entries.py --report           list every flagged entry; reads and writes no baseline
  doctor-worktree-entries.py --baseline-entries [--force]   record the baseline
  options: --json, --launch-agents DIR, --settings FILE

Exit codes, with or without --json: 0 no new flagged entry (always, for
--report), 1 a new flagged entry, a broken baseline or an input that could not
be read, 42 no baseline recorded.

--json prints {"schema": 1, "mode": "check"|"report", "baseline_recorded_at",
"flagged": [{"entry", "file", "repository"}], "grandfathered": <count>, "error"}.
"flagged" holds the entries that are not in the baseline (every one, for
--report, where "grandfathered" is null). On an error "flagged" and
"grandfathered" are null: "could not look" is never an empty list.
"""
import argparse
import datetime
import json
import os
import plistlib
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
# Whitespace, quotes and shell punctuation end a word. A colon does not: it keeps
# `origin/main:scripts/x.sh` one relative word instead of a path under a `cd`.
SEPARATORS = re.compile(r'''[\s"'`;|&()<>=]+''')
HOME_PREFIXES = ('~/', '$HOME/', '${HOME}/')


def expand(word, home):
    for prefix in HOME_PREFIXES:
        if word.startswith(prefix):
            return str(home / word[len(prefix):])
    return word


def named_files(text, home, cwd=None):
    """Existing files a shell string names, in order."""
    found = []
    words = [word for word in SEPARATORS.split(text) if word]
    for index, word in enumerate(words):
        path = expand(word, home)
        if index and words[index - 1] == 'cd' and os.path.isabs(path):
            cwd = path
            continue
        if not os.path.isabs(path):
            if cwd is None or word.startswith(('-', '$')):
                continue
            path = os.path.normpath(os.path.join(cwd, path))
        if os.path.isfile(path):
            found.append(path)
    return found


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
        files = []
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
                files.extend(named_files(argument, home, cwd))
        yield f'launchd:{plist.name[:-len(".plist")]}', files


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
                    yield label, named_files(command, home)


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
    """Every (entry, file, repository) where a scheduled or hooked command runs a tracked file."""
    homes = sorted({str(home), os.path.realpath(home)}, key=len, reverse=True)

    def display(path):
        for prefix in homes:
            if path == prefix or path.startswith(prefix + os.sep):
                return '~' + path[len(prefix):]
        return path

    flagged = set()
    entries = list(launchd_entries(launch_agents, home)) + list(hook_entries(settings, home))
    for entry, files in entries:
        for path in files:
            top = repository(path)
            if top is not None:
                flagged.add((entry, display(path), display(top)))
    return flagged


def line(item):
    return f'{item[0]} runs {item[1]} (repository {item[2]})'


def report(mode, recorded_at, flagged, grandfathered, error):
    print(json.dumps({
        'schema': 1, 'mode': mode, 'baseline_recorded_at': recorded_at,
        'flagged': None if flagged is None else [dict(zip(FIELDS, item)) for item in sorted(flagged)],
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
        flagged = scan(launch_agents, settings, home)
        if args.json:
            report('report', None, flagged, None, None)
            return 0
        for item in sorted(flagged):
            print(f'[info] {line(item)}')
        print(f'[info] {len(flagged)} entr{"y runs" if len(flagged) == 1 else "ies run"} a file from a git '
              'working tree (report only: no baseline read or written)')
        return 0
    baseline = paths.machine_path('state') / BASELINE_NAME
    if args.baseline_entries:
        flagged = scan(launch_agents, settings, home)
        os.umask(0o077)
        baseline.parent.mkdir(parents=True, exist_ok=True)
        with baseline.open('w' if args.force else 'x', encoding='utf-8') as stream:
            json.dump({'recorded_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       'entries': [dict(zip(FIELDS, item)) for item in sorted(flagged)]}, stream, indent=2)
            stream.write('\n')
        print(f'[info] baseline recorded at {baseline}:')
        for item in sorted(flagged):
            print(f'  {line(item)}')
        return 0
    try:
        with baseline.open(encoding='utf-8') as stream:
            data = json.load(stream)
        if (not isinstance(data, dict) or not isinstance(data.get('recorded_at'), str)
                or not isinstance(data.get('entries'), list)
                or not all(isinstance(item, dict) and all(isinstance(item.get(field), str) for field in FIELDS)
                           for item in data['entries'])):
            raise ValueError('invalid baseline format')
        saved = {tuple(item[field] for field in FIELDS) for item in data['entries']}
    except FileNotFoundError:
        # A dangling baseline symlink is a broken opt-in, not an absent one.
        if os.path.lexists(baseline):
            raise
        if args.json:
            report('check', None, None, None,
                   f'no baseline recorded at {baseline}: run bstack doctor --baseline-entries')
        return 42  # No baseline: the ratchet is off on this machine.
    flagged = scan(launch_agents, settings, home)
    new = flagged - saved
    if args.json:
        report('check', data['recorded_at'], new, len(flagged & saved), None)
        return int(bool(new))
    for item in sorted(saved - flagged):
        print(f'[info] baselined entry no longer runs from a working tree: {line(item)} (baseline can be tightened)')
    for item in sorted(flagged & saved):
        print(f'[info] grandfathered entry: {line(item)}')
    for item in sorted(new):
        print(f'[failure] new entry runs a file from a git working tree: {line(item)}; an edit there takes '
              'effect without review. Run a released copy, or `git show origin/main:<path>` into a temp file')
    return int(bool(new))


if __name__ == '__main__':
    ARGS = arguments(sys.argv[1:])
    try:
        sys.exit(main(ARGS))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        if ARGS.json and not ARGS.baseline_entries:
            report('report' if ARGS.report else 'check', None, None, None, f'working-tree entry check: {exc}')
            sys.exit(1)
        print(f'[failure] working-tree entry check: {exc}', file=sys.stderr)
        sys.exit(1)
