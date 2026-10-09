"""Opaque protected-test storage; never expose retained tests to project discovery.

The logical binding (version/files/command) is unchanged. New roots are zip
files. A legacy directory locator also resolves its verified .zip companion,
so compaction needs no settings rewrite or new approval identity. Only the
locked run's own retained paths may be compacted; unrelated files are kept.
"""
import hashlib
import os
import shutil
import stat
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

try:
    from . import autocode_protected_paths as paths
except ImportError:
    import autocode_protected_paths as paths


def archive_path(record):
    root = Path(record['root'])
    if root.is_symlink():
        raise ValueError('Protected test root must not be a symlink')
    archive = root if root.suffix == '.zip' else Path(str(root) + '.zip')
    return archive if archive.exists() or archive.is_symlink() or root.suffix == '.zip' else None


def _links(files):
    """Check the same complete file-link closure as the directory representation."""
    directories = {''}
    for name in files:
        relative = paths.relative_name(name)
        directories.update(str(p) for p in relative.parents if str(p) != '.')
    if set(files) & directories:
        raise ValueError('Protected test bundle has a file where a directory is required')
    for start in files:
        name, seen = start, set()
        while 'symlink' in files[name]:
            if name in seen:
                raise ValueError('Protected test link cycle')
            seen.add(name)
            target = files[name]['symlink']
            if not target or PurePosixPath(target).is_absolute():
                raise ValueError('Protected test link must stay repository-relative')
            parts = list(PurePosixPath(name).parent.parts)
            tokens = target.split('/')
            for index, part in enumerate(tokens):
                if part == '..':
                    if not parts:
                        raise ValueError('Protected test link escapes the repository')
                    parts.pop()
                elif part not in ('', '.'):
                    parts.append(part)
                if index < len(tokens) - 1 and '/'.join(parts) not in directories:
                    raise ValueError('Protected test link traverses a non-directory or directory symlink')
            name = str(paths.relative_name('/'.join(parts)))
            if name not in files:
                raise ValueError('Protected test link target is not in the source snapshot')


def read(record, archive):
    """Validate member identities before using bytes; never extract archive paths."""
    archive = Path(archive)
    if archive.is_symlink() or not archive.is_file():
        raise ValueError('Original protected test archive is missing or is a symlink')
    files = record['files']
    _links(files)
    try:
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            if len(names) != len(files) or set(names) != set(files):
                raise ValueError('Original protected test bundle changed: archive inventory')
            for name, expected in files.items():
                info = bundle.getinfo(name)
                mode = info.external_attr >> 16
                if 'symlink' in expected:
                    if record['version'] != 2 or mode != stat.S_IFLNK | 0o777:
                        raise ValueError(f'Original protected test bundle changed: {name}')
                    size = len(os.fsencode(expected['symlink']))
                else:
                    size = expected['size']
                    if mode != stat.S_IFREG | expected['mode']:
                        raise ValueError(f'Original protected test bundle changed: {name}')
                if info.file_size != size or info.flag_bits & 1:
                    raise ValueError(f'Original protected test bundle changed: {name}')
                if 'symlink' in expected:
                    actual = {'symlink': os.fsdecode(bundle.read(name))}
                else:
                    digest, length = hashlib.sha256(), 0
                    with bundle.open(info) as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b''):
                            digest.update(chunk)
                            length += len(chunk)
                    actual = {'sha256': digest.hexdigest(), 'size': length, 'mode': mode & 0o777}
                if actual != expected:
                    raise ValueError(f'Original protected test bundle changed: {name}')
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, UnicodeError) as error:
        raise ValueError(f'Original protected test bundle changed: {error}') from error
    # A crash during compaction can leave original members behind. Check them
    # too: a valid archive is not authority to erase a modified legacy member.
    root = Path(record['root'])
    if root != archive and root.exists():
        for name, expected in files.items():
            old = root / name
            if old.exists() or old.is_symlink():
                if paths.identity(paths.path_in(root, name, allow_link=record['version'] == 2)) != expected:
                    raise ValueError(f'Original protected test bundle changed: {name}')


def capture(record, source, archive):
    """Publish a complete verified archive atomically; existing archives are immutable."""
    archive = Path(archive)
    if archive.parent.is_symlink():
        raise ValueError('Protected test storage directory must not be a symlink')
    if archive.exists() or archive.is_symlink():
        read(record, archive)
        return
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive.with_name('.capture-' + uuid.uuid4().hex + '.zip')
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as output, \
                zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as bundle:
            for name, expected in sorted(record['files'].items()):
                original = paths.path_in(source, name, allow_link=record['version'] == 2)
                if paths.identity(original) != expected:
                    raise ValueError(f'Protected input changed during capture: {name}')
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                mode = stat.S_IFLNK | 0o777 if 'symlink' in expected else stat.S_IFREG | expected['mode']
                info.external_attr = mode << 16
                if 'symlink' in expected:
                    bundle.writestr(info, os.fsencode(expected['symlink']))
                else:
                    with original.open('rb') as source_file, bundle.open(info, 'w', force_zip64=True) as target:
                        shutil.copyfileobj(source_file, target, 1024 * 1024)
        temporary.chmod(0o600)
        read(dict(record, root=str(temporary)), temporary)
        for name, expected in record['files'].items():
            if paths.identity(paths.path_in(source, name, allow_link=record['version'] == 2)) != expected:
                raise ValueError(f'Protected input changed during capture: {name}')
        temporary.rename(archive)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def opened(record, workspace):
    archive = archive_path(record)
    if archive is None:
        yield Path(record['root'])
        return
    read(record, archive)
    project = Path(workspace).resolve()
    parent = Path(tempfile.gettempdir()).resolve()
    if parent.is_relative_to(project):
        parent = project.parent
    if parent.is_relative_to(project):
        raise ValueError('Protected-test replay needs temporary storage outside the project')
    with tempfile.TemporaryDirectory(prefix='autocode-protected-', dir=parent) as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(archive) as bundle:
            for name, expected in record['files'].items():
                if 'symlink' in expected:
                    continue
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(name) as source, target.open('wb') as sink:
                    shutil.copyfileobj(source, sink, 1024 * 1024)
                target.chmod(expected['mode'])
                if paths.identity(target) != expected:
                    raise ValueError(f'Original protected test bundle changed: {name}')
        for name, expected in record['files'].items():
            if 'symlink' in expected:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(expected['symlink'])
        read(record, archive)
        yield root


def compact(record, run_dir):
    """Retire only an idle locked run's own directory copies, keeping exact originals."""
    root = Path(record['root'])
    expected = Path(run_dir) / 'protected-tests' / record['binding_hash']
    if root != expected or not root.exists():
        return
    if root.is_symlink():
        raise ValueError('Protected test root must not be a symlink')
    archive = Path(str(root) + '.zip')
    capture(record, root, archive)
    read(record, archive)
    # No rmtree: files outside the recorded inventory belong to someone else.
    for name in record['files']:
        old = root / name
        if old.exists() or old.is_symlink():
            if paths.identity(paths.path_in(root, name, allow_link=record['version'] == 2)) != record['files'][name]:
                raise ValueError(f'Original protected test bundle changed: {name}')
            old.unlink()
    directories = {root}
    for name in record['files']:
        directories.update((root / name).parents)
    for directory in sorted((p for p in directories if p == root or root in p.parents),
                            key=lambda p: len(p.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass  # unrelated contents are intentionally retained
