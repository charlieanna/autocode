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
  points at; what the JavaScript configuration in the folders a command runs in, and above
  them, loads; every test file and what a linked test points at; and the runner files the suite
  reaches, present or absent: the words of the suite command and of the package scripts it
  runs (and the scripts they chain, in whichever workspace package), of inline ``node -e`` code
  and of ``NAME=value`` and ``--flag=value`` words, followed through require/import literals,
  through links, through the manifest fields that resolve a ``#`` import, a self-reference or a
  folder, and through the child processes a runner starts with literal arguments. Each name
  Node would try before the file it loads is pinned absent, so nothing can shadow it. It also
  names the product code (what the base tests import), the manifest fields a pinned file
  resolves through, and why the runner closure could not be established. A runner may load
  product code in-process; a product file it reaches inside a child-process argument, or at all
  when it starts a child process from computed arguments, or from a configuration file, is a
  conflict neither side can place.
* ``plan``: the candidate's files on top of that, measured against the effective base, so a
  file a follow-up put back as base had it is the candidate's too; each changed package.json
  merged field by field (``merged_manifest``); the changed paths only an established closure
  can place; and the paths the candidate put where a folder holding pinned files was, or below
  a pinned file or link, which nothing can place.
* ``scratch_folder``: a folder outside the workspace for the tree. Yarn 1 reads .yarnrc and
  .npmrc from every parent folder and pnpm takes the nearest parent pnpm-workspace.yaml, so a
  tree inside the candidate's checkout would still get the candidate's configuration.

Reading is fail-closed: a JavaScript file the scanner cannot read to the end (an unterminated
string, comment, regex or template), a shell word it cannot place (a variable, a glob, a
command substitution) and a child-process module used other than as plain calls leave the
closure unestablished rather than silently empty.

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
# Configuration whose JSON `extends` names more configuration (tsconfig.base.json, a shared .babelrc).
EXTENDING_CONFIGURATION = ("tsconfig", "jsconfig", ".babelrc", "babel.config.", ".mocharc", ".swcrc", ".c8rc", ".nycrc",
                           ".taprc")
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
# Files the scanner reads as JavaScript: a definition file with one of these suffixes loads what it requires.
JS_SUFFIXES = frozenset({".js", ".cjs", ".mjs", ".jsx", ".ts", ".tsx", ".cts", ".mts"})
# Files nothing the suite runs can load, execute or read as a selector: a change to one needs no
# closure to place.
INERT_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt", ".adoc", ".html", ".htm", ".css", ".scss", ".less",
                            ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".woff", ".woff2",
                            ".ttf", ".eot", ".mp3", ".mp4"})
INERT_BASENAMES = frozenset({"NOTICE", "AUTHORS", "CODEOWNERS", ".gitignore", ".editorconfig", ".prettierignore",
                             ".npmignore", ".dockerignore"})
SHELLS = frozenset({"sh", "bash", "dash", "zsh"})
NODE = frozenset({"node", "nodejs"})
EVAL_FLAGS = frozenset({"-e", "--eval", "-p", "--print", "-pe", "-ep"})
# node options whose separate value is a module Node loads before the script.
NODE_MODULE_OPTIONS = frozenset({"-r", "--require", "--import", "--loader", "--experimental-loader"})
PACKAGE_MANAGERS = frozenset({"npm", "npm.cmd", "pnpm", "pnpm.cmd", "yarn", "yarn.cmd", "bun"})
# Monorepo task runners: `turbo run test`, `lerna run test`, `nx run-many -t test` run a package
# script of that name in every package they select.
TASK_RUNNERS = frozenset({"turbo", "lerna", "nx"})
LIFECYCLE = frozenset({"test", "start", "stop", "restart", "install", "publish", "version", "pack"})
SCRIPT_ALIASES = {"t": "test", "tst": "test"}
# Package-manager subcommands that run no package script: the scan of an invocation stops there.
NOT_SCRIPTS = frozenset({
    "install", "i", "ci", "add", "remove", "rm", "uninstall", "update", "up", "upgrade", "exec", "x", "dlx",
    "create", "init", "link", "unlink", "ls", "list", "view", "info", "cache", "config", "set", "get", "why",
    "audit", "outdated", "login", "logout", "whoami", "bin", "env", "import", "store", "fetch", "patch",
    "deploy", "setup", "dedupe", "prune", "rebuild", "root", "prefix", "search", "help", "doctor", "explain",
    "fund", "diff", "edit", "hook", "org", "profile", "repo", "shrinkwrap", "sbom", "query", "completion",
    "licenses", "self-update", "server", "pm", "build"})
OPERATORS = frozenset({"&&", "||", ";", ";;", "|", "|&", "&"})
REDIRECTIONS = frozenset({">", ">>", "<", "<<", "<<<", ">&", "<&", "&>", "&>>"})
LINK = "120000"
_FILE_MODES = frozenset({"100644", "100755", LINK})
# Modules that start child processes. child_process functions take what they run as a shell command
# line, as a file and an argument array, or as a module Node runs; the other libraries' calls are read
# by the shape of their arguments.
CHILD_PROCESS_MODULES = frozenset({"child_process", "node:child_process", "execa", "cross-spawn", "zx", "shelljs",
                                   "tinyexec", "nano-spawn"})
