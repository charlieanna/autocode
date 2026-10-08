"""The base suite definition an exit-code-only suite proof runs over the candidate (#587, #652).

npm, Yarn, pnpm, Bun and shell runners run whatever each tree's own definition says, so one
suite command can run fewer tests on the candidate than on base. When the suite reports no
per-test results, autocode_verify runs the base's definition once more over the candidate's
product code. This module decides what that extra run's tree receives. It reads the effective
base, ``base`` with the reviewed ``base_patch``, through Git plumbing and never writes to the
repository:

* ``definition``: the paths the extra run takes from the effective base. Package manifests,
  lockfiles, package-manager, task-runner, test-runner and transpiler configuration and Yarn
  releases and plugins, wherever they are (``is_definition_file``), and what a link among them
  points at; every test file and what a linked test points at; and the runner files the suite
  reaches, present or absent: the words of the suite command and of the package scripts it
  runs (and the scripts they chain), of inline ``node -e`` code and of ``NAME=value`` and
  ``--flag=value`` words, followed through require/import literals, through links, and through
  the manifest fields that resolve a ``#`` import, a self-reference or a folder. It also names
  the product code (what the base tests import), the manifest fields a pinned file resolves
  through, and why the runner closure could not be established. A runner may load product
  code in-process; a product file it reaches inside an exec argument, or at all when an exec
  argument is computed, is a conflict neither side can place.
* ``plan``: the candidate's files on top of that, measured against the effective base, so a
  file a follow-up put back as base had it is the candidate's too; each changed package.json
  merged field by field (``merged_manifest``); the changed paths only an established closure
  can place; and the paths the candidate put where a folder holding pinned files was, which
  nothing can place.
* ``scratch_folder``: a folder outside the workspace for the tree. Yarn 1 reads .yarnrc and
  .npmrc from every parent folder and pnpm takes the nearest parent pnpm-workspace.yaml, so a
  tree inside the candidate's checkout would still get the candidate's configuration.

Reading is fail-closed: a JavaScript file the scanner cannot read to the end (an unterminated
string, comment, regex or template) and a shell word it cannot place (a variable, a glob, a
command substitution) leave the closure unestablished rather than silently empty.

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
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

try:
    from . import autocode_scratch_overlay as scratch_overlay
except ImportError:
    import autocode_scratch_overlay as scratch_overlay

MANIFEST = "package.json"
# Manifests, lockfiles, and package-manager and monorepo task-runner configuration: .npmrc and
# .yarnrc can replace the script shell or add node options, .pnpmfile hooks run on every pnpm
# command, pnpm-workspace.yaml lists the packages `pnpm -r` runs, and turbo, nx and lerna decide
# which package scripts run and when a cached result is replayed instead. jsconfig, .swcrc and
# jasmine.json are read by their runners without the script naming them.
DEFINITION_FILE_BASENAMES = frozenset({
    MANIFEST, "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "pnpm-workspace.yaml",
    ".npmrc", ".yarnrc", ".yarnrc.yml", ".yarnrc.yaml", ".pnpmfile.cjs", ".pnpmfile.mjs", "pnpmfile.js", "bunfig.toml",
    "turbo.json", "nx.json", "lerna.json", "jsconfig.json", ".swcrc", "jasmine.json"})
# Test-runner configuration a test script reads without naming it (its spec list, ignore patterns,
# coverage thresholds), and the transpiler configuration a runner applies to every test file (a Babel
# plugin can turn a test into a skipped one), matched by name prefix: .mocharc.json, jest.config.ts,
# playwright.config.mjs, babel.config.cjs, .babelrc, tsconfig.test.json.
RUNNER_CONFIGURATION = ("jest.config.", "vitest.config.", "vitest.workspace.", ".mocharc", "ava.config.", ".c8rc",
                        ".nycrc", "nyc.config.", ".taprc", "karma.conf.", "playwright.config.", "cypress.config.",
                        "wdio.conf.", "web-test-runner.config.", ".wtrrc", "babel.config.", ".babelrc", "tsconfig")
# Yarn Berry runs the release .yarnrc.yml names and loads the plugins it lists.
YARN_DIRECTORIES = frozenset({"releases", "plugins"})
# package.json fields that say what the package's code is, how Node, bundlers and transpilers load
# it, what it installs, and its descriptive metadata. The candidate keeps these in the extra run, so
# its code still loads (an added `imports` map or `exports` self-reference), except a field a pinned
# runner or test resolves a module through (``Definition.used_fields``). Every other field is the
# base's: scripts, config, workspaces, files, name (it selects workspaces), test-runner keys such as
# jest, mocha, ava, c8, nyc and tap, babel (it rewrites test files), and any custom field a runner
# script might read.
KEPT_FIELDS = frozenset({
    "type", "main", "module", "browser", "exports", "imports", "bin", "man", "types", "typings",
    "typesVersions", "sideEffects", "browserslist",
    "dependencies", "devDependencies", "peerDependencies", "peerDependenciesMeta", "optionalDependencies",
    "bundleDependencies", "bundledDependencies", "dependenciesMeta", "overrides", "resolutions",
    "engines", "os", "cpu", "libc",
    "version", "description", "keywords", "homepage", "bugs", "license", "author", "contributors",
    "maintainers", "funding", "repository", "private", "publishConfig",
})
# How Node and TypeScript complete a relative specifier without an extension.
MODULE_EXTENSIONS = (".js", ".cjs", ".mjs", ".json", ".node", ".jsx", ".ts", ".tsx", ".cts", ".mts")
# Files nothing the suite runs can load, execute or read as a selector: a change to one needs no
# closure to place.
INERT_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt", ".adoc", ".html", ".htm", ".css", ".scss", ".less",
                            ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".woff", ".woff2",
                            ".ttf", ".eot", ".mp3", ".mp4"})
INERT_BASENAMES = frozenset({"NOTICE", "AUTHORS", "CODEOWNERS", ".gitignore", ".editorconfig", ".prettierignore",
                             ".npmignore", ".dockerignore"})
SHELLS = frozenset({"sh", "bash", "dash", "zsh"})
NODE = frozenset({"node", "nodejs"})
EVAL_FLAGS = frozenset({"-e", "--eval", "-p", "--print"})
PACKAGE_MANAGERS = frozenset({"npm", "npm.cmd", "pnpm", "pnpm.cmd", "yarn", "yarn.cmd", "bun"})
LIFECYCLE = frozenset({"test", "start", "stop", "restart", "install", "publish", "version", "pack"})
OPERATORS = frozenset({"&&", "||", ";", ";;", "|", "|&", "&"})
REDIRECTIONS = frozenset({">", ">>", "<", "<<", "<<<", ">&", "<&", "&>", "&>>"})
LINK = "120000"
_FILE_MODES = frozenset({"100644", "100755", LINK})
_JS_MODULE_CALL = re.compile(r"\b(?:require|import)\s*\(")
_JS_EXEC_CALL = re.compile(r"\b(?:exec(?:Sync|File(?:Sync)?)?|spawn(?:Sync)?|fork)\s*\(")
_CODE_TOKEN = re.compile(r"//|/\*|/|[\"'`{}]")
# A single- or double-quoted literal ends at its quote; one that reaches a newline or the end of the
# file is not JavaScript the runtime would accept, and the scanner refuses to guess past it.
_QUOTED = {quote: re.compile(quote + r"((?:[^" + quote + r"\\\n]|\\.)*)(" + quote + r"|\n|\Z)", re.S)
           for quote in "'\""}
_TEMPLATE_CHUNK = re.compile(r"(?:[^`\\$]|\\.|\$(?!\{))*", re.S)
_ESCAPE = re.compile(r"\\(.)", re.S)
# What may precede a division sign: a value. Anything else before a slash starts a regex literal,
# as do these keywords, which a value cannot follow.
_VALUE_END = re.compile(r"[\w$)\]]$")
_WORD_END = re.compile(r"[A-Za-z_$][\w$]*$")
_REGEX_KEYWORDS = frozenset({"return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw", "case",
                             "do", "else", "yield", "await"})
_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)
_OPTION_VALUE = re.compile(r"^(--?[A-Za-z][\w-]*)=(.*)$", re.S)
_COMPUTED_MARKS = ("$", "`", "*", "?")


class Unreadable(ValueError):
    """A file the scanner cannot read to the end, or a specifier it cannot resolve literally."""


def is_definition_file(path) -> bool:
    """A suite-definition path wherever it appears (module docstring)."""
    parts = PurePosixPath(path).parts
    if not parts:
        return False
    return (parts[-1] in DEFINITION_FILE_BASENAMES or parts[-1].startswith(RUNNER_CONFIGURATION)
            or any(a == ".yarn" and b in YARN_DIRECTORIES for a, b in zip(parts, parts[1:-1])))


def is_inert(path) -> bool:
    """A file nothing the suite runs can load, execute or read as a selector."""
    name = PurePosixPath(path).name
    return (name in INERT_BASENAMES or name.startswith(("LICENSE", "LICENCE", "CHANGELOG"))
            or PurePosixPath(path).suffix.lower() in INERT_SUFFIXES)


def package_scripts(text) -> dict:
    try:
        package = json.loads(text or "{}")
    except ValueError:
        return {}
    scripts = package.get("scripts") if isinstance(package, dict) else None
    return dict(scripts) if isinstance(scripts, dict) else {}


# --- reading JavaScript without running it ----------------------------------

def _regex_end(text, start):
    """The index after a regex literal whose body starts at ``start``, or None when it never closes
    on its line. A class ``[...]`` may hold an unescaped slash; a backslash escapes the next char."""
    index, n, in_class = start, len(text), False
    while index < n:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "\n":
            return None
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "/":
            index += 1
            while index < n and text[index].isalpha():
                index += 1
            return index
        index += 1
    return None


def _starts_regex(mask, start):
    """Whether a slash at ``start`` begins a regex literal: it does unless a value ends before it."""
    before = "".join(mask[max(0, start - 2000):start]).rstrip()
    if not _VALUE_END.search(before):
        return True
    word = _WORD_END.search(before)
    return bool(word and word.group() in _REGEX_KEYWORDS)


def js_scan(text):
    """(mask, strings): ``mask`` blanks comments, regex literals and quoted literals (same length),
    and ``strings`` lists each quoted literal as (start, end, value), so call structure and literal
    arguments can be read without executing anything.

    A template literal without substitutions is a literal. One with ``${...}`` is computed:
    its text is blanked but its opening backtick is not, so a call taking it as an argument
    is never read as a literal, and the code inside each substitution stays visible.

    Raises Unreadable for text that is not JavaScript the runtime would run to the end: a string
    reaching a newline, a comment, regex or template still open at the end of the file."""
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
        if end >= n:
            raise Unreadable("a template literal is still open at the end of the file")
        return end + 1, True, value  # the closing backtick

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
            if end < 0:
                raise Unreadable("a comment is still open at the end of the file")
            end += 2
            blank(start, end)
        elif kind == "/":
            if not _starts_regex(mask, start):
                end = start + 1
            else:
                end = _regex_end(text, start + 1)
                if end is None:
                    raise Unreadable("a regex literal does not end on its line")
                blank(start, end)
        elif kind in "'\"":
            quoted = _QUOTED[kind].match(text, start)
            if quoted.group(2) != kind:
                raise Unreadable("a string literal does not end on its line")
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
    if depths:
        raise Unreadable("a template literal is still open at the end of the file")
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
    import and export-from specifiers are literals by grammar. Raises Unreadable (js_scan).
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
    for pattern in (r"\bfrom\s*(['\"])", r"(?<![\w$.])import\s*(['\"])"):
        for match in re.finditer(pattern, text):
            quote = match.start(1)
            if quote in by_start:  # a real literal, not text inside a comment
                sites.append((by_start[quote][1], False))
    return sites


def exec_computed(text):
    """Whether an exec/spawn/fork call of JS source takes a first argument that is not a literal (or a
    fold of literals): then what it runs may come from any module the file loads. Raises Unreadable."""
    mask, strings = js_scan(text)
    for match in _JS_EXEC_CALL.finditer(mask):
        start, end = _argument_span(mask, match.end() - 1)
        depth, stop = 0, end  # the first argument ends at the first top-level comma
        for index in range(start + 1, end):
            if mask[index] in "([{":
                depth += 1
            elif mask[index] in ")]}":
                depth -= 1
            elif mask[index] == "," and depth == 0:
                stop = index
                break
        inner = [literal for literal in strings if start < literal[0] < stop]
        covered = {index for literal in inner for index in range(literal[0], literal[1])}
        residue = "".join(text[index] for index in range(start + 1, stop)
                          if index not in covered and not text[index].isspace() and mask[index] == text[index])
        if not inner or residue.strip("+"):
            return True
    return False


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
    present: frozenset = frozenset()  # the pinned paths the effective base holds
    used_fields: dict = field(default_factory=dict)  # {manifest path: fields a pinned file resolves through}
    conflicts: frozenset = frozenset()  # product files the runner closure also reaches: neither side can place them


def _module_paths(literal, directory, *, root_fallback=False):
    """Every tracked-tree path a relative ``literal`` can name: as written, then completed as Node
    and TypeScript complete it (an extension, a folder's index, a folder's package.json), relative to
    ``directory`` and, for a command word, to the root too (a command runs from the package folder, a
    require from its file's folder only)."""
    names = []
    for root in dict.fromkeys((directory, ".") if root_fallback else (directory,)):
        joined = posixpath.normpath(posixpath.join(root, literal))
        if joined == ".." or joined.startswith("../"):
            continue
        names += [joined] + [joined + extension for extension in MODULE_EXTENSIONS] + [
            posixpath.normpath(posixpath.join(joined, "index" + extension)) for extension in MODULE_EXTENSIONS] + [
            posixpath.normpath(posixpath.join(joined, MANIFEST))]
    return list(dict.fromkeys(names))


def _tokens(command):
    """Shell words with operators and parentheses as their own tokens whatever the spacing
    (``(cd lib && node run.js)``, ``node a.js;node b.js``). ValueError when a quote is unbalanced."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&")
    lexer.whitespace_split = True
    return list(lexer)


def _shell_bindings(command, directory, tracked):
    """([(word, directory it is read in)], unestablished reason) for one command line.

    A shell's -c operand is itself a command line, expanded in the directory active where that
    shell appears, so `sh -c 'node run-tests.js'` pins the base runner; so are the value of a
    ``NAME=value`` or ``--flag=value`` word (NODE_OPTIONS, --require=) and, read as JavaScript,
    the operand of `node -e`. A literal `cd <dir>` followed by `&&`, `;` or `||` moves that
    directory for what follows, and a subshell's `(` ... `)` restores it: `sh -c 'cd lib && node
    run-tests.js'` must pin lib/run-tests.js, not a root file of the same name. A computed cd
    target, a variable, a glob or a command substitution cannot establish the boundary.
    """
    parsed = _tokens(command)
    bindings, segment, current, saved = [], [], directory, []

    def finish(keep_directory):
        nonlocal current
        if segment[:1] == ["cd"] and keep_directory:
            target = segment[1] if len(segment) == 2 else ""
            if (len(segment) != 2 or not target or target.startswith(("-", "/", "~"))
                    or any(mark in target for mark in _COMPUTED_MARKS)):
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

    def nested(text):
        found, reason = _shell_bindings(text, current, tracked)
        if reason:
            return reason
        bindings.extend(found)
        return ""

    index = 0
    while index < len(parsed):
        word = parsed[index]
        if posixpath.basename(word) in SHELLS and index + 2 < len(parsed) and parsed[index + 1] == "-c":
            reason = finish(False) if segment else ""
            if reason:
                return [], reason
            reason = nested(parsed[index + 2])
            if reason:
                return [], reason
            index += 3
            continue
        if posixpath.basename(word) in NODE and index + 2 < len(parsed) and parsed[index + 1] in EVAL_FLAGS:
            # Inline JavaScript: its relative require/import literals are runner sites in this directory.
            flag = f"{word} {parsed[index + 1]}"
            try:
                sites = import_sites(parsed[index + 2])
            except Unreadable as error:
                return [], f"the inline JavaScript of {flag} cannot be read ({error})"
            for specifier, _ in sites:
                if specifier is None:
                    return [], f"the inline JavaScript of {flag} selects a module through a computed path"
                if specifier.startswith(("./", "../")):
                    segment.append(specifier)
            segment.append(word)
            index += 3
            continue
        if word in OPERATORS:
            reason = finish(word in {"&&", ";", "||"})
            if reason:
                return [], reason
            index += 1
            continue
        if word == "(":
            reason = finish(False) if segment else ""
            if reason:
                return [], reason
            saved.append(current)
            index += 1
            continue
        if word == ")":
            reason = finish(True)
            if reason:
                return [], reason
            if saved:
                current = saved.pop()
            index += 1
            continue
        if word in REDIRECTIONS:
            index += 2  # a redirection and its target name no runner
            continue
        if any(mark in word for mark in _COMPUTED_MARKS):
            return [], f"the word {word!r} is computed by the shell, so the suite definition boundary is unestablished"
        assignment = _ASSIGNMENT.match(word) or _OPTION_VALUE.match(word)
        if assignment and assignment.group(2):
            reason = nested(assignment.group(2))
            if reason:
                return [], reason
            index += 1
            continue
        segment.append(word)
        index += 1
    reason = finish(False)
    return ([], reason) if reason else (bindings, "")


def _script_references(bindings, scripts):
    """[(script name, everywhere)]: the scripts a command line runs by name. After a package
    manager (`npm test`, `pnpm -r run X`, `yarn X`) the name may run in every workspace package;
    a bare word (or an `npm:` word) naming a script, as run-s, run-p, npm-run-all and concurrently
    take them, runs in this manifest."""
    words = [word for word, _ in bindings]
    found = {}
    for index, word in enumerate(words):
        if posixpath.basename(word) in PACKAGE_MANAGERS:
            positional = [item for item in words[index + 1:index + 6] if not item.startswith("-")]
            if positional and positional[0] in ("run", "run-script"):
                if len(positional) > 1:
                    found[positional[1]] = True
            elif positional and positional[0] in LIFECYCLE:
                found[positional[0]] = True
            elif positional and posixpath.basename(word).startswith("yarn") and positional[0] in scripts:
                found[positional[0]] = True
            continue
        name = word[4:] if word.startswith("npm:") else word
        if name in scripts:
            found.setdefault(name, False)
    return [(name, everywhere) for name, everywhere in found.items() if name in scripts]


def _link_targets(files, entries, tracked, read):
    """What links among ``files`` point at, through further links: a tracked file, every tracked
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


def _conditions(value):
    """The target a conditional exports/imports value names for a CommonJS or ESM load, or None."""
    for _ in range(8):
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            value = value[0] if value else None
        elif isinstance(value, dict):
            value = next((value[key] for key in ("require", "node", "default", "import") if key in value), None)
        else:
            return None
    return None


def _export_target(exports, key):
    """The relative target an ``exports`` field names for subpath ``key`` ("." or "./sub"), or None."""
    if isinstance(exports, dict) and any(name.startswith(".") for name in exports):
        return _conditions(exports.get(key))
    return _conditions(exports) if key == "." else None


def definition(workspace, base, suite_command, *, is_test, base_patch=None) -> Definition:
    """The base suite definition (module docstring). ``is_test`` is the verifier's test-path rule.

    Pinned by rule: definition files (``is_definition_file``) at any depth and what a link
    among them points at; every test file and what a linked test points at; and the closure of
    paths reachable from the words of the suite command and of the package scripts it runs. A
    runner's literal is followed through links and through the manifest fields that resolve a
    ``#`` import, a self-reference or a folder; those fields then stay the base's. Files the
    base tests import are product code and never pinned; a runner that reaches one, or a script
    word that names one, is a conflict the closure cannot resolve. A word or literal naming a
    path the effective base does not hold is pinned absent; any other reach, a computed path or
    word, a computed or missing cd target, an unreadable file or an unsplittable script leaves
    the closure unestablished (``boundary``), and then only definition files, link targets and
    tests are pinned. Raises OSError, RuntimeError or ValueError when the effective base cannot
    be read.
    """
    with _effective_base(workspace, base, base_patch) as (entries, read, patched):
        tracked = set(entries)
        links = {}
        for path, (mode, _) in entries.items():
            if mode == LINK:
                target = posixpath.normpath(posixpath.join(posixpath.dirname(path),
                                                           read(path).decode("utf-8", "replace")))
                links[path] = None if target in ("", "..") or target.startswith(("../", "/")) else target

        def realpath(name):
            """(the tracked-tree path ``name`` reaches through links, or None when it leaves the tree,
            the links it passed)."""
            parts, passed, hops, index = name.split("/"), [], 0, 0
            while index < len(parts):
                prefix = "/".join(parts[:index + 1])
                if prefix in links:
                    passed.append(prefix)
                    hops += 1
                    if links[prefix] is None or hops > 16:
                        return None, passed
                    parts = links[prefix].split("/") + parts[index + 1:]
                    index = 0
                    continue
                index += 1
            return "/".join(parts), passed

        def text(path):
            real, _ = realpath(path)
            return read(real if real in tracked else path).decode("utf-8", "replace")

        tests = {path for path in tracked if is_test(path)}
        files = {path for path in tracked if is_definition_file(path)}
        rule = files | tests | _link_targets(files | tests, entries, tracked, read)
        manifests = {path: read(path) for path in sorted(files)
                     if PurePosixPath(path).name == MANIFEST and entries[path][0] != LINK}
        parsed = {}
        for path, data in manifests.items():
            try:
                value = json.loads(data.decode("utf-8-sig"))
            except ValueError:
                value = None
            parsed[path] = value if isinstance(value, dict) else {}
        used_fields: dict = {}

        def scope(directory):
            """The nearest manifest at or above ``directory``."""
            while True:
                manifest = posixpath.normpath(posixpath.join(directory, MANIFEST))
                if manifest in parsed:
                    return manifest
                if directory in ("", "."):
                    return None
                directory = posixpath.dirname(directory) or "."

        def use(manifest, *fields):
            used_fields.setdefault(manifest, set()).update(fields)

        def file_at(name):
            real, passed = realpath(name)
            if real in tracked and entries[real][0] != LINK:
                return real, passed
            return None, passed

        def resolve(literal, directory, *, root_fallback=False, specifier=True):
            """(tracked file, link paths passed, candidate names) for a module ``specifier`` read in
            ``directory``, or for a shell word (``specifier=False``, always a path): a relative path
            completed as Node completes it, a folder's manifest main or exports, a `#` import through
            the scope's imports map, a self-reference through its exports. A package or core module
            gives (None, [], []). Raises Unreadable for a map entry that is not a literal."""
            if specifier and literal.startswith("#"):
                manifest = scope(directory)
                imports = (parsed.get(manifest) or {}).get("imports")
                target = _conditions(imports.get(literal)) if isinstance(imports, dict) else None
                if manifest is None or not isinstance(target, str) or not target.startswith("./"):
                    raise Unreadable(f"{literal} is not a literal entry of the imports map in the base "
                                     f"{manifest or MANIFEST}")
                use(manifest, "imports")
                return resolve(target, posixpath.dirname(manifest) or ".")
            if specifier and not literal.startswith(("./", "../")) and literal not in (".", ".."):
                manifest = scope(directory)
                name = (parsed.get(manifest) or {}).get("name")
                if manifest and isinstance(name, str) and (literal == name or literal.startswith(name + "/")):
                    key = "." if literal == name else "./" + literal[len(name) + 1:]
                    target = _export_target((parsed.get(manifest) or {}).get("exports"), key)
                    if not isinstance(target, str) or not target.startswith("./"):
                        raise Unreadable(f"{literal} is not a literal entry of the exports map in the base {manifest}")
                    use(manifest, "exports", "name")
                    return resolve(target, posixpath.dirname(manifest) or ".")
                return None, [], []  # a package or core module, resolved by the runtime
            names = _module_paths(literal, directory, root_fallback=root_fallback)
            for name in names:
                if PurePosixPath(name).name == MANIFEST:
                    continue  # a folder's manifest is read for its main below, never loaded as the module
                found, passed = file_at(name)
                if found:
                    return found, passed, names
            for root in dict.fromkeys((directory, ".") if root_fallback else (directory,)):
                folder = posixpath.normpath(posixpath.join(root, literal))
                real, passed = realpath(posixpath.normpath(posixpath.join(folder, MANIFEST)))
                if real not in parsed:
                    continue
                data, base_dir = parsed[real], posixpath.dirname(real) or "."
                for key, target in (("exports", _export_target(data.get("exports"), ".")), ("main", data.get("main"))):
                    if not isinstance(target, str):
                        continue
                    use(real, key)
                    entry = posixpath.normpath(posixpath.join(base_dir, target))
                    for name in [entry] + [entry + ext for ext in MODULE_EXTENSIONS] + [
                            posixpath.normpath(posixpath.join(entry, "index" + ext)) for ext in MODULE_EXTENSIONS]:
                        found, more = file_at(name)
                        if found:
                            return found, passed + more, names
                    break
            return None, [], names

        # Product code: what base tests import transitively through relative literals. The
        # candidate's version must enter the definition tree, or a runner importing the module
        # under test would pin it (#587's product carve-out).
        product, queue, seen = set(), sorted(tests), set(tests)
        while queue:
            path = queue.pop()
            real, passed = realpath(path)
            if path in tests and real in tracked:
                rule.update(passed)
                rule.add(real)  # a linked test's real file is the test the suite runs
            try:
                sites = import_sites(text(path))
            except Unreadable:
                continue  # a test the scanner cannot read names no product; nothing is placed for it
            for specifier, _ in sites:
                if not specifier:
                    continue
                try:
                    target, passed, _ = resolve(specifier, posixpath.dirname(real if real in tracked else path) or ".")
                except Unreadable:
                    continue
                if target and target not in seen:
                    seen.add(target)
                    rule.update(passed)
                    product.add(target)
                    queue.append(target)

        conflicts = set()

        def found(pinned, boundary=""):
            return Definition(frozenset(pinned), frozenset(product), boundary, patched, manifests,
                              frozenset(pinned & tracked), {key: frozenset(value) for key, value in used_fields.items()},
                              frozenset(conflicts))

        # The commands the suite runs: the suite command, then the package scripts it names and the
        # scripts those chain, with their pre and post hooks; a package manager may run a script of that
        # name in every workspace package (`pnpm -r test`). A script the suite never runs cannot select
        # its tests, so its words and reaches do not bind the closure.
        scripts = {path: package_scripts(text(path)) for path in manifests}
        every = {name for table in scripts.values() for name in table}
        commands, seeded = [("the suite command", suite_command or "", ".", None)], set()

        def seed(name, manifest):
            for path, table in scripts.items():
                if manifest is not None and path != manifest:
                    continue
                for variant in (name, "pre" + name, "post" + name):
                    if variant in table and (path, variant) not in seeded:
                        seeded.add((path, variant))
                        commands.append((f'the "{variant}" script in {path}', str(table[variant]),
                                         posixpath.dirname(path) or ".", path))

        runners, absent = set(), set()
        while commands:
            origin, command, directory, manifest = commands.pop(0)
            try:
                bindings, reason = _shell_bindings(command, directory, tracked)
            except ValueError as error:  # an unbalanced quote: what the script runs is unknown
                return found(rule, f"{origin} cannot be split into shell words ({error})")
            if reason:
                return found(rule, f"in {origin}, {reason}")
            for name, everywhere in _script_references(bindings, scripts[manifest] if manifest else every):
                seed(name, None if everywhere else manifest)
            for word, effective in bindings:
                base_name = posixpath.basename(word)
                if word.startswith("-") or base_name in PACKAGE_MANAGERS | NODE | SHELLS or word in ("run", "run-script"):
                    continue
                if word in every or (word.startswith("npm:") and word[4:] in every):
                    continue  # a script name, seeded above
                name = posixpath.normpath(posixpath.join(effective, word))
                if name.startswith(("../", "/")) or name == "..":
                    continue
                try:
                    target, passed, names = resolve(word, effective, root_fallback=True, specifier=False)
                except Unreadable as error:
                    return found(rule, f"{origin} runs {word}: {error}")
                if target in product:
                    conflicts.add(target)
                    return found(rule, f"{origin} runs {target}, which the base tests also import, so the suite "
                                       "definition boundary is unestablished")
                if target is None:
                    absent.update(names)
                    continue
                rule.update(passed)
                runners.add(target)
        queue = sorted(runners)
        while queue:
            path = queue.pop()
            if is_test(path) or is_definition_file(path):
                continue  # tests are pinned wholesale, never scanned; manifests were read above
            try:
                source = text(path)
                sites, computed = import_sites(source), exec_computed(source)
            except Unreadable as error:
                return found(rule, f"{path} cannot be read ({error})")
            for specifier, inside_exec in sites:
                if specifier is None:
                    return found(rule, f"{path} selects its suite inputs through a computed path")
                try:
                    target, passed, names = resolve(specifier, posixpath.dirname(path) or ".")
                except Unreadable as error:
                    return found(rule, f"{path} reaches {specifier}: {error}")
                if target is None:
                    absent.update(names)  # the base has nothing there, and neither does the extra run
                    continue
                rule.update(passed)
                if target in product:
                    # A runner may load the product in-process (#587 T16); what it RUNS may come from that
                    # file only through an exec argument, read inside one or computed from anything loaded.
                    if inside_exec or computed:
                        conflicts.add(target)
                        return found(rule, f"{path} reaches {target}, which the base tests also import, so the "
                                           "suite definition boundary is unestablished")
                    continue
                if is_test(target) or is_definition_file(target):
                    continue
                if not inside_exec:
                    return found(rule, f"{path} reaches {target}, which is neither a test, a manifest "
                                       "nor an input of the executed test command")
                if target not in runners:
                    runners.add(target)
                    queue.append(target)
        pinned = rule | runners | absent
        return found(pinned | _link_targets(pinned, entries, tracked, read))


# --- the extra run's tree ------------------------------------------------------

def _manifest(data) -> dict:
    value = json.loads(data.decode("utf-8-sig"))  # npm reads a manifest with a byte order mark
    if not isinstance(value, dict):
        raise ValueError("package.json is not an object")
    return value


def merged_manifest(original, candidate, kept=KEPT_FIELDS):
    """The package.json bytes the extra run uses, or None for no file.

    ``original`` is the effective base's file and ``candidate`` the candidate's, None where there
    is none. The merge keeps the candidate's ``kept`` fields and takes every other field from the
    original; a file either side cannot read as a JSON object comes back as the original. An added
    package.json enters only when it holds nothing but kept fields (a nested
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
        return candidate if after.keys() <= kept else None
    merged = {key: value if key in kept else before[key]
              for key, value in after.items() if key in kept or key in before}
    merged.update((key, value) for key, value in before.items() if key not in kept and key not in merged)
    if merged == before:
        return original
    return candidate if merged == after else (json.dumps(merged, indent=2, ensure_ascii=False) + "\n").encode()


def plan(workspace, changes, found, *, is_test) -> dict:
    """What the extra run's tree receives on top of the effective base.

    ``changes`` are the candidate's changes measured against ``base``; a path only the reviewed
    patch changed is measured too, so a file the candidate holds as base had it replaces the
    patch's version. Returns ``overlay`` ({path: status} for make_tree: every measured path the
    definition does not pin), ``manifests`` ({path: bytes}: each changed package.json that
    merged_manifest gives a different content), ``unclassified`` (overlay paths that are neither
    product code nor inert: only an established closure can say they are not part of the
    definition; a product file the closure also reaches counts) and ``blocked`` ([(path, pinned
    path)]: a file or link the candidate put where a folder holding a pinned path was, which no
    overlay can place without losing that path).
    """
    workspace = Path(workspace)
    measured = dict(changes)
    for path in found.patched - changes.keys():
        measured[path] = "modified" if os.path.lexists(workspace / path) else "deleted"
    present = sorted(found.present)
    blocked = []
    for path in sorted(measured):
        held = next((name for name in present if name.startswith(path + "/")), None)
        if held is not None and measured[path] != "deleted":
            blocked.append((path, held))
    unplaceable = {path for path, _ in blocked}
    overlay = {path: status for path, status in sorted(measured.items())
               if path not in found.pinned and path not in unplaceable
               and not is_definition_file(path) and not is_test(path)}
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
        merged = merged_manifest(original, candidate, KEPT_FIELDS - set(found.used_fields.get(path, ())))
        if merged is not None and merged != original:
            manifests[path] = merged
    return {"overlay": overlay, "manifests": manifests, "blocked": blocked,
            "unclassified": [path for path in overlay if not is_inert(path)
                             and (path not in found.product or path in found.conflicts)]}


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
