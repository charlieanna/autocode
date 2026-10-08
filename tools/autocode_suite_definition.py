"""The base suite definition an exit-code-only suite proof runs over the candidate (#587, #652).

npm, Yarn, pnpm, Bun and shell runners run whatever each tree's own definition says, so one
suite command can run fewer tests on the candidate than on base. When the suite reports no
per-test results, autocode_verify runs the base's definition once more over the candidate's
product code. This module decides what that extra run's tree receives. It reads the effective
base, ``base`` with the reviewed ``base_patch``, through Git plumbing and never writes to the
repository:

* ``definition``: the paths the extra run takes from the effective base. Package manifests,
  lockfiles, package-manager, task-runner and test-runner configuration and Yarn releases and
  plugins, wherever they are (``is_definition_file``), and what a definition-named link points
  at; every test file; and the runner files the package scripts and the suite command reach
  through path literals, present or absent. It also names the product code (what the base
  tests import) and why the runner closure could not be established, if it could not.
* ``plan``: the candidate's files on top of that, measured against the effective base, so a
  file a follow-up put back as base had it is the candidate's too; each changed package.json
  merged field by field (``merged_manifest``); and the changed paths that are neither tests,
  product code nor definition files, which only an established closure can place.
* ``scratch_folder``: a folder outside the workspace for the tree. Yarn 1 reads .yarnrc and
  .npmrc from every parent folder and pnpm takes the nearest parent pnpm-workspace.yaml, so a
  tree inside the candidate's checkout would still get the candidate's configuration.

It imports nothing that imports the verifier: the verifier passes its test-path rule, builds
the tree and runs the suite.
"""
from __future__ import annotations

import contextlib
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

try:
    from . import autocode_scratch_overlay as scratch_overlay
except ImportError:
    import autocode_scratch_overlay as scratch_overlay

MANIFEST = "package.json"
# Manifests, lockfiles, and package-manager and monorepo task-runner configuration: .npmrc and
# .yarnrc can replace the script shell or add node options, .pnpmfile hooks run on every pnpm
# command, pnpm-workspace.yaml lists the packages `pnpm -r` runs, and turbo, nx and lerna decide
# which package scripts run and when a cached result is replayed instead.
DEFINITION_FILE_BASENAMES = frozenset({
    MANIFEST, "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "pnpm-workspace.yaml",
    ".npmrc", ".yarnrc", ".yarnrc.yml", ".pnpmfile.cjs", ".pnpmfile.mjs", "bunfig.toml",
    "turbo.json", "nx.json", "lerna.json"})
# Test-runner configuration a test script reads without naming it (its spec list, ignore patterns,
# coverage thresholds), matched by name prefix: .mocharc.json, jest.config.ts, .nycrc.yml.
RUNNER_CONFIGURATION = ("jest.config.", "vitest.config.", "vitest.workspace.", ".mocharc", "ava.config.", ".c8rc",
                        ".nycrc", "nyc.config.", ".taprc", "karma.conf.")
