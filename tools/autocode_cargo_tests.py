"""Per-test results from ``cargo test``'s own output, so a Rust project's named tests can be proved.

A live bug fix in a Rust project (Claude models, 2026-11-13) planned each criterion as a named test.
Cargo's results were not read per test, so the proof saw one exit code, every named criterion ended
unverified, and the Resolver asked the person to accept the log lines by hand. Those lines are what the
test harness itself prints for every test binary::

         Running tests/pages.rs (target/debug/deps/pages-e003a3cc7525206f)

    running 3 tests
    test exact_multiple ... ok
    test test_partial_last_page ... FAILED

    failures:
    ...
    test result: FAILED. 2 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.10s

Each test is ``FILE::NAME``: FILE as Cargo printed it after ``Running`` (``unittests src/lib.rs`` is
``src/lib.rs``), NAME the harness's own path (``tests::adds`` for a test in ``mod tests``), so a plan's
``test: test_partial_last_page`` names ``fn test_partial_last_page``. A documentation test is
``Doc-tests CRATE::NAME``. An ignored test is skipped.

Only the lines between ``running N tests`` and the first blank line are read, where the harness prints
nothing but results: a test's captured output is shown later, under ``failures:``. A binary's results
count only when they agree with its own ``test result:`` line. One that printed no such line (it exited
or crashed mid-run), or whose counts disagree, is reported as ``FILE::[no result]``, failed, and the
results are incomplete. A build that fails runs no test and is reported as ``TARGET::[build failed]``,
failed and uncollected, as a Go package that does not build is. In a workspace two crates may print the
same file and test name; the two are one identity, failed when either failed.

``instrument`` adds ``--no-fail-fast``: Cargo otherwise stops at the first failing binary, and the tests
of the binaries after it would be missing from one side of the comparison. A command that changes what
the harness prints (``-q``, ``--format``, ``--nocapture``) is left alone and judged by its exit code.

A feature's new test usually calls a function the original code lacks, so the original code with the
change's test files does not build and none of its tests ran there. ``changed_filter`` then says which
tests the change itself adds or edits, as autocode_go_tests.changed_filter does for Go (#2505): only
those can show the new behavior, and an untouched test that passed on the original code was kept.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path, PurePosixPath

try:
    from . import autocode_project_kinds as project_kinds
    from . import autocode_rust_inline_tests as rust_inline_tests
except ImportError:
    import autocode_project_kinds as project_kinds
    import autocode_rust_inline_tests as rust_inline_tests

BUILD_FAILED = "::[build failed]"
NO_RESULT = "::[no result]"
# What cargo itself must not be told: no tests run, or its messages are not the human ones.
_CARGO_REFUSED = ("-q", "--quiet", "--no-run", "--help", "-h", "--version", "-V")
# What the test harness must not be told: each changes the lines it prints between its header and summary.
_HARNESS_REFUSED = ("-q", "--quiet", "--list", "--nocapture", "--no-capture", "--show-output", "--report-time")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_RUNNING = re.compile(r"^\s*Running (?:unittests )?(\S+) \(\S+\)\s*$")
_DOC_TESTS = re.compile(r"^\s*Doc-tests (\S+)\s*$")
_HEADER = re.compile(r"^running (\d+) tests?$")
_TEST = re.compile(r"^test (.+?) \.\.\. (ok|FAILED|ignored)(?:, .*)?$")
_BENCH = re.compile(r"^test (.+?) \.\.\. bench:")
_SUMMARY = re.compile(r"^test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured;")
_BUILD_ERROR = re.compile(r"^error: could not compile `([^`]+)`(?: \(([^)]*)\))?")
_STATUS = {"ok": "passed", "FAILED": "failed", "ignored": "skipped"}
# `#[should_panic]` is shown after the name; it is not part of it. A documentation test keeps its own text.
_SHOULD_PANIC = " - should panic"


def command_words(command):
    """The words of a plain ``cargo test`` command whose per-test lines can be read, or None.

    Leading ``NAME=value`` words are kept, as an approved Go command's are."""
    if not isinstance(command, str) or re.search(r"[;&|<>`$()\n\r\\]", command):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    start = 0
    while start < len(words) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[start]):
        start += 1
    if words[start : start + 2] != ["cargo", "test"]:
        return None
    rest = words[start + 2 :]
    split = rest.index("--") if "--" in rest else len(rest)
    cargo, harness = rest[:split], rest[split + 1 :]
    if any(word in _CARGO_REFUSED or word.startswith("--message-format") for word in cargo):
        return None
    if any(word in _HARNESS_REFUSED or word.startswith(("--format", "--logfile", "-Z")) for word in harness):
        return None
    return words


