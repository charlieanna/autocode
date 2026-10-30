"""Recognize literal Go test invocations and parse their native JSON results."""

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path


def _words(command):
    if not isinstance(command, str) or any(c in command for c in "\x00\n\r"):
        return None
    quote, escaped = None, False
    for character in command:
        if escaped:
            if quote == '"' and character in "$`":
                return None  # shlex and the shell interpret these escapes differently.
            escaped = False
        elif quote == "'":
            if character == "'":
                quote = None
        elif character == "\\":
            escaped = True
        elif quote == '"':
            if character == '"':
                quote = None
            elif character in "$`":
                return None
        elif character in "'\"":
            quote = character
        elif character in ";&|<>`$(){}*?[]#~":
            return None
    try:
        return shlex.split(command)
    except ValueError:
        return None


@dataclass(frozen=True)
class Invocation:
    prefix: tuple[str, ...]
    executable: str
    arguments: tuple[str, ...]
    command: str

    @property
    def json_flags(self):
        flags, index = [], 0
        while index < len(self.arguments):
            word = self.arguments[index]
            if word in ("-args", "--"):
                break  # The remaining flags belong to the test program.
            if word in ("-run", "-bench", "-fuzz"):
                index += 2  # A literal selector may itself start with -json.
                continue
            if word == "-json" or word.startswith("-json="):
                flags.append(word)
            index += 1
        return flags

    @property
    def json_disabled(self):
        # An explicit disabled/malformed reporter cannot supply native test
        # identities, even if the test program happens to print JSON itself.
        return any(
            word not in ("-json", "-json=1", "-json=t", "-json=T", "-json=true", "-json=TRUE", "-json=True")
            for word in self.json_flags
        )

    def instrument(self):
        # The leading flag is unambiguous. A later -json token could instead
        # be another flag's value; it must not prevent native JSON collection.
        # Repeating a later enabled reporter preserves its native semantics.
        if self.json_disabled or (
            self.arguments and (self.arguments[0] == "-json" or self.arguments[0].startswith("-json="))
        ):
            return self.command
        return shlex.join([*self.prefix, self.executable, "test", "-json", *self.arguments])


def parse(command):
    """Accept Go directly or behind env with literal NAME=value assignments.

    Quotes may protect literal regex characters; expansions, shell operators,
    globbing and shell wrappers never become a test collection claim.
    """
    words = _words(command)
    if not words:
        return None
    offset = 0
    if words[0] in ("env", "/usr/bin/env", "/bin/env"):
        offset = 1
        while offset < len(words) and re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", words[offset]):
            offset += 1
    tail = words[offset:]
    if len(tail) < 2 or Path(tail[0]).name not in ("go", "go.exe") or tail[1] != "test":
        return None
    return Invocation(tuple(words[:offset]), tail[0], tuple(tail[2:]), command)


def results(text):
    """Per-test results from `go test -json` events (one JSON object per line; other lines, such as
    compiler errors, are ignored). A test is ``package::Name`` (a subtest ``package::Name/sub``). A
    package that fails without running any test (it did not build, so its tests could not run, like
    a Python module that fails to import) is a collection error named ``package::[build failed]``.
    None when there are no events at all."""
    outcome, ran_in, package_fail, test_fail, seen = {}, set(), set(), set(), False
    for line in text.splitlines():
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or not event.get("Action"):
            continue
        seen = True
        package, test, action = event.get("Package") or "", event.get("Test"), event["Action"]
        if test:
            key = f"{package}::{test}"
            if action == "run":
                outcome.setdefault(key, None)
                ran_in.add(package)
            elif action in ("pass", "fail", "skip"):
                outcome[key] = action
                ran_in.add(package)
                if action == "fail":
                    test_fail.add(package)
        elif action == "fail":
            package_fail.add(package)
    if not seen:
        return None
    passed = {key for key, action in outcome.items() if action == "pass"}
    skipped = {key for key, action in outcome.items() if action == "skip"}
    failed = {key for key, action in outcome.items() if action == "fail"}
    collection = {f"{package}::[build failed]" for package in package_fail if package not in ran_in}
    # A package that ran tests and failed with none of them failing (TestMain returned nonzero, goleak, a
    # panic outside a test) is still a failure: the package line is all that names it (#1113).
    package_failed = {f"{package}::[package failed]" for package in package_fail if package in ran_in - test_fail}
    failed |= collection | package_failed
    # A test that started but never ended (the binary panicked or timed out) is not attributed.
    complete = all(action is not None for action in outcome.values())
    return {
        "passed": sorted(passed),
        "failed": sorted(failed),
        "skipped": sorted(skipped),
        "collection_errors": sorted(collection),
        "uncollected": sorted(collection),
        "total": len(outcome) + len(collection) + len(package_failed),
        "complete": complete,
    }


_TEST_FUNCTION = re.compile(r"^func\s+((?:Test|Example|Fuzz)\w*)\s*\(", re.M)
_MODULE = re.compile(r"^\s*module\s+(\S+)", re.M)


def definitions(text):
    """Top-level test, example and fuzz functions in a Go test file: name -> source, from ``func``
    to the closing brace at the start of a line (gofmt's layout)."""
    found = {}
    for match in _TEST_FUNCTION.finditer(text or ""):
        end = re.compile(r"^}", re.M).search(text, match.end())
        found[match.group(1)] = text[match.start() : end.end() if end else len(text)]
    return found


def module(go_mod):
    """The module path a go.mod declares, or None."""
    match = _MODULE.search(go_mod or "")
    return match.group(1).strip("\"`") if match else None


def changed_names(files, module_of):
    """``(package, function)`` for each test function the change adds or edits (#2505).

    ``files`` gives ``(path, base_text, candidate_text)`` for each changed Go test file (``base_text``
    is None for a new file); ``module_of(directory)`` gives ``(module_path, module_directory)`` for
    the nearest go.mod, or None. The package is the import path ``go test -json`` reports. None when
    a file's package cannot be named, so the caller keeps every test.
    """
    names = set()
    for path, before, after in files:
        directory = str(Path(path).parent).replace("\\", "/")
        owner = module_of(directory)
        if owner is None:
            return None
        path_of_module, root = owner
        relative = "" if directory == root else directory[len(root) + 1 :] if root not in ("", ".") else directory
        relative = "" if relative == "." else relative
        package = f"{path_of_module}/{relative}" if relative else path_of_module
        old = definitions(before)
        names |= {(package, name) for name, source in definitions(after).items() if old.get(name) != source}
    return names


def defined_in(names, test):
    """Whether a ``package::Name[/subtest]`` result is one of ``changed_names``."""
    package, _, name = test.partition("::")
    return (package, name.split("/", 1)[0]) in names