# Yarn Berry runs the release .yarnrc.yml names and loads the plugins it lists.
YARN_DIRECTORIES = frozenset({"releases", "plugins"})
# package.json fields that say what the package's code is, how Node, bundlers and transpilers load
# it, what it installs, and its descriptive metadata. The candidate keeps these in the extra run, so
# its code still loads (an added `imports` map or `exports` self-reference). Every other field is
# the base's: scripts, config, workspaces, files, name (it selects workspaces), test-runner keys
# such as jest, mocha, ava, c8, nyc and tap, and any custom field a runner script might read.
KEPT_FIELDS = frozenset({
    "type", "main", "module", "browser", "exports", "imports", "bin", "man", "types", "typings",
    "typesVersions", "sideEffects", "babel", "browserslist",
    "dependencies", "devDependencies", "peerDependencies", "peerDependenciesMeta", "optionalDependencies",
    "bundleDependencies", "bundledDependencies", "dependenciesMeta", "overrides", "resolutions",
    "engines", "os", "cpu", "libc",
    "version", "description", "keywords", "homepage", "bugs", "license", "author", "contributors",
    "maintainers", "funding", "repository", "private", "publishConfig",
})
# How Node and TypeScript complete a relative specifier without an extension.
MODULE_EXTENSIONS = (".js", ".cjs", ".mjs", ".json", ".node", ".jsx", ".ts", ".tsx", ".cts", ".mts")
SHELLS = frozenset({"sh", "bash", "dash", "zsh"})
LINK = "120000"
_FILE_MODES = frozenset({"100644", "100755", LINK})
_JS_MODULE_CALL = re.compile(r"\b(?:require|import)\s*\(")
_JS_EXEC_CALL = re.compile(r"\b(?:exec(?:Sync|File(?:Sync)?)?|spawn(?:Sync)?|fork)\s*\(")
_CODE_TOKEN = re.compile(r"//|/\*|[\"'`{}]")
_QUOTED = {quote: re.compile(quote + r"((?:[^" + quote + r"\\]|\\.)*)(?:" + quote + r"|\Z)", re.S) for quote in "'\""}
_TEMPLATE_CHUNK = re.compile(r"(?:[^`\\$]|\\.|\$(?!\{))*", re.S)
_ESCAPE = re.compile(r"\\(.)", re.S)


def is_definition_file(path) -> bool:
    """A suite-definition path wherever it appears (module docstring)."""
    parts = PurePosixPath(path).parts
    if not parts:
        return False
    return (parts[-1] in DEFINITION_FILE_BASENAMES or parts[-1].startswith(RUNNER_CONFIGURATION)
            or any(a == ".yarn" and b in YARN_DIRECTORIES for a, b in zip(parts, parts[1:-1])))


def package_scripts(text) -> dict:
    try:
        package = json.loads(text or "{}")
    except ValueError:
        return {}
    scripts = package.get("scripts") if isinstance(package, dict) else None
    return dict(scripts) if isinstance(scripts, dict) else {}


# --- reading JavaScript without running it ----------------------------------

def js_scan(text):
    """(mask, strings): ``mask`` blanks comments and quoted literals (same length), and
    ``strings`` lists each literal as (start, end, value), so call structure and literal
    arguments can be read without executing anything.

    A template literal without substitutions is a literal. One with ``${...}`` is computed:
    its text is blanked but its opening backtick is not, so a call taking it as an argument
    is never read as a literal, and the code inside each substitution stays visible."""
    mask, strings, n = list(text), [], len(text)
    depths = []  # open braces inside each enclosing template substitution

    def blank(start, end):
        mask[start:end] = " " * (end - start)

    def template(start):
        """Scan template text from ``start``: (index after it, closed, the cooked text)."""
        end = _TEMPLATE_CHUNK.match(text, start).end()
        value = _ESCAPE.sub(r"\1", text[start:end])
        if text.startswith("${", end):
            depths.append(0)
            return end + 2, False, value
        return min(end + 1, n), True, value  # the closing backtick, or an unterminated literal

    i = 0
    while True:
        token = _CODE_TOKEN.search(text, i)
        if token is None:
            break
        start, kind = token.start(), token.group()
        if kind == "//":
            end = text.find("\n", start)
            end = n if end < 0 else end
            blank(start, end)
        elif kind == "/*":
            end = text.find("*/", start + 2)
            end = n if end < 0 else end + 2
            blank(start, end)
        elif kind in "'\"":
            quoted = _QUOTED[kind].match(text, start)
            end = quoted.end()
            strings.append((start, end, _ESCAPE.sub(r"\1", quoted.group(1))))
            blank(start, end)
        elif kind == "`":
            end, closed, value = template(start + 1)
            if closed:
                strings.append((start, end, value))
                blank(start, end)
            else:
                blank(start + 1, end)
        elif kind == "{":
            end = start + 1
            if depths:
                depths[-1] += 1
        else:  # "}"
            end = start + 1
            if depths and depths[-1]:
                depths[-1] -= 1
            elif depths:
                depths.pop()  # the substitution ends; its template continues
                end = template(start + 1)[0]
                blank(start, end)
        i = end
    return "".join(mask), strings


