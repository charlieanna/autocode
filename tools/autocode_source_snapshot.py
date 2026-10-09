"""Git source plus explicitly selected deliverables, including ignored files.

Callers supply literal paths from runner-validated authority. This utility never
loads run state or trusts a Builder's changed-files report. The selected paths
must travel with a saved source snapshot so a later freshness check uses the
same inventory policy. No process-global or workspace-global policy is changed.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


_INTERNAL = {'.git', '.autocode', '.autocode-ui'}


def literal_paths(paths):
    """Validate and canonicalize a bounded, explicit source inventory policy."""
    if not isinstance(paths, (list, tuple)):
        raise ValueError('Source paths must be an explicit list')
    result = set()
    for name in paths:
        if (not isinstance(name, str) or not name or name.startswith('/')
                or any(c in name for c in '*?[]\\\x00\n\r')):
            raise ValueError('Source paths must be literal repository-relative paths')
        name = name.rstrip('/')
        if any(p in ('', '.', '..') or p.casefold() in _INTERNAL for p in name.split('/')):
            raise ValueError('Source path crosses an internal or unbounded directory')
        result.add(name)
    return sorted(result)


def _identity(path, info):
    if stat.S_ISLNK(info.st_mode):
        return 'symlink:' + os.readlink(path)
    if stat.S_ISREG(info.st_mode):
        return ('executable:' if info.st_mode & 0o111 else '') + util.file_hash(path)
    raise ValueError('Unsupported source file type: ' + str(path))


def _include(root, relative, files):
    # Never dereference a parent symlink. Even an in-repository target can move
    # after this check, so the link itself is part of source, not its contents.
    for parent in relative.parents:
        if parent != Path('.') and (root / parent).is_symlink():
            raise ValueError('Source path has a symlinked parent: ' + str(relative))
    # Preserve Git boundaries; nested selection is applied recursively below.
    if any(files.get(parent.as_posix(), '').startswith(('submodule:', 'uninitialized-submodule'))
           for parent in relative.parents):
        return
    path = root / relative
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode):
        if (path / '.git').exists():
            files[relative.as_posix()] = 'submodule:' + util.snapshot(path)['revision']
            return
        for child in sorted(path.iterdir()):
            if child.name.casefold() not in _INTERNAL and child.name != '__pycache__' and not child.name.endswith('.pyc'):
                _include(root, child.relative_to(root), files)
    else:
        files[relative.as_posix()] = _identity(path, info)


def snapshot(workspace, *, paths=(), base_snapshot=None):
    """Hash Git-visible source and selected outputs without altering Git state.

    Omitted paths preserve the legacy snapshot exactly. A nonempty policy is
    included separately for subsequent copy/freshness checks; source revision
    remains a digest of actual HEAD and content, not of selection spelling.
    Missing selected files are absent (creation/deletion changes the files map).
    """
    selected = literal_paths(paths)
    root = Path(workspace)
    original = (base_snapshot or util.snapshot)(root)
    if not selected:
        return original
    # A supplied reader may return a cached value. Do not mutate its inventory.
    result = {**original, "files": dict(original["files"])}
    for name in selected:
        _include(root, Path(name), result['files'])
    for name, identity in list(result['files'].items()):
        if identity.startswith('submodule:'):
            nested = nested_paths(root, selected, name)
            if nested:
                result['files'][name] = 'submodule:' + snapshot(root / name, paths=nested)['revision']
    if result['files'] != original['files']:
        result['revision'] = util.digest({'head': result['head'], 'files': result['files']})
    result['source_paths'] = selected
    return result


def nested_paths(workspace, paths, name):
    """Translate outer literal scope into one nested Git repository's scope."""
    selected = []
    for path in literal_paths(paths):
        if path == name or name.startswith(path + '/'):
            selected.extend(child.name for child in (Path(workspace) / name).iterdir()
                            if child.name.casefold() not in _INTERNAL and child.name != '__pycache__'
                            and not child.name.endswith('.pyc'))
        elif path.startswith(name + '/'):
            selected.append(path[len(name) + 1:])
    return literal_paths(selected)


def inventory(workspace, *, paths=()):
    """Flatten copyable source files while retaining nested Git scope in identity."""
    root = Path(workspace)
    result = {}
    for name, identity in snapshot(root, paths=paths)['files'].items():
        if identity.startswith('submodule:'):
            nested = inventory(root / name, paths=nested_paths(root, paths, name))
            result.update({name + '/' + child: value for child, value in nested.items()})
        elif identity != 'uninitialized-submodule':
            result[name] = identity
    return result


def preserve_after(path, current, *, write_json=util.atomic_json):
    """Keep a completed attempt's original snapshot bytes during reconciliation.

    A legacy snapshot may lack selection metadata. Matching source does not
    authorize rewriting that evidence with today's metadata or scope. A source
    mismatch must be reconciled explicitly, never rebound to the old report.
    """
    path = Path(path)
    if path.is_symlink():
        raise util.Paused('PAUSED_STALE_VALIDATION', 'Saved after-source snapshot is a symlink')
    if path.exists():
        saved = util.read_object(path)
        if any(saved.get(key) != current.get(key) for key in ('head', 'files', 'revision')):
            raise util.Paused('PAUSED_STALE_VALIDATION',
                              'Saved after-source snapshot differs from current source; retain the stopped attempt for inspection')
        return
    write_json(path, current)
