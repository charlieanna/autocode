"""Select read-only runtime dependencies for declared commands before containment.

Declared commands, Git inspection and available Python helpers select tools. Unknown wrappers fail
before a paid launch; this never executes arbitrary project scripts to discover
what filesystem authority they might need.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from . import autocode_elf_dependencies as elf
    from . import autocode_macho_dependencies as macho
    from . import autocode_python_entrypoints as python_entrypoints
except ImportError:
    import autocode_elf_dependencies as elf
    import autocode_macho_dependencies as macho
    import autocode_python_entrypoints as python_entrypoints


def _binary_dependencies(tool):
    """The loader files a selected tool needs: Mach-O on macOS, ELF on Linux (#709)."""
    return (macho if sys.platform == "darwin" else elf).dependencies(tool)


def _symlink_hops(path):
    """Each (link, target) a lookup of path follows, in order, without reading any file.

    Seatbelt lets a contained shell read symlink metadata anywhere; bubblewrap shows only
    what is mounted, so it recreates these links for the selected tools (#709).
    """
    hops, current, pending = [], Path("/"), list(Path(path).absolute().parts[1:])
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            current = current.parent
            continue
        candidate = current / part
        if not candidate.is_symlink():
            current = candidate
            continue
        if len(hops) >= 40:
            raise RuntimeError(f"Too many symbolic links selecting a tool: {path}")
        target = os.readlink(candidate)
        hops.append([str(candidate), target])
        pending = list((current / target).parts[1:]) + pending
        current = Path("/")
    return hops


def _python_executable(tool):
    """Resolve Apple's known developer-tool trampoline without running Python.

    The contained shell clears DEVELOPER_DIR. Ask only the fixed system selector
    for the same active developer root; never execute a project-selected shim.
    """
    if sys.platform != "darwin" or tool != Path("/usr/bin/python3"):
        return tool
    selected = subprocess.run(
        ["/usr/bin/xcode-select", "-p"], env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=10
    )
    if selected.returncode or not selected.stdout.strip():
        raise RuntimeError("System Python requires an installed selected developer toolchain")
    executable = (Path(selected.stdout.strip()) / "usr/bin/python3").resolve(strict=True)
    if executable == tool:
        raise RuntimeError("System Python developer trampoline did not resolve an interpreter")
    return executable


def discover(workspace, commands, environment):
    root = Path(workspace).resolve()
    paths, probes, selected, links = set(), [], set(), []
    # Linked worktrees keep object/index metadata outside the task checkout.
    # Read only the locations named by Git's own pointer files, not the main
    # checkout (which can contain unrelated source and credentials).
    git_pointer = root / ".git"
    if git_pointer.is_file():
        pointer = git_pointer.read_text().strip()
        if not pointer.startswith("gitdir: "):
            raise RuntimeError("Cannot identify linked-worktree Git metadata")
        git_dir = (root / pointer[len("gitdir: ") :]).resolve(strict=True)
        paths.add(git_dir)
        common_pointer = git_dir / "commondir"
        if common_pointer.is_file():
            paths.add((git_dir / common_pointer.read_text().strip()).resolve(strict=True))
    search_path = environment.get("PATH", "")
    search_path = ":".join(
        str(root / part) if not Path(part).is_absolute() else part for part in search_path.split(":")
    )
    pending = ["git", *[shlex.split(command)[0] for command in commands if command.strip()]]
    # Validators use Python for JSON and CLI assertions even in non-Python
    # projects. Qualify the actual PATH interpreters, not a substituted runtime.
    pending.extend(name for name in ("python", "python3") if shutil.which(name, path=search_path))
    for manifest, executable in (("package.json", "npm"), ("go.mod", "go"), ("Cargo.toml", "cargo")):
        if (root / manifest).is_file():
            pending.append(executable)
    while pending:
        name = pending.pop(0)
        if name in selected:
            continue
        selected.add(name)
        resolved = str(root / name) if "/" in name and not Path(name).is_absolute() else name
        located = shutil.which(resolved, path=search_path)
        if not located:
            raise RuntimeError(f"Approved tool is unavailable before launch: {name}")
        original, tool = Path(located).absolute(), Path(located).resolve()
        links.extend(hop for hop in _symlink_hops(original) if hop not in links)
        family = original.name
        if family == "pytest":
            interpreter = python_entrypoints.pytest_interpreter(original)
            paths.add(tool)
            pending.append(str(interpreter))
            probes.append(shlex.join([str(original), "--version"]))
            continue
        if family in ("npm", "npx"):
            package = next((p for p in tool.parents if (p / "package.json").is_file()), None)
            if package is None or json.loads((package / "package.json").read_text()).get("name") != "npm":
                raise RuntimeError(f"Cannot identify the selected npm installation: {name}")
            paths.add(package)
            pending.append("node")
            probes.append(shlex.join([str(original), "--version"]))
            continue
        if family in ("sh", "bash"):
            # Approvals may use the documented shell negative-control form.
            # Only the fixed native system shells qualify, never a PATH shim
            # or project script with a shell's name. Probes run in containment.
            # A merged-/usr Linux resolves /bin/<shell> into /usr/bin; the same file qualifies.
            if tool != (Path("/bin") / family).resolve():
                raise RuntimeError(f"Contained toolchain discovery requires the native system shell: {name}")
            paths.update(_binary_dependencies(tool))
            probes.append(shlex.join([str(original), "-c", ":"]))
            continue
        if family not in ("git", "node", "go", "python", "python3") and not family.startswith("python3."):
            raise RuntimeError(f"Contained toolchain discovery does not support {name}; no model launched")
        paths.update(_binary_dependencies(tool))
        if family.startswith("python"):
            tool = _python_executable(tool)
            paths.update(_binary_dependencies(tool))
        if family == "go":
            install = tool.parent.parent
            if not (install / "src/runtime").is_dir() or not (install / "pkg/tool").is_dir():
                raise RuntimeError(f"Cannot identify the selected Go toolchain: {name}")
            paths.add(install)
            probes.append(shlex.join([str(original), "version"]))
        elif family.startswith("python"):
            # Preserve a virtualenv's lexical root rather than its interpreter
            # symlink, so imports use the selected environment's site-packages.
            install = original.parent.parent
            if (install / "pyvenv.cfg").is_file():
                paths.add(install)
            stdlibs = []
            for ancestor in tool.parents:
                candidates = list((ancestor / "lib").glob("python*/encodings/__init__.py"))
                if candidates:
                    stdlibs = [item.parent.parent.resolve() for item in candidates]
                    break
            if not stdlibs:
                raise RuntimeError(f"Cannot identify the selected Python standard library: {name}")
            paths.update(stdlibs)
            for stdlib in stdlibs:
                for extension in sorted((stdlib / "lib-dynload").glob("*.so")):
                    paths.update(_binary_dependencies(extension))
            probes.append(
                shlex.join(
                    [
                        str(original),
                        "-I",
                        "-S",
                        "-B",
                        "-c",
                        "import sys, json, ssl, sqlite3, ctypes, hashlib; print(sys.version)",
                    ]
                )
            )
        elif family == "node":
            probes.append(shlex.join([str(original), "-e", "console.log(process.version)"]))
        else:
            probes.append(shlex.join([str(original), "status", "--short"]))
    # The workspace already permits reads; adding it as an external runtime
    # would also protect it from Builder writes under the containment policy.
    return {
        "read_roots": [str(p) for p in sorted(paths) if not p.is_relative_to(root)],
        "probes": probes,
        "links": [hop for hop in links if not Path(hop[0]).is_relative_to(root)],
    }
