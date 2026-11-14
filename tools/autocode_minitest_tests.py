"""Per-test results from Minitest's built-in verbose output, requiring no added gem (#3467).

A Ruby project without RSpec runs Minitest via ``ruby -Ilib -Itest ...`` or ``rake test``.
Adding ``-v`` (or ``TESTOPTS=-v`` for Rake) prints one result line per executed test::

    PagerTest#test_counts_a_partial_page = 0.00 s = .
    Pager::.page_count#test_0001_counts a partial last page = 0.00 s = .

    2 runs, 2 assertions, 0 failures, 0 errors, 0 skips

Each test id is formatted as ``FILE::ClassName::test_method`` when the defining test file under
``root`` is uniquely known (matching ``_RUBY_ID`` in ``autocode_test_cases`` and enabling named
``test:``, ``guard:``, and ``supersedes``), or ``ClassName::test_method`` otherwise.

Lower layer: imports only ``autocode_project_kinds``.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path, PurePosixPath

try:
    from . import autocode_project_kinds as project_kinds
except ImportError:
    import autocode_project_kinds as project_kinds

COLLECTION = "::[collection]"
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_META = re.compile(r"[;&|<>`$()\n\r\\]")
# Quiet/custom Minitest formatters drop the standard `# = X.XX s = .` lines we parse.
_CONFLICTING_FLAGS = frozenset({"-q", "--quiet", "--pride", "--junit", "--junit-filename"})
_TEST_LINE = re.compile(r"^(.+)#(test_.+?)\s+=\s+\d+(?:\.\d+)?\s+s\s+=\s+([.FES])\s*$")
_SUMMARY_LINE = re.compile(r"^(\d+) runs?, \d+ assertions?, (\d+) failures?, (\d+) errors?, (\d+) skips?\s*$")
_TRACEBACK_FILE = re.compile(r"^\s*(?:from\s+)?([^\s:][^:\n]*\.rb):\d+:(?:in\b|\s)", re.M)
_ERROR_KIND = re.compile(r"\((?:LoadError|SyntaxError|NameError)\)|\bsyntax error\b|\bcannot load such file\b")
_CLASS_DEF = re.compile(r"^\s*(?:class|module)\s+([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)")
_DESCRIBE_DEF = re.compile(r"""^\s*(?:describe|context)\s+(?:"([^"]+)"|'([^']+)'|([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*))""")


def _strip_prefix(words: list[str]) -> tuple[list[str], list[str], int] | None:
    """Return ``(env_prefix, cmd_words, after_exec_index)`` or None for unquotable assignments."""
    idx = 0
    while idx < len(words) and _ASSIGNMENT.match(words[idx]):
        if shlex.quote(words[idx]) != words[idx]:
            return None
        idx += 1
    env = words[:idx]
    rest = words[idx:]
    if not rest:
        return None
    start = 2 if rest[:2] == ["bundle", "exec"] else 0
    return env, rest, start


def _ruby_test_command(rest: list[str], start: int) -> str | None:
    """Classify a Ruby/Rake Minitest invocation as ``'ruby-e'``, ``'ruby-file'``, ``'rake'``, or None."""
    if start >= len(rest):
        return None
    exe = PurePosixPath(rest[start]).name
    args = rest[start + 1 :]
    if exe == "rake":
        if not any(arg == "test" or arg.startswith("test:") for arg in args if not _ASSIGNMENT.match(arg)):
            return None
        return "rake"
    if exe not in ("ruby", "ruby.exe"):
        return None
    before_sep = args[: args.index("--")] if "--" in args else args
    after_sep = args[args.index("--") + 1 :] if "--" in args else []
    if any(
        word in _CONFLICTING_FLAGS or word.startswith("--junit-filename=")
        for word in (*after_sep, *(args if "--" not in args else ()))
    ):
        return None
    i = 0
    has_eval = False
    has_test_file = False
    while i < len(before_sep):
        word = before_sep[i]
        if word == "-e":
            has_eval = True
            i += 2
            continue
        if word in ("-I", "-r", "-W", "-C", "-E"):
            if word == "-r" and i + 1 < len(before_sep):
                req = before_sep[i + 1].removeprefix("./")
                if req.endswith(".rb") and project_kinds.is_test_file(req):
                    has_test_file = True
            i += 2
            continue
        if word.startswith(("-I", "-r", "-W", "-e")):
            if word.startswith("-r") and project_kinds.is_test_file(word[2:].removeprefix("./")):
                has_test_file = True
            i += 1
            continue
        if word.startswith("-"):
            i += 1
            continue
        if word.endswith(".rb") and project_kinds.is_test_file(word):
            return "ruby-file"
        return None
    if has_test_file and has_eval:
        return "ruby-e"
    if has_eval:
        eval_code = " ".join(before_sep)
        if "_test" in eval_code or "test_" in eval_code:
            return "ruby-e"
    return None


