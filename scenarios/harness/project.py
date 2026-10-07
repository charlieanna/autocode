"""Materialize a scenario's starting project as a fresh Git repository."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .oracle import IGNORED

GIT_IDENTITY = ["-c", "user.name=Scenario", "-c", "user.email=scenario@example.test"]
# A commit starts `git maintenance run --auto --detach`. On Git 2.55 it can repack while a copy or a
# cleanup walks .git (docs/bugs/git-background-repack-cleanup-race.md).
NO_MAINTENANCE = ["-c", "maintenance.auto=false", "-c", "gc.auto=0"]


def git(project: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(project), *GIT_IDENTITY, *args],
                          check=True, capture_output=True, text=True).stdout


def without_maintenance(project: Path) -> Path:
    """Keep later commits in a test fixture from starting background maintenance, so the fixture can
    be copied or deleted at once. Scenario runs keep Git's default."""
    git(project, "config", "maintenance.auto", "false")
    git(project, "config", "gc.auto", "0")
    return project


def materialize(seed: Path, project: Path, *overlays: Path) -> Path:
    """Copy and commit the seed, then lay overlays on top without committing them. The seed commit
    starts no background maintenance; the project's own Git configuration is left alone."""
    if seed.is_dir():
        shutil.copytree(seed, project, ignore=IGNORED)
    else:
        project.mkdir(parents=True)
    git(project, "init", "-q", "-b", "main")
    git(project, "add", "-A")
    git(project, *NO_MAINTENANCE, "commit", "-q", "--allow-empty", "-m", "Scenario seed")
    for overlay in overlays:
        shutil.copytree(overlay, project, dirs_exist_ok=True, ignore=IGNORED)
    return project


def overlay_paths(overlay: Path) -> list[str]:
    return sorted(str(path.relative_to(overlay)) for path in overlay.rglob("*")
                  if path.is_file() and "__pycache__" not in path.parts and ".fake-turns" not in path.parts)