def instrument(command):
    """``command`` running every test binary, so one failing binary hides no other's results."""
    words = command_words(command)
    if not words or "--no-fail-fast" in words:
        return command
    split = words.index("--") if "--" in words else len(words)
    return shlex.join([*words[:split], "--no-fail-fast", *words[split:]])


def results(text):
    """Per-test results from the output of one ``cargo test`` run, or None when it shows no test binary
    and no failed build."""
    outcome, order = {}, []
    broken, collection = [], []
    container, block = None, None
    complete = True

    def record(name, status):
        key = f"{container or '[unknown]'}::{name}"
        if key not in outcome:
            order.append(key)
        # Two crates of a workspace may print one identity; a pass cannot erase the other's failure.
        if outcome.get(key) != "failed" and not (outcome.get(key) == "passed" and status == "skipped"):
            outcome[key] = status

    def close(summary=None):
        """End the open binary: its lines count only when they agree with its own summary."""
        nonlocal block, complete
        if block is None:
            return
        counted = {status: sum(1 for _, found in block["tests"] if found == status) for status in _STATUS.values()}
        agrees = summary is not None and (
            (counted["passed"], counted["failed"], counted["skipped"]) == summary[:3]
            and len(block["tests"]) + summary[3] == block["expected"]
        )
        for name, status in block["tests"]:
            record(name, status)
        if not agrees:
            complete = False
            broken.append(f"{container or '[unknown]'}{NO_RESULT}")
        block = None

    for raw in _ANSI.sub("", text or "").splitlines():
        line = raw.rstrip()
        if found := _RUNNING.match(line) or _DOC_TESTS.match(line):
            close()
            container = found.group(1) if _RUNNING.match(line) else f"Doc-tests {found.group(1)}"
            continue
        if found := _HEADER.match(line):
            close()
            block = {"expected": int(found.group(1)), "tests": [], "open": True}
            continue
        if found := _BUILD_ERROR.match(line):
            target = found.group(1) + (f" ({found.group(2)})" if found.group(2) else "")
            if target + BUILD_FAILED not in collection:
                collection.append(target + BUILD_FAILED)
            continue
        if block is None:
            continue
        if found := _SUMMARY.match(line):
            close(tuple(int(value) for value in found.groups()))
            container = None
            continue
        if not block["open"]:
            continue
        if found := _TEST.match(line):
            name = found.group(1)
            if name.endswith(_SHOULD_PANIC) and not (container or "").startswith("Doc-tests "):
                name = name[: -len(_SHOULD_PANIC)]
            block["tests"].append((name, _STATUS[found.group(2)]))
        elif not _BENCH.match(line) and (block["tests"] or line.strip()):
            # The first line after the results that is not one: captured output follows under "failures:".
            block["open"] = False
    close()
    if not order and not collection and not broken:
        return None
    passed = [key for key in order if outcome[key] == "passed"]
    skipped = [key for key in order if outcome[key] == "skipped"]
    failed = [key for key in order if outcome[key] == "failed"] + broken + collection
    return {
        "passed": sorted(passed),
        "failed": sorted(set(failed)),
        "skipped": sorted(skipped),
        "collection_errors": sorted(collection),
        "uncollected": sorted(collection),
        "total": len(order) + len(set(broken)) + len(collection),
        "complete": complete,
    }


_FUNCTION = re.compile(
    r"^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?(?:(?:async|const|unsafe)[ \t]+)*fn[ \t]+([A-Za-z_]\w*)", re.M
)


