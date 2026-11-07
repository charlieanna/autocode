"""Source-bound build space for read-only native verification commands.

The runner creates this before launch. Captures validate retained inputs before
execution; contained providers additionally protect them with a kernel policy.
New build outputs can persist between captures. Accepted checks still undergo
the independent clean-source replay before a PASS counts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

try:
    from . import autocode_joint_inputs as joint_inputs
    from . import autocode_source_snapshot as source
    from . import autocode_util as util
    from .autocode_verification_budget import PREPARATION_ALLOWANCE_SECONDS
except ImportError:
    import autocode_joint_inputs as joint_inputs
    import autocode_source_snapshot as source
    import autocode_util as util
    from autocode_verification_budget import PREPARATION_ALLOWANCE_SECONDS


def _identity(path):
    if path.is_symlink():
        return "symlink:" + os.readlink(path)
    if path.is_file():
        return ("executable:" if path.stat().st_mode & 0o111 else "") + util.file_hash(path)
    return "deleted" if not path.exists() else "directory"


def _remaining(deadline, command, *, ceiling=None):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(command, PREPARATION_ALLOWANCE_SECONDS)
    return min(remaining, ceiling) if ceiling is not None else remaining


def _pack_source_blobs(tree, scratch, copied, env, *, deadline):
    """Prime this standalone repository from verified copied bytes only.

    Blob commands create no commits or refs. The ordinary add still applies
    Git attributes and records file modes before the original baseline commit.
    """
    with tempfile.TemporaryFile(dir=scratch) as stream:
        for name, expected in copied.items():
            _remaining(deadline, "Verification source packing")
            path = tree / name
            if _identity(path) != expected:
                raise ValueError("Source changed while packing: " + name)
            if path.is_symlink():
                data = os.fsencode(os.readlink(path))
                observed = "symlink:" + os.fsdecode(data)
                stream.write(b"blob\ndata " + str(len(data)).encode("ascii") + b"\n")
                stream.write(data)
            else:
                before = path.lstat()
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError("Unsupported copied source while packing: " + name)
                digest, count = hashlib.sha256(), 0
                with path.open("rb") as source_stream:
                    opened = os.fstat(source_stream.fileno())
                    if (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_size) != (
                        before.st_dev,
                        before.st_ino,
                        before.st_mode,
                        before.st_size,
                    ):
                        raise ValueError("Source changed while opening pack input: " + name)
                    stream.write(b"blob\ndata " + str(before.st_size).encode("ascii") + b"\n")
                    for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                        count += len(chunk)
                        if count > before.st_size:
                            raise ValueError("Source grew while packing: " + name)
                        digest.update(chunk)
                        stream.write(chunk)
                    finished = os.fstat(source_stream.fileno())
                current = path.lstat()
                if (
                    count != before.st_size
                    or (finished.st_mode, finished.st_size) != (before.st_mode, before.st_size)
                    or (current.st_dev, current.st_ino, current.st_mode, current.st_size)
                    != (before.st_dev, before.st_ino, before.st_mode, before.st_size)
                ):
                    raise ValueError("Source changed while streaming pack input: " + name)
                observed = ("executable:" if before.st_mode & 0o111 else "") + digest.hexdigest()
            if observed != expected or _identity(path) != expected:
                raise ValueError("Source changed while packing: " + name)
            stream.write(b"\n")
        stream.write(b"done\n")
        stream.seek(0)
        # This disposable source pack is consumed immediately. Compressing tens
        # of thousands of blobs can exhaust preparation's bounded time window;
        # object identities and normal Git attribute handling do not need it.
        command = ["/usr/bin/git", "-c", "pack.compression=0", "fast-import", "--quiet", "--done", "--depth=0"]
        subprocess.run(
            command,
            cwd=tree,
            env=env,
            stdin=stream,
            check=True,
            capture_output=True,
            timeout=_remaining(deadline, command),
        )


def _inventory(root, source_paths=()):
    return source.inventory(root, paths=source_paths)


def _retain_history(root, tree, head, env, *, deadline):
    """Retain actual HEAD history as owned data, without sharing Git authority.

    Export only objects reachable from the snapshotted commit. No original
    configuration, refs, hooks, alternates or replacement objects enter the
    copy. The fixed native setup sequence still creates its own index/config;
    this trusted preparation step replaces only its synthetic history before
    the complete copy is sealed and any verification command can run.
    """
    environment = {
        **env,
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_GRAFT_FILE": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    git = ["/usr/bin/git", "--no-replace-objects", "-c", "core.fsmonitor=false"]
    command = [*git, "rev-parse", "--git-path", "shallow"]
    shallow_path = (
        subprocess.run(
            command,
            cwd=root,
            env=environment,
            check=True,
            capture_output=True,
            timeout=_remaining(deadline, command),
        )
        .stdout.decode()
        .strip()
    )
    shallow_path = root / shallow_path
    shallow = shallow_path.read_bytes() if shallow_path.exists() else None
    if shallow is not None and any(
        not re.fullmatch(rb"[0-9a-f]{" + str(len(head)).encode() + rb"}", line) for line in shallow.splitlines()
    ):
        raise ValueError("Invalid source Git shallow boundary")
    command = [
        *git,
        "pack-objects",
        "--revs",
        "--compression=0",
        "--window=0",
        "--threads=1",
        str(tree / ".git/objects/pack/history"),
    ]
    subprocess.run(
        command,
        cwd=root,
        env=environment,
        input=(head + "\n").encode(),
        check=True,
        capture_output=True,
        timeout=_remaining(deadline, command),
    )
    if shallow is not None:
        (tree / ".git/shallow").write_bytes(shallow)
    # The index keeps the copied current files, including uncommitted changes.
    # A detached HEAD exposes the real delivered commit instead of the setup
    # commit. Remove only metadata freshly created inside this owned copy.
    for name in ("refs", "logs"):
        shutil.rmtree(tree / ".git" / name, ignore_errors=True)
    (tree / ".git/refs").mkdir()
    (tree / ".git/HEAD").write_text(head + "\n")


GIT_SETUP_ARGUMENTS = (
    ("init", "-q"),
    ("config", "maintenance.auto", "false"),
    ("config", "gc.auto", "0"),
    ("add", "-f", "--all"),
    (
        "-c",
        "user.name=AutoCode verification",
        "-c",
        "user.email=verification@localhost",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--allow-empty",
        "-qm",
        "Verification source",
    ),
)


def native_git_setup(scratch):
    """Read exact native setup inputs without executing a tool or selecting PATH.

    Apple's /usr/bin/git is a developer selector. Only the canonical installed
    Git receives a setup-only read; its libraries use the existing OS policy.
    Empty owned template/exec directories avoid granting the CLT installation.
    The original native owner calls this again before seal, release and finish.
    """
    scratch = Path(scratch).absolute()
    executable = Path("/usr/bin/git" if sys.platform == "linux" else "/Library/Developer/CommandLineTools/usr/bin/git")
    if executable.resolve(strict=True) != executable or not executable.is_file():
        raise ValueError("Native Git setup requires the canonical installed executable")
    identity = executable.stat()
    if identity.st_uid != 0 or identity.st_mode & 0o022 or not identity.st_mode & 0o111:
        raise ValueError("Native Git setup requires a root-owned nonwritable executable")
    empty = scratch / "native-git-empty"
    if (
        scratch.resolve(strict=True) != scratch
        or empty.resolve(strict=True) != empty
        or not empty.is_dir()
        or stat.S_IMODE(empty.stat().st_mode) != 0o500
        or any(empty.iterdir())
    ):
        raise ValueError("Native Git setup requires its unchanged empty resource directory")
    environment = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(scratch),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TEMPLATE_DIR": str(empty),
        "GIT_EXEC_PATH": str(empty),
    }
    return {
        "executable": str(executable),
        "sha256": util.file_hash(executable),
        "environment": environment,
        "commands": [[str(executable), *args] for args in GIT_SETUP_ARGUMENTS],
    }


def _run_directory(root, run_dir):
    directory = Path(run_dir)
    storage = root / ".autocode"
    if storage.is_symlink():
        raise ValueError("Verification storage must not be a symlink")
    if (
        directory.parent != storage / "runs"
        or not directory.is_dir()
        or directory.is_symlink()
        or directory.resolve() != directory
    ):
        raise ValueError("Verification copy needs an existing physical task run directory")
    return directory


def allocate(workspace, run_dir, *, source_paths=()):
    """Allocate one owned capture copy, without claiming an OS tool boundary.

    Retain successful copies as stage evidence. On preparation failure remove
    only this call's fresh control directory, never another stage's outputs.
    The layout is the existing capture manifest's authority contract.
    """
    deadline = time.monotonic() + PREPARATION_ALLOWANCE_SECONDS
    root = Path(workspace).resolve()
    run = _run_directory(root, run_dir)
    control = run / ("tool-containment-" + uuid.uuid4().hex)
    control.mkdir(mode=0o700)
    try:
        scratch = control / "scratch"
        scratch.mkdir(mode=0o700)
        result = create(root, scratch, source_paths=source_paths, run_dir=run, _deadline=deadline)
        tree, provenance = execution(result["manifest"], result["sha256"], root)
        _remaining(deadline, "Verification source final authentication")
        return {
            "manifest": result["manifest"],
            "sha256": result["sha256"],
            "workspace": str(root),
            "tree": str(tree),
            **provenance,
        }
    except BaseException:
        shutil.rmtree(control)
        raise


def create(workspace, scratch, *, source_paths=(), setup_executor=None, run_dir=None, _deadline=None):
    # Copying, packing, staging and committing share one finite allowance.
    # Outer command/client/stage supervisors keep their existing deadlines.
    started = time.monotonic()
    deadline = (
        min(_deadline, started + PREPARATION_ALLOWANCE_SECONDS)
        if _deadline is not None
        else (started + PREPARATION_ALLOWANCE_SECONDS)
    )
    root, scratch = Path(workspace).resolve(), Path(scratch).resolve()
    if not scratch.is_relative_to(root / ".autocode"):
        raise ValueError("Verification copy must be inside the owned task scratch")
    run = _run_directory(root, run_dir) if run_dir is not None else None
    if run is not None and (
        scratch.name != "scratch"
        or scratch.parent.parent != run
        or not re.fullmatch(r"tool-containment-[0-9a-f]{32}", scratch.parent.name)
    ):
        raise ValueError("Verification copy must be inside its owning run control directory")
    tree = scratch / "verification"
    tree.mkdir()  # Never reuse a previous stage's outputs.
    before = source.snapshot(root, paths=source_paths)
    inputs = _inventory(root, source_paths)
    copied = {}
    for name, identity in inputs.items():
        _remaining(deadline, "Verification source copying")
        original, target = root / name, tree / name
        if identity == "deleted":
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if original.is_symlink():
            link = os.readlink(original)
            if os.path.isabs(link) and Path(link).is_relative_to(root):
                link = str(tree / Path(link).relative_to(root))
            target.symlink_to(link)
        else:
            shutil.copy2(original, target)
        expected = "symlink:" + link if original.is_symlink() else identity
        if _identity(original) != identity or _identity(target) != expected:
            raise ValueError("Source changed while copying: " + name)
        copied[name] = expected
    # A real standalone Git repository avoids accidentally inspecting the
    # parent task's index when a check invokes Git from the copy.
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(scratch),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }
    # This environment is built from scratch, so the suite-wide GIT_CONFIG_COUNT
    # that stops every other repository starting detached maintenance (#515)
    # never reaches it; write the keys into the repository instead, or a repack
    # under .git/objects after the manifest walk reads as a changed input.
    if len(before["head"]) == 64:
        # Reinitializing this fresh SHA-256 format preserves the exact fixed
        # setup argv/environment used by the native owner as well.
        (tree / ".git").mkdir()
        (tree / ".git/config").write_text("[core]\nrepositoryformatversion = 1\n[extensions]\nobjectformat = sha256\n")
    for index, args in enumerate(GIT_SETUP_ARGUMENTS):
        command = ["/usr/bin/git", *args]
        if setup_executor is None:
            if index == 3:
                # Prime ordinary copies after init/config and before add/commit.
                # Native copies retain their exact authenticated setup sequence.
                _pack_source_blobs(tree, scratch, copied, env, deadline=deadline)
            subprocess.run(
                command,
                cwd=tree,
                env=env,
                check=True,
                capture_output=True,
                timeout=_remaining(deadline, command, ceiling=30 if index < 3 else None),
            )
        else:
            # The concrete native owner executes these fixed setup commands
            # through its original journal/held root, before sealing the copy.
            result = setup_executor(command, cwd=tree, env=env, timeout=_remaining(deadline, command, ceiling=30))
            if result.get("exit_code") != 0 or result.get("timed_out") or result.get("error"):
                raise ValueError("Owned verification Git setup failed: " + str(result))
    _retain_history(root, tree, before["head"], env, deadline=deadline)
    retained_joint_inputs = joint_inputs.copy(root, tree)
    # Match the clean replay's read-only task-local dependency lookup. These
    # links grant no new authority. A contained provider's sandbox also keeps
    # their originals read-only; an uncontained provider gets no such claim.
    for name in ("node_modules", ".venv", "venv", "vendor"):
        original, target = root / name, tree / name
        if original.is_dir() and not target.exists() and not target.is_symlink():
            target.symlink_to(original.resolve(), target_is_directory=True)
    if source.snapshot(root, paths=source_paths) != before:
        raise ValueError("Source changed while preparing verification copy")
    _remaining(deadline, "Verification source authentication")
    files = {str(p.relative_to(tree)): _identity(p) for p in tree.rglob("*") if p.is_symlink() or p.is_file()}
    protected = [str(tree), *(str(p) for p in tree.rglob("*"))]
    manifest = scratch.parent / "verification-copy.json"
    data = {
        "version": 1,
        "workspace": str(root),
        "tree": str(tree),
        "source_revision": before["revision"],
        "source_paths": before.get("source_paths", []),
        "files": files,
        "joint_inputs": retained_joint_inputs,
        "directories": [str(p.relative_to(tree)) for p in tree.rglob("*") if p.is_dir() and not p.is_symlink()],
    }
    _remaining(deadline, "Verification source manifest")
    if run is not None:
        data["run_dir"] = str(run)
    util.atomic_json(manifest, data)
    result = {"manifest": str(manifest), "sha256": util.file_hash(manifest), "protected_paths": protected}
    _remaining(deadline, "Verification source manifest publication")
    return result


def execution(manifest, expected_hash, workspace):
    """Validate retained inputs, returning the cwd and provenance for capture."""
    root, path = Path(workspace).resolve(), Path(manifest)
    control = path.parent
    legacy = control.parent == root / ".autocode"
    run = None
    if not legacy:
        run = _run_directory(root, control.parent)
    if (
        path.is_symlink()
        or path.resolve() != path
        or path.name != "verification-copy.json"
        or not control.name.startswith("tool-containment-")
        or (run is not None and not re.fullmatch(r"tool-containment-[0-9a-f]{32}", control.name))
        or util.file_hash(path) != expected_hash
    ):
        raise ValueError("Verification copy authority changed")
    data = json.loads(path.read_text())
    tree = control / "scratch" / "verification"
    if (
        data.get("version") != 1
        or data.get("workspace") != str(root)
        or data.get("tree") != str(tree)
        or data.get("run_dir") != (str(run) if run is not None else None)
    ):
        raise ValueError("Unexpected verification copy layout")
    if (
        tree.resolve() != tree
        or source.snapshot(root, paths=data.get("source_paths", []))["revision"] != data["source_revision"]
        or joint_inputs.inventory(root) != data.get("joint_inputs", {})
        or joint_inputs.inventory(tree) != data.get("joint_inputs", {})
    ):
        raise ValueError("Verification source changed; request fresh validation")
    for name in data["directories"]:
        relative = Path(name)
        directory = tree / relative
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or directory.is_symlink()
            or not directory.is_dir()
            or directory.resolve() != directory
        ):
            raise ValueError("Verification directory changed: " + name)
    for name, expected in data["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or _identity(tree / relative) != expected:
            raise ValueError("Verification input changed: " + name)
    return tree, {"source_revision": data["source_revision"], "manifest_sha256": expected_hash}
