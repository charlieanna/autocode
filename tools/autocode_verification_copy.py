"""Source-bound build space for read-only native verification commands.

The runner creates this before launch. Existing inputs are protected by the
kernel policy; new build outputs can persist between captures. Accepted checks
still undergo the independent clean-source replay before a PASS counts.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

try:
    from . import autocode_util as util, autocode_source_snapshot as source
except ImportError:
    import autocode_util as util
    import autocode_source_snapshot as source


def _identity(path):
    if path.is_symlink():
        return 'symlink:' + os.readlink(path)
    if path.is_file():
        return ('executable:' if path.stat().st_mode & 0o111 else '') + util.file_hash(path)
    return 'deleted' if not path.exists() else 'directory'


def _inventory(root, source_paths=()):
    return source.inventory(root, paths=source_paths)


def create(workspace, scratch, *, source_paths=()):
    root, scratch = Path(workspace).resolve(), Path(scratch).resolve()
    if not scratch.is_relative_to(root / '.autocode'):
        raise ValueError('Verification copy must be inside the owned task scratch')
    tree = scratch / 'verification'
    tree.mkdir()  # Never reuse a previous stage's outputs.
    before = source.snapshot(root, paths=source_paths)
    inputs = _inventory(root, source_paths)
    for name, identity in inputs.items():
        original, target = root / name, tree / name
        if identity == 'deleted':
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if original.is_symlink():
            link = os.readlink(original)
            if os.path.isabs(link) and Path(link).is_relative_to(root):
                link = str(tree / Path(link).relative_to(root))
            target.symlink_to(link)
        else:
            shutil.copy2(original, target)
        expected = 'symlink:' + link if original.is_symlink() else identity
        if _identity(original) != identity or _identity(target) != expected:
            raise ValueError('Source changed while copying: ' + name)
    # A real standalone Git repository avoids accidentally inspecting the
    # parent task's index when a check invokes Git from the copy.
    env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': str(scratch),
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
    for args in [('init', '-q'), ('add', '-f', '--all'),
                 ('-c', 'user.name=AutoCode verification', '-c', 'user.email=verification@localhost',
                  '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-qm', 'Verification source')]:
        subprocess.run(['/usr/bin/git', *args], cwd=tree, env=env, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    # Match the clean replay's read-only task-local dependency lookup. These
    # links grant no new authority; the sandbox keeps their originals read-only.
    for name in ('node_modules', '.venv', 'venv', 'vendor'):
        original, target = root / name, tree / name
        if original.is_dir() and not target.exists() and not target.is_symlink():
            target.symlink_to(original.resolve(), target_is_directory=True)
    if source.snapshot(root, paths=source_paths) != before:
        raise ValueError('Source changed while preparing verification copy')
    files = {str(p.relative_to(tree)): _identity(p) for p in tree.rglob('*')
             if p.is_symlink() or p.is_file()}
    protected = [str(tree), *(str(p) for p in tree.rglob('*'))]
    manifest = scratch.parent / 'verification-copy.json'
    data = {'version': 1, 'workspace': str(root), 'tree': str(tree),
            'source_revision': before['revision'], 'source_paths': before.get('source_paths', []), 'files': files,
            'directories': [str(p.relative_to(tree)) for p in tree.rglob('*')
                            if p.is_dir() and not p.is_symlink()]}
    util.atomic_json(manifest, data)
    return {'manifest': str(manifest), 'sha256': util.file_hash(manifest),
            'protected_paths': protected}


def execution(manifest, expected_hash, workspace):
    """Validate retained inputs, returning the cwd and provenance for capture."""
    root, path = Path(workspace).resolve(), Path(manifest)
    control = path.parent
    if (path.is_symlink() or path.resolve() != path or path.name != 'verification-copy.json'
            or control.parent != root / '.autocode' or not control.name.startswith('tool-containment-')
            or util.file_hash(path) != expected_hash):
        raise ValueError('Verification copy authority changed')
    data = json.loads(path.read_text())
    tree = control / 'scratch' / 'verification'
    if data.get('version') != 1 or data.get('workspace') != str(root) or data.get('tree') != str(tree):
        raise ValueError('Unexpected verification copy layout')
    if tree.resolve() != tree or source.snapshot(root, paths=data.get('source_paths', []))['revision'] != data['source_revision']:
        raise ValueError('Verification source changed; request fresh validation')
    for name in data['directories']:
        relative = Path(name)
        directory = tree / relative
        if (relative.is_absolute() or '..' in relative.parts or directory.is_symlink()
                or not directory.is_dir() or directory.resolve() != directory):
            raise ValueError('Verification directory changed: ' + name)
    for name, expected in data['files'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or _identity(tree / relative) != expected:
            raise ValueError('Verification input changed: ' + name)
    return tree, {'source_revision': data['source_revision'], 'manifest_sha256': expected_hash}
