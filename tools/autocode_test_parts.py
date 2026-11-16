"""Tests written inside a code file, and the change's ones put on the original code.

The regression proof runs the change's tests on the original code, so it splits the change into tests
and code, and a rule over paths says which files are tests. Some languages write tests inside the file
they test: Python's doctests are examples in a docstring (``python -m doctest textutil.py``, ``pytest
--doctest-modules``), Rust runs the code blocks of its doc comments (``cargo test``), and Rust's unit
tests sit in a ``#[cfg(test)] mod tests`` of the same file. Such a file is code by every path rule, so
a fix that added its example beside the fix had no test at all: "No regression test was added or
changed", and with no test file the project had no test command either. A real doctest bug fix asked
the person twice how the fix should be proven, and ended with the test directory the request had said
not to add (survey, section 37). Rust's ``#[cfg(test)]`` modules had been given a mechanism of their own
(autocode_rust_inline_tests, #3467); doctests and doc comments had none.

What they share is a part of a file that a test command reads and that the program does not run: a
docstring, a doc comment, a module compiled only for tests. Which parts those are is the language's own
syntax, read by its parser or lexer, not guessed from names (``parts``). Two answers follow:

- a file holds tests when such a part holds an example or a test (``holds_tests``); a change edits a
  file's tests when those parts differ (``changed``);
- the original code with the change's tests (``splice``) is the original file with the change's edits
  that lie wholly inside such parts, on both sides, and nothing else. It is checked against the
  original: with those parts removed, the program is the original's exactly (Python compares the syntax
  trees without docstrings), so a fix can never ride along with a test.

The proof treats such a file as a changed test and keeps it as code (``partition``): its changed lines
outside the test parts still need the Tester's evidence, and a change confined to test parts is no
product change. What must pass is unchanged: the change's tests must fail on the original code and pass
with the change.

Pure functions over bytes, and Git reads of a checkout. Imports nothing from AutoCode but the Rust lexer.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import subprocess
from pathlib import Path

try:
    from . import autocode_plan_proof_command as plan_command
    from . import autocode_rust_inline_tests as rust
except ImportError:
    import autocode_plan_proof_command as plan_command
    import autocode_rust_inline_tests as rust

# Documents doctest reads as a whole (``python -m doctest docs/usage.txt``): the program never runs them.
DOCUMENTS = (".txt", ".rst", ".md")
# The kinds of file whose parts this module reads.
SUFFIXES = (".py", ".rs", *DOCUMENTS)
# The kinds whose test command runs those tests with every other test (``cargo test``). A doctest runs
# only under a command that asks for doctests, so it counts as the change's test only where no test
# file changed: beside one, it would put a file of code among the tests a runner is given by name.
ALWAYS_RUN = (".rs",)
# Text no example can be written without.
_MARKS = {".py": b">>>", ".rs": b"```", **dict.fromkeys(DOCUMENTS, b">>>")}


def _python_docs(data: bytes) -> tuple[set[int], set[int]] | None:
    """(the lines of every docstring, the lines of the docstrings that hold an example), or None when the
    file does not parse."""
    try:
        tree = ast.parse(data)
    except (SyntaxError, ValueError):
        return None
    every: set[int] = set()
    examples: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) or not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            lines = set(range(first.lineno, (first.end_lineno or first.lineno) + 1))
            every |= lines
            if ">>>" in first.value.value:
                examples |= lines
    return every, examples


def _is_test_table(node) -> bool:
    """``__test__ = {...}``: the table of extra docstrings doctest reads from a module."""
    return (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "__test__"
    )


def _python_program(data: bytes) -> str | None:
    """The file's syntax tree without its docstrings and doctest's ``__test__`` table: what the program
    does, or None."""
    try:
        tree = ast.parse(data)
    except (SyntaxError, ValueError):
        return None
    tree.body = [node for node in tree.body if not _is_test_table(node)]
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) or not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            node.body = node.body[1:]
    return ast.dump(tree)


def _rust_doc_blocks(data: bytes) -> set[int]:
    """The doc comment lines of the comments that hold a code block, which rustdoc runs as a test."""
    lines = sorted(rust.doc_comment_lines(data))
    text = data.splitlines()
    found: set[int] = set()
    run: list[int] = []
    for line in [*lines, None]:
        if run and (line is None or line != run[-1] + 1):
            if any(b"```" in text[number - 1] for number in run):
                found.update(run)
            run = []
        if line is not None:
            run.append(line)
    return found


def _lines_of(data: bytes, spans) -> set[int]:
    found: set[int] = set()
    for start, end in spans:
        first = data.count(b"\n", 0, start) + 1
        last = data.count(b"\n", 0, max(start, end - 1)) + 1
        found.update(range(first, last + 1))
    return found


def parts(path, data: bytes) -> tuple[set[int], set[int]]:
    """(the lines of the file's parts the program does not run, the lines of those that hold tests or
    examples), by the language's syntax. Empty for a file of another kind or one that does not parse."""
    if path.endswith(".py"):
        return _python_docs(data) or (set(), set())
    if path.endswith(".rs"):
        modules = _lines_of(data, [(start, end) for _, start, end in rust.test_modules(data)])
        return modules | rust.doc_comment_lines(data), modules | _rust_doc_blocks(data)
    if path.endswith(DOCUMENTS):
        every = set(range(1, len(data.splitlines()) + 1))
        return every, (every if _MARKS[".txt"] in data else set())
    return set(), set()


