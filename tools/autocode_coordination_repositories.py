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
    from . import autocode_repo_catalog as source_catalog
    from . import autocode_util as util
    from .autocode_coordination_store import CoordinationError
except ImportError:
    import autocode_github as github
    import autocode_registry as registry
    import autocode_repo_catalog as source_catalog
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
    """Inventory configured source roots plus legacy explicitly registered workspaces."""
    discovery = source_catalog.discover()
    if not discovery["complete"]:
        codes = ", ".join(sorted({row["code"] for row in discovery["diagnostics"]}))
        raise CoordinationError("Configured source discovery is incomplete" + (f" ({codes})" if codes else ""))
    entries = {}
    for group in discovery["repositories"]:
        for candidate in group["candidates"]:
            entry = identity(candidate["path"])
            tree = git(entry["origin"], "rev-parse", "HEAD^{tree}")
            if entry["base"] != candidate["head"] or tree != candidate["tree"]:
                raise CoordinationError("Repository changed during authorized source discovery; rediscover it")
            if entry["remote"] and candidate.get("remote"):
                # Preserve a usable transport URL for GitHub validation and
                # publication, while the source catalog separately groups URL aliases.
                entry["remote"] = candidate["remote"]
            entries[entry["git_dir"]] = entry
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


def source_snapshot():
    """Return the exact shared catalog and source revision snapshot for coordination."""
    discovery = source_catalog.discover()
    if not discovery["complete"]:
        codes = ", ".join(sorted({row["code"] for row in discovery["diagnostics"]}))
        raise CoordinationError("Configured source discovery is incomplete" + (f" ({codes})" if codes else ""))
    return {
        "catalog_identity": discovery["catalog_identity"],
        "source_snapshot": discovery["source_snapshot"],
        "catalog": discovery["catalog"],
    }


def candidates(target, entries):
    name = str(target).strip().casefold().removesuffix(".git")
    if not name or name.startswith(("/", "~", ".")) or "://" in name or ".." in name.split("/"):
        return []
    exact = [entry for entry in entries if name in aliases(entry)]
    if exact:
        return exact
    # A model may append a job label or the repository noun to an exact name.
    # This still resolves only against the authorized catalog, never a path.
    described = re.fullmatch(r"(.+?)\s+(?:\([^()]{1,80}\)|repo(?:sitory)?)", name)
    found = [entry for entry in entries if described and described[1] in aliases(entry)]
    if found:
        return found
    # Prefer a real repository named "coordinated checkout" above. Only this
    # bounded wrapper is a final fallback; never recursively strip descriptions.
    coordinated = re.fullmatch(r"coordinated\s+(.+?)\s+repo(?:sitory)?", name)
    if not coordinated or coordinated[1].startswith(("/", "~", ".")) or ".." in coordinated[1].split("/"):
        return []
    return [entry for entry in entries if coordinated[1] in aliases(entry)]


JOB_WORD = re.compile(r"[\w-]+")


def _job_words(alias, body):
    """The bounded job words ``body`` appends to ``alias``: none, or one or two
    real words. Dots are not meaningful job words, and a longer tail is not a
    bounded description of this alias."""
    if body == alias:
        return []
    if not body.startswith(alias + " "):
        return None
    words = body[len(alias) :].split()
    if len(words) > 2 or any(not JOB_WORD.fullmatch(word) for word in words):
        return None
    return words


def member_role_identities(members, target, entries):
    """Resolve the core's own member role noun to authorized catalog identities.

    Coordinated member reports may name a linked repository "<name> <job words>
    member" (the noun this core's member context teaches). Reports are now
    corrected before this (#2420); this bounded fallback also resolves labels
    earlier runs already accepted. It runs only when the catalog's own exact,
    noun, parenthetical and coordinated matches found nothing, so their
    ambiguity and choices stay untouched. Every authorized alias-prefix identity
    is enumerated (multi-word aliases included), a job word or job phrase naming
    another repository refuses the whole label, and only an identity currently
    in the authorized catalog can match: model text never names a path or remote.
    """
    name = str(target).strip().casefold()
    if not name.endswith(" member"):
        return {}
    body = name[: -len(" member")].strip()
    if not body:
        return {}
    names = {}
    for entry in entries:
        for alias in aliases(entry):
            names.setdefault(alias, set()).add(entry["git_dir"])
    for member in members:
        for alias in {str(member["target"]).casefold(), *aliases(member)}:
            names.setdefault(alias, set()).add(member["git_dir"])
    matched = set()
    for alias, identities in names.items():
        job = _job_words(alias, body)
        if job is None:
            continue
        phrases = {word for word in job} | ({" ".join(job)} if len(job) > 1 else set())
        if any(phrase in names and not names[phrase] <= identities for phrase in phrases):
            return {}  # a job word or phrase names another repository: refuse the label
        matched |= identities
    # Catalog order, never set-iteration order, so candidate lists are stable
    # across processes and the public choose binding shows a stable offer.
    return {entry["git_dir"]: entry for entry in entries if entry["git_dir"] in matched}


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