CORE_CHILD_PROCESS = frozenset({"child_process", "node:child_process"})
CHILD_PROCESS_SHAPES = {"exec": "shell", "execSync": "shell", "execFile": "file", "execFileSync": "file",
                        "spawn": "file", "spawnSync": "file", "fork": "fork"}
_IDENTIFIER = r"[A-Za-z_$][\w$]*"
_JS_MODULE_CALL = re.compile(r"\b(?:require|import)\s*\(")
# A child_process function called by its own name, bound in a way the scan did not see.
_JS_EXEC_CALL = re.compile(r"(?<![\w$.])(exec|execSync|execFile|execFileSync|spawn|spawnSync|fork)\s*\(")
_CALL_BEFORE = re.compile(r"(?:require|import)\s*\(\s*$")
_AWAITED_BEFORE = re.compile(r"\(\s*await\s+import\s*\(\s*$")
_BINDING_BEFORE = re.compile(r"(?:const|let|var)\s+(\{[^{}]*\}|" + _IDENTIFIER + r")\s*=\s*(?:await\s+)?"
                             r"(?:require|import)\s*\(\s*$")
_STATIC_IMPORT_BEFORE = re.compile(r"import\s+(?:(\{[^{}]*\})|\*\s*as\s+(" + _IDENTIFIER + r")|(" + _IDENTIFIER
                                   + r"))\s*from\s*$")
_EFFECT_IMPORT_BEFORE = re.compile(r"import\s*$")
_MEMBER_CALL = re.compile(r"\s*\.\s*(" + _IDENTIFIER + r")\s*\(")
_CALL = re.compile(r"\s*\(")
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
# The tokens of a literal argument list: a value no loaded module can change (an atom), an object
# key, and the punctuation of folds, arrays and objects. A quoted literal is read from the scan.
_ARGUMENT_TOKEN = re.compile(
    r"(?P<atom>process\.execPath|process\.cwd\(\s*\)|process\.env|process\.argv(?:\.slice\(\s*\d*\s*\))?"
    r"|process\.platform|__dirname|__filename|true|false|null|undefined|-?\d+(?:\.\d+)?)(?![\w$.(])"
    r"|(?P<key>[A-Za-z_$][\w$]*)(?=\s*:)"
    r"|(?P<punct>\.\.\.|[\[\]{},:+])")
_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)
_OPTION_VALUE = re.compile(r"^(--?[A-Za-z][\w-]*)=(.*)$", re.S)
_COMPUTED_MARKS = ("$", "`", "*", "?")
_EXTENDS = re.compile(r'"extends"\s*:\s*(?:"([^"]*)"|\[([^\]]*)\])')
# The conditions Node matches, in an exports/imports object, for each way a module is loaded; the
# first key in the object's order among them wins. Other well-known conditions are inactive without
# --conditions; an unknown one leaves the entry unreadable.
_CONDITIONS = {"require": frozenset({"node", "require", "node-addons", "module-sync", "default"}),
               "import": frozenset({"node", "import", "node-addons", "module-sync", "default"})}
_INACTIVE_CONDITIONS = frozenset({"require", "import", "types", "typings", "browser", "deno", "bun", "workerd",
                                  "edge-light", "react-native", "electron", "development", "production", "worker",
                                  "module", "esnext", "source", "style", "sass", "css"})


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

@dataclass(frozen=True)
class Site:
    """An import/require site. ``specifier`` is the literal it names (a fold of literals joined by
    ``+`` counts), None when computed at runtime: a fail-closed boundary (#587). ``inside_exec``:
    it sits in a child-process call's arguments. ``kind``: "require" or "import", which decides a
    conditional map entry as Node does."""
    specifier: object
    inside_exec: bool
    kind: str = "require"


@dataclass(frozen=True)
class Atom:
    """A non-literal argument no loaded module can change: process.execPath, process.env, __dirname,
    a number or a boolean."""
    name: str


@dataclass(frozen=True)
class Child:
    """A child-process call site. ``shape`` says how its arguments name what runs: "shell" (a
    command line), "file" (a file and an argument array), "fork" (a module Node runs) or "other"
    (a call the scan does not read). ``arguments`` holds the parsed literal arguments (strings,
    arrays, option objects, atoms), or None when any is computed."""
    start: int
    end: int
    shape: str
    arguments: object


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


def _scan(text):
    """(mask, strings, templates): the scan behind js_scan, with every template literal's span."""
    mask, strings, templates, n = list(text), [], [], len(text)
    depths, opened = [], []  # open braces inside each enclosing template substitution; where each began

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
    if text.startswith("#!"):  # Node's hashbang line is a comment at offset 0 only
        i = text.find("\n")
        i = n if i < 0 else i
        blank(0, i)
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
                templates.append((start, end))
                blank(start, end)
            else:
                opened.append(start)
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
                end, closed, _ = template(start + 1)
                blank(start, end)
                if closed:
                    templates.append((opened.pop(), end))
        i = end
    if depths:
        raise Unreadable("a template literal is still open at the end of the file")
    return "".join(mask), strings, templates


