"""Select and qualify the fixed Bash runtime used inside native tool boundaries."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

try:
    from . import autocode_elf_dependencies as elf
    from . import autocode_macho_dependencies as macho
except ImportError:
    import autocode_elf_dependencies as elf
    import autocode_macho_dependencies as macho

HOMEBREW_PREFIXES = (Path("/opt/homebrew"), Path("/usr/local"))


def executable() -> Path:
    """Use installed Homebrew Bash on macOS; never take a project/PATH shim."""
    if sys.platform == "darwin":
        for prefix in HOMEBREW_PREFIXES:
            candidate = prefix / "opt/bash/bin/bash"
            if candidate.is_file():
                target = candidate.resolve(strict=True)
                if target.is_relative_to(prefix / "Cellar/bash") and target.name == "bash":
                    return target
    return Path("/bin/bash").resolve(strict=True)


def runtime() -> dict:
    binary = executable()
    return {
        "path": str(binary),
        "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "read_roots": [str(path) for path in sorted((macho if sys.platform == "darwin" else elf).dependencies(binary))],
    }


def install_alias(runtime: dict, control: Path) -> dict:
    """Expose only Bash ahead of the selected tools, in their protected control area."""
    directory = control / "shell-bin"
    directory.mkdir(mode=0o700)
    alias = directory / "bash"
    alias.symlink_to(runtime["path"])
    return {**runtime, "alias": str(alias)}


def unchanged(runtime: dict, *, control: Path | None = None) -> bool:
    if not isinstance(runtime, dict) or not all(isinstance(runtime.get(key), str) for key in ("path", "sha256")):
        return False
    try:
        if hashlib.sha256(Path(runtime["path"]).read_bytes()).hexdigest() != runtime["sha256"]:
            return False
        if "alias" not in runtime:
            return True  # Retained boundaries created before the private alias.
        if not isinstance(runtime["alias"], str):
            return False
        alias = Path(runtime["alias"])
        return (
            (control is None or alias == control / "shell-bin/bash")
            and not alias.parent.is_symlink()
            and set(alias.parent.iterdir()) == {alias}
            and alias.is_symlink()
            and alias.readlink() == Path(runtime["path"])
        )
    except (OSError, ValueError):
        return False


def search_path(runtime: dict, path: str) -> str:
    return str(Path(runtime["alias"]).parent) + os.pathsep + path


# The selected toolchain may pass a nonsecret Rustup selection; its home is
# replaced with owned scratch metadata and read-only selected toolchains.
TOOL_VARIABLES = ("RUSTUP_HOME", "RUSTUP_TOOLCHAIN")


def _rustup_settings(source):
    """Read only a bounded regular settings file; never follow its final symlink."""
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("Platform cannot safely read rustup settings")
    try:
        descriptor = os.open(source / "settings.toml", os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError("Selected rustup settings cannot be read safely") from error
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024 * 1024:
            raise ValueError("Selected rustup settings are not a bounded regular file")
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("Selected rustup settings are not a bounded regular file")
    return data


def _rustup_home(scratch, selected):
    """Give rustup writable metadata without permitting writes to the selected toolchain."""
    source = Path(selected)
    if not source.is_absolute() or not (source / "toolchains").is_dir():
        raise ValueError("Selected rustup toolchains are unavailable")
    settings = _rustup_settings(source)
    home = scratch / "rustup-home"
    home.mkdir(mode=0o700)
    (home / "toolchains").symlink_to((source / "toolchains").resolve(strict=True), target_is_directory=True)
    if settings is not None:
        copied = home / "settings.toml"
        copied.write_bytes(settings)
        copied.chmod(0o600)
    return str(home)


def _tool_path(runtime, scratch, path, aliases):
    """Add owned aliases at the selected PATH position, preserving earlier choices."""
    if not aliases:
        return search_path(runtime, path)
    if not isinstance(aliases, list):
        raise ValueError("Invalid selected tool aliases")
    parts = path.split(os.pathsep)
    seen = set()
    groups = []
    for group in aliases:
        if not isinstance(group, dict) or group.get("path") != path:
            raise ValueError("Selected tool aliases do not match PATH")
        index, target, names = group.get("index"), group.get("proxy"), group.get("names")
        if (
            type(index) is not int
            or index < 0
            or index >= len(parts)
            or index in seen
            or not isinstance(target, str)
            or not Path(target).is_absolute()
            or "\0" in target
            or not isinstance(names, list)
            or not names
            or len(names) > 4096
            or any(
                not isinstance(name, str)
                or name in ("", ".", "..")
                or "\0" in name
                or len(name.encode()) > 255
                or Path(name).name != name
                for name in names
            )
            or len(set(names)) != len(names)
        ):
            raise ValueError("Invalid selected tool aliases")
        seen.add(index)
        groups.append((index, target, names))
    for index, target, names in sorted(groups, reverse=True):
        directory = scratch / f"tool-aliases-{index}"
        directory.mkdir(mode=0o700)
        for name in names:
            (directory / name).symlink_to(target)
        parts.insert(index, str(directory))
    return search_path(runtime, os.pathsep.join(parts))


def environment(runtime, scratch, control_name, original, verification, tools=None, *, aliases=None):
    """Tools receive their owned temporary area and selected nonsecret runtime setup."""
    safe = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "OPENSSL_CONF": "/dev/null",
        "NPM_CONFIG_USERCONFIG": str(scratch / "npm-user.conf"),
        "NPM_CONFIG_GLOBALCONFIG": str(scratch / "npm-global.conf"),
        "PATH": _tool_path(runtime, scratch, original.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"), aliases),
        "HOME": str(scratch),
        "TMPDIR": str(scratch),
        "TMP": str(scratch),
        "TEMP": str(scratch),
        "SHELL": runtime["path"],
        "BASH_ENV": "/dev/null",
        "ENV": "/dev/null",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "CI": "1",
        "LANG": "en_US.UTF-8",
        "AUTOCODE_TOOL_CONTAINMENT_ID": control_name,
        "AUTOCODE_OUTPUT_STORE": str(scratch / "output-store"),
    }
    for name, value in (tools or {}).items():
        if name not in TOOL_VARIABLES or not isinstance(value, str):
            raise ValueError(f"Unsupported toolchain variable for tool containment: {name}")
        safe[name] = _rustup_home(scratch, value) if name == "RUSTUP_HOME" else value
    if verification:
        safe["AUTOCODE_VERIFICATION_COPY"] = verification["manifest"]
        safe["AUTOCODE_VERIFICATION_COPY_SHA256"] = verification["sha256"]
    if original.get("AUTOCODE_CAPTURE_CONTEXT"):
        context = json.loads(original["AUTOCODE_CAPTURE_CONTEXT"])
        if not isinstance(context, dict) or not all(
            isinstance(context.get(key), str) for key in ("attempt", "nonce", "source_revision")
        ):
            raise ValueError("Invalid runner capture context for tool containment")
        safe["AUTOCODE_CAPTURE_CONTEXT"] = json.dumps(context)
    return safe


def input_probe() -> str:
    """Observe small and file-backed input, subshells and literal preservation."""
    large = "x" * 32768
    return (
        "IFS= read -r value <<'AUTOCODE_SMALL'\n"
        "literal '$HOME' \\ tests/__init__.py 李雷\nAUTOCODE_SMALL\n"
        "test \"$value\" = 'literal '\"'\"'$HOME'\"'\"' \\ tests/__init__.py 李雷' || exit 121\n"
        "IFS= read -r value <<'AUTOCODE_LARGE'\n"
        + large
        + "\nAUTOCODE_LARGE\n"
        + 'test "${#value}" -eq 32768 || exit 122\n'
        + "bash --noprofile --norc -c 'IFS= read -r value <<<nested; test \"$value\" = nested'"
    )
