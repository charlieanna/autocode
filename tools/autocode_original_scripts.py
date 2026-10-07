"""The project suite run as originally defined (#528).

An npm, Yarn, pnpm or Bun suite runs whatever each tree's own ``package.json``
and package-manager configuration say, so the same suite command can run a
narrower suite on the candidate than on base. When that suite reports no
per-test results, its green exit code shows nothing about the old tests. So
when the candidate changed those definitions, the verifier runs the suite once
more on the candidate's code with the original definitions and the original
tests put back, and that run decides.

Which files count is decided by name, or by a definition-named link that points
at them, wherever they are, so wrapped commands, ``cd web && npm test``,
workspaces and scripts the test script reaches need no command parsing.

The run's tree is built outside the workspace (``outside``): Yarn 1 reads
``.npmrc`` and ``.yarnrc`` from every parent folder and pnpm takes its settings
from the nearest parent ``pnpm-workspace.yaml``, so in a tree inside the
candidate's checkout the candidate's own configuration would still apply.

This module imports nothing that imports the verifier: it decides which files
are definitions, computes what the extra run's tree receives, and judges that
run. The verifier builds the tree and runs the suite.
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import subprocess
import tempfile

try:
    from . import autocode_command_receipt as command_receipt, autocode_scratch_overlay as scratch_overlay
except ImportError:
    import autocode_command_receipt as command_receipt
    import autocode_scratch_overlay as scratch_overlay

MANIFEST = "package.json"
# Package-manager and monorepo task-runner configuration. None of it is code the product loads,
# so restoring it cannot stop the candidate's code from loading. .npmrc can replace the script
# shell or add node options; .pnpmfile hooks run on every pnpm command; pnpm-workspace.yaml lists
# the packages `pnpm -r` runs; turbo, nx and lerna decide which package scripts run and when a
# cached result (kept in the linked node_modules) is replayed instead of running them.
CONFIGURATION = frozenset({".npmrc", ".yarnrc", ".yarnrc.yml", ".pnpmfile.cjs", ".pnpmfile.mjs",
                           "pnpm-workspace.yaml", "bunfig.toml", "turbo.json", "nx.json", "lerna.json"})
# Yarn Berry runs the release .yarnrc.yml names and loads the plugins it lists, so they come back
# with it (`yarn set version` swaps both).
YARN_FILES = frozenset({"releases", "plugins"})
# package.json fields that say what the package's code is, how Node, bundlers and transpilers load
# it, what it installs, and its descriptive metadata. The candidate keeps these, so its code still
# loads. Every other field is put back as base defines it: scripts, config, workspaces, wireit,
# packageManager, name (it selects workspaces), test-runner keys such as jest, mocha, ava, c8, nyc
# and tap, and any custom field a runner script might read.
KEPT_FIELDS = frozenset({
    "type", "main", "module", "browser", "exports", "imports", "bin", "man", "types", "typings",
    "typesVersions", "sideEffects", "babel", "browserslist",
    "dependencies", "devDependencies", "peerDependencies", "peerDependenciesMeta", "optionalDependencies",
    "bundleDependencies", "bundledDependencies", "dependenciesMeta", "overrides", "resolutions",
    "engines", "os", "cpu", "libc",
    "version", "description", "keywords", "homepage", "bugs", "license", "author", "contributors",
    "maintainers", "funding", "repository", "private", "publishConfig",
})
# Test-runner configuration files beside a package.json, which a test script reads without naming.
RUNNER_CONFIGURATION = ("jest.config.", "vitest.config.", "vitest.workspace.", ".mocharc", "ava.config.", ".c8rc",
                        ".nycrc", "nyc.config.", ".taprc", "karma.conf.")
LABEL = "suite_with_original_definitions"


def is_definition(path) -> bool:
    """package.json, package-manager or task-runner configuration, or a Yarn release or plugin."""
    parts = PurePosixPath(path).parts
    return bool(parts) and (parts[-1] == MANIFEST or parts[-1] in CONFIGURATION
                            or any(a == ".yarn" and b in YARN_FILES for a, b in zip(parts, parts[1:-1])))


def _manifest(data) -> dict:
    value = json.loads(data.decode("utf-8-sig"))  # npm reads a manifest with a byte order mark
    if not isinstance(value, dict):
        raise ValueError("package.json is not an object")
    return value


def restored(path, original, candidate, kind=None):
    """What ``path`` holds in the proof tree: bytes, or None for no file.

    ``original`` is the file on base (with any reviewed patch), ``candidate`` the
    candidate's; None means the file does not exist. ``kind`` is the name that sets the
    rule when it is not the path's own (a file a definition link points at).
    Configuration returns whole: a deleted file comes back and an added one goes. A
    changed package.json keeps the candidate's KEPT_FIELDS and takes every other field
    from base; one that cannot be read as a JSON object is put back whole. An added one
    stays only when it holds nothing but KEPT_FIELDS (a nested ``{"type": "module"}``):
    otherwise it is a package the original suite never ran, and without it the folder
    drops out of npm and pnpm workspaces and npm finds the parent's package.json, as on
    base.
    """
    if candidate is None or (kind or PurePosixPath(path).name) != MANIFEST:
        return original
    try:
        after = _manifest(candidate)
        before = None if original is None else _manifest(original)
    except ValueError:
        return original
    if before is None:
        return candidate if after.keys() <= KEPT_FIELDS else None
    merged = {key: value if key in KEPT_FIELDS else before[key]
              for key, value in after.items() if key in KEPT_FIELDS or key in before}
    merged.update((key, value) for key, value in before.items() if key not in KEPT_FIELDS and key not in merged)
    return candidate if merged == after else (json.dumps(merged, indent=2, ensure_ascii=False) + "\n").encode()


def plan(pairs, kinds=None) -> dict:
    """{path: restored bytes or None} for each ``path: (original, candidate)`` that restoring changes."""
    changed = {}
    for path, (original, candidate) in sorted(pairs.items()):
        value = restored(path, original, candidate, (kinds or {}).get(path))
        if value != candidate:
            changed[path] = value
    return changed


def _scripts(data) -> dict:
    try:
        scripts = None if data is None else _manifest(data).get("scripts")
    except ValueError:
        return {}
    return {name: text for name, text in scripts.items() if isinstance(text, str)} if isinstance(scripts, dict) else {}


def _names(script, path) -> bool:
    return re.search(rf"(?<![\w./-])(?:\./)?{re.escape(path)}(?![\w./-])", script) is not None


def co_changed(pairs, changed) -> list:
    """``[(path, manifest, script)]``: a file the candidate changed along with an original test script that
    runs or configures it.

    The proof run puts the scripts back and keeps the candidate's other files. A test script
    (its name says test, so pre and post hooks count) names the files it runs, such as
    ``node run-tests.js``, and its runner reads configuration beside the package.json
    (RUNNER_CONFIGURATION). When the candidate changed both such a script and such a file,
    the run would pair the original script with the new file, which existed on neither
    side, so it proves nothing either way. ``changed`` holds the candidate's other changed
    files, deleted ones included."""
    found = []
    for manifest, (original, candidate) in sorted(pairs.items()):
        if PurePosixPath(manifest).name != MANIFEST:
            continue
        folder = posixpath.dirname(manifest) or "."
        after = _scripts(candidate)
        for name, text in sorted(_scripts(original).items()):
            if "test" in name.lower() and after.get(name) != text:
                found += [(path, manifest, name) for path in sorted(changed)
                          if _names(text, posixpath.relpath(path, folder))
                          or ((posixpath.dirname(path) or ".") == folder
                              and PurePosixPath(path).name.startswith(RUNNER_CONFIGURATION))]
    return found


def _git(repo, *args, env=None):
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, env=env)
    if result.returncode:
        raise ValueError(f"git {args[0]} failed: {result.stderr.decode(errors='replace').strip()[-300:]}")
    return result.stdout


def _listing(repo, env, treeish, paths=None) -> dict:
    """{path: (mode, blob)} for every entry of ``treeish``, or for each of ``paths`` it has."""
    if paths is not None and not paths:
        return {}
    listing = _git(repo, "ls-tree", "-r", "-z", treeish, *(["--", *sorted(paths)] if paths else []), env=env)
    entries = {}
    for record in filter(None, listing.split(b"\0")):
        metadata, _, path = record.decode(errors="replace").partition("\t")
        if paths is None or path in paths:
            mode, _kind, blob = metadata.split()[:3]
            entries[path] = (mode, blob)
    return entries


def _state(repo, env, path, entry):
    """A Git entry as a tree holds it: ("file", bytes, mode), ("link", target) or None."""
    if entry is None:
        return None
    mode, blob = entry
    if mode not in ("100644", "100755", "120000"):
        raise ValueError(f"{path} is not a file or a link in the original code")
    data = _git(repo, "cat-file", "blob", blob, env=env)
    return ("link", data.decode(errors="replace")) if mode == "120000" else (
        "file", data, 0o755 if mode == "100755" else 0o644)


def _on_disk(path):
    if path.is_symlink():
        return ("link", os.readlink(path))
    if path.is_file():
        return ("file", path.read_bytes(), 0o755 if path.stat().st_mode & 0o100 else 0o644)
    return None


def prepare(repo, base, patch, changes, is_test_path):
    """What the proof run's tree receives, read from Git; None when restoring undoes no definition.

    Every package definition the candidate or the reviewed patch changed is restored by
    ``plan`` (a deleted package.json only while the candidate keeps files in its folder:
    a removed package is not a narrowed one). Every test file is as the original has it,
    so the run is the original suite: existing tests the candidate changed come back, as
    the protected-test gate runs them (docs/protected-tests.md), and added tests are left
    out; they already ran in the candidate's own suite, and one that needs a changed
    definition or helper would fail here for that reason alone. A definition under a test
    path returns whole, like any test file.

    Returns ``files`` ({path: (bytes, mode)}), ``links`` ({path: target}) and ``removed``
    for ``install``, and ``definitions``, ``tests``, ``left_out`` and ``co_changed``
    (see that function) for the verdict.

    The original is ``base`` with ``patch`` applied, the code a follow-up is proven
    against, so a follow-up that undoes the patch's change is included although its
    file matches ``base``. ``base`` is the run's base commit, never HEAD: for an
    in-place run it holds the files uncommitted or untracked at launch
    (verify.commit_worktree), and ``changes`` is measured against it. The patch is
    applied in a private index and object directory, so nothing is written into the
    repository. A changed file's candidate content is read from ``repo``'s working
    tree, which the candidate tree is built from; any other file is as ``base`` has it.
    """
    touched = set(changes)
    with tempfile.TemporaryDirectory(prefix="original-definitions-") as scratch:
        objects = Path(repo, _git(repo, "rev-parse", "--git-path", "objects").decode().strip()).resolve()
        # Paths are matched literally; a private index and object directory keep the repository unwritten.
        env = {**{key: value for key, value in os.environ.items() if not key.endswith("_PATHSPECS")},
               "GIT_LITERAL_PATHSPECS": "1", "GIT_INDEX_FILE": str(Path(scratch) / "index"),
               "GIT_OBJECT_DIRECTORY": str(Path(scratch) / "objects"), "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(objects)}
        (Path(scratch) / "objects").mkdir()
        tree, patched = base, set()
        if patch:
            _git(repo, "read-tree", base, env=env)
            _git(repo, "apply", "--cached", str(patch), env=env)
            tree = _git(repo, "write-tree", env=env).decode().strip()
            listing = _git(repo, "diff-tree", "-r", "-z", "--name-only", "--no-renames", base, tree, env=env)
            patched = set(filter(None, listing.decode(errors="replace").split("\0")))
            touched |= patched
        original = _listing(repo, env, tree)
        kinds = {}  # a changed file that a definition link points at: the link's name
        for path, (mode, blob) in original.items():
            if mode == "120000" and is_definition(path):
                target = posixpath.normpath(posixpath.join(posixpath.dirname(path), _git(
                    repo, "cat-file", "blob", blob, env=env).decode(errors="replace")))
                if target in touched and not is_definition(target):
                    kinds[target] = PurePosixPath(path).name
        definitions = {path for path in touched if is_definition(path) or path in kinds}
        if not definitions:
            return None
        tests = {path for path in touched if is_test_path(path)}
        committed = _listing(repo, env, base, touched - set(changes))

        def candidate(path):
            if path not in changes:
                return _state(repo, env, path, committed.get(path))
            return None if changes[path] == "deleted" else _on_disk(Path(repo) / path)

        def emptied(folder):
            if folder == ".":
                return False
            names = _git(repo, "ls-tree", "-r", "-z", "--name-only", base, "--", folder + "/", env=env)
            kept = [name for name in filter(None, names.decode(errors="replace").split("\0"))
                    if changes.get(name) != "deleted"]
            return not kept and not any(path.startswith(folder + "/") and status != "deleted"
                                        for path, status in changes.items())

        pairs, modes = {}, {}
        for path in sorted(definitions - tests):
            before = _state(repo, env, path, original.get(path))
            if before and before[0] != "file":
                raise ValueError(f"{path} is not a regular file in the original code")
            if path in changes:
                try:  # through a link, as the package manager reads it
                    after = None if changes[path] == "deleted" else (Path(repo) / path).read_bytes()
                except OSError:
                    after = None  # a dangling link: the package manager finds no file there either
            else:
                after = candidate(path)
                if after and after[0] != "file":
                    raise ValueError(f"{path} is not a regular file in the base code")
                after = after and after[1]
            if (before and after is None and (kinds.get(path) or PurePosixPath(path).name) == MANIFEST
                    and emptied(posixpath.dirname(path) or ".")):
                continue
            pairs[path] = (before and before[1], after)
            modes[path] = before[2] if before else 0o644
        restore = plan(pairs, kinds)
        if not restore and not definitions & tests:
            return None
        files = {path: (data, modes[path]) for path, data in restore.items() if data is not None}
        links, removed, put_back, left_out = {}, [path for path, data in restore.items() if data is None], [], []
        for path in sorted(tests):
            before = _state(repo, env, path, original.get(path))
            if before == candidate(path):
                continue
            if before is None:
                removed.append(path)
                left_out.append(path)
                continue
            put_back.append(path)
            if before[0] == "link":
                links[path] = before[1]
            else:
                files[path] = before[1:]
        restored_definitions = sorted(set(restore) | {path for path in put_back + left_out if is_definition(path)})
        if not restored_definitions:
            return None
        others = touched - definitions - tests
        changed = {path for path in others
                   if not (path in changes and path in patched) or candidate(path) != _state(
                       repo, env, path, original.get(path))}
    return {"files": files, "links": links, "removed": sorted(removed), "definitions": restored_definitions,
            "tests": put_back, "left_out": left_out, "co_changed": co_changed(pairs, changed)}


def summary(prepared) -> dict:
    """The receipt fields that say what the proof run restored."""
    return {"restored": prepared["definitions"], "restored_tests": prepared["tests"],
            "left_out_tests": prepared["left_out"], "co_changed": prepared["co_changed"]}


@contextlib.contextmanager
def outside(workspace):
    """A temporary folder for the proof tree, outside ``workspace`` (module docstring), removed afterwards."""
    holder = Path(tempfile.mkdtemp(prefix="autocode-original-definitions-")).resolve()
    try:
        if holder.is_relative_to(Path(workspace).resolve()):
            raise ValueError("the temporary folder is inside the workspace, where the candidate's package "
                             "configuration would still apply")
        yield holder
    finally:
        shutil.rmtree(holder, ignore_errors=True)


def install(tree, prepared, staging):
    """Write ``prepare``'s files and links into ``tree`` through the scratch overlay, never through a link.

    The files are kept under ``staging`` as evidence of what the run used."""
    staging = Path(staging)
    shutil.rmtree(staging, ignore_errors=True)
    files = {}
    for path, (data, mode) in prepared["files"].items():
        source = staging / path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(data)
        source.chmod(mode)
        files[path] = source
    # The overlay clears each link's place and makes its folders real directories in the tree first.
    scratch_overlay.apply(tree, files, None, removed=[*prepared["removed"], *prepared["links"]])
    for path, target in prepared["links"].items():
        (Path(tree) / path).symlink_to(target)


def decided_by_exit_code(on_candidate, base_suite) -> bool:
    """The candidate's suite passed and no per-test comparison with the base run decided it."""
    base_receipt = (base_suite or {}).get("receipt") or {}
    return bool(base_suite and on_candidate["exit_code"] == 0 and not on_candidate["timed_out"]
                and command_receipt.completed(on_candidate) and command_receipt.completed(base_receipt)
                and (on_candidate.get("results") is None or base_receipt.get("results") is None))