def _text(data: bytes, lines) -> list[bytes]:
    text = data.splitlines()
    return [text[number - 1].strip() for number in sorted(lines) if 0 < number <= len(text)]


def holds_tests(path, data: bytes) -> bool:
    """Whether the file holds tests in a part of it: a doctest, a doc example, a test module."""
    if path.endswith((".py", *DOCUMENTS)) and b">>>" not in data:
        return False  # read without parsing: a project's every file is looked at
    return bool(parts(path, data)[1])


def changed(path, before: bytes | None, after: bytes) -> bool:
    """Whether the file's tests differ from ``before``'s (None: a file the change adds)."""
    tests = _text(after, parts(path, after)[1])
    if before is None:
        return bool(tests)
    return bool(tests or parts(path, before)[1]) and tests != _text(before, parts(path, before)[1])


def program(path, data: bytes):
    """What the file's program is, with its test parts left out, to compare two versions of it."""
    if path.endswith(".py"):
        return _python_program(data)
    if path.endswith(".rs"):
        kept = rust.product_bytes(data)
        docs = rust.doc_comment_lines(kept)
        return [line.rstrip() for n, line in enumerate(kept.splitlines(), 1) if n not in docs and line.strip()]
    if path.endswith(DOCUMENTS):
        return b""
    return data


def _hunks(base: bytes, candidate: bytes, base_lines, candidate_lines) -> bytes:
    """``base`` with each of the edits from ``base`` to ``candidate`` that lies wholly inside the given
    lines of both: a test part edited, added where one was, or removed."""
    old, new = base.splitlines(keepends=True), candidate.splitlines(keepends=True)
    out: list[bytes] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        inside = all(n + 1 in base_lines for n in range(i1, i2)) and all(
            n + 1 in candidate_lines for n in range(j1, j2)
        )
        out += new[j1:j2] if tag != "equal" and inside else old[i1:i2]
    spliced = b"".join(out)
    if old and new and not old[-1].endswith(b"\n") and spliced.endswith(b"\n") and not candidate.endswith(b"\n"):
        spliced = spliced[:-1]
    return spliced


def _examples(data: bytes) -> dict[str, str]:
    """{qualified name: docstring} of the docstrings that hold an example."""
    try:
        tree = ast.parse(data)
    except (SyntaxError, ValueError):
        return {}
    found: dict[str, str] = {}

    def visit(node, prefix):
        for child in node.body if hasattr(node, "body") else ():
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(child, clean=False)
                if doc and ">>>" in doc:
                    found[prefix + child.name] = doc
                visit(child, prefix + child.name + ".")

    module = ast.get_docstring(tree, clean=False)
    if module and ">>>" in module:
        found["(module)"] = module
    visit(tree, "")
    return found


def _missing_examples(data: bytes, candidate: bytes) -> bytes:
    """``data`` with doctest's ``__test__`` table holding the candidate's examples it lacks: those of a
    function or class the change adds, which has no docstring on the original code to hold them. On the
    original code doctest runs them, and they fail there as a new test of a new function does."""
    have = set(_examples(data).values())
    missing = {name: doc for name, doc in _examples(candidate).items() if doc not in have}
    if not missing:
        return data
    table = ", ".join(f"{name!r}: {doc!r}" for name, doc in missing.items())
    line = f'__test__ = {{**globals().get("__test__", {{}}), {table}}}\n'.encode()
    return data + (b"" if data.endswith(b"\n") or not data else b"\n") + b"\n" + line


