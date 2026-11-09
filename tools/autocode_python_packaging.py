"""Literal Python distribution ownership, read from the original tree only.

Unknown/dynamic build configuration grants no ownership. These declarations do
not make a module a test and never authorize copying product bytes into a base.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath

NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\Z")


def _relative(value):
    if not isinstance(value, str):
        raise ValueError("package directories must be literal strings")
    path = PurePosixPath(value or ".")
    if path.is_absolute() or ".." in path.parts or ".git" in path.parts or "\\" in value:
        raise ValueError("package directories must stay repository-relative")
    return str(path)


@dataclass(frozen=True)
class Ownership:
    packages: frozenset[str] = frozenset()
    modules: frozenset[str] = frozenset()
    reason: str = ""
    aliases: tuple[tuple[str, str], ...] = ()

    def contains(self, path):
        value = PurePosixPath(path)
        return value.suffix == ".py" and (path in self.modules or str(value.parent) in self.packages)


def declared(source):
    """Support explicit setuptools package/module lists and literal package-dir mappings.

    A package owns its immediate Python modules, not arbitrary nested folders:
    an unlisted ``pkg/tests`` directory is not a shipped subpackage by inference.
    ``find``, setup.py execution and other build backends remain unestablished.
    """
    if source is None:
        return Ownership(reason="no original pyproject.toml declaration")
    try:
        data = tomllib.loads(source)
        backend = data.get("build-system", {}).get("build-backend", "setuptools.build_meta")
        if backend not in ("setuptools.build_meta", "setuptools.build_meta:__legacy__"):
            return Ownership(reason="original build backend does not establish setuptools ownership")
        if "setuptools" not in data.get("tool", {}):
            return Ownership(reason="no supported original setuptools ownership declaration")
        setup = data.get("tool", {}).get("setuptools", {})
        if not isinstance(setup, dict):
            raise ValueError("setuptools configuration is not a table")
        packages, modules = setup.get("packages", []), setup.get("py-modules", [])
        if not isinstance(packages, list) or not isinstance(modules, list):
            raise ValueError("only explicit package and py-module lists are supported")
        mapping = setup.get("package-dir", {})
        if not isinstance(mapping, dict):
            raise ValueError("package-dir is not a literal table")
        mapping = {key: _relative(value) for key, value in mapping.items()}
        if any(key and not NAME.fullmatch(key) for key in mapping):
            raise ValueError("package-dir has an invalid package name")
        roots, aliases = set(), []
        for package in packages:
            if not isinstance(package, str) or not NAME.fullmatch(package):
                raise ValueError("package names must be literal dotted identifiers")
            prefix = max(
                (key for key in mapping if not key or package == key or package.startswith(key + ".")),
                key=len,
                default="",
            )
            suffix = package[len(prefix) :].lstrip(".") if prefix else package
            directory = str(PurePosixPath(mapping.get(prefix, "."), *suffix.split(".")))
            roots.add(directory)
            aliases.append((package, directory))
        files = set()
        for module in modules:
            if not isinstance(module, str) or not NAME.fullmatch(module) or "." in module:
                raise ValueError("py-modules must name literal top-level modules")
            files.add(str(PurePosixPath(mapping.get("", "."), module + ".py")))
        return Ownership(frozenset(roots), frozenset(files), aliases=tuple(aliases))
    except (tomllib.TOMLDecodeError, TypeError, AttributeError, ValueError) as error:
        return Ownership(reason="original packaging ownership is unsupported: " + str(error))
