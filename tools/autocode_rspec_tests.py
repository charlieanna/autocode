"""Per-test results from RSpec's built-in JSON formatter, so an RSpec suite needs no added gem (#2889).

A live bug fix in a Rails backend (idlecampus, 2026-11-07) planned every criterion as an RSpec example. The
project had no JUnit formatter, and AutoCode never installs a tool into a project, so the proof saw only an
exit code and every named criterion ended unverified: no Rails project without rspec_junit_formatter could
complete. RSpec ships a JSON formatter. ``instrument`` adds it to a plain RSpec command, writing to a file
in the proof's evidence while the progress output stays on stdout, and ``results`` reads that file.

Each example's id is ``FILE::FULL DESCRIPTION``, FILE relative to where RSpec ran, as a node:test id is: a
plan's ``test: test_grades_case_insensitively`` names the example ``it "test_grades_case_insensitively"``.
A pending or skipped example is skipped. A spec file that fails to load runs no example, so it is reported
as ``FILE::[collection]``, failed and uncollected, as Node reports an import failure. An error outside every
example that names no file, two examples with one id, or counts that disagree with the summary give no
results at all, so nothing is attributed to a test that may not have run.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path, PurePosixPath

COLLECTION = "::[collection]"
# "An error occurred while loading ./spec/new_spec.rb. - Did you mean? ..." (RSpec 3.13).
_LOAD_ERROR = re.compile(r"An error occurred while loading (\S+?)\.(?=\s|$)")


def command_words(command):
    """The words of a plain ``rspec`` or ``bundle exec rspec`` command, or None."""
    if not isinstance(command, str) or re.search(r"[;&|<>`$()\n\r\\]", command):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words:
        return None
    runner = 2 if words[:2] == ["bundle", "exec"] else 0
    if len(words) <= runner or PurePosixPath(words[runner]).name != "rspec":
        return None
    # A command that writes its own output file, or reads options from elsewhere, is left alone.
    if any(word in ("--out", "-o") or word.startswith(("--out=", "--options")) for word in words[runner + 1 :]):
        return None
    return words


def instrument(command, result_path):
    """``command`` with RSpec's JSON formatter writing to ``result_path``; progress output stays on stdout.

    Formatters named on the command line replace those in a project's .rspec, so a command naming none gets
    the progress formatter back."""
    words = command_words(command)
    if not words:
        return command
    named = any(word in ("--format", "-f") or word.startswith(("--format=", "-f")) for word in words[1:])
    shown = [] if named else ["--format", "progress"]
    return shlex.join([*words, *shown, "--format", "json", "--out", str(Path(result_path).resolve())])


def _file(path):
    text = str(path or "")
    text = text[2:] if text.startswith("./") else text
    parts = PurePosixPath(text).parts
    return text if text and not PurePosixPath(text).is_absolute() and ".." not in parts else None


def results(path):
    """Complete per-test results from one RSpec JSON report, or None for absent or ambiguous evidence."""
    try:
        report = json.loads(Path(path).read_text())
        summary, examples = report["summary"], report["examples"]
        passed, failed, skipped, collection = set(), set(), set(), set()
        counts = {"passed": 0, "failed": 0, "pending": 0}
        for example in examples:
            file, name, status = _file(example["file_path"]), example["full_description"], example["status"]
            if file is None or not isinstance(name, str) or not name.strip() or status not in counts:
                return None
            identity = f"{file}::{name}"
            if identity in passed | failed | skipped:
                return None
            counts[status] += 1
            {"passed": passed, "failed": failed, "pending": skipped}[status].add(identity)
        outside = summary["errors_outside_of_examples_count"]
        loading = {_file(found) for message in report.get("messages") or [] for found in _LOAD_ERROR.findall(message)}
        if type(outside) is not int or None in loading or len(loading) != outside:
            return None
        if (
            summary["example_count"] != len(examples)
            or summary["failure_count"] != counts["failed"]
            or summary["pending_count"] != counts["pending"]
        ):
            return None
        collection = {file + COLLECTION for file in loading}
        failed |= collection
        if not passed | failed | skipped:
            return None
        return {
            "passed": sorted(passed),
            "failed": sorted(failed),
            "skipped": sorted(skipped),
            "collection_errors": sorted(collection),
            "uncollected": sorted(collection),
            "total": len(passed | failed | skipped),
            "complete": True,
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None
