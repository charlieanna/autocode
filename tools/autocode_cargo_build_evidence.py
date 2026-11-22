"""Join Cargo's compiler targets to the test executables it actually runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote, urlsplit


class Target(NamedTuple):
    manifest: str
    package: str
    name: str
    kinds: tuple[str, ...]
    source: str


@dataclass
class Build:
    """Compiler observations from the JSON prefix of a single Cargo invocation."""

    cwd: Path
    binaries: dict[Path, Target]
    failed: set[Target]
    success: bool

    def running(self, path: str) -> Target | None:
        try:
            return self.binaries.get((self.cwd / path).resolve())
        except (OSError, RuntimeError, ValueError):
            return None


def _target(row, root: Path) -> Target | None:
    package = urlsplit(row["package_id"])
    source = Path(row["target"]["src_path"])
    manifest = Path(row["manifest_path"])
    name, kinds = row["target"]["name"], row["target"]["kind"]
    if (
        package.scheme not in ("path+file", "file")
        or package.netloc
        or package.query
        or not package.fragment
        or Path(unquote(package.path)).resolve() != manifest.parent.resolve()
        or manifest.name != "Cargo.toml"
        or not isinstance(name, str)
        or not name
        or not isinstance(kinds, list)
        or not kinds
        or not all(isinstance(kind, str) and kind for kind in kinds)
        or not source.is_absolute()
        or not manifest.is_absolute()
    ):
        return None
    return Target(
        manifest.resolve().relative_to(root).as_posix(),
        package.fragment,
        name,
        tuple(kinds),
        source.resolve().relative_to(root).as_posix(),
    )


def emitted(text: str, root: str | Path) -> Build | None:
    """Read the build prefix only; a test can print arbitrary text after it."""
    root = Path(root).resolve()
    binaries: dict[Path, Target] = {}
    failed: set[Target] = set()
    for line in text.splitlines():
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                continue
            reason = row.get("reason")
            if reason == "build-finished":
                ok = row.get("success")
                return Build(root, binaries, failed, ok) if type(ok) is bool and not (ok and failed) else None
            if (
                reason == "compiler-artifact"
                and row.get("executable")
                and row["profile"].get("test") is True
                and (target := _target(row, root))
            ):
                executable = Path(row["executable"])
                if not executable.is_absolute():
                    return None
                executable = executable.resolve()
                prior = binaries.setdefault(executable, target)
                if prior != target:
                    return None
            if (
                reason == "compiler-message"
                and row["message"].get("level") == "error"
                and (target := _target(row, root))
            ):
                failed.add(target)
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
            # Dependencies outside the owned tree, or older opaque Cargo
            # formats, cannot bind a workspace test to a failed target.
            continue
    return None


def descriptor(target: Target) -> list:
    manifest, package, name, kinds, source = target
    return [manifest, package, name, list(kinds), source]


def was_blocked(base: dict, candidate: dict, name: str) -> bool:
    """An executed test has exactly one owner and that build failed on the base."""
    before = base.get("cargo_build") or {}
    after = candidate.get("cargo_build") or {}
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    tests, failed = after.get("tests"), before.get("failed_targets")
    owner = tests.get(name) if isinstance(tests, dict) else None
    return bool(
        before.get("success") is False
        and after.get("success") is True
        and owner is not None
        and isinstance(failed, list)
        and owner in failed
    )
