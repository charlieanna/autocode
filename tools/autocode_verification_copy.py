"""Source-bound build space for read-only native verification commands.

The runner creates this before launch. Captures validate retained inputs before
execution; contained providers additionally protect them with a kernel policy.
New build outputs can persist between captures. Accepted checks still undergo
the independent clean-source replay before a PASS counts.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

try:
    from . import autocode_source_snapshot as source
    from . import autocode_util as util
except ImportError:
    import autocode_source_snapshot as source
    import autocode_util as util


def _identity(path):
    if path.is_symlink():
        return 'symlink:' + os.readlink(path)
    if path.is_file():
        return ('executable:' if path.stat().st_mode & 0o111 else '') + util.file_hash(path)
    return 'deleted' if not path.exists() else 'directory'


def _inventory(root, source_paths=()):
    return source.inventory(root, paths=source_paths)


def _run_directory(root, run_dir):
    directory = Path(run_dir)
    storage = root / '.autocode'
    if storage.is_symlink():
        raise ValueError('Verification storage must not be a symlink')
    if (directory.parent != storage / 'runs' or not directory.is_dir()
            or directory.is_symlink() or directory.resolve() != directory):
        raise ValueError('Verification copy needs an existing physical task run directory')
    return directory


def allocate(workspace, run_dir, *, source_paths=()):
    """Allocate one owned capture copy, without claiming an OS tool boundary.

    Retain successful copies as stage evidence. On preparation failure remove
    only this call's fresh control directory, never another stage's outputs.
    The layout is the existing capture manifest's authority contract.
    """
    root = Path(workspace).resolve()
    run = _run_directory(root, run_dir)
    control = run / ('tool-containment-' + uuid.uuid4().hex)
    control.mkdir(mode=0o700)
    try:
        scratch = control / 'scratch'
        scratch.mkdir(mode=0o700)
        result = create(root, scratch, source_paths=source_paths, run_dir=run)
        tree, provenance = execution(result['manifest'], result['sha256'], root)
        return {'manifest': result['manifest'], 'sha256': result['sha256'],
                'workspace': str(root), 'tree': str(tree), **provenance}
    except BaseException:
        shutil.rmtree(control)
        raise


def create(workspace, scratch, *, source_paths=(), run_dir=None):
    root, scratch = Path(workspace).resolve(), Path(scratch).resolve()
    if not scratch.is_relative_to(root / '.autocode'):
        raise ValueError('Verification copy must be inside the owned task scratch')
    run = _run_directory(root, run_dir) if run_dir is not None else None
    if run is not None and (scratch.name != 'scratch' or scratch.parent.parent != run
            or not re.fullmatch(r'tool-containment-[0-9a-f]{32}', scratch.parent.name)):
        raise ValueError('Verification copy must be inside its owning run control directory')
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
    # This environment is built from scratch, so the suite-wide GIT_CONFIG_COUNT
    # that stops every other repository starting detached maintenance (#515)
    # never reaches it; write the keys into the repository instead, or a repack
    # under .git/objects after the manifest walk reads as a changed input.
    for args in [('init', '-q'), ('config', 'maintenance.auto', 'false'), ('config', 'gc.auto', '0'),
                 ('add', '-f', '--all'),
                 ('-c', 'user.name=AutoCode verification', '-c', 'user.email=verification@localhost',
                  '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-qm', 'Verification source')]:
        subprocess.run(['/usr/bin/git', *args], cwd=tree, env=env, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    # Match the clean replay's read-only task-local dependency lookup. These
    # links grant no new authority. A contained provider's sandbox also keeps
    # their originals read-only; an uncontained provider gets no such claim.
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
    if run is not None:
        data['run_dir'] = str(run)
    util.atomic_json(manifest, data)
    return {'manifest': str(manifest), 'sha256': util.file_hash(manifest),
            'protected_paths': protected}


def execution(manifest, expected_hash, workspace):
    """Validate retained inputs, returning the cwd and provenance for capture."""
    root, path = Path(workspace).resolve(), Path(manifest)
    control = path.parent
    legacy = control.parent == root / '.autocode'
    run = None
    if not legacy:
        run = _run_directory(root, control.parent)
    if (path.is_symlink() or path.resolve() != path or path.name != 'verification-copy.json'
            or not control.name.startswith('tool-containment-')
            or (run is not None and not re.fullmatch(r'tool-containment-[0-9a-f]{32}', control.name))
            or util.file_hash(path) != expected_hash):
        raise ValueError('Verification copy authority changed')
    data = json.loads(path.read_text())
    tree = control / 'scratch' / 'verification'
    if (data.get('version') != 1 or data.get('workspace') != str(root) or data.get('tree') != str(tree)
            or data.get('run_dir') != (str(run) if run is not None else None)):
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
