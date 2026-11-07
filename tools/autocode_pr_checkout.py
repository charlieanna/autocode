"""Own a fetched pull-request checkout; no task state or controller dependencies."""

from __future__ import annotations

import base64
import json
import os
import re
import stat
import subprocess
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    from . import autocode_github as github
except ImportError:
    import autocode_github as github


class CheckoutError(ValueError):
    """The pull request cannot be fetched and bound to an owned worktree."""


def child_environment(env=None):
    """Keep provider configuration, excluding GitHub posting/auth helper credentials."""
    result = dict(os.environ if env is None else env)
    for key in list(result):
        if (
            key in {"GH_TOKEN", "GH_ENTERPRISE_TOKEN", "GH_CONFIG_DIR", "GIT_ASKPASS", "SSH_ASKPASS", "GIT_SSH_COMMAND"}
            or (key.startswith("GITHUB_") and ("TOKEN" in key or "SECRET" in key))
            or key.startswith("GIT_CONFIG_")
        ):
            result.pop(key)
    result.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    return result


def task_environment(env=None):
    """Override stripped inherited values because public TaskRun merges its parent env."""
    effective = {**os.environ, **(env or {})}
    cleaned = child_environment(effective)
    return {**{key: "" for key in effective if key not in cleaned}, **cleaned, "GIT_CONFIG_COUNT": "0"}


def follow_system_links(value, *, trusted_uid=0):
    """``value`` with each existing parent that is a symbolic link owned by ``trusted_uid`` (root), in a
    directory that user owns, replaced by its target: macOS's /tmp -> private/tmp. Any other link stays
    literal, so create_root still refuses it. Paths with ``..`` are returned unchanged for it to refuse."""
    path = Path(value).absolute()
    if ".." in path.parts or path == Path(path.anchor):
        return path
    current = Path(path.anchor)
    for index, name in enumerate(path.parts[1:-1], start=1):
        candidate = current / name
        try:
            link, parent = os.lstat(candidate), os.lstat(current)
        except OSError:
            return current.joinpath(*path.parts[index:])
        system = stat.S_ISLNK(link.st_mode) and link.st_uid == trusted_uid and parent.st_uid == trusted_uid
        current = Path(os.path.realpath(candidate)) if system else candidate
    return current / path.name


def _redirected_parent(root):
    """The first existing parent of ``root`` that is a symbolic link, or None."""
    for parent in reversed(root.parents):
        if parent.is_symlink():
            return parent
    return None


def create_root(value):
    """Create a new literal directory through a pinned, non-symlink parent chain."""
    root = Path(value).absolute()
    if ".." in root.parts or root == Path(root.anchor):
        raise CheckoutError("Review output must be a new directory without parent traversal")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        with ExitStack() as opened:
            parent = os.open(root.anchor, flags)
            opened.callback(os.close, parent)
            for name in root.parts[1:-1]:
                with suppress(FileExistsError):
                    os.mkdir(name, dir_fd=parent)
                parent = os.open(name, flags, dir_fd=parent)
                opened.callback(os.close, parent)
            os.mkdir(root.name, dir_fd=parent)
            leaf = os.open(root.name, flags, dir_fd=parent)
            opened.callback(os.close, leaf)
    except FileExistsError:
        raise CheckoutError(f"Review output already exists: {root}; use a new --out directory") from None
    except OSError as error:
        link = _redirected_parent(root)
        if link is not None:
            raise CheckoutError(
                f"Review output has a redirected parent: {link} is a symbolic link; "
                f"use {Path(os.path.realpath(link)) / root.relative_to(link)} instead"
            ) from error
        raise CheckoutError("Review output has an inaccessible or redirected parent") from error
    return root


