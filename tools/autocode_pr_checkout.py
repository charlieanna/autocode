"""Own a fetched pull-request checkout; no task state or controller dependencies."""

from __future__ import annotations

import base64
import os
import re
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
        raise CheckoutError("Review output has an inaccessible or redirected parent") from error
    return root


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

    def unchanged(self):
        if git(self.workspace, "rev-parse", "HEAD") != self.head:
            raise CheckoutError("Reviewed checkout HEAD changed")
        if git(self.workspace, "-c", "core.fileMode=true", "diff", "HEAD", "--name-only"):
            raise CheckoutError("A review changed tracked source")
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
    names = git(repo, "ls-tree", "-r", "--name-only", head, "-z").split("\0")
    if any(p == ".autocode" or p.startswith((".autocode/", "review/")) or p == "review" for p in names):
        raise CheckoutError(
            "Pull request contains reserved .autocode/ or review/ artifacts; review refused before writing"
        )
    git(repo, "worktree", "add", "--detach", "-q", str(workspace), head)
    merged = git(workspace, "merge-base", base, head)
    result = Checkout(root, workspace, head, base, merged)
    result.unchanged()
    return result