def _has_verbose(words: list[str], *, after_sep_only: bool = False) -> bool:
    if after_sep_only:
        tail = words[words.index("--") + 1 :] if "--" in words else []
        return any(w in ("-v", "--verbose") for w in tail)
    return any(w in ("-v", "--verbose") for w in words)


def _has_unquoted_shell_meta(command: str) -> bool:
    """True when ``command`` contains shell control/expansion syntax outside literal quotes."""
    mode: str | None = None
    for ch in command:
        if mode is None:
            if ch in ("'", '"'):
                mode = ch
            elif _SHELL_META.search(ch):
                return True
        elif mode == "'":
            if ch == "'":
                mode = None
            elif ch in ("\n", "\r"):
                return True
        else:
            if ch == '"':
                mode = None
            elif ch in ("`", "$", "\\", "\n", "\r"):
                return True
    return mode is not None


def command_words(command) -> list[str] | None:
    """The words of a plain Minitest ``ruby`` or ``rake test`` invocation, or None."""
    if not isinstance(command, str) or _has_unquoted_shell_meta(command):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    parsed = _strip_prefix(words)
    if parsed is None:
        return None
    _, rest, start = parsed
    kind = _ruby_test_command(rest, start)
    if kind is None:
        return None
    if kind == "rake":
        testopts = next((w.partition("=")[2] for w in words if w.startswith("TESTOPTS=")), "")
        if any(flag in shlex.split(testopts) for flag in _CONFLICTING_FLAGS):
            return None
    return words


def instrument(command, _result_path=None) -> str:
    """``command`` instrumented to print Minitest ``-v`` per-test lines; non-Minitest commands stay unchanged."""
    words = command_words(command)
    if not words:
        return command
    parsed = _strip_prefix(words)
    assert parsed is not None
    _, rest, start = parsed
    kind = _ruby_test_command(rest, start)
    assert kind is not None
    if kind == "rake":
        out = list(words)
        for idx, word in enumerate(out):
            if word.startswith("TESTOPTS="):
                opts = shlex.split(word.partition("=")[2])
                if not _has_verbose(opts):
                    out[idx] = "TESTOPTS=" + shlex.join([*opts, "-v"]).strip()
                return shlex.join(out)
        return shlex.join([*out, "TESTOPTS=-v"])
    if kind == "ruby-e":
        if "--" in words:
            return command if _has_verbose(words, after_sep_only=True) else shlex.join([*words, "-v"])
        return shlex.join([*words, "--", "-v"])
    # For a positional script `ruby test/a_test.rb`, Ruby stops parsing flags at the file name,
    # so appending `-- -v` would pass `--` into Minitest's OptionParser; `-v` goes directly after it.
    tail = words[words.index(next(w for w in words if w.endswith(".rb") and project_kinds.is_test_file(w))) + 1 :]
    return command if _has_verbose(tail) else shlex.join([*words, "-v"])


