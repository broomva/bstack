#!/usr/bin/env python3
"""Baseline and audit machine folders outside the unified Broomva home."""
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


def main():
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
    for folder in sorted(saved - folders):
        print(f'[info] baselined folder no longer exists: ~/{folder} (baseline can be tightened)')
    for folder in sorted(folders & saved):
        print(f'[info] grandfathered folder: ~/{folder}')
    unknown = folders - saved - known
    for folder in sorted(unknown):
        root, name = folder.rsplit('/', 1)
        print(f'[failure] unregistered stack folder: ~/{folder}; place it under ~/.broomva/{ROOTS[root]}/{name}')
    return int(bool(unknown))


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f'[failure] machine folder check: {exc}', file=sys.stderr)
        sys.exit(1)