def _restored(receipt):
    tests, left_out = receipt.get("restored_tests") or [], receipt.get("left_out_tests") or []
    return ", ".join(receipt.get("restored") or []) + (
        f"; the candidate's changes to {len(tests)} existing test file(s) were undone too" if tests else "") + (
        f"; its {len(left_out)} added test file(s) were left out" if left_out else "")


def judge(receipt, base_suite, fail, unverified, notes, *, compare=None):
    """Judge the suite on the candidate's code under the original definitions, like an unchanged suite.

    ``compare(receipt, fail, unverified, notes)`` is the verifier's per-test comparison with
    the base run, used when both runs report per-test results."""
    restored = _restored(receipt)
    if receipt.get("co_changed"):
        pairs = "; ".join(f'{path} with the "{script}" script in {manifest}'
                          for path, manifest, script in receipt["co_changed"][:5])
        unverified.append(f"The candidate changed a package test script together with a file it runs or the "
                          f"runner configuration beside it ({pairs}), so the suite as originally defined cannot "
                          "be rerun on the new code and preservation of existing behavior is unproven. Leave "
                          "the script or that file as it was")
        return
    if receipt.get("error"):
        unverified.append("The candidate changed its package definitions, and the project suite could not be run "
                          f"as originally defined ({receipt['error']}); preservation of existing behavior is "
                          "unproven")
        return
    if receipt["timed_out"]:
        unverified.append(f"The project suite as originally defined timed out on the candidate (restored: "
                          f"{restored}); preservation of existing behavior is unproven")
        return
    if not command_receipt.completed(receipt):
        unverified.append("Command ownership was interrupted while running the project suite as originally "
                          "defined; preservation is unproven")
        return
    passed = (f"The candidate changed what the project suite runs; the suite as originally defined, with the "
              f"original tests, also passes on the candidate (restored: {restored})")
    base_receipt = (base_suite or {}).get("receipt") or {}
    if compare and receipt.get("results") is not None and base_receipt.get("results") is not None:
        failures, open_reasons = [], []
        compare(receipt, failures, open_reasons, [])
        fail.extend(f"The project suite as originally defined (restored: {restored}): {reason}" for reason in failures)
        unverified.extend(f"The project suite as originally defined: {reason}" for reason in open_reasons)
        if not failures and not open_reasons:
            notes.append(passed)
        return
    if receipt["exit_code"] == 0:
        if receipt.get("results_expected") and receipt.get("results") is None:
            unverified.append("The project suite as originally defined exited 0 on the candidate without "
                              "reporting any test result (did the process exit early?)")
        else:
            notes.append(passed)
        return
    if base_receipt.get("exit_code") == 0:
        fail.append(f"The project suite as originally defined passes on base but fails on the candidate "
                    f"(restored: {restored}). Changing what the suite runs does not prove the old behavior: the "
                    "original package scripts, configuration and tests must pass on the new code, so keep the "
                    "files those scripts run and keep the code working without the changed package.json "
                    "scripts, config or test-runner settings")
    else:
        unverified.append(f"The project suite as originally defined fails on the candidate and did not pass on "
                          f"base either, so new failures cannot be ruled out (restored: {restored})")
