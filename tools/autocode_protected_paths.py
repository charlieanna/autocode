"""Portable protected-file identities, including original internal file links.

A link captures its whole file-link chain and terminal file. Directory links,
external targets and targets omitted from the source snapshot remain refused.
"""
from pathlib import Path, PurePosixPath
import os
import shutil
try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


def relative_name(name):
    relative = PurePosixPath(name)
    if (not name or name == '.' or relative.is_absolute() or '..' in relative.parts
            or '.git' in relative.parts or str(relative) != name):
        raise ValueError('Protected test paths must be portable repository-relative paths')
    return relative


def path_in(root, name, *, allow_link=False):
    relative_name(name)
    root = Path(root)
    path = root / name
    current = root
    if current.is_symlink():
        raise ValueError(f'Protected test root must not be a symlink: {root}')
    for part in Path(name).parts[:-1]:
        current /= part
        if current.is_symlink():
            raise ValueError(f'Protected test must not follow a directory symlink: {name}')
    if path.is_symlink():
        if allow_link:
            return path
        raise ValueError(f'Protected test must not follow a symlink: {name}')
    if not path.is_file():
        raise ValueError(f'Protected test is missing: {name}')
    return path


def identity(path):
    if path.is_symlink():
        return {'symlink': os.readlink(path)}
    return {'sha256': util.file_hash(path), 'size': path.stat().st_size,
            'mode': path.stat().st_mode & 0o777}


def destination(root, name, target):
    """Resolve lexical parents without following an intermediate directory link."""
    if not target or PurePosixPath(target).is_absolute():
        raise ValueError(f'Protected test link must stay repository-relative: {name}')
    parts = list(PurePosixPath(name).parent.parts)
    tokens = target.split('/')
    for index, part in enumerate(tokens):
        if part == '..':
            if not parts:
                raise ValueError(f'Protected test link escapes the repository: {name}')
            parts.pop()
        elif part not in ('', '.'):
            parts.append(part)
        if index < len(tokens) - 1:
            parent = Path(root).joinpath(*parts)
            if parent.is_symlink() or not parent.is_dir():
                raise ValueError(f'Protected test link traverses a non-directory or directory symlink: {name}')
    return str(relative_name('/'.join(parts)))


def collect(root, names, available):
    """Capture eligible files and their complete, snapshot-bound link closure."""
    files = {}
    for start in names:
        name, visited = start, set()
        while name not in files:
            if name in visited:
                raise ValueError(f'Protected test link cycle: {start}')
            visited.add(name)
            if name not in available or available[name] == 'deleted':
                raise ValueError(f'Protected test link target is not in the source snapshot: {name}')
            path = path_in(root, name, allow_link=True)
            value = identity(path)
            # Insert after the chain is checked, so a cycle cannot look complete.
            if 'symlink' not in value:
                break
            name = destination(root, name, value['symlink'])
        for name in visited:
            files[name] = identity(path_in(root, name, allow_link=True))
    return dict(sorted(files.items()))


def verify_links(root, files):
    captured = collect(root, files, files)
    if captured != files:
        raise ValueError('Original protected test bundle changed or has an incomplete link closure')


def copy_entry(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        target.symlink_to(os.readlink(source))
    else:
        shutil.copy2(source, target)
