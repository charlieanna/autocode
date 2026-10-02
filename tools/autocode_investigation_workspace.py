"""Prepare complete, isolated source for a bug investigation before its model runs."""
from functools import partial
from pathlib import Path
import shutil
import tempfile


EXCLUDED = {'.git', '.autocode'}


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


def ignored_entries(workspace, directory, names, *, reuse_virtualenvs=False):
    """Exclude metadata and reject links that could expose state or the original source."""
    workspace = Path(workspace).resolve()
    ignored = EXCLUDED.intersection(names)
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
    return ignored


def prepare(workspace):
    """Return a fresh complete copy; prior attempts and application files remain intact."""
    workspace = Path(workspace).resolve()
    root = scratch_root(workspace)
    target = Path(tempfile.mkdtemp(prefix='bug-', dir=root))
    shutil.copytree(workspace, target, dirs_exist_ok=True,
                    ignore=partial(ignored_entries, workspace, reuse_virtualenvs=True))
    return target