def js_scan(text):
    """(mask, strings): ``mask`` blanks comments, regex literals and quoted literals (same length),
    and ``strings`` lists each quoted literal as (start, end, value), so call structure and literal
    arguments can be read without executing anything.

    A template literal without substitutions is a literal. One with ``${...}`` is computed:
    its text is blanked but its opening backtick is not, so a call taking it as an argument
    is never read as a literal, and the code inside each substitution stays visible. A hashbang
    line is a comment.

    Raises Unreadable for text that is not JavaScript the runtime would run to the end: a string
    reaching a newline, a comment, regex or template still open at the end of the file."""
    mask, strings, _ = _scan(text)
    return mask, strings


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


class _Computed(Exception):
    pass


def _literal_arguments(text, mask, strings, start, end):
    """The arguments of the call whose parentheses span ``start``..``end`` when each is a literal:
    a string (strings joined by ``+`` fold), an array of such, an object of such (an option object;
    ``...process.env`` is allowed in it) or an atom; else None, the call is computed."""
    literal = {s: (e, v) for s, e, v in strings}
    tokens, index = [], start + 1
    while index < end:
        if index in literal:
            after, value = literal[index]
            tokens.append(("str", value))
            index = after
            continue
        if mask[index] != text[index] or text[index].isspace():
            index += 1  # blanked by the scan (a comment, or a regex no literal argument holds), or space
            continue
        match = _ARGUMENT_TOKEN.match(mask, index)
        if match is None:
            return None
        tokens.append((match.lastgroup, match.group(match.lastgroup)))
        index = match.end()

    def value(position):
        kind, item = tokens[position]
        if kind == "str":
            position += 1
            while position + 1 < len(tokens) and tokens[position] == ("punct", "+") and tokens[position + 1][0] == "str":
                item += tokens[position + 1][1]
                position += 2
            return item, position
        if kind == "atom":
            return Atom(item), position + 1
        if (kind, item) == ("punct", "["):
            items, position = [], position + 1
            while tokens[position] != ("punct", "]"):
                spread = tokens[position] == ("punct", "...")
                element, position = value(position + 1 if spread else position)
                if spread and not isinstance(element, (list, Atom)):
                    raise _Computed
                items.extend(element if isinstance(element, list) else [element])
                if tokens[position] == ("punct", ","):
                    position += 1
            return items, position + 1
        if (kind, item) == ("punct", "{"):
            fields, position = {}, position + 1
            while tokens[position] != ("punct", "}"):
                if tokens[position] == ("punct", "..."):
                    element, position = value(position + 1)
                    if isinstance(element, dict):
                        fields.update(element)
                    elif element != Atom("process.env"):
                        raise _Computed
                else:
                    kind, name = tokens[position]
                    if kind not in ("str", "key") or tokens[position + 1] != ("punct", ":"):
                        raise _Computed
                    fields[name], position = value(position + 2)
                if tokens[position] == ("punct", ","):
                    position += 1
            return fields, position + 1
        raise _Computed

    arguments, position = [], 0
    try:
        while position < len(tokens):
            item, position = value(position)
            arguments.append(item)
            if position < len(tokens):
                if tokens[position] != ("punct", ","):
                    raise _Computed
                position += 1
    except (_Computed, IndexError):
        return None
    return arguments


def _destructured(declared):
    """{local name: imported name} of a destructuring or import clause ``{a, b: c, d as e}``, or
    None when it holds a rest or a nested pattern."""
    names = {}
    for item in declared.strip("{} \t\n").split(","):
        item = item.strip()
        if not item:
            continue
        parts = re.split(r"\s*:\s*|\s+as\s+", item)
        if len(parts) > 2 or not all(re.fullmatch(_IDENTIFIER, part) for part in parts):
            return None
        names[parts[-1]] = parts[0]
    return names


