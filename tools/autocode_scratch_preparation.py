"""Prepare a disposable source copy with optional patch and retained overlays.

Tree operations are supplied by the verification runner; this module never
imports it. Review input admission happens before applying the explicit patch.
"""

import os
import subprocess
from pathlib import Path

try:
    from . import autocode_dependency_dirs as dependency_dirs
    from . import autocode_joint_inputs as joint_inputs
    from . import autocode_review_proof as review_proof
    from . import autocode_scratch_overlay as scratch_overlay
    from . import autocode_source_snapshot as source_snapshot
    from . import autocode_test_environment as test_environment
    from . import autocode_util as util
except ImportError:
    import autocode_dependency_dirs as dependency_dirs
    import autocode_joint_inputs as joint_inputs
    import autocode_review_proof as review_proof
    import autocode_scratch_overlay as scratch_overlay
    import autocode_source_snapshot as source_snapshot
    import autocode_test_environment as test_environment
    import autocode_util as util


class ScratchPatchError(ValueError):
    """An unapplied patch has no execution receipt."""


def _source_access(reader, *args, **kwargs):
    """Transport a specifically unsafe source path through the owned worker."""
    try:
        return reader(*args, **kwargs)
    except source_snapshot.UnsafeSourcePath as error:
        raise util.Paused("PAUSED_SOURCE_UNAVAILABLE", str(error)) from error


def _installed_link(tree, name, source, expected_files):
    """An extra visible link created by the existing installed-dependency copier."""
    path = Path(name)
    target = Path(tree) / path
    if not target.is_symlink():
        return False
    try:
        link = target.readlink()
    except OSError:
        return False
    roots = test_environment.dependency_roots(source)
    if path.parent == Path(".") and path.name in (".venv", "venv"):
        original = next((root / path.name for root in roots if (root / path.name).is_dir()), None)
        try:
            return original is not None and link == original.resolve()
        except (OSError, RuntimeError):
            return False
    if path.name == "node_modules":
        package = path.parent
        if package != Path(".") and (package / "package.json").as_posix() not in expected_files:
            return False
        sources = roots[:1] if package == Path(".") else roots
        if not any((root / package / "node_modules").is_dir() for root in sources):
            return False
        view = Path(tree) / dependency_dirs.VIEWS / package / "node_modules"
        return link == view and view.is_dir()
    return False


def _dependency_links(tree, source, expected_files):
    """Read each generated installed-dependency link, including Git-ignored ones."""
    root, result = Path(tree), {}

    def unreadable(_error):
        raise util.Paused("PAUSED_SOURCE_UNAVAILABLE", "Verification dependency links could not be inspected")

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=unreadable):
        for name in (*dirs, *files):
            if name not in dependency_dirs.NAMES:
                continue
            target = Path(directory) / name
            relative = target.relative_to(root).as_posix()
            if relative in expected_files:
                continue
            if not target.is_symlink():
                result[relative] = "ordinary-directory" if target.is_dir() else "ordinary-file"
                continue
            if not _installed_link(root, relative, source, expected_files):
                raise util.Paused(
                    "PAUSED_SOURCE_UNAVAILABLE", "Verification dependency link is not bound to its source"
                )
            try:
                result[relative] = str(target.readlink())
            except OSError:
                unreadable(None)
        dirs[:] = [name for name in dirs if name not in (*dependency_dirs.NAMES, ".git", ".autocode")]
    return result


class DependencyGuard:
    """Keep a pinned scratch copy's generated dependency links stable during its command."""

    def __init__(self):
        self.binding = None

    def capture(self, tree, source, expected_files):
        self.binding = (Path(source), expected_files, _dependency_links(tree, source, expected_files))

    def check(self, tree):
        if self.binding is not None:
            source, files, before = self.binding
            if _dependency_links(tree, source, files) != before:
                raise util.Paused("PAUSED_SOURCE_UNAVAILABLE", "Verification dependency links changed during the check")


def finished(tree, review_snapshot, source_paths, dependency_guard):
    changed = (
        review_snapshot is not None
        and source_snapshot.snapshot(tree, paths=source_paths)["files"] != review_snapshot["files"]
    )
    dependency_guard.check(tree)
    return changed


def invalid_review_inputs(review_mutation, tests, command, files, links, expected_source_revision):
    return (
        review_mutation is not True
        or bool(tests or command is not None or files or links)
        or expected_source_revision is not None
    )


def source_parameters(parameters, revision, review_source):
    """Transport the source pin and optional review scope to the owned worker."""
    if revision is not None:
        parameters["expected_source_revision"] = revision
    if review_source is not None:
        parameters["review_source"] = review_source
    return parameters


