"""Why a bug fix's regression tests cannot even run on the unfixed code: they use a seam the fix adds.

A bug fix is proven by a test that builds, runs and fails on the unfixed code
(autocode_verify). A test that observes the fix through something the fix itself
adds (a hook, a package variable, an injectable function) cannot build there: Go
reports "undefined: X", Python "cannot import name 'X'". A compile or import error
is not a reproduction, so the proof stays FAIL; this module only recognizes the
case and says what to do instead (issue #299). A log line the fix adds is no
substitute: a fix with the real call removed and the log kept passes such a test.

Pure functions over text. Imports nothing from AutoCode.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

# Messages naming an identifier the code under test lacks, as compilers and test runners print them.
MISSING = tuple(re.compile(pattern) for pattern in (
    r"undefined: (?:[A-Za-z_]\w*\.)?([A-Za-z_]\w*)",  # Go
    r"has no field or method ([A-Za-z_]\w*)",  # Go
    r"cannot import name '([A-Za-z_]\w*)'",  # Python
    r"has no attribute '([A-Za-z_]\w*)'",  # Python
    r"name '([A-Za-z_]\w*)' is not defined",  # Python
    r"No module named '(?:[\w.]*\.)?([A-Za-z_]\w*)'",  # Python
    r"does not provide an export named '([A-Za-z_$][\w$]*)'",  # JavaScript modules
    r"ReferenceError: ([A-Za-z_$][\w$]*) is not defined",  # JavaScript
    r"has no exported member '([A-Za-z_$][\w$]*)'",  # TypeScript
    r"cannot find (?:value|function|type|struct|module|macro) `([A-Za-z_]\w*)`",  # Rust
    r"symbol:\s+(?:variable|method|class)\s+([A-Za-z_]\w*)",  # Java
))
WORD = re.compile(r"[A-Za-z_$][\w$]*")


def words(text: str) -> set[str]:
    return set(WORD.findall(text))


def missing_names(output: str) -> set[str]:
    """Identifiers a test run's output reports missing."""
    return {name for pattern in MISSING for name in pattern.findall(output)}


def added_names(path: str, before: str, after: str) -> set[str]:
    """Identifiers on the lines a source change adds and on none it removes; a new file also adds its module."""
    old, new = set(before.splitlines()), set(after.splitlines())
    names = words("\n".join(new - old)) - words("\n".join(old - new))
    if not before:
        module = PurePosixPath(path)
        names.add(module.parent.name if module.stem == "__init__" else module.stem)
    return names


def used(output: str, added: set[str], test_words: set[str]) -> list[str]:
    """The seam: names the unfixed run reports missing that the source change adds and the tests use."""
    return sorted(missing_names(output) & added & test_words)


def reason(collection_errors, names) -> str:
    """The proof's failure when the new tests cannot build on the unfixed code because of a seam."""
    seam = ", ".join(names[:5])
    return ("On the unfixed code the new tests only fail to import or collect ("
            + ", ".join(collection_errors[:5]) + f") because they use {seam}, which only the fix adds. "
            "A compile or import error is not a reproduction of the bug, and a seam (hook, package variable, "
            "injectable function) added with the fix cannot make the unfixed code build. Rewrite the regression "
            "tests to use only code that exists before the fix: drive the real failure path through its existing "
            "public APIs (for example a real file, directory or input that makes the failing operation fail) and "
            "assert the behavior itself, such as the returned error, the result or the saved state. A log line "
            "or message alone does not prove the behavior. If no existing API can reach that path, report that "
            "this bug needs a separately approved instrumentation-only base patch: the runner adds none, so a "
            f"test that uses {seam} cannot pass this proof.")


def note(collection_errors, names) -> str:
    """Why some tests were left out of a proof that other tests satisfy."""
    return ("Not counted as proof: on the unfixed code " + ", ".join(collection_errors[:5])
            + f" could not build because the tests use {', '.join(names[:5])}, which only the fix adds")