def _cache_directory(workspace, relative, *, create=False):
    """Bind one setup-created empty cache directory through literal open parents."""
    target = Path(workspace).absolute() / relative
    if Path(relative).is_absolute() or ".." in target.parts or target.name != "__pycache__":
        raise CheckoutError("Python cache protection path is not a derived workspace directory")
    directories = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        with ExitStack() as opened:
            parent = os.open(target.anchor, flags)
            opened.callback(os.close, parent)
            for name in target.parts[1:-1]:
                child = os.open(name, flags, dir_fd=parent)
                opened.callback(os.close, child)
                directories.append((parent, name, child, os.fstat(child)))
                parent = child
            if create:
                # Never adopt or chmod a PR-controlled existing cache path.
                os.mkdir(target.name, mode=0o555, dir_fd=parent)
                created = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
                if not stat.S_ISDIR(created.st_mode) or created.st_uid != os.geteuid():
                    raise CheckoutError("Python cache setup directory was replaced")
            leaf = os.open(target.name, flags, dir_fd=parent)
            opened.callback(os.close, leaf)
            if create:
                observed = os.fstat(leaf)
                bound = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
                captured = (created.st_dev, created.st_ino, created.st_mode, created.st_uid)
                if any((row.st_dev, row.st_ino, row.st_mode, row.st_uid) != captured for row in (observed, bound)):
                    raise CheckoutError("Python cache setup directory changed before protection")
                os.fchmod(leaf, 0o555)
            before = os.fstat(leaf)
            if before.st_mode & 0o777 != 0o555 or os.listdir(leaf):
                raise CheckoutError("Python cache protection is no longer empty and read-only")
            after = os.fstat(leaf)
            bound = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
            for anchor, name, child, info in directories:
                observed = os.stat(name, dir_fd=anchor, follow_symlinks=False)
                current = os.fstat(child)
                if any(
                    (row.st_dev, row.st_ino, row.st_mode) != (info.st_dev, info.st_ino, info.st_mode)
                    for row in (observed, current)
                ):
                    raise CheckoutError("Python cache protection parent changed")
            identity = lambda info: (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_uid,
                info.st_gid,
                info.st_nlink,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )
            if identity(before) != identity(after) or identity(before) != identity(bound):
                raise CheckoutError("Python cache protection changed during admission")
            return identity(before)
    except OSError as error:
        raise CheckoutError("Python cache protection path is existing, inaccessible or redirected") from error


def _protect_caches(workspace, python_sources):
    """Prevent incidental import caches without making source parents read-only."""
    paths = sorted({(Path(name).parent / "__pycache__").as_posix() for name in python_sources})
    return tuple((name, _cache_directory(workspace, name, create=True)) for name in paths)


def protect_python_caches(workspace, python_sources):
    """Exclusively protect derived import-cache directories in an owned checkout.

    Keep the returned identities in memory and recheck them with
    require_python_caches before accepting a review result.
    """
    return _protect_caches(workspace, python_sources)


def require_python_caches(workspace, protected):
    """Reject replacement, permission changes or cache payloads after setup."""
    for name, expected in protected:
        if _cache_directory(workspace, name) != expected:
            raise CheckoutError("Python cache protection identity or permissions changed")


def git(root: Path, *args, env=None):
    proc = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "credential.helper=",
            "-c",
            "maintenance.auto=false",
            "-c",
            "gc.auto=0",
            "-c",
            "protocol.ext.allow=never",
            "-C",
            str(root),
            *args,
        ],
        capture_output=True,
        text=True,
        env=child_environment() if env is None else env,
        timeout=120,
    )
    if proc.returncode:
        # Git errors can include remote credentials; allowed clone URLs never carry them.
        raise CheckoutError("Git checkout failed: " + proc.stderr.strip()[-1000:])
    return proc.stdout.rstrip("\n")


