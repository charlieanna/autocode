"""A node:test file that runs another test runner cannot prove a named case (#380).

The runner reads node:test's own per-test results as named proof (autocode_node_tests). A
node:test case that spawns ``npm test -- -t NAME`` through child_process passes or fails on that
runner's exit code, and Vitest exits 0 when a ``-t`` filter matches no test: a live wrapper that
asserted exit 0 and the name in the output (npm echoes it) passed for a case that did not exist.
The runner cannot read the inner runner's per-test results, so no test in such a file is matched
to a case (autocode_regression.check_cases).

The check reads the file, never runs it. A file counts when it imports child_process and one of its
spawn or exec calls starts a test runner (npm/pnpm/yarn/bun test, npx vitest, Jest, Mocha,
playwright test, node --test). A test that runs the product's own CLI (``node cli.js add x``), git or
a build is unaffected, and runner names that appear only in assertions or comments do not count. A
command built at run time from values the file does not spell out can escape the check; the proof
guidance forbids wrappers in any form (autocode_test_cases.NAMED_PROOF_NOTE).
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path, PurePosixPath

try:
    from . import autocode_node_tests as node_tests
except ImportError:
    import autocode_node_tests as node_tests

SPAWN_MODULE = re.compile(r"(?:\bfrom\s*|\b(?:require|import)\s*\(\s*)['\"](?:(?:node:)?child_process|execa|cross-spawn)['\"]")
# How each spawn function takes its command: a shell string, an executable and its arguments, or a
# Node module (run by node).
SHELL_CALLS = ("exec", "execSync", "execaCommand", "execaCommandSync")
ARGV_CALLS = ("spawn", "spawnSync", "execFile", "execFileSync", "execa", "execaSync", "crossSpawn")
MODULE_CALLS = ("fork", "execaNode")
CALLS = SHELL_CALLS + ARGV_CALLS + MODULE_CALLS
RUNNERS = frozenset({"vitest", "jest", "mocha", "_mocha", "ava", "jasmine"})
RUNNER_COMMANDS = {"playwright": "test", "cypress": "run"}  # tools that run tests only through this command
RUNNER_PATH = re.compile(r"(?:^|/)(?:\.bin/_?(?:vitest|jest|mocha)|vitest/vitest\.mjs|jest(?:-cli)?/bin/jest\.js"
                         r"|mocha/bin/_?mocha(?:\.js)?)$")
PACKAGE_MANAGERS = frozenset({"npm", "pnpm", "yarn", "bun"})
TEST_COMMANDS = frozenset({"test", "t", "tst", "it", "cit", "install-test", "clean-install-test"})
# Options whose value is the next word: npm --prefix web test, pnpm --dir web test, npx -p vitest vitest,
# node -r x --test.
VALUE_OPTIONS = frozenset({"--prefix", "-C", "--cwd", "--dir", "--filter", "-F", "--workspace", "-w", "-p",
                           "--package", "-r", "--require", "--import", "--loader", "--experimental-loader",
                           "--env-file", "--run"})

STRING = re.compile(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"|`((?:[^`\\]|\\.)*)`", re.S)
TOKEN = re.compile(STRING.pattern + r"|/\*.*?\*/|//[^\n]*", re.S)
IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
PLACEHOLDER = "\0"  # an argument whose value the source does not spell out


def spawned_runner(source: str) -> str | None:
    """The test-runner command a JavaScript test file spawns through child_process, or None."""
    code = TOKEN.sub(lambda match: match.group() if match.group()[0] in "'\"`" else " ", source)
    if not SPAWN_MODULE.search(code):
        return None
    calls = {name: name for name in CALLS}
    # Renamed imports and promisified functions: {spawnSync: run}, {spawnSync as run}, promisify(cp.exec).
    for name, alias in re.findall(r"\b(" + "|".join(CALLS) + r")\s*(?::|\bas\b)\s*([A-Za-z_$][\w$]*)", code):
        calls.setdefault(alias, name)
    for alias, name in re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:[\w$]+\.)?promisify\(\s*"
                                  r"(?:[\w$]+\.)?(" + "|".join(CALLS) + r")\s*\)", code):
        calls.setdefault(alias, name)
    # spawn.sync(...) is cross-spawn's spawnSync.
    for match in re.finditer(r"(?<![\w$])(" + "|".join(map(re.escape, calls)) + r")(?:\.sync)?\s*\(", code):
        args = _split(code, match.end() - 1)
        if not args:
            continue
        kind = calls[match.group(1)]
        if kind in SHELL_CALLS:
            commands = [text for text in _strings(code, args[0]) if _shell_runner(text)]
            if commands:
                return commands[0].strip()
            continue
        rest = _elements(code, args[1]) if len(args) > 1 else []
        if kind in MODULE_CALLS:
            argvs = [["node", module, *rest] for module in _strings(code, args[0])]
        else:
            # A command with spaces runs in a shell (spawn(..., {shell: true})).
            argvs = [[command, *rest] for command in _strings(code, args[0])]
        for argv in argvs:
            if (" " in argv[0] and _shell_runner(argv[0])) or _argv_runner(argv):
                return " ".join("…" if word == PLACEHOLDER else word for word in argv).strip()
    return None


def refusals(root, test_ids) -> dict[str, str]:
    """Why each node:test test in ``test_ids`` cannot prove a named case, for those in a wrapper file.

    A node:test id is ``file::name`` (autocode_node_tests.results), the file relative to ``root``;
    ids of other runners, and files outside ``root``, are left alone.
    """
    found, refused = {}, {}
    for test in test_ids:
        file = test.split("::", 1)[0]
        path = PurePosixPath(file)
        if "::" not in test or path.is_absolute() or ".." in path.parts:
            continue
        if file not in found:
            found[file] = _refusal(root, file)
        if found[file]:
            refused[test] = found[file]
    return refused


def _refusal(root, file):
    if not node_tests.test_files(root, [file]):
        return None
    try:
        runner = spawned_runner((Path(root) / file).read_text(errors="replace"))
    except OSError:
        return None
    return reason(file, runner) if runner else None


def reason(file: str, runner: str) -> str:
    return (f"{file} runs another test runner through child_process ({runner[:120]}), so its tests pass on "
            "that runner's exit code, not on a named test (Vitest exits 0 when a -t filter matches no test), "
            "and they are not named proof: assert the behavior directly in node:test, or use a runner AutoCode "
            "reads per test (unittest/pytest, Go, native Vitest, or node:test via node --test)")


def _split(code, start):
    """The top-level comma-separated items between the bracket at ``start`` and its match."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    items, depth, begin, index = [], 0, start + 1, start + 1
    while index < len(code):
        char = code[index]
        if char in "'\"`":
            match = STRING.match(code, index)
            index = match.end() if match else index + 1
            continue
        if char in pairs:
            depth += 1
        elif char in ")]}":
            if depth == 0:
                items.append(code[begin:index])
                return [item for item in items if item.strip()]
            depth -= 1
        elif char == "," and depth == 0:
            items.append(code[begin:index])
            begin = index + 1
        index += 1
    return []  # unbalanced: not a call the check can read


