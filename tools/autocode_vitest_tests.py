"""Runner-owned per-test evidence from direct Vitest invocations.

A package script is supported only when it resolves to a single `vitest run`
command. JSON is read from a fresh sidecar, never from npm's echoed command or
ordinary stdout. This does not certify the semantics of arbitrary Node wrappers.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

try:
    from . import autocode_npm_scripts as npm_scripts
    from . import autocode_repeated_tests as repeated_tests
except ImportError:
    import autocode_npm_scripts as npm_scripts
    import autocode_repeated_tests as repeated_tests


_words = npm_scripts.words


def _vitest(words):
    if not words:
        return False
    if Path(words[0]).name in ("vitest", "vitest.cmd"):
        options = words[1:]
    elif Path(words[0]).name in ("node", "node.exe") and len(words) > 1 and Path(words[1]).name == "vitest.mjs":
        options = words[2:]
    elif Path(words[0]).name in ("npx", "npx.cmd") and words[1:3] == ["--no-install", "vitest"]:
        options = words[3:]
    else:
        return False
    return (
        bool(options)
        and (options[0] == "run" or "--run" in options)
        and "--" not in options
        and not any(
            word.lower().replace("-", "").startswith(("reporter", "outputfile", "watch", "ui", "mergereports"))
            or word == "-w"
            or word.startswith("--run=")
            for word in options
            if word.startswith("-")
        )
    )


# The Vitest major version AutoCode's reporter (autocode_vitest_reporter.cjs) reads.
REPORTER_MAJOR = "4"


def installed_version(tree) -> str | None:
    """The version of the Vitest that Node resolves from ``tree``, or None when none is found."""
    for directory in (Path(tree), *Path(tree).parents):
        try:
            package = json.loads((directory / "node_modules" / "vitest" / "package.json").read_text())
        except (OSError, ValueError):
            continue
        version = package.get("version") if isinstance(package, dict) else None
        return version if isinstance(version, str) else None
    return None


def command_words(command, tree=None):
    """Recognize a one-shot command whose JSON reporter the runner can own.

    Not when the project's own Vitest is another major version than the reporter reads: the reporter
    would end that run with an error of its own and turn a passing suite into a broken one (Vitest 3,
    setup sweep, docs/setup-sweep.md). Such a suite is read by its exit code, as any other command is.
    """
    if tree is not None and (version := installed_version(tree)) and version.split(".")[0] != REPORTER_MAJOR:
        return None
    words = _words(command)
    if _vitest(words):
        return words
    found = npm_scripts.test_script(command, tree)
    script = _words(found[0].get("test")) if found else None
    if script is None or not _vitest([*script, *found[1]]):
        return None
    return words


def instrument(command, result_path, tree=None):
    words = command_words(command, tree)
    if not words:
        return command
    if Path(words[0]).name in ("npm", "npm.cmd") and "--" not in words:
        words = [*words, "--"]
    reporter = Path(__file__).with_name("autocode_vitest_reporter.cjs")
    return shlex.join(
        [
            *words,
            "--reporter=default",
            "--reporter=" + str(reporter.resolve()),
            "--exclude=**/.autocode/**",
            "--outputFile=" + str(Path(result_path).resolve()),
        ]
    )


def results(path, tree):
    """Read only an owned, complete protocol; absent/ambiguous cases never pass."""
    try:
        value = json.loads(Path(path).read_text())
        if (
            not isinstance(value, dict)
            or value.get("protocol") != "autocode-vitest-tests"
            or type(value.get("version")) is not int
            or value["version"] != 1
            or value.get("complete") is not True
            or not re.fullmatch(r"4\.\d+\.\d+(?:[-+].*)?", str(value.get("vitest_version", "")))
            or value.get("reason") not in ("passed", "failed")
        ):
            return None
        files, count, errors = value["files"], value["total"], value["unhandled_errors"]
        if not isinstance(files, list) or type(count) is not int or type(errors) is not int or errors < 0:
            return None
        root = Path(tree).resolve()
        states: dict[str, set[str]] = {}
        reported, file_names, collection = 0, set(), set()
        uncollected = set()
        for file in files:
            if not isinstance(file, dict) or not isinstance(file.get("file"), str) or not file["file"]:
                return None
            name = Path(file["file"])
            source = name.resolve() if name.is_absolute() else (root / name).resolve()
            if not source.is_relative_to(root):
                return None
            relative = source.relative_to(root).as_posix()
            if relative in file_names or type(file.get("collection_error")) is not bool:
                return None
            file_names.add(relative)
            if file["collection_error"]:
                collection.add(relative + "::[collection]")
                uncollected.add(relative + "::[collection]")
            tests = file["tests"]
            if not isinstance(tests, list):
                return None
            for test in tests:
                if (
                    not isinstance(test, dict)
                    or not isinstance(test.get("name"), str)
                    or not test["name"]
                    or type(test.get("collection_error")) is not bool
                ):
                    return None
                identity = relative + "::" + test["name"]
                state = test["state"]
                if state not in repeated_tests.STATES:
                    return None
                reported += 1
                # Tests of one name in one file (a `test.each` table) are one identity (autocode_repeated_tests).
                states.setdefault(identity, set())
                if test["collection_error"] or file["collection_error"]:
                    collection.add(identity)
                else:
                    states[identity].add(state)
        identities = set(states)
        groups = repeated_tests.settle({identity: seen for identity, seen in states.items() if seen})
        groups["passed"] -= collection
        groups["skipped"] -= collection
        if count != reported or (value["reason"] == "passed" and (groups["failed"] or collection or errors)):
            return None
        if errors or (value["reason"] == "failed" and not groups["failed"] and not collection):
            collection.add("[vitest runtime error]")
        return {
            "passed": sorted(groups["passed"]),
            "failed": sorted(groups["failed"] | collection),
            "skipped": sorted(groups["skipped"]),
            "collection_errors": sorted(collection),
            "uncollected": sorted(uncollected),
            "total": len(identities | collection),
            "complete": True,
        }
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return None
