"""Reserve an exact fresh task-run directory without rebinding saved artifacts.

Only the new-run interface calls this; resumptions keep their existing paths.
No launch, lock, state, or provider authority is implied by an empty directory.
"""

from __future__ import annotations

import os
from contextlib import ExitStack, suppress
from pathlib import Path


def reserve(workspace: Path, destination: Path) -> Path:
    """Exclusively create one immediate child of the checkout's run store.

    Reject links and existing destinations, including empty ones. In particular,
    never normalize a symlink into another run or overwrite an earlier attempt.
    """
    workspace = Path(workspace).resolve()
    destination = Path(destination).absolute()
    store = workspace / ".autocode" / "runs"
    if ".." in destination.parts or destination.parent != store or destination.name in ("", ".", ".."):
        raise ValueError("destination must be an immediate child of workspace/.autocode/runs")
    for path in (workspace / ".autocode", store, destination):
        if path.is_symlink():
            raise ValueError("fresh run destination must not follow a symlink")
    # Pin each directory and refuse links on open, so replacing a pathname
    # during admission cannot redirect this write outside the run store.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        fd = os.open(workspace, flags)
        stack.callback(os.close, fd)
        for name in (".autocode", "runs"):
            with suppress(FileExistsError):
                os.mkdir(name, dir_fd=fd)
            fd = os.open(name, flags, dir_fd=fd)
            stack.callback(os.close, fd)
        os.mkdir(destination.name, dir_fd=fd)
        identity = os.fstat(fd)
        current = store.stat(follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            raise ValueError("run store changed during fresh admission")
    return destination