def _initializer(code, name):
    """The expression a ``const``/``let``/``var`` declaration assigns to ``name``, or ""."""
    match = re.search(r"\b(?:const|let|var)\s+" + re.escape(name) + r"\s*=\s*", code)
    if not match:
        return ""
    if code[match.end():match.end() + 1] == "[":
        items = _split(code, match.end())
        return "[" + ",".join(items) + "]"
    # To the end of the statement: a semicolon, or a line break not followed by a continuation.
    end = re.compile(r";|\n(?!\s*[?:.+|&])").search(code, match.end())
    return code[match.end():end.start() if end else len(code)]


def _resolve(code, text, depth=0):
    """An expression, with a variable replaced by what its declaration assigns (two levels deep)."""
    text = text.strip()
    if re.fullmatch(r"process\.(?:execPath|argv0|argv\[0\])", text):
        return "'node'"
    if IDENTIFIER.fullmatch(text) and depth < 2:
        assigned = _initializer(code, text)
        return _resolve(code, assigned, depth + 1) if assigned else text
    return text


def _literal(match):
    value = next(group for group in match.groups() if group is not None)
    return re.sub(r"\$\{[^}]*\}", "X", value) if match.group(3) is not None else value


def _strings(code, text):
    """The string values an expression (or the variable it names) could be."""
    return [_literal(match) for match in STRING.finditer(_resolve(code, text))]


