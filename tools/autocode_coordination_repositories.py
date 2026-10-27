"""Resolve authorized local repositories and prepare pinned task worktrees."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

try:
    from . import autocode_github as github
    from . import autocode_registry as registry
    from . import autocode_util as util
    from .autocode_coordination_store import CoordinationError
except ImportError:
    import autocode_github as github
    import autocode_registry as registry
    import autocode_util as util
    from autocode_coordination_store import CoordinationError


def git(workspace, *args, binary=False, accepted=(0,), input=None):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(
        ["git", "-C", str(workspace), *map(str, args)],
        env=environment,
        capture_output=True,
        text=not binary,
        input=input,
        timeout=120,
    )
    if result.returncode not in accepted:
        detail = result.stderr.decode(errors="replace") if binary else result.stderr
        raise CoordinationError("Repository operation refused: " + util.redact(detail.strip()[-1200:]))
    return result.stdout if binary else result.stdout.strip()


def identity(workspace):
    root = Path(workspace).resolve(strict=True)
    if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise CoordinationError("Repository catalog entry is not a Git root")
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    base = git(root, "rev-parse", "--verify", "HEAD^{commit}")
    remote = git(root, "remote", "get-url", "origin", accepted=(0, 2, 128))
    push_remote = git(root, "remote", "get-url", "--push", "origin", accepted=(0, 2, 128))
    return {"origin": str(root), "git_dir": str(common), "base": base, "remote": remote, "push_remote": push_remote}


def require_clean_product(entry):
    changed = git(entry["origin"], "diff", "HEAD", "--name-only", "-z", binary=True)
    changed += git(entry["origin"], "ls-files", "--others", "--exclude-standard", "-z", binary=True)
    product = [
        os.fsdecode(name)
        for name in changed.split(b"\0")
        if name and not name.startswith((b"docs/bugs/", b".autocode/", b".autocode-ui/"))
    ]
    if product:
        raise CoordinationError(
            "Commit or stash local product edits before coordinating a pinned baseline: " + ", ".join(product[:5])
        )


def require_identity(member):
    require_worktree(member, member["workspace"])
    actual = identity(member["workspace"])
    if any(actual[key] != member[key] for key in ("git_dir", "remote", "push_remote")):
        raise CoordinationError("Repository or publication remote changed after discovery")


def aliases(entry):
    names = {Path(entry["origin"]).name.casefold()}
    repository = github.repo_from_remote(entry["remote"]) if entry["remote"] else None
    if repository:
        names.update((repository[1].casefold(), "/".join(repository).casefold()))
    # Package names are corroborating catalog metadata, never paths to act on.
    package = Path(entry["origin"]) / "package.json"
    try:
        name = json.loads(package.read_text()).get("name")
        if isinstance(name, str) and len(name) < 200:
            names.add(name.casefold())
    except (OSError, ValueError):
        pass
    return names


def publication_repository(remote):
    expected, port = github.web_authority(os.environ.get("AUTOCODE_GITHUB_API") or github.API)
    if remote.startswith("git@") and ":" in remote:
        host = remote.split("@", 1)[1].split(":", 1)[0]
    else:
        parsed = urlsplit(remote)
        if (
            parsed.scheme not in ("https", "ssh")
            or parsed.password
            or (parsed.scheme == "https" and (parsed.username or (parsed.port or 443) != port))
        ):
            raise CoordinationError("Use an authenticated GitHub origin with credentials in a credential helper")
        host = parsed.hostname
    repository = github.repo_from_remote(remote)
    if host != expected or not repository:
        raise CoordinationError("Origin does not identify a repository on the configured GitHub host")
    return repository


def catalog():
    entries = {}
    for row in registry.listing()["workspaces"]:
        if row.get("availability") != "available":
            continue
        try:
            entry = identity(row["workspace"])
        except (OSError, ValueError):
            continue
        # A run-created worktree is not a second repository candidate.
        key = entry["git_dir"]
        old = entries.get(key)
        if old is None or Path(entry["origin"]) / ".git" == Path(key):
            entries[key] = entry
    return list(entries.values())


def candidates(target, entries):
    name = str(target).strip().casefold().removesuffix(".git")
    if not name or name.startswith(("/", "~", ".")) or "://" in name or ".." in name.split("/"):
        return []
    exact = [entry for entry in entries if name in aliases(entry)]
    if exact:
        return exact
    # A model may append a job label to an otherwise exact repository name.
    # This still resolves only against the authorized catalog, never a path.
    described = re.fullmatch(r"(.+?)\s+\([^()]{1,80}\)", name)
    return [entry for entry in entries if described and described[1] in aliases(entry)]


def member_aliases(members, choices):
    """Aliases whose selected catalog identity is already an owned member."""
    origins = {row["origin"]: row["target"] for row in members if row.get("origin")}
    return {
        alias: origins[origin] for alias, origin in choices.items() if origin in origins and alias != origins[origin]
    }


def slug(target):
    name = re.sub(r"[^a-zA-Z0-9._-]+", "-", target).strip(".-")[:50] or "repository"
    return name + "-" + hashlib.sha256(target.encode()).hexdigest()[:8]


def literal_destination(destination):
    destination = Path(destination).absolute()
    if ".." in destination.parts or any(path.is_symlink() for path in (destination, *destination.parents)):
        raise CoordinationError("Reserved worktree path is redirected")
    return destination


def require_worktree(entry, destination):
    """An owned member must be a literal linked checkout, never its original."""
    destination = literal_destination(destination)
    marker = destination / ".git"
    if destination == Path(entry["origin"]).resolve() or marker.is_symlink() or not marker.is_file():
        raise CoordinationError("Reserved path is not an isolated linked worktree")
    directory = Path(git(destination, "rev-parse", "--absolute-git-dir"))
    common = Path(entry["git_dir"])
    backlink = directory / "gitdir"
    if directory.parent != common / "worktrees" or backlink.is_symlink():
        raise CoordinationError("Reserved path does not match its isolated worktree registration")
    literal_destination(directory)
    try:
        with backlink.open("rb") as handle:
            registered = Path(os.fsdecode(handle.read(16_385).removesuffix(b"\n")))
    except OSError as error:
        raise CoordinationError("Reserved path has no isolated worktree registration") from error
    if registered != marker:
        raise CoordinationError("Reserved path does not match its isolated worktree registration")


def prepare(entry, destination):
    destination = literal_destination(destination)
    if destination.exists():
        require_worktree(entry, destination)
        actual = identity(destination)
        if actual["git_dir"] != entry["git_dir"] or actual["base"] != entry["base"]:
            raise CoordinationError("Reserved worktree exists with a different repository or base")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    git(entry["origin"], "worktree", "add", "--detach", "--", destination, entry["base"])
    require_worktree(entry, destination)


def source_files(workspace):
    names = git(workspace, "ls-files", "--cached", "--others", "--exclude-standard", "-z", binary=True)
    result = {}
    for raw in sorted(set(names.split(b"\0"))):
        name = os.fsdecode(raw)
        if (
            not name
            or name.startswith((".autocode/", ".autocode-ui/"))
            or "/__pycache__/" in "/" + name
            or name.endswith(".pyc")
        ):
            continue
        path = Path(workspace) / name
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink() or not path.is_file():
            raise CoordinationError("Joint snapshots currently require ordinary files: " + name)
        result[name] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "executable": bool(path.stat().st_mode & 0o111),
        }
    return result


def fingerprint(workspace):
    return hashlib.sha256(json.dumps(source_files(workspace), sort_keys=True).encode()).hexdigest()