def _argument_span(mask, open_paren):
    """(start, end) covering a call's parentheses; literals and comments never nest."""
    depth = 0
    for index in range(open_paren, len(mask)):
        if mask[index] == "(":
            depth += 1
        elif mask[index] == ")":
            depth -= 1
            if depth == 0:
                return open_paren, index
    return open_paren, len(mask)


def import_sites(text):
    """Every import/require site of JS source as (specifier, inside_exec_argument).

    ``specifier`` is the argument when it is one literal or a fold of literals joined by
    ``+``, and None when it is computed at runtime: a fail-closed boundary (#587). Static
    import and export-from specifiers are literals by grammar.
    """
    mask, strings = js_scan(text)
    by_start = {start: (end, value) for start, end, value in strings}
    exec_spans = [_argument_span(mask, match.end() - 1) for match in _JS_EXEC_CALL.finditer(mask)]
    sites = []
    for match in _JS_MODULE_CALL.finditer(mask):
        open_paren = match.end() - 1
        start, end = _argument_span(mask, open_paren)
        inner = [literal for literal in strings if start < literal[0] < end]
        covered = {index for literal in inner for index in range(literal[0], literal[1])}
        residue = "".join(text[index] for index in range(start + 1, end)
                          if index not in covered and not text[index].isspace() and mask[index] == text[index])
        specifier = "".join(literal[2] for literal in inner) if not residue.strip("+") else None
        sites.append((specifier, any(a <= open_paren < b for a, b in exec_spans)))
    for pattern in (r"\bfrom\s*(['\"])", r"(?m)^\s*import\s*(['\"])"):
        for match in re.finditer(pattern, text):
            quote = match.start(1)
            if quote in by_start:  # a real literal, not text inside a comment
                sites.append((by_start[quote][1], False))
    return sites


# --- the effective base ------------------------------------------------------

def _git(workspace, *args, env=None) -> str:
    result = subprocess.run(["git", "-C", str(workspace), *args], capture_output=True, env=env)
    if result.returncode:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.decode('utf-8', 'replace').strip()[-300:]}")
    return result.stdout.decode("utf-8", "replace")