def _child_process_sites(text, mask, strings, templates):
    """([Child], opaque): the child-process call sites of JS source, and why the file's use of a
    child-process module cannot be read as calls ("" when it can): a binding passed on, promisified
    or rebound, a member read without a call, or the module loaded in a form the scan does not read.
    A call by a child_process function's own name counts whatever bound it."""
    calls, opaque, excluded, members, namespaces = [], [], [], {}, {}
    n = len(text)

    def bind(declared, position, core):
        excluded.append((position, position + len(declared)))
        if declared.startswith("{"):
            names = _destructured(declared)
            if names is None:
                opaque.append(f"{declared} binds a child-process module in a form the scan does not read")
                return
            for local, name in names.items():
                members[local] = (name, core)
        else:
            namespaces[declared] = core

    for start, end, value in strings:
        if value not in CHILD_PROCESS_MODULES:
            continue
        core = value in CORE_CHILD_PROCESS
        before = mask[max(0, start - 300):start]
        after = mask[end:end + 300]
        if _AWAITED_BEFORE.search(before):
            member = re.match(r"\s*\)\s*\)", after)
            member = member and _MEMBER_CALL.match(after, member.end())
            if member:
                calls.append((member.group(1), end + member.end() - 1, core))
                continue
        elif _CALL_BEFORE.search(before):
            close = re.match(r"\s*\)", after)
            member = close and _MEMBER_CALL.match(after, close.end())
            if member:
                calls.append((member.group(1), end + member.end() - 1, core))
                continue
            binding = _BINDING_BEFORE.search(before)
            if binding and close:
                bind(binding.group(1), start - len(before) + binding.start(1), core)
                continue
        else:
            static = _STATIC_IMPORT_BEFORE.search(before)
            if static:
                declared = static.group(1) or static.group(2) or static.group(3)
                bind(declared, start - len(before) + static.start(static.lastindex), core)
                continue
            if _EFFECT_IMPORT_BEFORE.search(before):
                continue  # import 'x' for its effect binds nothing
        opaque.append(f"{value} is loaded in a form the scan does not read")

    def free(local):
        pattern = re.compile(r"(?<![\w$.])" + re.escape(local) + r"(?![\w$])")
        return [match for match in pattern.finditer(mask) if not any(a <= match.start() < b for a, b in excluded)]

    children = []
    for local, (name, core) in members.items():
        for match in free(local):
            if local == "$":
                position = match.end()
                while position < n and text[position] in " \t":
                    position += 1
                span = next(((a, b) for a, b in templates if a == position), None)
                if span is None:
                    opaque.append(f"{local} is used other than as a command template")
                    continue
                literal = next((value for start, _, value in strings if start == position), None)
                children.append(Child(span[0], span[1], "shell", None if literal is None else [literal]))
                continue
            call = _CALL.match(mask, match.end())
            if call is None:
                opaque.append(f"{local} (child_process.{name}) is used other than as a call")
                continue
            calls.append((name, call.end() - 1, core))
    for local, core in namespaces.items():
        for match in free(local):
            member = _MEMBER_CALL.match(mask, match.end())
            if member is None:
                opaque.append(f"{local} is used other than as a call of one of its functions")
                continue
            calls.append((member.group(1), member.end() - 1, core))
    opened = {open_paren for _, open_paren, _ in calls}
    for match in _JS_EXEC_CALL.finditer(mask):
        if match.end() - 1 not in opened:
            calls.append((match.group(1), match.end() - 1, True))
    for name, open_paren, core in calls:
        start, end = _argument_span(mask, open_paren)
        arguments = _literal_arguments(text, mask, strings, start, end)
        if core:
            shape = CHILD_PROCESS_SHAPES.get(name, "other")
        else:
            shape = "file" if arguments and len(arguments) > 1 and isinstance(arguments[1], list) else "shell"
        children.append(Child(start, end + 1, shape, arguments))
    return children, "; ".join(dict.fromkeys(opaque))


def scan_source(text):
    """([Site], [Child], opaque) for JS source: its import/require sites, the child processes it
    starts, and why its child-process use is opaque (``_child_process_sites``). Raises Unreadable."""
    mask, strings, templates = _scan(text)
    children, opaque = _child_process_sites(text, mask, strings, templates)
    by_start = {start: (end, value) for start, end, value in strings}
    sites = []
    for match in _JS_MODULE_CALL.finditer(mask):
        open_paren = match.end() - 1
        start, end = _argument_span(mask, open_paren)
        inner = [literal for literal in strings if start < literal[0] < end]
        covered = {index for literal in inner for index in range(literal[0], literal[1])}
        residue = "".join(text[index] for index in range(start + 1, end)
                          if index not in covered and not text[index].isspace() and mask[index] == text[index])
        specifier = "".join(literal[2] for literal in inner) if not residue.strip("+") else None
        kind = "require" if text[match.start():match.start() + 7] == "require" else "import"
        sites.append(Site(specifier, any(child.start <= open_paren < child.end for child in children), kind))
    for pattern in (r"\bfrom\s*(['\"])", r"(?<![\w$.])import\s*(['\"])"):
        for match in re.finditer(pattern, text):
            quote = match.start(1)
            if quote in by_start:  # a real literal, not text inside a comment
                sites.append(Site(by_start[quote][1], False, "import"))
    return sites, children, opaque


def import_sites(text):
    """Every import/require site of JS source (``Site``). Raises Unreadable (js_scan)."""
    return scan_source(text)[0]


def exec_computed(text):
    """Whether what the child processes of JS source run may come from any module the file loads:
    a child-process call takes a computed argument, or the file's use of a child-process module
    cannot be read as calls. Raises Unreadable."""
    _, children, opaque = scan_source(text)
    return bool(opaque) or any(child.arguments is None for child in children)


def _child_command(child, directory, folder):
    """(command line, directory it runs in) for a child-process call with literal arguments, read
    as `node x` for a fork and as a shell line otherwise, or None when the scan does not read its
    shape or its working directory (a cwd option other than a literal path inside the tree,
    __dirname or process.cwd())."""
    options = next((item for item in child.arguments if isinstance(item, dict)), {})
    cwd = options.get("cwd")
    if cwd is None or cwd == Atom("process.cwd()"):
        where = directory
    elif cwd == Atom("__dirname"):
        where = folder
    elif isinstance(cwd, str) and not cwd.startswith("/"):
        where = posixpath.normpath(posixpath.join(directory, cwd))
        if where == ".." or where.startswith("../"):
            return None
    else:
        return None
    words = [item for item in child.arguments if not isinstance(item, dict)]
    if not words or child.shape == "other":
        return None
    first = "node" if words[0] == Atom("process.execPath") else words[0]
    if not isinstance(first, str):
        return None
    if child.shape == "shell":
        return first, where
    rest = words[1] if len(words) > 1 and isinstance(words[1], list) else []
    names = (["node"] if child.shape == "fork" else []) + [first] + [item for item in rest if isinstance(item, str)]
    shell = options.get("shell")
    line = " ".join(names) if shell == Atom("true") or isinstance(shell, str) else shlex.join(names)
    return line, where


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


