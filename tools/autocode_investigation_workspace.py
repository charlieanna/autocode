"""Prepare complete, isolated source for a bug investigation before its model runs."""
from functools import partial
from pathlib import Path
import shutil
import subprocess
import tempfile


EXCLUDED = {'.git', '.autocode', '.autocode-ui'}


def scratch_root(workspace):
    """Locate scratch inside this workspace without traversing a runner-state symlink."""
    workspace = Path(workspace).resolve()
    if not workspace.is_dir():
        raise ValueError('Investigation requires an existing workspace')
    root = workspace / '.autocode' / 'investigation'
    for path in (root.parent, root):
        if path.is_symlink():
            raise ValueError('Investigation scratch must not use a symlinked runner directory')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def ignored_entries(workspace, directory, names, *, reuse_virtualenvs=False, inventory=None):
    """Exclude metadata and reject links that could expose state or the original source."""
    workspace = Path(workspace).resolve()
    ignored = EXCLUDED.intersection(names)
    if inventory is not None:
        relative = Path(directory).relative_to(workspace)
        ignored.update(name for name in names if relative / name not in inventory)
    if reuse_virtualenvs and Path(directory) == workspace:
        # Virtualenvs contain external interpreter links (and, on Linux, lib64
        # directory links). Reuse their interpreter through the handoff instead
        # of weakening the source-copy boundary or copying a nonportable env.
        for name in {'.venv', 'venv'}.intersection(names):
            environment = workspace / name
            config = environment / 'pyvenv.cfg'
            if not environment.is_symlink() and config.is_file() and not config.is_symlink():
                ignored.add(name)
    for name in set(names) - ignored:
        source = Path(directory) / name
        if not source.is_symlink():
            continue
        try:
            relative = source.resolve(strict=True).relative_to(workspace)
        except (OSError, ValueError) as error:
            raise ValueError(f'Investigation cannot copy an external or invalid symlink: {source}') from error
        if EXCLUDED.intersection(relative.parts) or source.is_dir():
            raise ValueError(f'Investigation cannot copy a state or directory symlink: {source}')
        if inventory is not None and relative not in inventory:
            raise ValueError(f'Investigation cannot copy a symlink to ignored or excluded source: {source}')
    return ignored


def source_inventory(workspace):
    """Eligible Git paths and their ancestors; non-Git callers retain their copy rule."""
    workspace = Path(workspace).resolve()
    root = subprocess.run(['git', '-C', str(workspace), 'rev-parse', '--show-toplevel'],
                          capture_output=True, text=True, check=False)
    if root.returncode or Path(root.stdout.strip()).resolve() != workspace:
        return None
    listed = subprocess.run(['git', '-C', str(workspace), 'ls-files', '-z', '--cached',
                             '--others', '--exclude-standard'], capture_output=True, check=False)
    if listed.returncode:
        raise ValueError('Investigation cannot enumerate the Git source inventory')
    included = set()
    for name in filter(None, listed.stdout.decode().split('\0')):
        relative = Path(name)
        if EXCLUDED.intersection(relative.parts) or '__pycache__' in relative.parts or name.endswith('.pyc'):
            continue
        source = workspace / relative
        if source.is_dir() and not source.is_symlink():
            nested = source_inventory(source) if (source / '.git').exists() else None
            if nested is None:
                continue
            included.update(relative / item for item in nested)
        else:
            included.add(relative)
        included.update(relative.parents)
    return included


def prepare(workspace):
    """Return a fresh complete copy; prior attempts and application files remain intact."""
    workspace = Path(workspace).resolve()
    inventory = source_inventory(workspace)
    root = scratch_root(workspace)
    target = Path(tempfile.mkdtemp(prefix='bug-', dir=root))
    shutil.copytree(workspace, target, dirs_exist_ok=True,
                    ignore=partial(ignored_entries, workspace, reuse_virtualenvs=True, inventory=inventory))
    return target