@contextlib.contextmanager
def _effective_base(workspace, base, patch=None):
    """(entries, read, patched) for the tracked files of ``base`` with ``patch`` applied.

    This is the tree the baseline executed, so a runner or selector the patch introduces is
    part of the definition. ``entries`` maps each path to (mode, object); ``read(path)`` returns
    its bytes (a link's target text, nothing for a submodule), every file through one
    ``git cat-file --batch`` process; ``patched`` holds the paths the patch changed. The index
    and object store live in a scratch directory that vanishes afterwards.
    """
    workspace = Path(workspace)
    with tempfile.TemporaryDirectory(prefix="autocode-base-definition-") as scratch:
        objects = Path(workspace, _git(workspace, "rev-parse", "--git-path", "objects").strip()).resolve()
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch, "index")),
               "GIT_OBJECT_DIRECTORY": str(Path(scratch, "objects")),
               "GIT_ALTERNATE_OBJECT_DIRECTORIES": os.pathsep.join(
                   filter(None, (str(objects), os.environ.get("GIT_ALTERNATE_OBJECT_DIRECTORIES"))))}
        Path(scratch, "objects").mkdir()
        _git(workspace, "read-tree", str(base), env=env)
        patched = set()
        if patch:
            try:
                _git(workspace, "apply", "--cached", str(patch), env=env)
            except RuntimeError as error:
                raise ValueError(f"git apply --cached {patch} failed") from error
            patched = set(filter(None, _git(workspace, "diff-index", "--cached", "--name-only", "-z", "--no-renames",
                                            str(base), env=env).split("\0")))
        entries = {}
        for entry in _git(workspace, "ls-files", "-s", "-z", env=env).split("\0"):
            fields = entry.split("\t", 1)
            metadata = fields[0].split()  # <mode> <object> <stage>
            if len(fields) == 2 and len(metadata) == 3:
                entries[fields[1]] = (metadata[0], metadata[1])
        reader = subprocess.Popen(["git", "-C", str(workspace), "cat-file", "--batch"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        cache = {}

        def read(path) -> bytes:
            mode, name = entries[path]
            if mode not in _FILE_MODES:
                return b""
            if path not in cache:
                reader.stdin.write(name.encode() + b"\n")
                reader.stdin.flush()
                header = reader.stdout.readline().split()
                if len(header) != 3:
                    raise RuntimeError(f"git cat-file could not read {path}")
                cache[path] = reader.stdout.read(int(header[2]))
                reader.stdout.read(1)  # the newline after each object
            return cache[path]

        try:
            yield entries, read, frozenset(patched)
        finally:
            with contextlib.suppress(OSError):
                reader.stdin.close()
            try:
                reader.wait(timeout=10)
            except subprocess.TimeoutExpired:
                reader.kill()
                reader.wait()
            reader.stdout.close()


# --- the definition ------------------------------------------------------------

@dataclass(frozen=True)
class Definition:
    pinned: frozenset    # paths the extra run takes from the effective base, or keeps absent as it has them
    product: frozenset   # files the base tests import: the candidate's version enters
    boundary: str        # why the runner closure is unestablished, "" when it is established
    patched: frozenset   # paths the reviewed patch changed
    manifests: dict      # {path: bytes} of each package.json the effective base holds as a file


def _module_paths(literal, directory):
    """Every tracked-tree path a relative ``literal`` can name: as written, then completed as Node
    and TypeScript complete it, relative to ``directory`` and then to the root (a command runs
    from the package folder, a require from the file's)."""
    names = []
    for root in dict.fromkeys((directory, ".")):
        joined = posixpath.normpath(posixpath.join(root, literal))
        if joined == ".." or joined.startswith("../"):
            continue
        names += [joined] + [joined + extension for extension in MODULE_EXTENSIONS] + [
            posixpath.normpath(posixpath.join(joined, "index" + extension)) for extension in MODULE_EXTENSIONS]
    return list(dict.fromkeys(names))


def _shell_bindings(command, directory, tracked):
    """([(word, directory it is read in)], unestablished reason) for one command line.

    shlex keeps an ordinary quoted filename as one operand (ValueError when it cannot split
    the line). A shell's -c operand is itself a command line, expanded in the directory active
    where that shell appears, so `sh -c 'node run-tests.js'` pins the base runner. A literal
    `cd <dir> &&` or `cd <dir>;` moves that directory for what follows: `sh -c 'cd lib && node
    run-tests.js'` must pin lib/run-tests.js, not a root file of the same name. A computed cd
    target cannot establish the boundary.
    """
    parsed = shlex.split(command)
    bindings, segment, current = [], [], directory

    def finish(keep_directory):
        nonlocal current
        if segment[:1] == ["cd"] and keep_directory:
            target = segment[1] if len(segment) == 2 else ""
            if (len(segment) != 2 or not target or target.startswith(("-", "/", "~"))
                    or any(mark in target for mark in ("$", "`", "*", "?"))):
                return "a cd target is computed, so the suite definition boundary is unestablished"
            landed = posixpath.normpath(posixpath.join(current, target))
            inside = (landed in ("", ".") or (
                not landed.startswith("../") and any(
                    path == landed or path.startswith(landed + "/") for path in tracked)))
            if not inside:
                return f"cd {target} does not name a directory in the base tree"
            current = "." if landed in ("", ".") else landed
        else:
            bindings.extend((word, current) for word in segment)
        segment.clear()
        return ""

    index = 0
    while index < len(parsed):
        word = parsed[index]
        if posixpath.basename(word) in SHELLS and index + 2 < len(parsed) and parsed[index + 1] == "-c":
            if segment:
                reason = finish(False)
                if reason:
                    return [], reason
            nested, reason = _shell_bindings(parsed[index + 2], current, tracked)
            if reason:
                return [], reason
            bindings.extend(nested)
            index += 3
            continue
        if word in {"&&", ";", "||", "|", "&"}:
            reason = finish(word in {"&&", ";"})
            if reason:
                return [], reason
            index += 1
            continue
        segment.append(word)
        index += 1
    reason = finish(False)
    return ([], reason) if reason else (bindings, "")


def _link_targets(files, entries, tracked, read):
    """What definition-named links point at, through further links: a tracked file, every tracked
    file under a tracked directory, or the absent path itself, so a candidate cannot fill it."""
    found, queue = set(), sorted(files)
    while queue:
        path = queue.pop()
        if path not in entries or entries[path][0] != LINK:
            continue
        target = posixpath.normpath(posixpath.join(posixpath.dirname(path),
                                                   read(path).decode("utf-8", "replace")))
        if target in ("", ".", "..") or target.startswith(("../", "/")):
            continue  # the tree or outside it: nothing the candidate's overlay can replace
        reached = [target] if target in tracked else (
            [name for name in tracked if name.startswith(target + "/")] or [target])
        for name in reached:
            if name not in found and name not in files:
                found.add(name)
                queue.append(name)
    return found


def definition(workspace, base, suite_command, *, is_test, base_patch=None) -> Definition:
    """The base suite definition (module docstring). ``is_test`` is the verifier's test-path rule.

    Pinned by rule: definition files (``is_definition_file``) at any depth and what a
    definition-named link points at; every test file; and the closure of path literals
    reachable from every manifest script's words and the suite command's words. Files the base
    tests import through relative literals are product code and never pinned. A runner's
    literal inside an exec argument is pinned and followed; a word or literal naming a path the
    effective base does not hold is pinned absent; any other reach, a computed path, a computed
    or missing cd target, or a script shlex cannot split leaves the closure unestablished
    (``boundary``), and then only definition files, link targets and tests are pinned.
    Raises OSError, RuntimeError or ValueError when the effective base cannot be read.
    """
    with _effective_base(workspace, base, base_patch) as (entries, read, patched):
        tracked = set(entries)

        def text(path):
            return read(path).decode("utf-8", "replace")

        tests = {path for path in tracked if is_test(path)}
        files = {path for path in tracked if is_definition_file(path)}
        rule = files | tests | _link_targets(files, entries, tracked, read)
        manifests = {path: read(path) for path in sorted(files)
                     if PurePosixPath(path).name == MANIFEST and entries[path][0] != LINK}

        def resolve(literal, directory):
            names = _module_paths(literal, directory)
            return next((name for name in names if name in tracked), None), names

        # Product code: what base tests import transitively through relative literals. The
        # candidate's version must enter the definition tree, or a runner importing the module
        # under test would pin it (#587's product carve-out).
        product, queue, seen = set(), sorted(tests), set(tests)
        while queue:
            path = queue.pop()
            for specifier, _ in import_sites(text(path)):
                if not specifier or not specifier.startswith(("./", "../")):
                    continue
                target, _ = resolve(specifier, posixpath.dirname(path) or ".")
                if target and target not in seen:
                    seen.add(target)
                    product.add(target)
                    queue.append(target)

        def found(pinned, boundary=""):
            return Definition(frozenset(pinned), frozenset(product), boundary, patched, manifests)

        commands = [(f'the "{name}" script in {path}', str(script), posixpath.dirname(path) or ".")
                    for path in sorted(tracked) if PurePosixPath(path).name == MANIFEST and entries[path][0] != LINK
                    for name, script in package_scripts(text(path)).items()]
        commands.append(("the suite command", suite_command or "", "."))
        runners, absent = set(), set()
        for origin, command, directory in commands:
            try:
                bindings, reason = _shell_bindings(command, directory, tracked)
            except ValueError as error:  # an unbalanced quote: what the script runs is unknown
                return found(rule, f"{origin} cannot be split into shell words ({error})")
            if reason:
                return found(rule, reason)
            for word, effective in bindings:
                name = posixpath.normpath(posixpath.join(effective, word))
                if name.startswith(("../", "/")) or name == ".." or name in product:
                    continue
                (runners if name in tracked else absent).add(name)
        queue = sorted(runners)
        while queue:
            path = queue.pop()
            if is_test(path) or is_definition_file(path):
                continue  # tests are pinned wholesale, never scanned; manifests were read above
            for specifier, inside_exec in import_sites(text(path)):
                if specifier is None:
                    return found(rule, f"{path} selects its suite inputs through a computed path")
                if not specifier.startswith(("./", "../")):
                    continue  # a package or core module, resolved by the runtime
                target, names = resolve(specifier, posixpath.dirname(path) or ".")
                if target is None:
                    absent.update(names)  # the base has nothing there, and neither does the extra run
                    continue
                if target in product or is_test(target) or is_definition_file(target):
                    continue
                if not inside_exec:
                    return found(rule, f"{path} reaches {target}, which is neither a test, a manifest "
                                       "nor an input of the executed test command")
                if target not in runners:
                    runners.add(target)
                    queue.append(target)
        return found(rule | runners | absent)


# --- the extra run's tree ------------------------------------------------------

def _manifest(data) -> dict:
    value = json.loads(data.decode("utf-8-sig"))  # npm reads a manifest with a byte order mark
    if not isinstance(value, dict):
        raise ValueError("package.json is not an object")
    return value


def merged_manifest(original, candidate):
    """The package.json bytes the extra run uses, or None for no file.

    ``original`` is the effective base's file and ``candidate`` the candidate's, None where there
    is none. The merge keeps the candidate's KEPT_FIELDS and takes every other field from the
    original; a file either side cannot read as a JSON object comes back as the original. An added
    package.json enters only when it holds nothing but KEPT_FIELDS (a nested
    ``{"type": "module"}``): otherwise it is a package the base suite never ran.
    """
    if candidate is None:
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
    if merged == before:
        return original
    return candidate if merged == after else (json.dumps(merged, indent=2, ensure_ascii=False) + "\n").encode()


def plan(workspace, changes, found, *, is_test) -> dict:
    """What the extra run's tree receives on top of the effective base.

    ``changes`` are the candidate's changes measured against ``base``; a path only the reviewed
    patch changed is measured too, so a file the candidate holds as base had it replaces the
    patch's version. Returns ``overlay`` ({path: status} for make_tree: every measured path the
    definition does not pin), ``manifests`` ({path: bytes}: each changed package.json that
    merged_manifest gives a different content) and ``unclassified`` (overlay paths that are not
    product code: only an established closure can say they are not part of the definition).
    """
    workspace = Path(workspace)
    measured = dict(changes)
    for path in found.patched - changes.keys():
        measured[path] = "modified" if os.path.lexists(workspace / path) else "deleted"
    overlay = {path: status for path, status in sorted(measured.items())
               if path not in found.pinned and not is_definition_file(path) and not is_test(path)}
    manifests = {}
    for path, status in sorted(measured.items()):
        linked = path in found.pinned and path not in found.manifests  # a link on base, or pinned absent
        if (PurePosixPath(path).name != MANIFEST or status == "deleted" or is_test(path) or linked
                or (workspace / path).is_symlink()):
            continue  # a deleted, linked or test-held manifest stays as the base has it
        try:
            candidate = (workspace / path).read_bytes()
        except OSError:
            continue
        original = found.manifests.get(path)
        merged = merged_manifest(original, candidate)
        if merged is not None and merged != original:
            manifests[path] = merged
    return {"overlay": overlay, "manifests": manifests,
            "unclassified": [path for path in overlay if path not in found.product]}


def scratch_folder(workspace) -> Path:
    """A new temporary folder for the extra run's tree, outside ``workspace`` (module docstring)."""
    holder = Path(tempfile.mkdtemp(prefix="autocode-base-definition-")).resolve()
    if holder.is_relative_to(Path(workspace).resolve()):
        shutil.rmtree(holder, ignore_errors=True)
        raise ValueError(f"the temporary folder {holder} is inside the workspace, where the candidate's "
                         "package configuration would still apply")
    return holder


def install_manifests(tree, manifests, staging):
    """Write merged package.json files into ``tree`` through the scratch overlay, never through a
    link; the bytes stay under ``staging`` while the run uses them."""
    files = {}
    for path, data in manifests.items():
        source = Path(staging) / path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(data)
        files[path] = source
    if files:
        scratch_overlay.apply(tree, files, None)