def _tokens(command):
    """Shell words with operators and parentheses as their own tokens whatever the spacing
    (``(cd lib && node run.js)``, ``node a.js;node b.js``). ValueError when a quote is unbalanced."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&")
    lexer.whitespace_split = True
    return list(lexer)


def _shell_bindings(command, directory, tracked):
    """([(word, directory it is read in)], unestablished reason) for one command line.

    A shell's -c operand is itself a command line, expanded in the directory active where that
    shell appears, whatever option words precede it (`bash -lc '...'`, `sh -e -c '...'`), so
    `sh -c 'node run-tests.js'` pins the base runner; so are the value of a ``NAME=value`` or
    ``--flag=value`` word (NODE_OPTIONS, --require=) and, read as JavaScript, the operand of
    `node -e` after any node options (its relative require/import literals are words, the
    child processes it starts with literal arguments are command lines). A literal `cd <dir>`
    followed by `&&`, `;` or `||` moves that directory for what follows, and a subshell's `(`
    ... `)` restores it: `sh -c 'cd lib && node run-tests.js'` must pin lib/run-tests.js, not a
    root file of the same name. A computed cd target, a variable, a glob or a command
    substitution cannot establish the boundary.
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

    def nested(text, where=None):
        found, reason = _shell_bindings(text, current if where is None else where, tracked)
        if reason:
            return reason
        bindings.extend(found)
        return ""

    def inline(flag, code):
        try:
            sites, children, _ = scan_source(code)
        except Unreadable as error:
            return f"the inline JavaScript of {flag} cannot be read ({error})"
        for site in sites:
            if site.specifier is None:
                return f"the inline JavaScript of {flag} selects a module through a computed path"
            if site.specifier.startswith(("./", "../")):
                segment.append(site.specifier)
        for child in children:
            started = _child_command(child, current, current) if child.arguments is not None else None
            if started:
                reason = nested(*started)
                if reason:
                    return reason
        return ""

    index = 0
    while index < len(parsed):
        word = parsed[index]
        if posixpath.basename(word) in SHELLS:
            # `sh [options] -c <command line> [args]`; without -c the next word is a script file, a word.
            cursor, operand = index + 1, None
            while cursor < len(parsed) and parsed[cursor][:1] in ("-", "+") and len(parsed[cursor]) > 1:
                option = parsed[cursor]
                if option == "--":
                    cursor += 1
                    break
                if not option.startswith("--") and "c" in option[1:]:
                    operand = cursor + 1
                    cursor += 2
                    break
                cursor += 2 if not option.startswith("--") and option[-1] in "oO" else 1
            if operand is not None:
                if operand >= len(parsed):
                    return [], f"{word} -c has no command line"
                reason = finish(False) if segment else ""
                if reason:
                    return [], reason
                reason = nested(parsed[operand])
                if reason:
                    return [], reason
                index = cursor
                continue
        if posixpath.basename(word) in NODE:
            segment.append(word)
            cursor = index + 1
            while cursor < len(parsed):
                option = parsed[cursor]
                if option in EVAL_FLAGS:
                    if cursor + 1 >= len(parsed):
                        return [], f"{word} {option} has no code"
                    reason = inline(f"{word} {option}", parsed[cursor + 1])
                    if reason:
                        return [], reason
                    cursor += 2
                    break  # what follows is the code's argv: ordinary words
                if option in OPERATORS or option in REDIRECTIONS or option in ("(", ")") or not option.startswith("-"):
                    break  # the script file, or the end of this command: ordinary words
                if option in NODE_MODULE_OPTIONS and cursor + 1 < len(parsed):
                    segment.append(parsed[cursor + 1])
                    cursor += 2
                    continue
                value = _OPTION_VALUE.match(option)
                if value and value.group(2):
                    reason = nested(value.group(2))
                    if reason:
                        return [], reason
                elif any(mark in option for mark in _COMPUTED_MARKS):
                    return [], f"the word {option!r} is computed by the shell, so the suite definition boundary is unestablished"
                cursor += 1
            index = cursor
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