def _elements(code, text, depth=0):
    """An argument array as words, with PLACEHOLDER for values the source does not spell out."""
    text = _resolve(code, text)
    if not text.startswith("[") or depth > 3:
        return []
    words = []
    for item in _split(text, 0):
        item = item.strip()
        if item.startswith("..."):  # [...BASE_ARGS, name]
            words += _elements(code, item[3:], depth + 1) or [PLACEHOLDER]
            continue
        literal = STRING.fullmatch(item)
        if literal:
            words.append(_literal(literal))
        else:
            # require.resolve('vitest/vitest.mjs'), path.join(root, 'node_modules', '.bin', 'jest')
            parts = [_literal(match) for match in STRING.finditer(item)]
            words.append("/".join(parts) if parts else PLACEHOLDER)
    return words


def _name(word):
    return re.sub(r"\.(?:cmd|exe|js|mjs|cjs)$", "", PurePosixPath(word.replace("\\", "/")).name).lower()


def _after_options(words):
    index = 0
    while index < len(words) and words[index].startswith("-"):
        index += 2 if words[index] in VALUE_OPTIONS else 1
    return words[index:]


def _argv_runner(words) -> bool:
    """True when an argument vector, executable first, starts a test runner."""
    while words and (re.fullmatch(r"\w+=.*", words[0]) or _name(words[0]) in ("env", "cross-env")):
        words = words[1:]  # FOO=1 npm test, cross-env CI=1 vitest
    if not words:
        return False
    executable, rest = _name(words[0]), words[1:]
    command = _after_options(rest)
    if executable in RUNNERS:  # vitest, node_modules/.bin/jest
        return True
    if executable in RUNNER_COMMANDS:  # playwright test, but not playwright install
        return command[:1] == [RUNNER_COMMANDS[executable]]
    if executable in ("node", "nodejs"):  # node --test, node --run test, node node_modules/vitest/vitest.mjs
        options = rest[:len(rest) - len(command)]
        return ("--test" in options or any(flag == "--run" and _test_script(value) for flag, value in zip(options, options[1:]))
                or bool(command and RUNNER_PATH.search(command[0])))
    if executable in ("npx", "bunx", "pnpx"):  # npx vitest run, but not npx tsx cli.ts
        return _argv_runner(command)
    if executable in PACKAGE_MANAGERS:
        while command[:1] == ["workspace"] and len(command) > 2:  # yarn workspace web test
            command = _after_options(command[2:])
        verb, script = (command + [PLACEHOLDER, PLACEHOLDER])[:2]
        if verb in ("run", "run-script"):
            return _test_script(script)
        if verb in ("exec", "x", "dlx"):
            return _argv_runner(_after_options(command[1:]))
        return verb in TEST_COMMANDS or _argv_runner(command)  # npm test, yarn vitest, pnpm playwright test
    if executable in ("sh", "bash", "zsh", "dash", "cmd"):
        return any(flag in ("-c", "-lc", "/c") and _shell_runner(script) for flag, script in zip(rest, rest[1:]))
    return False


def _test_script(name) -> bool:
    """A package script that runs the tests by convention: test, test:unit, or one named after a runner."""
    return name == "test" or name.startswith("test:") or name in RUNNERS


def _shell_runner(command) -> bool:
    """True when a shell command line starts a test runner in any of its commands."""
    for part in re.split(r"&&|\|\||[;|()\n]", command):
        try:
            words = shlex.split(part)
        except ValueError:
            words = part.split()
        if _argv_runner(words):
            return True
    return False
