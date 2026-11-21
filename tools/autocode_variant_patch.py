"""A wrong implementation's patch as copies of the files it changes (#3977).

The writer of wrong implementations writes each patch by hand. Such a diff often has hunks with no context lines
(``@@ -20 +20 @@``) or line counts that are off by one; ``patch -p1`` takes both, and the prompt names the
command the runner uses instead, ``git apply --recount --unidiff-zero``, which takes both too. Plain
``git apply``, which the scratch runner uses for the patches git itself produced, refuses them: in the first live
run six of eight variants were dropped as ``invalid`` that way although each one applied.

``files`` applies the patch to copies of the files it names, outside any repository, and returns them as the
scratch runner's file overlay ({repository-relative name: copy}), the form a counterexample edit already takes
(autocode_counterexample). The patch never touches the workspace. A patch that names a path outside the
repository, a symlink, or a file it deletes is refused with the reason, because an overlay can only replace and
add files.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

try:
    from . import autocode_util as util
    from .autocode_protected_paths import relative_name
except ImportError:
    import autocode_util as util
    from autocode_protected_paths import relative_name

APPLY: tuple[str, ...] = ("git", "apply", "--recount", "--unidiff-zero", "--whitespace=nowarn")


def _git(folder: Path, *args: str, data: bytes | None = None) -> subprocess.CompletedProcess:
    # The folder is a repository of its own, so the patch's paths never resolve against the project's.
    return subprocess.run(
        ["git", "-C", str(folder), *args],
        input=data,
        capture_output=True,
        env=util.git_environment({"GIT_CEILING_DIRECTORIES": str(folder.parent)}),
    )


def _names(folder: Path, data: bytes) -> list[str]:
    listed = _git(folder, *APPLY[1:], "--numstat", "-z", "-", data=data)
    if listed.returncode:
        raise ValueError(listed.stderr.decode(errors="replace").strip()[-300:] or "the patch could not be read")
    fields = [field for field in listed.stdout.decode(errors="replace").split("\0") if field]
    # Each record is "added\tdeleted\tpath"; a rename is "added\tdeleted\t", then the old and the new path.
    names, index = [], 0
    while index < len(fields):
        added_deleted_path = fields[index].split("\t", 2)
        if len(added_deleted_path) == 3 and added_deleted_path[2]:
            names.append(added_deleted_path[2])
            index += 1
        else:
            names.extend(fields[index + 1 : index + 3])
            index += 3
    # The numstat names a renamed file by its new path only; the headers name what the patch takes from.
    for line in data.decode(errors="replace").splitlines():
        for prefix in ("--- a/", "rename from ", "copy from "):
            if line.startswith(prefix):
                name = line[len(prefix) :].split("\t", 1)[0].strip()
                if name and name not in names:
                    names.append(name)
    if not names:
        raise ValueError("the patch changes no file")
    return names


def files(workspace, patch: str, folder) -> dict[str, str]:
    """The overlay that makes ``patch`` in a scratch copy of ``workspace``; ValueError says why it cannot be made."""
    workspace, folder = Path(workspace), Path(folder)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    initialised = _git(folder, "init", "-q")
    if initialised.returncode:
        raise ValueError(initialised.stderr.decode(errors="replace").strip()[-300:])
    data = (patch if patch.endswith("\n") else patch + "\n").encode()
    names = _names(folder, data)
    root = workspace.resolve()
    for name in names:
        try:
            relative_name(name)
        except ValueError:
            raise ValueError(f"the patch names {name!r}, which is not a repository-relative path") from None
        source = workspace / name
        if source.is_symlink() or (source.exists() and not source.is_file()):
            raise ValueError(f"the patch changes {name!r}, which is not a regular file")
        if source.exists():
            if not source.resolve().is_relative_to(root):
                raise ValueError(f"the patch names {name!r}, which is outside the repository")
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    applied = _git(folder, *APPLY[1:], "-", data=data)
    if applied.returncode:
        raise ValueError(applied.stderr.decode(errors="replace").strip()[-300:] or "git apply failed")
    overlay = {}
    for name in names:
        target = folder / name
        if not target.is_file():
            raise ValueError(f"the patch deletes {name!r}; a wrong implementation may change files, not delete them")
        overlay[name] = str(target)
    return overlay
