"""Safe, explicit synchronization and commit-pinned worktree creation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import Callable

from .compatibility import CompatibilityManifest


class SyncError(RuntimeError):
    """A requested upstream checkout cannot be safely prepared."""


CommandObserver = Callable[[tuple[str, ...]], None]


@dataclass(frozen=True)
class PreparedUpstream:
    checkout: Path
    commit: str
    manifest_identity: str


class UpstreamSynchronizer:
    """Fetch a single branch into its tracking ref and use a detached worktree."""

    def __init__(
        self,
        manifest: CompatibilityManifest,
        observer: CommandObserver | None = None,
        *,
        remote: str = "origin",
        branch: str = "master",
    ) -> None:
        self.manifest = manifest
        self.observer = observer
        self.remote = remote
        self.branch = branch

    def _git(self, repository: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        command = ("git", "-C", str(repository), *arguments)
        if self.observer:
            self.observer(command)
        completed = subprocess.run(command, text=True, capture_output=True)
        if check and completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip() or "unknown Git error"
            raise SyncError(f"Git command failed safely: {' '.join(arguments)}: {detail}")
        return completed

    def _require_clean(self, repository: Path, label: str) -> None:
        status = self._git(repository, "status", "--porcelain").stdout
        if status.strip():
            raise SyncError(f"refusing to use dirty {label}: {repository}")

    def _resolve_commit(self, repository: Path) -> str:
        remote_ref = f"refs/heads/{self.branch}"
        found = self._git(repository, "ls-remote", "--exit-code", self.remote, remote_ref, check=False)
        if found.returncode:
            raise SyncError(f"upstream ref {self.remote}/{self.branch} does not resolve; verify the remote and branch")
        tracking_ref = f"refs/remotes/{self.remote}/{self.branch}"
        self._git(repository, "fetch", "--no-tags", self.remote, f"+{remote_ref}:{tracking_ref}")
        return self._git(repository, "rev-parse", "--verify", f"{tracking_ref}^{{commit}}").stdout.strip()

    def _reuse_or_create(self, source: Path, destination: Path, commit: str) -> Path:
        if destination.exists():
            if not (destination / ".git").exists():
                raise SyncError(f"refusing to overwrite existing checkout destination: {destination}")
            self._require_clean(destination, "pinned checkout")
            detached = self._git(destination, "symbolic-ref", "-q", "HEAD", check=False)
            if detached.returncode != 1:
                raise SyncError(f"existing checkout is not detached: {destination}")
            current = self._git(destination, "rev-parse", "HEAD").stdout.strip()
            if current != commit:
                raise SyncError(f"existing checkout is pinned to {current}, not requested commit {commit}")
            return destination
        self._git(source, "worktree", "add", "--detach", str(destination), commit)
        return destination

    @staticmethod
    def _is_within(path: Path, directory: Path) -> bool:
        try:
            path.relative_to(directory)
        except ValueError:
            return False
        return True

    def _validate_record_path(self, record_path: Path, source: Path, checkout: Path) -> Path:
        """Return an absolute, non-resolved path after checking its resolved target.

        The resolved check catches paths which enter a protected checkout through an
        already-existing symlink.  The non-resolved path is retained for fd-relative
        creation below so a later symlink swap cannot redirect a write.
        """
        try:
            resolved = record_path.expanduser().resolve(strict=False)
        except OSError as error:
            raise SyncError(f"cannot safely resolve pin record path {record_path}: {error}") from error
        for protected in (source, checkout):
            if self._is_within(resolved, protected):
                raise SyncError(
                    "pin record must be outside both the upstream source and pinned checkout: "
                    f"{record_path}"
                )
        return Path(os.path.abspath(os.fspath(record_path.expanduser())))

    @staticmethod
    def _open_record_parent(record_path: Path) -> int:
        """Create and open record_path.parent without following directory symlinks."""
        if not hasattr(os, "O_NOFOLLOW"):
            raise SyncError("pin record creation is unavailable because this platform cannot reject symlinks")
        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        directory_flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(os.sep, directory_flags)
        except OSError as error:
            raise SyncError(f"cannot safely open filesystem root for pin record: {error}") from error
        try:
            for component in record_path.parent.parts[1:]:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                except OSError as error:
                    raise SyncError(
                        f"cannot safely create pin record parent component {component!r}: {error}"
                    ) from error
                try:
                    next_descriptor = os.open(component, directory_flags, dir_fd=descriptor)
                except OSError as error:
                    raise SyncError(
                        f"pin record parent contains an unsafe or inaccessible path component {component!r}: {error}"
                    ) from error
                os.close(descriptor)
                descriptor = next_descriptor
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    @staticmethod
    def _reuse_exact_record(parent_descriptor: int, name: str, expected: bytes) -> None:
        """Accept only a stable, regular record whose bytes are exactly expected."""
        read_flags = os.O_RDONLY | os.O_NOFOLLOW
        for _ in range(2):
            try:
                before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(before.st_mode):
                raise SyncError(f"refusing non-adapter-owned pin record: {name}")
            try:
                descriptor = os.open(name, read_flags, dir_fd=parent_descriptor)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise SyncError(f"refusing unsafe existing pin record {name}: {error}") from error
            try:
                opened = os.fstat(descriptor)
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    continue
                contents = bytearray()
                while chunk := os.read(descriptor, 65536):
                    contents.extend(chunk)
            finally:
                os.close(descriptor)
            if bytes(contents) == expected:
                return
            raise SyncError(f"refusing non-adapter-owned pin record: {name}")
        raise SyncError(f"pin record changed while being validated: {name}")

    def _write_record(self, record_path: Path, source: Path, checkout: Path, result: PreparedUpstream) -> None:
        safe_path = self._validate_record_path(record_path, source, checkout)
        contents = (
            json.dumps(
                {"compatibility_manifest": result.manifest_identity, "upstream_commit": result.commit},
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        parent_descriptor = self._open_record_parent(safe_path)
        create_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        try:
            try:
                descriptor = os.open(safe_path.name, create_flags, 0o600, dir_fd=parent_descriptor)
            except FileExistsError:
                self._reuse_exact_record(parent_descriptor, safe_path.name, contents)
                return
            try:
                view = memoryview(contents)
                while view:
                    written = os.write(descriptor, view)
                    if written == 0:
                        raise SyncError(f"unable to write pin record: {safe_path}")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            os.close(parent_descriptor)

    def prepare(self, source: Path, destination: Path, *, record_path: Path | None = None) -> PreparedUpstream:
        source = source.resolve()
        destination = destination.resolve()
        if not (source / ".git").exists():
            raise SyncError(f"upstream source is not a Git checkout: {source}")
        self._require_clean(source, "upstream checkout")
        commit = self._resolve_commit(source)
        checkout = self._reuse_or_create(source, destination, commit)
        self.manifest.verify(checkout)
        result = PreparedUpstream(checkout=checkout, commit=commit, manifest_identity=self.manifest.identity)
        if record_path:
            self._write_record(record_path, source, checkout, result)
        return result
