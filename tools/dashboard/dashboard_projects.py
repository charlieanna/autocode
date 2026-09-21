"""Reversible dashboard project exclusions; project files are never modified."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
import threading


_INVALID_STORE = (
    'The saved project list is invalid. Restore or repair removed-projects.json '
    'in the dashboard data directory, then retry. No project files were changed.'
)
_UNSAFE_STORE = (
    'The dashboard project list or its lock is not a regular file. '
    'Replace the unsafe link or file in the dashboard data directory, then retry.'
)


def _workspace(value):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError('Project paths must be nonempty absolute paths without null characters.')
    try:
        path = Path(value).expanduser()
    except (RuntimeError, ValueError) as error:
        raise ValueError('The project path could not be expanded. Use an absolute path.') from error
    if not path.is_absolute():
        raise ValueError('Project paths must be absolute paths.')
    return path


def _lexical(path):
    # Stored identities must remain usable if a project disappears or its former
    # path later becomes a symlink. Do not resolve saved identities on reads.
    return os.path.normpath(str(path))


class ProjectStore:
    """An atomic exclusion list, independent of the runner's project registry."""
    filename = 'removed-projects'
    collection = 'removed'
    identity_key = 'workspace'
    timestamp_key = 'removed_at'
    invalid_message = _INVALID_STORE

    def __init__(self, root=None):
        if root is None:
            home = Path(os.environ.get('AUTOCODE_HOME', '~/.autocode')).expanduser()
            root = Path(os.environ.get('AUTOCODE_DASHBOARD_HOME', str(home / 'dashboard'))).expanduser()
        self.root = Path(root).expanduser().absolute()
        self.path = self.root / (self.filename + '.json')
        self.lock_path = self.root / (self.filename + '.lock')
        self._lock = threading.RLock()

    def _check_root(self, create=False):
        try:
            info = self.root.lstat()
        except FileNotFoundError:
            if not create:
                return False
            self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
            info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('The dashboard data directory must be a regular directory, not a symbolic link.')
        if create:
            os.chmod(self.root, 0o700)
        return True

    @staticmethod
    def _check_file(path):
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(_UNSAFE_STORE)
        return True

    @staticmethod
    def _open(path, flags, mode=0o600):
        try:
            descriptor = os.open(path, flags | os.O_NOFOLLOW, mode)
        except OSError:
            # A symlink may have appeared after lstat. Preserve normal IO errors
            # for unavailable directories but never follow an unsafe file.
            ProjectStore._check_file(path)
            raise
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise ValueError(_UNSAFE_STORE)
        return descriptor

    @contextmanager
    def _guard(self, write=False):
        with self._lock:
            if not self._check_root(create=write):
                yield
                return
            self._check_file(self.path)
            lock_exists = self._check_file(self.lock_path)
            if not write and not lock_exists:
                # A missing store must remain absent after polling. An imported
                # JSON file without a lock is still an atomic, immutable snapshot;
                # _read takes a shared lock on its descriptor without creating one.
                yield
                return
            flags = os.O_RDWR | os.O_CREAT if write else os.O_RDONLY
            descriptor = self._open(self.lock_path, flags)
            try:
                if write:
                    os.fchmod(descriptor, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def _read(self):
        if not self._check_file(self.path):
            return []
        try:
            descriptor = self._open(self.path, os.O_RDONLY)
            with os.fdopen(descriptor, 'r', encoding='utf-8') as source:
                fcntl.flock(source.fileno(), fcntl.LOCK_SH)
                document = json.load(source)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError(self.invalid_message) from error
        if (not isinstance(document, dict) or set(document) != {'version', self.collection}
                or type(document['version']) is not int or document['version'] != 1
                or not isinstance(document[self.collection], list)):
            raise ValueError(self.invalid_message)
        seen = set()
        result = []
        for row in document[self.collection]:
            if not isinstance(row, dict) or set(row) != {self.identity_key, self.timestamp_key}:
                raise ValueError(self.invalid_message)
            path, when = row[self.identity_key], row[self.timestamp_key]
            if (not isinstance(path, str) or not path or '\x00' in path
                    or not Path(path).is_absolute() or _lexical(path) != path or path in seen
                    or not isinstance(when, str)):
                raise ValueError(self.invalid_message)
            try:
                timestamp = datetime.fromisoformat(when)
                if timestamp.tzinfo is None:
                    raise ValueError('Missing timezone')
            except ValueError as error:
                raise ValueError(self.invalid_message) from error
            seen.add(path)
            result.append({'workspace': path, 'removed_at': when})
        return result

    def _write(self, rows):
        # Guard both the original and replacement path even after taking the
        # process lock. Atomic replacement makes reads all-or-nothing.
        self._check_file(self.path)
        descriptor, temporary = tempfile.mkstemp(prefix='.' + self.filename + '-', suffix='.tmp', dir=self.root)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
                os.fchmod(target.fileno(), 0o600)
                saved = [{self.identity_key: row['workspace'], self.timestamp_key: row['removed_at']} for row in rows]
                json.dump({'version': 1, self.collection: saved}, target, ensure_ascii=False, indent=2)
                target.write('\n')
                target.flush()
                os.fsync(target.fileno())
            self._check_file(self.path)
            os.replace(temporary, self.path)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def list(self):
        with self._guard():
            return self._read()

    def remove(self, workspace):
        path = _workspace(workspace)
        with self._guard(write=True):
            rows = self._read()
            literal = _lexical(path)
            # A retry of an existing removal still identifies that saved
            # project, even if its old location now points somewhere else.
            existing = next((row for row in rows if row['workspace'] == literal), None)
            if existing:
                return existing
            try:
                identity = str(path.resolve(strict=False))
            except (OSError, RuntimeError) as error:
                raise ValueError('The project path could not be resolved. Check its symbolic links, then retry.') from error
            existing = next((row for row in rows if row['workspace'] == identity), None)
            if existing:
                return existing
            row = {'workspace': identity,
                   'removed_at': datetime.now(timezone.utc).isoformat(timespec='milliseconds')}
            self._write([*rows, row])
            return dict(row)

    def restore(self, workspace):
        path = _workspace(workspace)
        with self._guard(write=True):
            rows = self._read()
            literal = _lexical(path)
            # First match the stored canonical identity exactly, even when that
            # location now points at a different project or no longer exists.
            identity = literal
            if not any(row['workspace'] == literal for row in rows):
                try:
                    identity = str(path.resolve(strict=False))
                except (OSError, RuntimeError) as error:
                    raise ValueError('The project path could not be resolved. Check its symbolic links, then retry.') from error
            kept = [row for row in rows if row['workspace'] != identity]
            removed = len(kept) != len(rows)
            if removed:
                self._write(kept)
            return {'workspace': identity, 'restored': True, 'removed': removed}
