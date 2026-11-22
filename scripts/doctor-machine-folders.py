#!/usr/bin/env python3
"""Baseline and audit machine folders outside the unified Broomva home.

  doctor-machine-folders.py                  audit against the baseline (doctor section 31)
  doctor-machine-folders.py --json           the same audit as one JSON object on stdout
  doctor-machine-folders.py --baseline-roots [--force]   record the baseline

Exit codes, with or without --json: 0 no unregistered folder, 1 an unregistered
folder or a broken baseline, 42 no baseline recorded.

--json prints {"schema": 1, "baseline_recorded_at", "unregistered": [{"path",
"suggested"}], "grandfathered": <count>, "error"}. When the baseline is missing or
invalid, "error" holds the reason and "unregistered" and "grandfathered" are null:
"could not look" is never an empty list.
"""
import datetime
import json
import os
import sys
from pathlib import Path

# Isolated Python (-I) omits the script directory from sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import broomva_paths as paths

ROOTS = {
    '.config/broomva': 'config',
    '.local/share/broomva': 'data',
    '.local/state/broomva': 'state',
    '.cache/broomva': 'cache',
    '.control': 'state',
}


def scan(home):
    folders = set()
    for root in ROOTS:
        try:
            with os.scandir(home / root) as entries:
                folders.update(f'{root}/{e.name}' for e in entries if e.is_dir())
        except FileNotFoundError:
            continue
    return folders


def suggested(folder):
    root, name = folder.rsplit('/', 1)
    return f'~/.broomva/{ROOTS[root]}/{name}'


def report(recorded_at, unregistered, grandfathered, error):
    print(json.dumps({'schema': 1, 'baseline_recorded_at': recorded_at, 'unregistered': unregistered,
                      'grandfathered': grandfathered, 'error': error}))


def main():
    as_json = '--json' in sys.argv
    home = Path(os.environ.get('BSTACK_DOCTOR_HOME', str(Path.home()))).absolute()
    if 'BSTACK_DOCTOR_HOME' in os.environ:
        os.environ['HOME'] = str(home)
        os.environ['BROOMVA_HOME'] = str(home / '.broomva')
    baseline = paths.machine_path('state') / 'doctor-machine-folders.json'
    if '--baseline-roots' in sys.argv:
        folders = scan(home)
        os.umask(0o077)
        baseline.parent.mkdir(parents=True, exist_ok=True)
        mode = 'w' if '--force' in sys.argv else 'x'
        with baseline.open(mode, encoding='utf-8') as stream:
            json.dump({'recorded_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       'folders': sorted(folders)}, stream, indent=2)
            stream.write('\n')
        print(f'[info] baseline recorded at {baseline}:')
        for folder in sorted(folders):
            print(f'  ~/{folder}')
        return 0
    try:
        with baseline.open(encoding='utf-8') as stream:
            data = json.load(stream)
        if (not isinstance(data, dict) or not isinstance(data.get('recorded_at'), str)
                or not isinstance(data.get('folders'), list)
                or not all(isinstance(item, str) for item in data['folders'])):
            raise ValueError('invalid baseline format')
        saved = set(data['folders'])
    except FileNotFoundError:
        # A dangling baseline symlink is a broken opt-in, not an absent one.
        if os.path.lexists(baseline):
            raise
        if as_json:
            report(None, None, None, f'no baseline recorded at {baseline}: run bstack doctor --baseline-roots')
        return 42  # No baseline: report an ordinary doctor gap.
    folders = scan(home)
    known = set()
    for _, old in paths.MACHINE.values():
        if old and old.startswith('~/'):
            known.add(old[2:])
        elif old and os.path.isabs(old):
            try:
                known.add(str(Path(old).relative_to(home)))
            except ValueError:
                pass
    unknown = folders - saved - known
    if as_json:
        report(data['recorded_at'],
               [{'path': f'~/{folder}', 'suggested': suggested(folder)} for folder in sorted(unknown)],
               len(folders & saved), None)
        return int(bool(unknown))
    for folder in sorted(saved - folders):
        print(f'[info] baselined folder no longer exists: ~/{folder} (baseline can be tightened)')
    for folder in sorted(folders & saved):
        print(f'[info] grandfathered folder: ~/{folder}')
    for folder in sorted(unknown):
        print(f'[failure] unregistered stack folder: ~/{folder}; place it under {suggested(folder)}')
    return int(bool(unknown))


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        if '--json' in sys.argv and '--baseline-roots' not in sys.argv:
            report(None, None, None, f'machine folder check: {exc}')
            sys.exit(1)
        print(f'[failure] machine folder check: {exc}', file=sys.stderr)
        sys.exit(1)
