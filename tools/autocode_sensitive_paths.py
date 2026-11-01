"""A project's ``sensitive-paths``: the files a shorter planning path may never touch (#2021).

A team lists the path patterns of its sensitive code (authentication, payments, migrations, build and
CI configuration, dependency manifests) as ``sensitive-paths`` in its project settings
(``.autocode/config.toml``, or ``[tool.autocode]`` in ``pyproject.toml``: autocode_project_config). The
key has no flag: a new run saves the list in its settings (``SETTING``) when it starts, so a changed file
never alters a run in progress and no command line sets or overrides it. The list is a trusted input to
the compact planning path (autocode_compact_planning): a bug fix whose files match any pattern takes the
full path. New runs without a project declaration snapshot DEFAULT_PATTERNS; saved runs keep their
existing policy. An empty list is a declaration too: the project says none of its files are sensitive.

A pattern is a shell-style glob over the repository-relative POSIX path, matched segment by segment from
the right as ``PurePosixPath.match`` does: ``src/billing/*`` matches the files in that directory,
``src/billing/**`` or ``src/billing/`` everything under it, ``*.lock`` a lockfile anywhere. Matching is by
name only: it misses sensitive behaviour that lives in ordinary files. Reproduction, diagnosis,
independent plan review and exact-plan approval still gate the compact path.

Bottom layer: imports nothing from AutoCode.
"""

from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

KEY = "sensitive-paths"  # the project-file key (autocode_project_config.FILE_ONLY)
SETTING = "sensitive_paths"  # the run's settings key, and the attribute autocode_project_config sets on args
PATTERN_LIMIT = 512
DEFAULT_PATTERNS = (
    "*auth*",
    "*auth*/**",
    "*security*",
    "*security*/**",
    "*payment*",
    "*payment*/**",
    "*billing*",
    "*billing*/**",
    "*migration*",
    "*migration*/**",
    "*secret*",
    "*secret*/**",
    "*certificat*",
    "*certificat*/**",
    ".env*",
    "*.pem",
    "*.key",
    ".github/**",
    ".gitlab-ci.yml",
    "Jenkinsfile*",
    "Dockerfile*",
    "*docker-compose*",
    "Makefile",
    "CMakeLists.txt",
    "*.tf",
    "*.tfvars",
    "package.json",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements*.txt",
    "Pipfile",
    "Gemfile",
    "Cargo.toml",
    "go.mod",
    "go.sum",
    "pom.xml",
    "build.gradle*",
    "*.csproj",
    "*.lock",
    "*-lock.json",
)


def new_run_patterns(configured) -> list[str]:
    """Snapshot the project's declaration, or the shipped conservative defaults, for a new run."""
    return list(DEFAULT_PATTERNS if configured is None else configured)


def checked(value, source: str) -> list[str]:
    """The patterns a project file's ``sensitive-paths`` lists, in order and without repeats.

    It takes a list of path patterns (possibly empty: nothing is sensitive); anything else raises ValueError
    naming ``source``. Whitespace around a pattern is dropped; a blank or unprintable entry is refused.
    """
    if not isinstance(value, list) or not all(isinstance(entry, str) for entry in value):
        raise ValueError(
            f'{source}: {KEY} takes a list of path patterns, such as ["src/billing/**", "*.lock"] '
            "(an empty list declares that no path is sensitive)"
        )
    patterns: list[str] = []
    for entry in value:
        pattern = entry.strip()
        if not pattern or len(pattern) > PATTERN_LIMIT or not pattern.isprintable():
            raise ValueError(f"{source}: {KEY} entry {entry!r} is not a path pattern such as src/billing/**")
        if pattern not in patterns:
            patterns.append(pattern)
    return patterns


def matched(paths, patterns) -> list[str]:
    """The ``paths`` that any of ``patterns`` names, in order and without repeats."""
    found: list[str] = []
    for path in paths:
        text = PurePosixPath(str(path).strip()).as_posix()
        if text and text not in found and any(_matches(text, pattern) for pattern in patterns):
            found.append(text)
    return found


def _matches(path: str, pattern: str) -> bool:
    pattern = pattern.strip()
    if not pattern:
        return False
    if pattern.endswith("/") or pattern.endswith("/**"):
        root = pattern[: -1 if pattern.endswith("/") else -3].rstrip("/")
        return path == root or path.startswith(root + "/") or fnmatch.fnmatchcase(path, root + "/*")
    return PurePosixPath(path).match(pattern)
