"""Explicit, lock-bound dependencies for disposable npm scenario projects."""

from __future__ import annotations

import fcntl
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

# Vitest is a dev dependency; Vite's platform binaries are optional packages.
# Include both even when the host's npm configuration would omit them.
NPM_CI_ARGUMENTS = ("ci", "--include=dev", "--include=optional", "--ignore-scripts", "--no-audit", "--no-fund")


def _runtime_identity(directory: Path) -> dict:
    """Identify native optional-package selection without network access or lifecycle scripts."""
    probe = (
        "JSON.stringify({platform:process.platform,arch:process.arch,node:process.version,"
        "abi:process.versions.modules,glibc:process.report.getReport().header.glibcVersionRuntime || null})"
    )
    try:
        node = subprocess.run(
            ["node", "-p", probe], cwd=directory, check=True, capture_output=True, text=True, timeout=20
        )
        npm = subprocess.run(
            ["npm", "config", "list", "--json"],
            cwd=directory,
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        runtime, config = json.loads(node.stdout), json.loads(npm.stdout)
        identity = {key: runtime[key] for key in ("platform", "arch", "node", "abi", "glibc")}
        identity.update({key: config.get(key) for key in ("npm-version", "cpu", "os", "libc")})
        if any(
            not isinstance(identity[key], str) or not identity[key]
            for key in ("platform", "arch", "node", "abi", "npm-version")
        ):
            raise ValueError("missing runtime identity")
        if any(
            identity[key] is not None and not isinstance(identity[key], str) for key in ("glibc", "cpu", "os", "libc")
        ):
            raise ValueError("invalid install target")
        return identity
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, AttributeError):
        # npm config may contain private settings; never include its output in an error.
        raise RuntimeError(
            "Cannot identify the scenario npm install runtime; no dependency cache was accepted"
        ) from None


def prepare(seed: Path, project: Path) -> None:
    """Install a catalog seed's frozen lock once, then expose it as ignored input.

    Only explicit scenario setup installs packages. The cache is outside model
    workspaces and is bound to both manifests, the install policy and the native
    runtime/target. Older keys remain on disk but cannot satisfy this identity.
    npm lifecycle scripts are disabled. Each project gets its own dependency
    directory: investigation snapshots refuse external symlinks, and model
    edits must not affect the shared cache or another scenario.
    """
    manifests = [seed / name for name in ("package.json", "package-lock.json")]
    if not all(path.is_file() and not path.is_symlink() for path in manifests):
        raise ValueError(f"npm setup needs regular package.json and package-lock.json in {seed}")
    cache_root = Path(__file__).resolve().parents[2] / ".scenario-runs" / "npm-dependencies"
    cache_root.mkdir(parents=True, exist_ok=True)
    runtime = json.dumps(_runtime_identity(cache_root), sort_keys=True, separators=(",", ":")).encode()
    policy = b"\0".join(argument.encode() for argument in NPM_CI_ARGUMENTS)
    digest = hashlib.sha256(b"\0".join([runtime, policy, *(path.read_bytes() for path in manifests)])).hexdigest()
    cache = cache_root / digest
    if cache.is_symlink():
        raise ValueError("npm setup refuses a redirected dependency cache")
    cache.mkdir(parents=True, exist_ok=True)
    for name in ("install.lock", "ready", "node_modules", "package.json", "package-lock.json"):
        if (cache / name).is_symlink():
            raise ValueError(f"npm setup refuses redirected cache entry: {name}")
    with (cache / "install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ready = cache / "ready"
        modules = cache / "node_modules"
        if not ready.is_file() or ready.read_text() != digest + "\n" or not modules.is_dir():
            for source in manifests:
                shutil.copyfile(source, cache / source.name)
            result = subprocess.run(["npm", *NPM_CI_ARGUMENTS], cwd=cache, capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise RuntimeError("Pinned scenario npm setup failed: " + (result.stdout + result.stderr)[-1200:])
            ready.write_text(digest + "\n")
        target = project / "node_modules"
        if target.exists() or target.is_symlink():
            raise ValueError(f"npm setup refuses existing dependencies at {target}")
        shutil.copytree(modules, target, symlinks=True)
