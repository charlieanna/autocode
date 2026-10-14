"""Pure literal scanning for the base suite definition.

Inputs are source text or repository-relative paths. The scanner executes no
commands, reads no files and imports no AutoCode runtime modules.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import PurePosixPath

_MANIFEST_BASENAMES = frozenset(
    {
        "package.json",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        ".npmrc",
        ".yarnrc",
        ".yarnrc.yml",
        "bunfig.toml",
    }
)
# Runner configs the tool reads from the working directory without the suite command naming
# them. A candidate can otherwise narrow a spec list here and the definition run never sees it (#662).
_RUNNER_CONFIG_BASENAMES = frozenset(
    {
        ".mocharc",
        ".mocharc.js",
        ".mocharc.cjs",
        ".mocharc.mjs",
        ".mocharc.json",
        ".mocharc.jsonc",
        ".mocharc.yml",
        ".mocharc.yaml",
        "mocha.opts",
        "jest.config.js",
        "jest.config.cjs",
        "jest.config.mjs",
        "jest.config.json",
        "jest.config.ts",
        "vitest.config.js",
        "vitest.config.cjs",
        "vitest.config.mjs",
        "vitest.config.ts",
        "vitest.config.mts",
    }
)
DEFINITION_FILE_BASENAMES = _MANIFEST_BASENAMES | _RUNNER_CONFIG_BASENAMES
_JS_MODULE_CALL = re.compile(r"\b(?:require|import)\s*\(")
_JS_EXEC_CALL = re.compile(r"\b(?:exec(?:Sync|File(?:Sync)?)?|spawn(?:Sync)?|fork)\s*\(")
_JS_READ_CALL = re.compile(r"\b(?:readFileSync|readFile|createReadStream)\s*\(")
_SHELL_INTERPRETERS = frozenset({"sh", "bash", "dash", "zsh"})


def _definition_file(path: str) -> bool:
    """A suite-definition filename wherever it appears: manifests, lockfiles, registry and
    runner config. Prefix families (.pnpmfile.*, .yarn/releases/…) are definition inputs a
    single basename cannot name (#652): pnpm reads .pnpmfile.* hooks and yarn reads its
    release shim, so a narrowed spec list hidden behind either must still be a definition
    change, not an invisible edit."""
    posix = PurePosixPath(path)
    return (
        posix.name in DEFINITION_FILE_BASENAMES
        or posix.name.startswith(".pnpmfile.")
        or path.startswith(".yarn/releases/")
    )


_CODE_TOKEN = re.compile(r"//|/\*|[\"'`{}]")
_QUOTED = {quote: re.compile(quote + r"((?:[^" + quote + r"\\]|\\.)*)(?:" + quote + r"|\Z)", re.S) for quote in "'\""}
_TEMPLATE_CHUNK = re.compile(r"(?:[^`\\$]|\\.|\$(?!\{))*", re.S)
_ESCAPE = re.compile(r"\\(.)", re.S)


def _js_scan(text):
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


def _import_sites(text):
    """Every import/require site of JS source as (specifier, inside_exec_argument).

    ``specifier`` is the argument when it is one quoted literal or a fold of literals
    joined by ``+``, and None when it is computed at runtime: a fail-closed boundary
    (#587). Static import and export-from specifiers are literals by grammar.
    """
    mask, strings = _js_scan(text)
    by_start = {start: (end, value) for start, end, value in strings}
    exec_spans = [_argument_span(mask, match.end() - 1) for match in _JS_EXEC_CALL.finditer(mask)]
    sites = []
    for match in _JS_MODULE_CALL.finditer(mask):
        open_paren = match.end() - 1
        start, end = _argument_span(mask, open_paren)
        inner = [literal for literal in strings if start < literal[0] < end]
        covered = {index for literal in inner for index in range(literal[0], literal[1])}
        residue = "".join(
            text[index]
            for index in range(start + 1, end)
            if index not in covered and not text[index].isspace() and mask[index] == text[index]
        )
        specifier = "".join(literal[2] for literal in inner) if not residue.strip("+") else None
        sites.append((specifier, any(a <= open_paren < b for a, b in exec_spans)))
    for pattern in (r"\bfrom\s*(['\"])", r"(?m)^\s*import\s*(['\"])"):
        for match in re.finditer(pattern, text):
            quote = match.start(1)
            if quote in by_start:  # a real literal, not text inside a comment
                sites.append((by_start[quote][1], False))
    return sites


def _shell_script(path, text):
    """True when ``path`` is a shell script the suite closure should read as a command line.

    A ``.sh`` / ``.bash`` file, or a shebang for sh, bash, dash or zsh. ``#!/usr/bin/env node``
    is not a shell script.
    """
    if PurePosixPath(path).suffix.lower() in {".sh", ".bash"}:
        return True
    line = text.lstrip("\ufeff").splitlines()[:1]
    if not line or not line[0].startswith("#!"):
        return False
    tokens = line[0][2:].split()
    interpreter = PurePosixPath(tokens[-1]).name if tokens else ""
    if interpreter in _SHELL_INTERPRETERS:
        return True
    return interpreter == "env" and any(token in _SHELL_INTERPRETERS for token in tokens)


def _first_argument_end(mask, open_paren, close):
    """Index of the first-argument comma at parenthesis depth 1, else ``close``."""
    depth = 0
    for index in range(open_paren, close + 1):
        if mask[index] == "(":
            depth += 1
        elif mask[index] == ")":
            depth -= 1
        elif mask[index] == "," and depth == 1:
            return index
    return close


def _static_path(literals, residue):
    """``("cwd"|"file", path)`` when the argument is a static path, else None.

    A quoted literal (or ``+`` fold of literals) is relative to the process cwd, which is how
    ``fs.readFileSync`` resolves it. ``path.join(__dirname, ...)`` of literals is relative to
    the file. A computed path (``path.join(__dirname, name)``, a variable) is None.
    """
    compact = "".join(char for char in residue if not char.isspace())
    if not compact.strip("+"):
        folded = "".join(literals)
        return ("cwd", folded) if folded else None
    if literals and re.fullmatch(r"(?:path\.)?join\(__dirname(?:,)+\)", compact):
        return ("file", posixpath.join(*literals))
    return None


def _specifier_between(text, mask, strings, start, end):
    inner = [literal for literal in strings if start < literal[0] < end]
    covered = {index for literal in inner for index in range(literal[0], literal[1])}
    residue = "".join(
        text[index]
        for index in range(start + 1, end)
        if index not in covered and not text[index].isspace() and mask[index] == text[index]
    )
    return _static_path([literal[2] for literal in inner], residue)


def _read_paths(text):
    """Literal paths a JS file passes to ``readFile``, ``readFileSync`` or ``createReadStream``.

    Computed arguments are omitted. Pinning the files that are named is what stops a candidate
    from narrowing a suite through a data file (#662); a path the scan cannot see stays unnamed.
    """
    mask, strings = _js_scan(text)
    paths = []
    for match in _JS_READ_CALL.finditer(mask):
        open_paren = match.end() - 1
        _start, close = _argument_span(mask, open_paren)
        end = _first_argument_end(mask, open_paren, close)
        specifier = _specifier_between(text, mask, strings, open_paren, end)
        if specifier:
            paths.append(specifier)
    return paths


def _exec_command_literals(text):
    """Quoted literals inside ``exec`` / ``spawn`` / ``fork`` argument lists.

    Each literal is something the runner may execute (``sh test/run.sh``, ``node list.js``) or an
    option word (``inherit``). The caller keeps the ones that name tracked files. A computed
    argument contributes only the literals it still contains, so a command built by concatenation
    does not by itself make the boundary unestablished.
    """
    mask, strings = _js_scan(text)
    literals = []
    for match in _JS_EXEC_CALL.finditer(mask):
        start, end = _argument_span(mask, match.end() - 1)
        literals.extend(literal[2] for literal in strings if start < literal[0] < end)
    return literals