def splice(path, base: bytes, candidate: bytes) -> bytes:
    """``base`` with ``candidate``'s test parts: the original program and the change's tests. Raises
    ValueError when they cannot be put together without changing the original program."""
    data = base
    if path.endswith(".rs") and rust.inline_test_changed(base, candidate):
        data, _ = rust.splice_bytes(base, candidate)
    data = _hunks(data, candidate, parts(path, data)[0], parts(path, candidate)[0])
    if path.endswith(".py"):
        data = _missing_examples(data, candidate)
    if program(path, data) is None or program(path, data) != program(path, base):
        raise ValueError(f"{path}: the change's tests could not be put on the original code without changing it")
    return data


def _base_bytes(root: Path, base, path) -> bytes | None:
    shown = subprocess.run(["git", "-C", str(root), "show", f"{base}:{path}"], capture_output=True)
    return shown.stdout if shown.returncode == 0 else None


def named_holding(commands, files, read) -> list[str]:
    """The ``files`` that a shell word of one of ``commands`` names and whose bytes (``read(path)``, None
    when absent) hold tests: existing tests those commands run, as ``python -m doctest textutil.py`` runs
    the examples in ``textutil.py``. The plan check, a guard and the proof's choice of command read this one
    answer. Only files a command names are read, so a large project is not read whole."""
    candidates = [path for path in files if path.endswith(SUFFIXES)]
    named = sorted({path for command in commands for path in plan_command.test_files(command, candidates)})
    return [path for path in named if (data := read(path)) is not None and holds_tests(path, data)]


def original_named(workspace, base, commands, files) -> list[str]:
    """``named_holding`` on the original code (``base``)."""
    root = Path(workspace)
    return named_holding(commands, files, lambda path: _base_bytes(root, base, path))


def changed_holding(workspace, base, changes) -> list[str]:
    """The changed code files (``changes``: path to status), not deleted, whose tests changed."""
    root = Path(workspace)
    found = []
    for path, status in changes.items():
        file = root / path
        if not path.endswith(SUFFIXES) or status == "deleted" or not file.is_file() or file.is_symlink():
            continue
        before = None if status == "added" else _base_bytes(root, base, path)
        if changed(path, before, file.read_bytes()):
            found.append(path)
    return found


def partition(workspace, base, tests, sources, changes) -> tuple[list[str], list[str]]:
    """(the changed tests the proof must see fail first, the product files): ``tests`` (the changed test
    files, not deleted) and every changed code file whose tests changed. Such a file stays a product
    file unless its program is the original's, so a change that only edits a file's tests is no fix."""
    runnable = [path for path in tests if changes.get(path) != "deleted"]
    kept = list(sources)
    root = Path(workspace)
    files_changed = bool(runnable)
    for path in sources:
        if not path.endswith(SUFFIXES) or changes.get(path) == "deleted" or path in runnable:
            continue
        if files_changed and not path.endswith(ALWAYS_RUN):
            continue
        file = root / path
        if not file.is_file() or file.is_symlink():
            continue
        after = file.read_bytes()
        before = None if changes.get(path) == "added" else _base_bytes(root, base, path)
        if changed(path, before, after):
            runnable.append(path)
            if before is not None and program(path, before) == program(path, after):
                kept.remove(path)
    return runnable, kept


def splice_tree(base_tree, candidate_tree, test_changes, changes, evidence_dir) -> dict | None:
    """Put the change's test parts of each edited code file onto ``base_tree`` (the original code with
    the change's test files), and record what was put there in ``evidence_dir``. Every file is checked
    before any is written."""
    paths = sorted(
        path
        for path, status in changes.items()
        if path.endswith(SUFFIXES) and status == "modified" and path not in test_changes
    )
    staged: list[tuple[Path, bytes]] = []
    files: dict[str, dict] = {}
    base_root, candidate_root = Path(base_tree), Path(candidate_tree)
    for path in paths:
        base_file, candidate_file = base_root / path, candidate_root / path
        if base_file.is_symlink() or candidate_file.is_symlink():
            continue
        if not base_file.is_file() or not candidate_file.is_file():
            continue
        base_data, candidate_data = base_file.read_bytes(), candidate_file.read_bytes()
        if not changed(path, base_data, candidate_data):
            continue
        spliced = splice(path, base_data, candidate_data)
        staged.append((base_file, spliced))
        files[path] = {
            "base_sha256": hashlib.sha256(base_data).hexdigest(),
            "candidate_sha256": hashlib.sha256(candidate_data).hexdigest(),
            "spliced_sha256": hashlib.sha256(spliced).hexdigest(),
        }
    if not staged:
        return None
    for target, spliced in staged:
        target.write_bytes(spliced)
    evidence = Path(evidence_dir)
    evidence.mkdir(parents=True, exist_ok=True)
    receipt = {"files": files}
    (evidence / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt
