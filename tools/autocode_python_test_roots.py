"""The directories a Python project's own pytest configuration names as where its tests are.

The role inventory (``autocode_python_test_roles``) knew test directories only by name (``tests/``,
a root ``test/``). Beside product code that imports modules by computed name, every test file kept
anywhere else was ambiguous, and a run stopped on it: pytest keeps its own suite in ``testing/``,
which its ``pyproject.toml`` names in ``testpaths`` (a live run, 2026-11-18). The project says where
its tests are in the file its runner reads, so the inventory reads that answer instead of guessing
from one more directory name.

Only the original tree's configuration is read, as with packaging ownership: a change cannot move
the project's test roots. A named directory is a test root only where nothing outside it imports
from it and no shipped package lies in it, so ``testpaths = ["src", "tests"]`` (doctests in the
product) never turns the product into tests. Pure functions over a reader; imports nothing from
AutoCode.
"""

from __future__ import annotations

import configparser
import hashlib
import posixpath
import tomllib
from dataclasses import dataclass, field
from pathlib import PurePosixPath

# pytest's own order of configuration files in one directory: the first that configures pytest is
# the one it reads (https://docs.pytest.org/en/stable/reference/customize.html#finding-the-rootdir).
CONFIG_FILES = ("pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")
_GLOB = frozenset("*?[")


@dataclass(frozen=True)
class Declared:
    """Directories configured as test paths, and the configuration files they were read from."""

    directories: frozenset[str] = frozenset()
    configs: dict[str, str] = field(default_factory=dict)


def _ini(source, section, *, bare):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(source)
    if not parser.has_section(section):
        return None if not bare else []
    return parser.get(section, "testpaths", fallback="").split()


def _toml(table):
    if not isinstance(table, dict):
        return None
    value = table.get("testpaths", [])
    if isinstance(value, str):
        value = value.split()
    return value if isinstance(value, list) else []


def _testpaths(name, source):
    """``testpaths`` from one file, or None when the file does not configure pytest."""
    if name in ("pytest.toml", ".pytest.toml"):
        return _toml(tomllib.loads(source).get("pytest")) or []
    if name in ("pytest.ini", ".pytest.ini"):
        # pytest.ini configures pytest even when it is empty.
        return _ini(source, "pytest", bare=True)
    if name == "pyproject.toml":
        tool = tomllib.loads(source).get("tool", {})
        pytest = tool.get("pytest") if isinstance(tool, dict) else None
        if not isinstance(pytest, dict):
            return None
        options = pytest.get("ini_options")
        return _toml(options) if isinstance(options, dict) else _toml(pytest)
    if name == "tox.ini":
        return _ini(source, "pytest", bare=False)
    return _ini(source, "tool:pytest", bare=False)


def _entry(directory, value):
    """One configured path below its configuration's directory, or None when it names no one directory."""
    if not isinstance(value, str) or not value or _GLOB & set(value) or PurePosixPath(value).is_absolute():
        return None
    joined = posixpath.normpath(posixpath.join(directory or ".", value))
    if joined in (".", "") or joined.startswith("../") or joined == ".." or ".git" in PurePosixPath(joined).parts:
        return None
    return joined


def declared(paths, read, links=frozenset()) -> Declared:
    """The test paths every pytest configuration in the tree names, each below its own directory.

    A configuration that cannot be read or parsed names nothing, so the inventory keeps its rule by
    name for that project. A configured path that is the configuration's own directory, a glob or a
    path outside the tree names nothing either: each would make more than the suite a test."""
    paths = frozenset(paths)
    found = {}
    for path in sorted(paths):
        name = PurePosixPath(path).name
        if name in CONFIG_FILES and path not in links:
            found.setdefault(str(PurePosixPath(path).parent), []).append(path)
    directories, configs = set(), {}
    for directory, names in found.items():
        directory = "" if directory == "." else directory
        for path in sorted(names, key=lambda item: CONFIG_FILES.index(PurePosixPath(item).name)):
            try:
                source = read(path)
                values = _testpaths(PurePosixPath(path).name, source)
            except (configparser.Error, tomllib.TOMLDecodeError, TypeError, ValueError, AttributeError, KeyError):
                break
            if values is None:
                continue
            configs[path] = hashlib.sha256(source.encode("utf-8", "surrogateescape")).hexdigest()
            for value in values:
                entry = _entry(directory, value)
                if entry is not None and any(item.startswith(entry + "/") for item in paths):
                    directories.add(entry)
            break
    return Declared(frozenset(directories), configs)


def under(path, directories):
    return any(path.startswith(directory + "/") for directory in directories)