def _same_source_after_copy(workspace, run_dir, tree, before, after, source_paths):
    """Ignore only this call's newly created scratch worktree in the source inventory."""
    if before["head"] != after["head"]:
        return False
    files = dict(after["files"])
    try:
        root = Path(workspace).resolve()
        actual = Path(tree).resolve()
        expected = (Path(run_dir) / "scratch" / "tree").resolve()
        relative = actual.relative_to(root).as_posix()
        scoped = any(
            relative == name or relative.startswith(name + "/") or name.startswith(relative + "/")
            for name in source_snapshot.literal_paths(source_paths)
        )
        key = relative + "/"
        if (
            actual == expected
            and not scoped
            and key not in before["files"]
            and files.get(key, "").startswith("submodule:")
        ):
            files.pop(key)
    except (OSError, ValueError, RuntimeError):
        pass
    return files == before["files"]


def prepare(
    workspace,
    run_dir,
    *,
    patch=None,
    files=None,
    links=None,
    source_paths=(),
    dependencies_from=None,
    generated_record=None,
    generated_unrecorded=False,
    ignored_inputs=None,
    make_tree,
    changed_files,
    remove_tree,
    git,
    _before_patch=None,
    review_source=None,
    expected_source_revision=None,
    _on_prepared=None,
):
    """Existing copy operations, supplied to the owned preparation worker."""
    workspace, run_dir = Path(workspace), Path(run_dir)
    before = _source_access(source_snapshot.snapshot, workspace, paths=source_paths)
    if expected_source_revision is not None and before["revision"] != expected_source_revision:
        raise util.Paused("PAUSED_SOURCE_UNAVAILABLE", "Verification source no longer matches its pinned revision")
    expected_files = _source_access(source_snapshot.bound_inventory, workspace, before, paths=source_paths)
    if unsafe := source_snapshot.first_symlinked_parent(workspace, expected_files):
        raise util.Paused("PAUSED_SOURCE_UNAVAILABLE", "Source path has a symlinked parent: " + unsafe)
    if expected_source_revision is not None and source_snapshot.unbound_symlinks(
        workspace, expected_files, lambda: git(workspace, "ls-files", "-z").split("\0")
    ):
        raise util.Paused("PAUSED_SOURCE_UNAVAILABLE", "Pinned verification source has an unbound symlink target")
    head = git(workspace, "rev-parse", "HEAD").strip()
    changes = changed_files(workspace, head, source_paths=source_paths)
    if any(
        status != "deleted"
        and path not in expected_files
        and not any(name.startswith(path.rstrip("/") + "/") for name in expected_files)
        for path, status in changes.items()
    ):
        raise util.Paused("PAUSED_SOURCE_UNAVAILABLE", "Verification copy contains an unpinned source path")
    tree = make_tree(
        workspace,
        head,
        run_dir / "scratch" / "tree",
        workspace,
        changes,
        dependencies_from=dependencies_from or workspace,
        generated_record=generated_record,
        generated_unrecorded=generated_unrecorded,
        ignored_inputs=ignored_inputs,
    )
    try:
        if review_source is not None:
            _source_access(review_proof.verify_source, tree, review_source)
        copied = _source_access(source_snapshot.copied_files, tree, expected_files)
        visible = _source_access(source_snapshot.inventory, tree, paths=source_paths)
        after = _source_access(source_snapshot.snapshot, workspace, paths=source_paths)
        if (
            copied != {name: value for name, value in expected_files.items() if value != "deleted"}
            or any(
                expected_files[name] != value
                if name in expected_files
                else not _installed_link(tree, name, dependencies_from or workspace, expected_files)
                for name, value in visible.items()
            )
            or git(tree, "rev-parse", "HEAD").strip() != before["head"]
            or not _same_source_after_copy(workspace, run_dir, tree, before, after, source_paths)
        ):
            raise util.Paused("PAUSED_SOURCE_UNAVAILABLE", "Verification source copy differs from its pinned source")
        if expected_source_revision is not None and _on_prepared is not None:
            _on_prepared(tree, dependencies_from or workspace, expected_files)
        if _before_patch is not None:
            _before_patch(tree)
        if patch:
            applied = subprocess.run(["git", "-C", str(tree), "apply", str(patch)], capture_output=True, text=True)
            if applied.returncode:
                raise ScratchPatchError(
                    f"git apply {patch} failed: {(applied.stderr or applied.stdout).strip()[-300:]}"
                )
        scratch_overlay.apply(tree, files, links)
        joint_inputs.copy(workspace, tree)
    except BaseException:
        remove_tree(workspace, tree)
        raise
    return tree
