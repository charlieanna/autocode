"""Select and qualify the fixed Bash runtime used inside native tool boundaries."""

from __future__ import annotations

import hashlib
import json
import os
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


# The only variables a selected toolchain's rule may hand the contained shell (autocode_toolchain.discover):
# each names a read-only toolchain location, never a credential or the person's own configuration.
TOOL_VARIABLES = ("RUSTUP_HOME",)


def environment(runtime, scratch, control_name, original, verification, tools=None):
    """Tools receive only their owned temporary area and nonsecret runtime setup.

    ``tools`` is the selected toolchains' own setup (autocode_toolchain.discover's ``environment``): only the
    names in ``TOOL_VARIABLES`` are taken, each a read-only toolchain location."""
    safe = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "OPENSSL_CONF": "/dev/null",
        "NPM_CONFIG_USERCONFIG": str(scratch / "npm-user.conf"),
        "NPM_CONFIG_GLOBALCONFIG": str(scratch / "npm-global.conf"),
        "PATH": search_path(runtime, original.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")),
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
        safe[name] = value
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