def commit(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", value):
        raise CheckoutError(f"Pull request has no valid {label} commit")
    return value


def clone_url(value, *, api, owner, repo):
    """Trust the configured API's base repository, never a PR-controlled fork URL."""
    if not isinstance(value, str):
        raise CheckoutError("Pull request has no base repository clone URL")
    parsed, server = urlsplit(value), urlsplit(api)
    host, port = github.web_authority(api)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CheckoutError("Base repository clone URL must not contain credentials or extra parameters")
    if (
        parsed.scheme == "https"
        and parsed.hostname == host
        and (443 if parsed.port is None else parsed.port) == port
        and parsed.path == f"/{owner}/{repo}.git"
    ):
        return value
    # Explicit loopback API fixtures may point to a local bare remote. Production
    # API responses cannot turn a GitHub review into a fetch from an arbitrary path.
    if (
        server.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed.scheme == "file"
        and not parsed.netloc
        and Path(unquote(parsed.path)).is_absolute()
    ):
        return value
    raise CheckoutError("Base clone URL does not belong to the configured GitHub repository")


@dataclass(frozen=True)
class Checkout:
    root: Path
    workspace: Path
    head: str
    base: str
    merge_base: str
    cache_directories: tuple[tuple[str, tuple[int, ...]], ...] = ()

    def unchanged(self):
        if git(self.workspace, "rev-parse", "HEAD") != self.head:
            raise CheckoutError("Reviewed checkout HEAD changed")
        if git(self.workspace, "-c", "core.fileMode=true", "diff", "HEAD", "--name-only"):
            raise CheckoutError("A review changed tracked source")
        require_python_caches(self.workspace, self.cache_directories)
        # Include ignored new files too: a PR-controlled .gitignore cannot hide edits.
        others = []
        for flags in [("--others", "--exclude-standard"), ("--others", "--ignored", "--exclude-standard")]:
            others.extend(git(self.workspace, "ls-files", *flags, "-z").split("\0"))
        stray = [p for p in others if p and not p.startswith((".autocode/", "review/"))]
        if stray:
            raise CheckoutError("A review left unexpected source files: " + ", ".join(sorted(set(stray))))


def fetch(root, ref, pull, client) -> Checkout:
    """Create a new owned bare repository and detached head worktree; retain all evidence."""
    head = commit((pull.get("head") or {}).get("sha"), "head")
    base = commit((pull.get("base") or {}).get("sha"), "base")
    repository = (pull.get("base") or {}).get("repo") or {}
    if repository.get("full_name") != f"{ref.owner}/{ref.repo}":
        raise CheckoutError("Pull request base repository differs from its URL")
    remote = clone_url(repository.get("clone_url"), api=client.api, owner=ref.owner, repo=ref.repo)
    root = create_root(root)
    repo, workspace = root / "repository.git", root / "workspace"
    repo.mkdir()
    git(repo, "init", "--bare", "-q")
    git(repo, "config", "maintenance.auto", "false")
    git(repo, "config", "gc.auto", "0")
    git(repo, "remote", "add", "origin", remote)
    env = child_environment()
    if client.token and remote.startswith("https://"):
        # Read authentication exists only in this fetch child's environment; it is
        # never saved in .git/config, argv, the brief or the model's environment.
        auth = base64.b64encode(("x-access-token:" + client.token).encode()).decode()
        env.update(
            GIT_CONFIG_COUNT="1",
            GIT_CONFIG_KEY_0=f"http.{remote}.extraheader",
            GIT_CONFIG_VALUE_0="AUTHORIZATION: basic " + auth,
        )
    git(
        repo,
        "fetch",
        "--no-tags",
        "origin",
        f"+refs/pull/{ref.number}/head:refs/autocode/head",
        f"+{base}:refs/autocode/base",
        env=env,
    )
    if git(repo, "rev-parse", "refs/autocode/head") != head or git(repo, "rev-parse", "refs/autocode/base") != base:
        raise CheckoutError("Fetched pull request commits differ from GitHub's captured head/base; try again")
    entries = [row.split("\t", 1) for row in git(repo, "ls-tree", "-r", head, "-z").split("\0") if row]
    names = [name for _, name in entries]
    python_sources = [
        name for metadata, name in entries if metadata.split()[0] in {"100644", "100755"} and name.endswith(".py")
    ]
    if any(p == ".autocode" or p.startswith((".autocode/", "review/")) or p == "review" for p in names):
        raise CheckoutError(
            "Pull request contains reserved .autocode/ or review/ artifacts; review refused before writing"
        )
    git(repo, "worktree", "add", "--detach", "-q", str(workspace), head)
    merged = git(workspace, "merge-base", base, head)
    protected = _protect_caches(workspace, python_sources)
    # Setup-owned metadata is outside source and exists before any task child.
    with (root / "python-cache-protection.json").open("x") as stream:
        json.dump(
            {
                "version": 1,
                "head_sha": head,
                "directories": [{"path": name, "identity": list(identity)} for name, identity in protected],
            },
            stream,
        )
        stream.write("\n")
    result = Checkout(root, workspace, head, base, merged, protected)
    result.unchanged()
    return result
