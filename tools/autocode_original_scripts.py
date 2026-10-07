"""The project suite run as originally defined (#528).

An npm, Yarn, pnpm or Bun suite runs whatever each tree's own ``package.json``
and package-manager configuration say, so the same suite command can run a
narrower suite on the candidate than on base. When that suite reports no
per-test results, its green exit code shows nothing about the old tests. So
when the candidate changed those definitions, the verifier runs the suite once
more on the candidate's code with the original definitions put back, together
with the original versions of existing test files the candidate changed, and
that run decides.

Which files count is decided by name, wherever they are, so wrapped commands,
``cd web && npm test``, workspaces and scripts the test script reaches need no
command parsing. A file under a test path is test data or test setup, never a
definition to merge.

This module imports nothing that imports the verifier: it decides which files
are definitions, computes what the extra run's tree receives, and judges that
run. The verifier builds the tree and runs the suite.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
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
LABEL = "suite_with_original_definitions"


def is_definition(path, is_test_path) -> bool:
    name = PurePosixPath(path).name
    return (name == MANIFEST or name in CONFIGURATION) and not is_test_path(path)


def _manifest(text) -> dict:
    value = json.loads(text.removeprefix("\ufeff"))  # npm reads a manifest with a byte order mark
    if not isinstance(value, dict):
        raise ValueError("package.json is not an object")
    return value


def restored(path, original, candidate):
    """What ``path`` holds in the proof tree: text, or None for no file.

    ``original`` is the file on base (with any reviewed patch), ``candidate`` the
    candidate's; None means the file does not exist. A deleted file comes back and
    added configuration goes. An added package.json stays: it is a new package, and
    a workspace member without its own scripts would fail ``npm test --workspaces``.
    A changed one keeps the candidate's KEPT_FIELDS and takes every other field from
    base; one that cannot be read as a JSON object is put back whole.
    """
    if candidate is None or PurePosixPath(path).name != MANIFEST:
        return original
    if original is None:
        return candidate
    try:
        before, after = _manifest(original), _manifest(candidate)
    except ValueError:
        return original
    merged = {key: value if key in KEPT_FIELDS else before[key]
              for key, value in after.items() if key in KEPT_FIELDS or key in before}
    merged.update((key, value) for key, value in before.items() if key not in KEPT_FIELDS and key not in merged)
    return candidate if merged == after else json.dumps(merged, indent=2, ensure_ascii=False) + "\n"


def plan(pairs) -> dict:
    """{path: restored text or None} for each ``path: (original, candidate)`` that restoring changes."""
    changed = {}
    for path, (original, candidate) in sorted(pairs.items()):
        value = restored(path, original, candidate)
        if value != candidate:
            changed[path] = value
    return changed


def _git(repo, *args, env=None):
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, env=env)
    if result.returncode:
        raise ValueError(f"git {args[0]} failed: {result.stderr.decode(errors='replace').strip()[-300:]}")
    return result.stdout


def _entries(repo, env, treeish, paths) -> dict:
    """{path: (mode, blob)} for each of ``paths`` that ``treeish`` has."""
    if not paths:
        return {}
    listing = _git(repo, "ls-tree", "-r", "-z", treeish, "--", *sorted(paths), env=env)
    entries = {}
    for record in filter(None, listing.split(b"\0")):
        metadata, _, path = record.decode(errors="replace").partition("\t")
        if path in paths:
            mode, _kind, blob = metadata.split()[:3]
            entries[path] = (mode, blob)
    return entries


def _blob(repo, env, path, entry):
    if entry is None:
        return None
    mode, blob = entry
    if mode not in ("100644", "100755"):
        raise ValueError(f"{path} is not a regular file in the original code")
    return _git(repo, "cat-file", "blob", blob, env=env)


def _text(data):
    return None if data is None else data.decode(errors="replace")


def originals(repo, base, patch, changes, is_test_path):
    """The original side of the proof run, read from git: ``(definitions, tests)``.

    ``definitions`` is ``{path: (original, candidate)}`` text for every package definition
    the candidate or the reviewed patch changed; ``plan`` decides what to restore. When
    there is any, ``tests`` is ``{path: (bytes, mode)}``: the original of every existing
    test file whose content the candidate changed or deleted, so the run is the original
    suite, as the protected-test gate runs it (docs/protected-tests.md), and an edited old
    assertion cannot pass in place of the original one. Added tests stay: new coverage
    runs alongside.

    The original is ``base`` with ``patch`` applied, the code a follow-up is proven
    against, so a follow-up that undoes the patch's change is included although its
    file matches ``base``. The patch is applied in a private index and object
    directory, so nothing is written into the repository. A changed file's candidate
    content is read from ``repo``'s working tree, which the candidate tree is built
    from; any other file is as ``base`` has it.
    """
    if not patch and not any(is_definition(path, is_test_path) for path in changes):
        return {}, {}
    touched = set(changes)
    with tempfile.TemporaryDirectory(prefix="original-definitions-") as scratch:
        objects = Path(repo, _git(repo, "rev-parse", "--git-path", "objects").decode().strip()).resolve()
        # Paths are matched literally; a private index and object directory keep the repository unwritten.
        env = {**{key: value for key, value in os.environ.items() if not key.endswith("_PATHSPECS")},
               "GIT_LITERAL_PATHSPECS": "1", "GIT_INDEX_FILE": str(Path(scratch) / "index"),
               "GIT_OBJECT_DIRECTORY": str(Path(scratch) / "objects"), "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(objects)}
        (Path(scratch) / "objects").mkdir()
        tree = base
        if patch:
            _git(repo, "read-tree", base, env=env)
            _git(repo, "apply", "--cached", str(patch), env=env)
            tree = _git(repo, "write-tree", env=env).decode().strip()
            listing = _git(repo, "diff-tree", "-r", "-z", "--name-only", "--no-renames", base, tree, env=env)
            touched |= set(filter(None, listing.decode(errors="replace").split("\0")))
        definitions = {path for path in touched if is_definition(path, is_test_path)}
        if not definitions:
            return {}, {}
        tests = {path for path in touched if is_test_path(path)}
        original = _entries(repo, env, tree, definitions | tests)
        committed = _entries(repo, env, base, (definitions | tests) - set(changes))

        def candidate(path):
            if path not in changes:
                return _blob(repo, env, path, committed.get(path))
            try:
                return None if changes[path] == "deleted" else (Path(repo) / path).read_bytes()
            except OSError:
                return None  # a dangling link: the package manager finds no file there either

        pairs = {path: (_text(_blob(repo, env, path, original.get(path))), _text(candidate(path)))
                 for path in sorted(definitions)}
        restored_tests = {}
        for path in sorted(tests):
            data = _blob(repo, env, path, original.get(path))
            if data is not None and data != candidate(path):
                restored_tests[path] = (data, 0o755 if original[path][0] == "100755" else 0o644)
    return pairs, restored_tests


def install(tree, definitions, tests, staging):
    """Write the restored files into ``tree`` through the scratch overlay, never through a link.

    ``definitions`` is ``plan``'s, ``tests`` is ``originals``'. The files are kept under
    ``staging`` as evidence of what the run used."""
    staging = Path(staging)
    files = {}
    for path, (data, mode) in {**{path: (text.encode(), 0o644) for path, text in definitions.items()
                                   if text is not None}, **tests}.items():
        source = staging / path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(data)
        source.chmod(mode)
        files[path] = source
    scratch_overlay.apply(tree, files, None, removed=[path for path, text in definitions.items() if text is None])


def decided_by_exit_code(on_candidate, base_suite) -> bool:
    """The candidate's suite passed and no per-test comparison with the base run decided it."""
    base_receipt = (base_suite or {}).get("receipt") or {}
    return bool(base_suite and on_candidate["exit_code"] == 0 and not on_candidate["timed_out"]
                and command_receipt.completed(on_candidate) and command_receipt.completed(base_receipt)
                and (on_candidate.get("results") is None or base_receipt.get("results") is None))


def _restored(receipt):
    tests = receipt.get("restored_tests") or []
    return ", ".join(receipt.get("restored") or []) + (
        f"; the candidate's changes to {len(tests)} existing test file(s) were undone too" if tests else "")


def judge(receipt, base_suite, fail, unverified, notes):
    """Judge the suite on the candidate's code under the original definitions, like an unchanged suite."""
    restored = _restored(receipt)
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
    if receipt["exit_code"] == 0:
        if receipt.get("results_expected") and receipt.get("results") is None:
            unverified.append("The project suite as originally defined exited 0 on the candidate without "
                              "reporting any test result (did the process exit early?)")
        else:
            notes.append(f"The candidate changed what the project suite runs; the suite as originally defined "
                         f"also passes on the candidate (restored: {restored})")
        return
    if ((base_suite or {}).get("receipt") or {}).get("exit_code") == 0:
        fail.append(f"The project suite as originally defined passes on base but fails on the candidate "
                    f"(restored: {restored}). Changing what the suite runs does not prove the old behavior: "
                    "keep the existing tests passing on the new code under the original definitions")
    else:
        unverified.append(f"The project suite as originally defined fails on the candidate and did not pass on "
                          f"base either, so new failures cannot be ruled out (restored: {restored})")