def _script_references(bindings, scripts, every):
    """([(script name, everywhere)], consumed word indexes): the scripts a command line runs by
    name. After a package manager or task runner, its subcommands, selectors and option values
    (`npm --workspace pkg run X`, `pnpm --filter pkg X`, `yarn workspace pkg run X`, `turbo run X`)
    lead to a script name that may run in every workspace package (``every``), `npm t` being
    `npm test`; a bare word (or an `npm:` word) naming a script, as run-s, run-p, npm-run-all and
    concurrently take them, runs in this manifest's ``scripts``. The consumed words name no file."""
    words = [word for word, _ in bindings]
    found, consumed = {}, set()
    index = 0
    while index < len(words):
        word = words[index]
        name = posixpath.basename(word)
        if name in PACKAGE_MANAGERS or name in TASK_RUNNERS:
            consumed.add(index)
            cursor, expecting = index + 1, None
            while cursor < len(words):
                item = words[cursor]
                if posixpath.basename(item) in PACKAGE_MANAGERS | TASK_RUNNERS | NODE | SHELLS:
                    break  # another command the invocation runs (`yarn node x.js`)
                consumed.add(cursor)
                cursor += 1
                if item == "--":
                    break  # what follows goes to the script
                if item.startswith("-"):
                    continue
                if expecting == "package":
                    expecting = None
                    continue
                if item in ("run", "run-script"):
                    expecting = "script"
                    continue
                if expecting == "script":
                    found[item] = True
                    break
                if item == "workspace" and name.startswith("yarn"):
                    expecting = "package"
                    continue
                script = SCRIPT_ALIASES.get(item, item)
                if script in LIFECYCLE or (not name.startswith("npm") and script in every):
                    found[script] = True
                    break
                if script in NOT_SCRIPTS:
                    break
                # a selector or an option's value: `--workspace packages/pkg`, `--filter pkg`
            index = cursor
            continue
        script = word[4:] if word.startswith("npm:") else word
        if script in scripts:
            found.setdefault(script, False)
            consumed.add(index)
        index += 1
    return [(name, everywhere) for name, everywhere in found.items()
            if name in (every if everywhere else scripts)], consumed


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


def _conditions(value, kind="require"):
    """The target a conditional exports/imports value names for a load of ``kind`` ("require" or
    "import"), as Node chooses it: the first key in the object's order among the conditions active
    for that kind. Raises Unreadable for a condition Node activates only on request."""
    for _ in range(8):
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            value = value[0] if value else None
        elif isinstance(value, dict):
            chosen = None
            for key, item in value.items():
                if key in _CONDITIONS[kind]:
                    chosen = item
                    break
                if key not in _INACTIVE_CONDITIONS:
                    raise Unreadable(f"the condition {key!r} is matched only when node runs with --conditions")
            value = chosen
        else:
            return None
    return None


def _mapped(mapping, key, kind):
    """The target an ``imports`` or subpath ``exports`` map names for ``key``, as Node matches it:
    the entry itself, else the pattern entry (one ``*``) with the longest prefix, its ``*`` filled
    with what the key's matched. None when the map names nothing for it."""
    if key in mapping:
        return _conditions(mapping[key], kind)
    best = None
    for pattern in mapping:
        if pattern.count("*") != 1:
            continue
        prefix, suffix = pattern.split("*")
        if (key.startswith(prefix) and key.endswith(suffix) and len(key) >= len(prefix) + len(suffix)
                and (best is None or (len(prefix), len(suffix)) > (len(best[0]), len(best[1])))):
            best = (prefix, suffix, pattern)
    if best is None:
        return None
    target = _conditions(mapping[best[2]], kind)
    return target.replace("*", key[len(best[0]):len(key) - len(best[1])]) if isinstance(target, str) else target


def _export_target(exports, key, kind="require"):
    """The relative target an ``exports`` field names for subpath ``key`` ("." or "./sub"), or None."""
    if isinstance(exports, dict) and any(name.startswith(".") for name in exports):
        return _mapped(exports, key, kind)
    return _conditions(exports, kind) if key == "." else None