def _index_test_files(root) -> tuple[list[str], dict[str, set[str]]]:
    """Map class/module/describe names defined in Ruby test files under ``root`` to relative POSIX paths."""
    if not root:
        return [], {}
    base = Path(root).resolve()
    if not base.is_dir():
        return [], {}
    all_tests: list[str] = []
    owners: dict[str, set[str]] = {}
    for path in sorted(base.rglob("*.rb")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(base).as_posix()
        if not project_kinds.is_test_file(rel):
            continue
        all_tests.append(rel)
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if m := _CLASS_DEF.match(line):
                name = m.group(1)
                owners.setdefault(name, set()).add(rel)
                owners.setdefault(name.rsplit("::", 1)[-1], set()).add(rel)
            elif m := _DESCRIBE_DEF.match(line):
                name = m.group(1) or m.group(2) or m.group(3)
                if name:
                    owners.setdefault(name, set()).add(rel)
                    owners.setdefault(name.rsplit("::", 1)[-1], set()).add(rel)
    return all_tests, owners


def _resolve_owner_file(owner: str, all_tests: list[str], owners: dict[str, set[str]]) -> str | None:
    if len(all_tests) == 1:
        return all_tests[0]
    candidates: set[str] | None = None
    top = owner.split("::", 1)[0]
    for key in (owner, owner.rsplit("::", 1)[-1], top):
        hits = owners.get(key)
        if hits and len(hits) == 1:
            return next(iter(hits))
        if hits:
            candidates = hits if candidates is None else (candidates & hits)
    if candidates and len(candidates) == 1:
        return next(iter(candidates))
    return None


def _traceback_collection_errors(text: str, root) -> set[str]:
    """Identify test ``.rb`` files inside ``root`` that crashed with a syntax or load error."""
    if not root or not _ERROR_KIND.search(text):
        return set()
    base = Path(root).resolve()
    found: set[str] = set()
    for raw in _TRACEBACK_FILE.findall(text):
        candidate = Path(raw)
        resolved = candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()
        if not resolved.is_relative_to(base):
            continue
        rel = resolved.relative_to(base).as_posix()
        if project_kinds.is_test_file(rel):
            found.add(rel + COLLECTION)
    return found


def results(text: str, root=None) -> dict | None:
    """Complete per-test results from Minitest ``-v`` output, or None on missing/inconsistent summary."""
    if not isinstance(text, str) or not text.strip():
        return None
    all_tests, owners = _index_test_files(root)
    passed: set[str] = set()
    failed: set[str] = set()
    skipped: set[str] = set()
    seen: set[str] = set()
    summary: tuple[int, int, int] | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if match := _TEST_LINE.match(line):
            owner, method, status = match.group(1), match.group(2), match.group(3)
            rel = _resolve_owner_file(owner, all_tests, owners)
            identity = f"{rel}::{owner}::{method}" if rel else f"{owner}::{method}"
            if identity in seen:
                return None
            seen.add(identity)
            if status == ".":
                passed.add(identity)
            elif status == "S":
                skipped.add(identity)
            else:
                failed.add(identity)
        elif smatch := _SUMMARY_LINE.match(line):
            runs, fails, errs, skips = map(int, smatch.groups())
            summary = (runs, fails + errs, skips)

    if summary is None:
        collection = _traceback_collection_errors(text, root)
        if collection:
            return {
                "passed": [],
                "failed": sorted(collection),
                "skipped": [],
                "collection_errors": sorted(collection),
                "uncollected": sorted(collection),
                "total": len(collection),
                "complete": True,
            }
        return None

    runs, fail_count, skip_count = summary
    if runs != len(seen) or fail_count != len(failed) or skip_count != len(skipped):
        return None
    if runs == 0:
        collection = _traceback_collection_errors(text, root)
        if collection:
            return {
                "passed": [],
                "failed": sorted(collection),
                "skipped": [],
                "collection_errors": sorted(collection),
                "uncollected": sorted(collection),
                "total": len(collection),
                "complete": True,
            }
        return None
    return {
        "passed": sorted(passed),
        "failed": sorted(failed),
        "skipped": sorted(skipped),
        "collection_errors": [],
        "uncollected": [],
        "total": runs,
        "complete": True,
    }
