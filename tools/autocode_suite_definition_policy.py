"""Package-script reachability and product loading fields for suite proofs.

Pure policy: the verifier supplies the effective base's manifests and shell
bindings. Unrelated application scripts are not roots of the test definition.
"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, field


@dataclass
class Definition:
    paths: set[str]
    boundary: str
    patched: set[str]
    manifests: dict[str, str] = field(default_factory=dict)
    changed: bool = True


# Only fields defining how product modules load. Scripts, lifecycle hooks,
# workspaces, config, files and arbitrary runner metadata stay pinned to base.
LOADING_FIELDS = frozenset({"imports", "exports", "type", "main", "module"})


def merged_manifest(original, candidate):
    """Retain base test policy while letting the candidate's modules load.

    A missing, invalid or linked candidate is handled by the caller as unchanged.
    New manifests may supply loading fields alone; a new workspace's scripts do
    not become part of the old suite.
    """
    try:
        before = json.loads(original) if original is not None else None
        after = json.loads(candidate)
    except (ValueError, TypeError):
        return original
    if not isinstance(after, dict) or (before is not None and not isinstance(before, dict)):
        return original
    if before is None:
        return candidate if after.keys() <= LOADING_FIELDS else None
    merged = {key: value for key, value in before.items() if key not in LOADING_FIELDS}
    merged.update((key, value) for key, value in after.items() if key in LOADING_FIELDS)
    return original if merged == before else json.dumps(merged, ensure_ascii=False) + "\n"


def script_roots(bindings, manifests):
    """Yield (manifest path, script name, body) for invoked scripts and hooks.

    Bindings are (shell word, effective directory), including words in sh -c
    and after cd. A manager may be wrapped in env/timeout. Workspace selectors
    conservatively include that script in every package, never unrelated script
    names. Unknown manager flags retain all scripts in the selected packages.
    """
    for index, (word, directory) in enumerate(bindings):
        manager = posixpath.basename(word).removesuffix(".cmd")
        if manager not in {"npm", "yarn", "pnpm", "bun", "node"}:
            continue
        args = []
        for argument, cwd in bindings[index + 1 :]:
            if cwd != directory or argument in {"&&", ";", "||", "|", "&"}:
                break
            args.append(argument)
        if manager == "node":
            if "--run" not in args and not any(arg.startswith("--run=") for arg in args):
                continue
            option = next(arg for arg in args if arg == "--run" or arg.startswith("--run="))
            name = option.partition("=")[2] or (args[args.index(option) + 1 : args.index(option) + 2] or [""])[0]
            recursive, unknown = False, not name or any(mark in name for mark in "$`*")
        else:
            recursive, unknown, positional = False, False, []
            offset = 0
            while offset < len(args):
                arg = args[offset]
                if arg == "--":
                    break
                if arg in {"--prefix", "--dir", "-C", "--cwd"} or arg.startswith(("--prefix=", "--dir=", "--cwd=")):
                    target = arg.partition("=")[2]
                    if "=" not in arg:
                        offset += 1
                        target = args[offset] if offset < len(args) else ""
                    if not target or target.startswith("/") or any(mark in target for mark in "$`*"):
                        raise ValueError("the package-manager directory is not a literal in-tree path")
                    directory = posixpath.normpath(posixpath.join(directory, target))
                    if directory == ".." or directory.startswith("../"):
                        raise ValueError("the package-manager directory is outside the base tree")
                elif arg in {"-w", "--workspace", "--filter"}:
                    recursive = True
                    offset += 1
                elif arg in {"-r", "--recursive", "-ws", "--workspaces"} or arg.startswith(
                    ("--workspace=", "--filter=")
                ):
                    recursive = True
                elif arg.startswith("-"):
                    unknown |= arg not in {"-s", "--silent", "--if-present", "--ignore-scripts"}
                else:
                    if positional and positional[-1] not in {"run", "run-script"}:
                        break  # script arguments, or the next shell command
                    positional.append(arg)
                offset += 1
            name = positional[-1] if positional else ""
            if name in {"exec", "install", "ci", "add", "dlx", "x"}:
                continue  # not a package script
            if name == "t":
                name = "test"
            if name in {"run", "run-script"} or any(mark in name for mark in "$`*"):
                unknown = True
            if name in {"workspace", "workspaces"}:
                unknown = True
            if unknown:
                recursive = True  # an unparsed selector may name another package
        selected = list(manifests) if recursive else []
        if not recursive:
            current = directory
            while True:
                path = posixpath.normpath(posixpath.join(current, "package.json"))
                if path in manifests:
                    selected = [path]
                    break
                if current in {"", "."}:
                    break
                current = posixpath.dirname(current) or "."
        for path in selected:
            try:
                package = json.loads(manifests[path])
                scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
            except ValueError:
                scripts = {}
            if not isinstance(scripts, dict):
                continue
            names = sorted(scripts) if unknown or not name else ["pre" + name, name, "post" + name]
            for selected_name in names:
                if selected_name in scripts:
                    yield path, selected_name, str(scripts[selected_name])