def definition(workspace, base, suite_command, *, is_test, base_patch=None) -> Definition:
    """The base suite definition (module docstring). ``is_test`` is the verifier's test-path rule.

    Pinned by rule: definition files (``is_definition_file``) at any depth and what a link
    among them points at; every test file and what a linked test points at; and the closure of
    paths reachable from the words of the suite command and of the package scripts it runs,
    from the child processes a runner starts with literal arguments, and from the JavaScript
    configuration in the folders those commands run in and above. A runner's literal is followed
    through links and through the manifest fields that resolve a ``#`` import, a self-reference
    or a folder; those fields then stay the base's, and so does every manifest an added one
    could re-scope a pinned file through. Files the base tests import are product code and
    never pinned; a runner that reaches one inside a child-process argument, or at all when it
    starts a child process from computed arguments, a configuration file that reaches one, or a
    script word that names one, is a conflict the closure cannot resolve. A word or literal
    naming a path the effective base does not hold is pinned absent, and so is every name Node
    would try before the file it loads; any other reach, a computed path or word, a computed or
    missing cd target, an unreadable file, an opaque use of a child-process module or an
    unsplittable script leaves the closure unestablished (``boundary``), and then only
    definition files, link targets and tests are pinned. Raises OSError, RuntimeError or
    ValueError when the effective base cannot be read.
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
        absent = set()

        def scope(directory):
            """The nearest manifest at or above ``directory``. Each manifest it steps over stays
            absent: an added one would become the package scope of what resolves from there."""
            while True:
                manifest = posixpath.normpath(posixpath.join(directory, MANIFEST))
                if manifest in parsed:
                    return manifest
                absent.add(manifest)
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

        def resolve(literal, directory, *, root_fallback=False, specifier=True, kind="require"):
            """(tracked file, link paths passed, the names tried before it) for a module ``specifier``
            loaded by ``kind`` in ``directory``, or for a shell word (``specifier=False``, always a
            path): a relative path completed as Node completes it (a file, then a folder's manifest
            main or exports, then its index), a `#` import through the scope's imports map, a
            self-reference through its exports. The names tried stay absent so nothing shadows the
            file; without a file, every name is tried. A package or core module gives (None, [], []).
            Raises Unreadable for a map entry that is not a literal."""
            if specifier and literal.startswith("#"):
                manifest = scope(directory)
                if manifest is None:
                    raise Unreadable(f"{literal} has no package.json to resolve through in the base")
                use(manifest, "imports")  # whatever the entry says, the map decides what loads
                imports = parsed[manifest].get("imports")
                target = _mapped(imports, literal, kind) if isinstance(imports, dict) else None
                if not isinstance(target, str) or not target.startswith("./"):
                    raise Unreadable(f"{literal} is not a literal entry of the imports map in the base {manifest}")
                return resolve(target, posixpath.dirname(manifest) or ".", kind=kind)
            if specifier and not literal.startswith(("./", "../")) and literal not in (".", ".."):
                manifest = scope(directory)
                name = (parsed.get(manifest) or {}).get("name")
                if manifest and isinstance(name, str) and (literal == name or literal.startswith(name + "/")):
                    use(manifest, "exports", "name")
                    key = "." if literal == name else "./" + literal[len(name) + 1:]
                    target = _export_target(parsed[manifest].get("exports"), key, kind)
                    if not isinstance(target, str) or not target.startswith("./"):
                        raise Unreadable(f"{literal} is not a literal entry of the exports map in the base {manifest}")
                    return resolve(target, posixpath.dirname(manifest) or ".", kind=kind)
                return None, [], []  # a package or core module, resolved by the runtime
            tried = []
            for root in dict.fromkeys((directory, ".") if root_fallback else (directory,)):
                joined = posixpath.normpath(posixpath.join(root, literal))
                if joined == ".." or joined.startswith("../"):
                    continue
                for name in [joined] + [joined + extension for extension in MODULE_EXTENSIONS]:
                    found, passed = file_at(name)
                    if found:
                        return found, passed, tried
                    tried.append(name)
                manifest = posixpath.normpath(posixpath.join(joined, MANIFEST))
                real, passed = realpath(manifest)
                if real in parsed:
                    data, base_dir = parsed[real], posixpath.dirname(real) or "."
                    for key in ("exports", "main"):
                        if data.get(key) is None:
                            continue
                        use(real, key)  # whatever the entry says, the field decides what loads
                        target = _export_target(data[key], ".", kind) if key == "exports" else data[key]
                        if not isinstance(target, str):
                            continue
                        entry = posixpath.normpath(posixpath.join(base_dir, target))
                        for name in [entry] + [entry + ext for ext in MODULE_EXTENSIONS] + [
                                posixpath.normpath(posixpath.join(entry, "index" + ext)) for ext in MODULE_EXTENSIONS]:
                            found, more = file_at(name)
                            if found:
                                return found, passed + more, tried
                            tried.append(name)
                        break
                else:
                    tried.append(manifest)  # a folder's manifest would name its main before any index
                for name in [posixpath.normpath(posixpath.join(joined, "index" + ext)) for ext in MODULE_EXTENSIONS]:
                    found, passed = file_at(name)
                    if found:
                        return found, passed, tried
                    tried.append(name)
            return None, [], tried

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
            folder = posixpath.dirname(real if real in tracked else path) or "."
            scope(folder)  # the manifests an added one could re-scope this file through stay absent
            try:
                sites = import_sites(text(path))
            except Unreadable:
                continue  # a test the scanner cannot read names no product; nothing is placed for it
            for site in sites:
                if not site.specifier:
                    continue
                try:
                    target, passed, _ = resolve(site.specifier, folder, kind=site.kind)
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

        # The runner closure: {path: (directory it runs in, "runner" or "definition")}. A runner is
        # scanned for what it loads and starts; a definition file (test-runner and transpiler
        # configuration) for what it loads, which is configuration too.
        runners, queue, configured = {}, [], set()

        def add(path, directory, kind):
            if path not in runners:
                runners[path] = (directory, kind)
                queue.append(path)

        def configure(path, directory):
            """A definition file: JavaScript is scanned; a JSON ``extends`` names more configuration."""
            if path in runners:
                return
            suffix = PurePosixPath(path).suffix
            if suffix in JS_SUFFIXES:
                add(path, directory, "definition")
                return
            runners[path] = (directory, "definition")
            if not PurePosixPath(path).name.startswith(EXTENDING_CONFIGURATION):
                return
            source = text(path)
            try:
                mask, strings, _ = _scan(source)  # the configuration may hold comments
            except Unreadable:
                return
            quoted = bytearray(len(source))
            for start, end, _ in strings:
                quoted[start:end] = b"\x01" * (end - start)
            cleaned = "".join(char if quoted[index] or mask[index] == char else " " for index, char in enumerate(source))
            for match in _EXTENDS.finditer(cleaned):
                for literal in [match.group(1)] if match.group(1) is not None else re.findall(r'"([^"]*)"', match.group(2)):
                    if literal.startswith(("./", "../")):
                        target, passed, tried = resolve(literal, posixpath.dirname(path) or ".", specifier=False)
                        absent.update(tried)
                        rule.update(passed)
                        if target:
                            rule.add(target)
                            configure(target, directory)

        def enter(directory):
            """A command runs in ``directory``: the configuration there and in every folder above is
            read by what it runs (Babel and TypeScript walk up), so it is scanned."""
            while directory not in configured:
                configured.add(directory)
                for path in sorted(files):
                    if (posixpath.dirname(path) or ".") == directory:
                        configure(path, directory)
                if directory in ("", "."):
                    return
                directory = posixpath.dirname(directory) or "."

        while commands or queue:
            if commands:
                origin, command, directory, manifest = commands.pop(0)
                try:
                    bindings, reason = _shell_bindings(command, directory, tracked)
                except ValueError as error:  # an unbalanced quote: what the script runs is unknown
                    return found(rule, f"{origin} cannot be split into shell words ({error})")
                if reason:
                    return found(rule, f"in {origin}, {reason}")
                enter(directory)
                for _, effective in bindings:
                    enter(effective)
                references, consumed = _script_references(bindings, scripts[manifest] if manifest else every, every)
                for name, everywhere in references:
                    seed(name, None if everywhere else manifest)
                for index, (word, effective) in enumerate(bindings):
                    base_name = posixpath.basename(word)
                    if (index in consumed or word.startswith("-")
                            or base_name in PACKAGE_MANAGERS | TASK_RUNNERS | NODE | SHELLS):
                        continue
                    name = posixpath.normpath(posixpath.join(effective, word))
                    if name.startswith(("../", "/")) or name == "..":
                        continue
                    try:
                        target, passed, tried = resolve(word, effective, root_fallback=True, specifier=False)
                    except Unreadable as error:
                        return found(rule, f"{origin} runs {word}: {error}")
                    absent.update(tried)
                    if target in product:
                        conflicts.add(target)
                        return found(rule, f"{origin} runs {target}, which the base tests also import, so the suite "
                                           "definition boundary is unestablished")
                    if target is None:
                        continue
                    rule.update(passed)
                    add(target, effective, "runner")
                continue
            path = queue.pop(0)
            directory, kind = runners[path]
            if is_test(path) or (is_definition_file(path) and PurePosixPath(path).suffix not in JS_SUFFIXES):
                continue  # tests are pinned wholesale, never scanned; a JSON manifest or config loads nothing
            folder = posixpath.dirname(path) or "."
            scope(folder)  # the manifests an added one could re-scope this file through stay absent
            try:
                sites, children, opaque = scan_source(text(path))
            except Unreadable as error:
                return found(rule, f"{path} cannot be read ({error})")
            computed = bool(opaque) or any(child.arguments is None for child in children)
            for site in sites:
                if site.specifier is None:
                    return found(rule, f"{path} selects its suite inputs through a computed path")
                try:
                    target, passed, tried = resolve(site.specifier, folder, kind=site.kind)
                except Unreadable as error:
                    return found(rule, f"{path} reaches {site.specifier}: {error}")
                absent.update(tried)  # the base has nothing there, and neither does the extra run
                if target is None:
                    continue
                rule.update(passed)
                if target in product:
                    # A runner may load the product in-process (#587 T16); what it RUNS may come from that
                    # file only through a child-process argument: read inside one, or computed from
                    # anything loaded. Configuration that loads it is that file's.
                    if kind == "definition" or site.inside_exec or computed:
                        conflicts.add(target)
                        return found(rule, f"{path} reaches {target}, which the base tests also import, so the "
                                           "suite definition boundary is unestablished" + (
                                               f" ({opaque})" if opaque and not site.inside_exec else ""))
                    continue
                if is_test(target):
                    continue
                if is_definition_file(target):
                    configure(target, directory)
                    continue
                if kind == "definition" or site.inside_exec:
                    add(target, directory, kind)
                    continue
                return found(rule, f"{path} reaches {target}, which is neither a test, a manifest "
                                   "nor an input of the executed test command")
            for child in children:
                started = _child_command(child, directory, folder) if child.arguments is not None else None
                if started:
                    commands.append((f"the child process {path} starts", started[0], started[1], scope(started[1])))
        pinned = rule | set(runners) | absent
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
    path)]: a file or link the candidate put where a folder holding a pinned path was, or a path
    it put below a pinned file or link, which no overlay can place without losing that path).
    """
    workspace = Path(workspace)
    measured = dict(changes)
    for path in found.patched - changes.keys():
        measured[path] = "modified" if os.path.lexists(workspace / path) else "deleted"
    present = sorted(found.present)
    blocked = []
    for path in sorted(measured):
        if measured[path] == "deleted":
            continue
        held = next((name for name in present if name.startswith(path + "/")), None)
        if held is None:
            held = next((name for name in present if path.startswith(name + "/")), None)
        if held is not None:
            blocked.append((path, held))
    unplaceable = {path for path, _ in blocked}
    overlay = {path: status for path, status in sorted(measured.items())
               if path not in found.pinned and path not in unplaceable
               and not is_definition_file(path) and not is_test(path)}
    manifests = {}
    for path, status in sorted(measured.items()):
        linked = path in found.pinned and path not in found.manifests  # a link on base, or pinned absent
        if (PurePosixPath(path).name != MANIFEST or status == "deleted" or is_test(path) or linked
                or path in unplaceable or (workspace / path).is_symlink()):
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
