"""Launch-bound ignored inputs for in-place scratch proofs (#529).

record is the sole writer of state['launch_sources']; supply reads it. Generated
source bytes are retained beside the manifest; generated and vendor inputs must
still match launch bytes/modes before they can support proof. Missing, changed,
or incompletely captured inputs cannot establish PASS. Added inputs are omitted.
Task worktrees use their separate project checkout's existing dependency policy.
Shared virtualenvs and node_modules are outside this inventory.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import uuid

try:
    from . import autocode_util as util, autocode_verify as verify
except ImportError:
    import autocode_util as util
    import autocode_verify as verify

STORE = 'launch-sources'
SHOWN = 10
_HASH = re.compile(r'[0-9a-f]{64}\Z')


def _names(names):
    names = sorted(names)
    return ', '.join(names[:SHOWN]) + (f' and {len(names) - SHOWN} more' if len(names) > SHOWN else '')


def _path(root, name):
    if not isinstance(name, str):
        raise ValueError('Ignored input path is not a repository-relative name')
    relative = PurePosixPath(name)
    if (not name or name == '.' or relative.is_absolute() or '..' in relative.parts
            or '.git' in relative.parts or str(relative) != name):
        raise ValueError('Ignored inputs must use contained repository-relative paths')
    current = Path(root)
    if current.is_symlink():
        raise ValueError('Ignored input root must not be a symlink')
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f'Ignored input must not follow a symlink: {name}')
    return current


@contextmanager
def _parent(root, name, *, create=False):
    """Pin every parent with no-follow directory opens before reading or writing."""
    _path(root, name)
    root_parts = Path(root).absolute().parts[1:]
    relative = PurePosixPath(name).parts
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(os.path.sep, flags)
    try:
        for index, part in enumerate((*root_parts, *relative[:-1])):
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create or index < len(root_parts):
                    raise
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, relative[-1]
    finally:
        os.close(fd)


def _read(root, name, limit=None):
    with _parent(root, name) as (parent, leaf):
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(fd, 'rb') as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or (limit is not None and info.st_size > limit):
            raise ValueError(f'Ignored input is not a bounded regular file: {name}')
        data = file.read() if limit is None else file.read(limit + 1)
        if limit is not None and len(data) > limit:
            raise ValueError(f'Ignored input exceeds its source bound: {name}')
    return data, info.st_mode & 0o777


def _write(root, name, data, *, mode=0o600):
    """Replace one capture file atomically through its pinned parent."""
    with _parent(root, name, create=True) as (parent, leaf):
        temporary = f'.{leaf}.{uuid.uuid4().hex}'
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent)
            with os.fdopen(fd, 'wb') as file:
                file.write(data)
                os.fchmod(file.fileno(), mode)
            os.replace(temporary, leaf, src_dir_fd=parent, dst_dir_fd=parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


def _entry(root, name, limit=None):
    data, mode = _read(root, name, limit)
    return [hashlib.sha256(data).hexdigest(), mode]


def record(state, workspace, run_dir):
    """Capture once for a new in-place run, before any provider or prerequisite."""
    workspace, store = Path(workspace), Path(run_dir) / STORE
    if store.is_symlink():
        raise ValueError('Launch input storage must not be a symlink')
    manifest = {'version': 1, 'workspace': str(workspace.resolve())}
    for kind, inventory in (('generated', verify.generated_sources), ('vendored', verify.vendored_files)):
        try:
            files = {}
            for name in inventory(workspace):
                data, mode = _read(workspace, name, verify.GENERATED_SOURCE_LIMIT if kind == 'generated' else None)
                digest = hashlib.sha256(data).hexdigest()
                if kind == 'generated':
                    _write(run_dir, f'{STORE}/{digest}', data)
                files[name] = [digest, mode]
            manifest[kind] = files
        except (OSError, ValueError):
            manifest[kind] = None  # failure is retained even if the files later disappear
    data = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    _write(run_dir, f'{STORE}/manifest.json', data)
    # The base this record vouches for: the one pinned at launch, or None when the checkout was busy and
    # the base is pinned later (autocode_build_loop); supply() trusts the record only for its own base.
    state['launch_sources'] = {'manifest_sha256': hashlib.sha256(data).hexdigest(),
                               'base_commit': state.get('base_commit')}


def _manifest(state, store, checkout):
    try:
        data, _ = _read(store, 'manifest.json', 8 * 1024 * 1024)
        expected = (state.get('launch_sources') or {}).get('manifest_sha256')
        if not isinstance(expected, str) or not _HASH.fullmatch(expected) or hashlib.sha256(data).hexdigest() != expected:
            return None
        value = json.loads(data)
        if (not isinstance(value, dict) or set(value) != {'version', 'workspace', 'generated', 'vendored'}
                or type(value['version']) is not int or value['version'] != 1
                or value['workspace'] != str(Path(checkout).resolve())):
            return None
        for kind in ('generated', 'vendored'):
            files = value[kind]
            if files is None:
                continue
            if not isinstance(files, dict):
                return None
            for name, entry in files.items():
                _path(store, name)  # structural path check; no inventory file is read here
                if (not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[0], str)
                        or not _HASH.fullmatch(entry[0]) or type(entry[1]) is not int or not 0 <= entry[1] <= 0o777
                        or (kind == 'vendored' and not name.startswith('vendor/'))):
                    return None
        return value
    except (OSError, ValueError, TypeError, AttributeError):
        return None


class Supply:
    """Exact launch inputs for one proof; uncertainty blocks execution and reuse.

    ``recorded``: a valid launch manifest, taken with the base the run is proven against, backs
    ``generated`` and ``vendored``, so with nothing ``unverified`` they are every ignored input the
    checkout held then, still unchanged (autocode_verify._document_only_base reads it). Without one,
    or for a run whose base was pinned after its record (the checkout was busy at launch), an empty
    Supply says nothing."""
    def __init__(self, checkout, store, generated, vendored, unverified, notes, *, recorded=False):
        self.checkout, self.store = Path(checkout), Path(store)
        self.generated, self.vendored = generated, vendored
        self.unverified, self.notes = unverified, notes
        self.recorded = recorded
        self.identity = util.digest({'generated': generated, 'vendored': vendored, 'unverified': unverified})

    def copy_into(self, tree):
        if self.unverified:
            raise ValueError('; '.join(self.unverified))
        tree = Path(tree)
        # Iterate the captured inventory, never a fresh scan that can silently lose an entry.
        for kind, files in (('generated', self.generated), ('vendored', self.vendored)):
            for name, expected in files.items():
                data, mode = _read(self.checkout, name, verify.GENERATED_SOURCE_LIMIT if kind == 'generated' else None)
                if [hashlib.sha256(data).hexdigest(), mode] != expected:
                    raise ValueError(f'Ignored input changed while preparing a scratch tree: {name}')
                if kind == 'generated':
                    captured, _ = _read(self.store, expected[0], verify.GENERATED_SOURCE_LIMIT)
                    if hashlib.sha256(captured).hexdigest() != expected[0]:
                        raise ValueError(f'The launch copy of {name} is missing or damaged')
                    data = captured
                with _parent(tree, name, create=True) as (parent, leaf):
                    try:
                        existing = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        existing = None
                    if existing is not None:
                        if not stat.S_ISREG(existing.st_mode):
                            raise ValueError(f'Ignored input destination is not a regular file: {name}')
                        continue  # a tracked base/overlay entry owns this path
                    fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent)
                    with os.fdopen(fd, 'wb') as file:
                        file.write(data)
                        os.fchmod(file.fileno(), mode)



def supply(state, checkout, run_dir):
    """Return the current in-place proof policy, or None for a separate task checkout."""
    checkout = Path(checkout)
    project = state.get('project_workspace')
    if project and Path(project).resolve() != checkout.resolve():
        return None
    store = Path(run_dir) / STORE
    present, errors = {}, []
    for kind, inventory in (('generated', verify.generated_sources), ('vendored', verify.vendored_files)):
        try:
            present[kind] = inventory(checkout)
        except (OSError, ValueError):
            present[kind] = []
            errors.append(f'The current ignored {kind} inputs cannot be inventoried')
    manifest = _manifest(state, store, checkout)
    if manifest is None:
        names = present['generated'] + present['vendored']
        legacy = state.get('generated_sources_at_start')
        if isinstance(legacy, dict):
            names = sorted(set(names) | {name for name in legacy if isinstance(name, str)})
        if 'launch_sources' in state or 'generated_sources_at_start' in state or names:
            errors.append('The record of the ignored files this in-place run started with is missing or damaged; '
                          'the proof copies none of them into its test copies' + (': ' + _names(names) if names else '')
                          + '. Start the fix again to prove it')
        return Supply(checkout, store, {}, {}, errors, [])
    notes, selected = [], {}
    for kind in ('generated', 'vendored'):
        recorded = manifest[kind]
        selected[kind] = {}
        if recorded is None:
            errors.append(f'The ignored {kind} files this in-place run started with could not be recorded. '
                          'Prepare readable regular files without symlinked paths, then start the fix again')
            continue
        changed, damaged = [], []
        for name, expected in recorded.items():
            try:
                if _entry(checkout, name, verify.GENERATED_SOURCE_LIMIT if kind == 'generated' else None) != expected:
                    changed.append(name)
                    continue
            except (OSError, ValueError):
                changed.append(name)
                continue
            if kind == 'generated':
                try:
                    if _entry(store, expected[0], verify.GENERATED_SOURCE_LIMIT)[0] != expected[0]:
                        raise ValueError('Captured source hash changed')
                except (OSError, ValueError):
                    damaged.append(name)
                    continue
            selected[kind][name] = expected
        if changed:
            errors.append(f'Ignored {kind} files changed or removed since this in-place run started: '
                          + _names(changed) + '. Restore them, or start the fix again')
        if damaged:
            errors.append('The launch copies of these ignored source files are missing or damaged: ' + _names(damaged))
        added = set(present[kind]) - set(recorded)
        if added:
            notes.append(f'Ignored {kind} files left out of every test copy (added since this in-place run started): '
                         + _names(added))
    base = (state.get('launch_sources') or {}).get('base_commit')
    return Supply(checkout, store, selected['generated'], selected['vendored'], errors, notes,
                  recorded=bool(base) and base == state.get('base_commit'))


def guard(state, workspace, run_dir):
    """Recheck launch inputs before current or cached evidence grants authority."""
    if run_dir is None:
        if 'launch_sources' in state:
            raise ValueError('Checking captured launch inputs requires the run directory')
        return None  # Legacy in-memory callers have no captured launch record.
    inputs = supply(state, workspace, run_dir)
    if inputs and inputs.unverified:
        raise ValueError('; '.join(inputs.unverified))
    return inputs


def runners(state, workspace, run_dir, scratch_run, execution_identity=None):
    """Bind fresh launch-input policy to injected clean runners without a controller."""
    dependencies = state.get('project_workspace') or str(workspace)
    def current():
        return guard(state, workspace, run_dir)
    def run(*args, **kwargs):
        inputs = current()
        receipt = scratch_run(*args, **kwargs, dependencies_from=dependencies, ignored_inputs=inputs)
        after = current()
        if (inputs.identity if inputs else None) != (after.identity if after else None):
            raise ValueError('Ignored launch inputs changed during clean execution')
        if after and after.notes:
            receipt = {**receipt, 'notes': [*(receipt.get('notes') or []), *after.notes]}
        return receipt
    def identity(*args, **kwargs):
        return execution_identity(*args, **kwargs, dependencies_from=dependencies, ignored_inputs=current())
    return run, identity if execution_identity else None