def definitions(text):
    """``(functions, rest)`` of a Rust file: each outermost function's name -> its sources, and the words
    of the file with those functions cut out.

    A function runs from ``fn`` to the brace that closes its body, counted without reading strings or
    comments: a miscount moves the same boundary on both sides of a comparison, and a difference it hides
    in one function shows in ``rest`` or in a neighbor.
    """
    text = text or ""
    found, rest, position = {}, [], 0
    for match in _FUNCTION.finditer(text):
        if match.start() < position:
            continue  # a function inside the previous one's body
        opening = text.find("{", match.end())
        ending = text.find(";", match.end())
        if opening < 0 or 0 <= ending < opening:
            continue  # a declaration without a body
        depth, end = 0, len(text)
        for index in range(opening, len(text)):
            depth += {"{": 1, "}": -1}.get(text[index], 0)
            if depth == 0:
                end = index + 1
                break
        start = match.start()
        while start > position:  # the attributes and doc comments above it are part of it
            above = text.rfind("\n", position, start - 1) + 1
            if not text[max(above, position) : start].lstrip().startswith(("#[", "///")):
                break
            start = max(above, position)
        found.setdefault(match.group(1), []).append(text[start:end])
        rest.append(text[position:start])
        position = end
    rest.append(text[position:])
    return found, " ".join("".join(rest).split())


def _is_mixed_rust_source(path: str) -> bool:
    if not path.endswith(".rs") or project_kinds.is_test_file(path):
        return False
    parts = PurePosixPath(path).parts[:-1]
    if set(parts) & project_kinds.TEST_DIRS:
        return False
    return not (parts and parts[0] in project_kinds.ROOT_TEST_DIRS)


def changed_names(files):
    """``(path, function)`` for each function the change adds or edits in its Rust test files; ``(path, None)``
    when anything outside a function changed there (an attribute, a macro, a constant), so every test
    of that file counts as changed. ``files`` gives ``(path, base_text, candidate_text)``, ``base_text``
    None for a new file."""
    names = set()
    for path, before, after in files:
        if _is_mixed_rust_source(path) and rust_inline_tests.test_modules((after or "").encode()):
            after_inline = rust_inline_tests.inline_test_text((after or "").encode())
            new, new_rest = definitions(after_inline)
            if before is None or not rust_inline_tests.test_modules(before.encode()):
                if new:
                    names |= {(path, name) for name in new}
                else:
                    names.add((path, None))
                continue
            before_inline = rust_inline_tests.inline_test_text(before.encode())
            old, old_rest = definitions(before_inline)
            if old_rest != new_rest:
                names.add((path, None))
            names |= {(path, name) for name, sources in new.items() if old.get(name) != sources}
            continue
        new, new_rest = definitions(after)
        if before is None:
            names.add((path, None))
            continue
        old, old_rest = definitions(before)
        if old_rest != new_rest:
            names.add((path, None))
        names |= {(path, name) for name, sources in new.items() if old.get(name) != sources}
    return names


def defined_in(names, test):
    """Whether a ``FILE::module::name`` result is one of ``changed_names``. Cargo prints FILE relative to
    its package, so a changed ``crates/a/tests/api.rs`` holds the tests of ``tests/api.rs``."""
    file, _, name = test.partition("::")
    function = name.rsplit("::", 1)[-1]
    return any((path == file or path.endswith("/" + file)) and changed in (None, function) for path, changed in names)


def changed_filter(root, changes, base_text):
    """Whether a named test is one the change in ``root`` adds or edits, or None to keep every test.

    ``changes`` maps the changed test files to their status; ``base_text(path)`` reads a file on base.
    """
    files = []
    for path, status in changes.items():
        if path.endswith(".rs") and status != "deleted":
            candidate = Path(root, path)
            after = candidate.read_text(errors="replace") if candidate.is_file() else ""
            files.append((path, base_text(path) if status != "added" else None, after))
    if not files:
        return None
    names = changed_names(files)
    return lambda test: defined_in(names, test)
