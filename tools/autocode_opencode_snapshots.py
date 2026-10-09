"""Read native OpenCode 1.x Git trees without trusting model descriptions of writes.

OpenCode stores snapshot repositories under its XDG data directory. Locate a
repository by its recorded project ID and core.worktree, never by guessing the
workspace hash. Tree IDs are content identities; every intermediate tree is
checked, including one followed by a restoration. Missing evidence fails closed.
No provider is launched, and no Git index or user exclusion is changed here.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path, PurePosixPath

HASH = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def context(workspace, env=None):
    """Capture location inputs from the exact provider environment, without secrets."""
    effective = dict(os.environ if env is None else env)
    root = Path(workspace).resolve()
    home = Path(effective.get('HOME') or Path.home())
    data = Path(effective.get('XDG_DATA_HOME') or home / '.local/share') / 'opencode/snapshot'
    return {'version': 1, 'workspace': str(root),
            'root': str((data if data.is_absolute() else root / data).resolve())}


def workspace(record):
    """The runner's native OpenCode command already records its absolute --dir."""
    command = record.get('command')
    if not isinstance(command, list) or command.count('--dir') != 1:
        return None
    index = command.index('--dir') + 1
    if index >= len(command) or not isinstance(command[index], str):
        return None
    path = Path(command[index])
    return path.resolve() if path.is_absolute() else None


def _git(*args):
    # Location/configuration supplied by the parent shell cannot redirect this
    # evidence lookup or run an external diff. Local core.worktree is read below.
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS='0')
    return subprocess.run(['git', '--no-pager', *args], env=env, capture_output=True,
                          check=True, timeout=5).stdout


def _repositories(root, location):
    pointers = {
        Path(os.fsdecode(_git('-C', str(root), 'rev-parse', '--path-format=absolute', '--git-path', 'opencode')).strip()),
        Path(os.fsdecode(_git('-C', str(root), 'rev-parse', '--path-format=absolute', '--git-common-dir')).strip()) / 'opencode',
    }
    for pointer in pointers:
        if not pointer.is_file() or pointer.is_symlink():
            continue
        project = pointer.read_text().strip()
        if not HASH.fullmatch(project):
            continue
        parent = location / project
        if not parent.is_dir() or not parent.resolve().is_relative_to(location):
            continue
        for repo in parent.iterdir():
            if not repo.is_dir() or repo.is_symlink() or not HASH.fullmatch(repo.name):
                continue
            if (repo / 'config').is_symlink():
                continue
            value = os.fsdecode(_git('--git-dir=' + str(repo), 'config', '--get', 'core.worktree')).strip()
            bound = Path(value)
            if bound.is_absolute() and bound.resolve() == root:
                yield repo


def _additions(repo, before, after, protected, prefixes):
    for tree in (before, after):
        if _git('--git-dir=' + str(repo), 'cat-file', '-t', tree).strip() != b'tree':
            return False
    raw = _git('--git-dir=' + str(repo), 'diff-tree', '--no-commit-id', '--raw', '-r', '-z',
               '--no-renames', '--no-ext-diff', '--no-textconv', '--ignore-submodules=none', before, after, '--')
    fields = raw.split(b'\0')
    if not raw or fields[-1] or len(fields) % 2 != 1:
        return False
    for header, encoded in zip(fields[0:-1:2], fields[1:-1:2]):
        parts = header.split()
        if len(parts) != 5 or parts[0] != b':000000' or parts[1] not in (b'100644', b'100755') or parts[4] != b'A':
            return False
        name = os.fsdecode(encoded)
        path = PurePosixPath(name)
        if (path.is_absolute() or '..' in path.parts or str(path) != name
                or not name.startswith(prefixes) or name in protected):
            return False
    return True


def only_new_artifacts(record, pairs, protected, prefixes):
    """Whether all changed native trees add only new regular permitted files.

    For old records without saved location metadata, the current environment is
    only a lookup hint. The recorded --dir, native tree IDs and core.worktree
    still have to match. A missing cache is never permission to accept a report.
    """
    try:
        root = workspace(record)
        if root is None or not pairs or any(not HASH.fullmatch(value) for pair in pairs for value in pair):
            return False
        saved = record.get('opencode_snapshot_context', context(root))
        if not isinstance(saved, dict) or saved.get('version') != 1 or Path(saved['workspace']).resolve() != root:
            return False
        location = Path(saved['root'])
        if not location.is_absolute() or not location.is_dir():
            return False
        location = location.resolve()
        for repo in _repositories(root, location):
            try:
                return all(_additions(repo, first, later, protected, prefixes) for first, later in pairs)
            except (OSError, ValueError, subprocess.SubprocessError):
                continue  # Another cache for the same workspace may hold the recorded trees.
    except (KeyError, TypeError, OSError, ValueError, subprocess.SubprocessError):
        pass
    return False
