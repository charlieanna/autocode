"""A scenario seed taken from a real repository at a pinned commit (#2944).

The catalog's own ``seed/`` directories are small fixtures. A real-repository case instead declares

    [seed]
    git = "https://github.com/owner/project.git"
    commit = "<the full 40-character commit hash>"

and the harness fetches exactly that commit once, keeps its files in a cache, and uses them as the seed everywhere a ``seed/`` directory is used (the committed starting project, the oracle's original
code, build comparisons). Nothing from the repository's history reaches the scenario project: it starts,
like every scenario, from one "Scenario seed" commit of those files.

The whole repository is the project, as a person would check it out: its own ignore rules keep the
files its suite writes out of the change (a subdirectory would lose a root ``.gitignore``, and AutoCode
would rightly reject a Builder whose test run left untracked output behind).

The commit hash is the pin: the harness fetches that exact object, never a branch or tag that can move.
A source that cannot be fetched makes the scenario unavailable (``SeedUnavailable``), reported as a missing requirement like a missing
toolchain, never a scenario failure.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# Where fetched seeds are kept between runs; ignored by Git with the rest of .scenario-runs/.
CACHE_ENV = "AUTOCODE_SCENARIO_SEED_CACHE"
DEFAULT_CACHE = REPO / ".scenario-runs" / "seed-cache"
KEYS = {"git", "commit"}
COMMIT = re.compile(r"[0-9a-f]{40}")
FETCH_SECONDS = 600


class SeedUnavailable(RuntimeError):
    """The pinned source could not be fetched or did not match its pin."""


@dataclass(frozen=True)
class PinnedSeed:
    git: str
    commit: str

    @property
    def label(self) -> str:
        return f"{self.git}@{self.commit[:12]}"


def parse(scenario_id: str, table: object) -> PinnedSeed:
    """The validated ``[seed]`` table of a scenario.toml."""
    if not isinstance(table, dict) or set(table) != KEYS:
        raise ValueError(f"{scenario_id}: [seed] needs exactly git and commit")
    git, commit = table["git"], table["commit"]
    if not isinstance(git, str) or not git.strip() or git.startswith("-"):
        raise ValueError(f"{scenario_id}: [seed] git must name a repository")
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise ValueError(f"{scenario_id}: [seed] commit must be a full 40-character lowercase commit hash")
    return PinnedSeed(git.strip(), commit)


def cache_root() -> Path:
    configured = os.environ.get(CACHE_ENV, "").strip()
    return Path(configured) if configured else DEFAULT_CACHE


def tree(seed: PinnedSeed, cache: Path | None = None) -> Path:
    """The repository's files at the pinned commit, fetched into the cache the first time.

    The cached directory appears only complete (an atomic rename of a finished extraction), so concurrent
    callers either find it or build their own copy and keep whichever lands first."""
    cache = cache or cache_root()
    key = hashlib.sha256(f"{seed.git}\0{seed.commit}".encode()).hexdigest()[:16]
    target = cache / f"{seed.commit[:12]}-{key}"
    if target.is_dir():
        return target
    cache.mkdir(parents=True, exist_ok=True)
    try:
        return _fetch(seed, cache, target)
    except SeedUnavailable as error:
        raise SeedUnavailable(f"{seed.label}: {error}") from None


def _fetch(seed: PinnedSeed, cache: Path, target: Path) -> Path:
    work = Path(tempfile.mkdtemp(prefix=".fetch-", dir=cache))
    try:
        repo = work / "repo"
        _git(work, "init", "-q", "--bare", str(repo))
        # Fetching by the full hash is the pin: Git refuses objects that do not hash to what was asked for.
        _git(repo, "fetch", "-q", "--depth", "1", "--no-tags", "--", seed.git, seed.commit)
        archive = work / "seed.tar"
        with archive.open("wb") as out:
            _git(repo, "archive", "--format=tar", seed.commit, stdout=out)
        extracted = work / "tree"
        extracted.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(extracted, filter="data")
        try:
            extracted.rename(target)
        except OSError:
            if not target.is_dir():  # another caller's identical tree landed first
                raise
        return target
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _git(cwd: Path, *args: str, stdout=None) -> str:
    # No terminal prompt for credentials, and no user or system configuration that could rewrite the URL.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            stdout=stdout if stdout is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=FETCH_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SeedUnavailable(f"git {args[0]}: {error}") from error
    if proc.returncode:
        message = proc.stderr.decode(errors="replace").strip().splitlines()
        raise SeedUnavailable(f"git {args[0]} failed: {message[-1] if message else f'exit {proc.returncode}'}")
    return proc.stdout.decode(errors="replace") if stdout is None else ""
