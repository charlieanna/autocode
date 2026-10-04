"""Immutable source captures for failed read-only jobs; no controller imports.

The runner writes record.job_source before admission and adds its stopped witness
before raising a failure. Recovery reads those hash-bound artifacts; it never
creates a witness from a later checkout. Capture blobs stay in the run-owned
.source directory when the manifest/witness are archived.
"""
from __future__ import annotations
import os
import stat
import shutil
import tempfile
from pathlib import Path
try:
    from . import autocode_util as util, autocode_jobs as jobs
except ImportError:
    import autocode_util as util
    import autocode_jobs as jobs


def _path(root, name):
    relative = Path(name)
    if (relative.is_absolute() or not relative.parts or any(p in ('.', '..', '.git', '.autocode') for p in relative.parts)):
        raise ValueError('Unsafe source path: ' + name)
    for parent in relative.parents:
        if (root / parent).is_symlink():
            raise ValueError('Symlinked source parent: ' + name)
    return root / relative


def _entry(root, name):
    path = _path(root, name)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {'kind': 'missing'}
    if stat.S_ISLNK(info.st_mode):
        return {'kind': 'symlink', 'link': os.readlink(path)}
    if stat.S_ISREG(info.st_mode):
        return {'kind': 'file', 'sha256': util.file_hash(path), 'mode': stat.S_IMODE(info.st_mode)}
    return {'kind': 'unsupported'}


def identity(workspace):
    root = Path(workspace)
    snap = util.snapshot(root)
    return {'head': snap['head'], 'files': {name: _entry(root, name) for name in snap['files']}}


def capture(workspace, base, record, before):
    if record.get('stage') not in jobs.STAGES or record.get('report_only'):
        return
    root, base = Path(workspace), Path(base)
    original = identity(root)
    if util.snapshot(root)['revision'] != before['revision']:
        raise util.Paused('PAUSED_STALE_VALIDATION', 'Source changed before workflow-job admission')
    directory = base.with_suffix('.source')
    directory.mkdir(mode=0o700)
    blobs = {}
    for name, entry in original['files'].items():
        if entry['kind'] == 'file':
            blob = directory / util.digest(name)
            with blob.open('xb') as handle:
                os.chmod(blob, 0o600)
                handle.write(_path(root, name).read_bytes())
            if util.file_hash(blob) != entry['sha256']:
                raise util.Paused('PAUSED_STALE_VALIDATION', 'Source changed while capturing ' + name)
            blobs[name] = str(blob)
    if original != identity(root):
        raise util.Paused('PAUSED_STALE_VALIDATION', 'Source changed during workflow-job capture')
    manifest = base.with_suffix('.source.json')
    util.atomic_json(manifest, {'original': original, 'blobs': blobs})
    record['job_source'] = {'capture': str(manifest), 'capture_hash': util.file_hash(manifest),
                            'paths': sorted(original['files']), 'before_identity': util.digest(original)}


def stopped(workspace, base, record):
    capture = record.get('job_source')
    if not capture or capture.get('witness'):
        return
    root, base = Path(workspace), Path(base)
    after = util.snapshot(root)
    witness = identity(root)
    # Include pre-existing untracked files that the attempt deleted.
    for name in capture['paths']:
        witness['files'].setdefault(name, _entry(root, name))
    path = base.with_suffix('.witness.json')
    util.atomic_json(path, witness)
    capture.update(witness=str(path), witness_hash=util.file_hash(path))
    util.atomic_json(base.with_suffix('.after.json'), after)
    record.update(after_ref=str(base.with_suffix('.after.json')), source_revision=after['revision'])
    before = util.read(Path(record['before_ref']))
    record['changed_files'] = util.changed_paths(before, after)


def _bound(path, expected):
    if not path or not expected or not Path(path).is_file() or util.file_hash(Path(path)) != expected:
        raise ValueError('Missing or corrupt source capture/witness')
    return util.read_object(Path(path))


def _replace(root, name, original, witnessed, blob):
    target = _path(root, name)
    if original['kind'] == 'missing':
        if _entry(root, name) != witnessed:
            raise ValueError('Source changed after the stopped witness')
        if target.is_file() or target.is_symlink():
            target.unlink()
        return 'deleted'
    if original['kind'] != 'file':
        raise ValueError('Source type cannot be restored safely')
    stored = Path(blob)
    if not stored.is_file() or util.file_hash(stored) != original['sha256']:
        raise ValueError('Missing or corrupt original bytes')
    # Stage on the target filesystem; set mode before replacing current bytes.
    fd, temporary = tempfile.mkstemp(prefix='.autocode-restore-', dir=target.parent)
    staged = Path(temporary)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(stored.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(staged, original['mode'])
        if util.file_hash(staged) != original['sha256'] or _entry(root, name) != witnessed:
            raise ValueError('Source changed after the stopped witness')
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)
    return 'restored'


def restore(workspace, record):
    result = {'restored': [], 'deleted': [], 'unrestored': []}
    capture = record.get('job_source') or {}
    if not capture and not (record.get('changed_files') or []):
        # The attempt crashed before capturing its source binding but also
        # reported no source changes (e.g. a provider exit during a diagnosis
        # stage): there is nothing of the attempt's to restore, and the retry
        # gate anchors on the recorded live-workspace identity instead.
        return result
    try:
        manifest = _bound(capture.get('capture'), capture.get('capture_hash'))
        witness = _bound(capture.get('witness'), capture.get('witness_hash'))
        original = manifest['original']
        if util.digest(original) != capture.get('before_identity'):
            raise ValueError('Original source identity changed')
    except (OSError, ValueError, KeyError, TypeError):
        result['unrestored'] = capture.get('paths') or record.get('changed_files') or ['original source capture']
        return result
    root = Path(workspace)
    if util.snapshot(root)['head'] != original['head']:
        result['unrestored'].append('Git HEAD')
    for name in sorted(original['files'].keys() | witness['files'].keys()):
        before = original['files'].get(name, {'kind': 'missing'})
        after = witness['files'].get(name, {'kind': 'missing'})
        try:
            current = _entry(root, name)
            if current == before:
                continue
            if current != after:
                raise ValueError('Source changed after the stopped witness')
            action = _replace(root, name, before, after, manifest['blobs'].get(name))
            result[action].append(name)
        except (OSError, ValueError, TypeError, KeyError):
            result['unrestored'].append(name)
    return result


def discard_prepared(record):
    """Remove only this never-admitted attempt's owned capture on lost admission."""
    capture = record.get('job_source') or {}
    base = Path(record['output']).with_suffix('')
    path = base.with_suffix('.source.json')
    directory = base.with_suffix('.source')
    if capture.get('capture') == str(path):
        if directory.is_dir() and not directory.is_symlink():
            shutil.rmtree(directory)
        path.unlink(missing_ok=True)
