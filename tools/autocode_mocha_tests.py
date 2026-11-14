"""Per-test results from Mocha's built-in JSON reporter, so a Mocha suite names each test (#3467).

A Mocha run previously gave the proof only an exit code. Mocha ships a built-in ``json``
reporter that writes structured test outcomes directly to a file with ``--reporter json
--reporter-option output=...`` without installing any package.

Each test's id is ``FILE::describe > title`` (or ``FILE::fullTitle`` when top-level), FILE
relative to where Mocha ran, matching Vitest and Jest. Suite hook failures reported in
``failures`` outside ``tests`` are recorded in ``collection_errors`` (and ``failed``) with empty
``uncollected`` because the suite hook executed. Counts that disagree with the report's
``stats``, duplicate test ids, or files outside ``tree`` return None.

Lower layer: imports only ``autocode_npm_scripts``.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path, PurePosixPath

try:
    from . import autocode_npm_scripts as npm_scripts
except ImportError:
    import autocode_npm_scripts as npm_scripts

RUNTIME = "[mocha runtime error]"
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# Options that alter the reporter or prevent a deterministic finite test run.
_OWNED = frozenset(
    {
        "reporter",
        "reporteroption",
        "reporteroptions",
        "watch",
        "watchall",
        "dryrun",
        "listinterfaces",
        "listreporters",
        "help",
        "version",
    }
)
_SHORT_OWNED = frozenset({"-R", "-O", "-w", "-h", "-V"})


def _mocha(words) -> bool:
    """Whether ``words`` are one Mocha invocation whose reporter AutoCode can own."""
    words = list(words or [])
    if words and Path(words[0]).name == "cross-env":
        words = words[1:]
    while words and _ASSIGNMENT.match(words[0]):
        if shlex.quote(words[0]) != words[0]:
            return False
        words = words[1:]
    if not words:
        return False
    first = Path(words[0]).name
    if first in ("mocha", "mocha.js", "mocha.cmd"):
        options = words[1:]
    elif first in ("node", "node.exe") and len(words) > 1 and Path(words[1]).name in ("mocha", "mocha.js"):
        options = words[2:]
    elif first in ("npx", "npx.cmd", "pnpm", "pnpm.cmd", "yarn", "yarn.cmd"):
        rest = words[1:]
        while rest and rest[0] in ("--no-install", "--no", "--silent", "-s"):
            rest = rest[1:]
        if not rest or Path(rest[0]).name not in ("mocha", "mocha.cmd"):
            return False
        options = rest[1:]
    elif first in ("bunx", "bunx.cmd") and len(words) > 1 and Path(words[1]).name in ("mocha", "mocha.cmd"):
        options = words[2:]
    else:
        return False
    return "--" not in options and not any(
        word in _SHORT_OWNED or word.lstrip("-").split("=", 1)[0].lower().replace("-", "") in _OWNED
        for word in options
        if word.startswith("-")
    )


def command_words(command, tree=None) -> list[str] | None:
    """The words of a plain Mocha command, or of a single-step ``npm test`` in ``tree``; else None."""
    words = npm_scripts.words(command)
    if _mocha(words):
        return words
    found = npm_scripts.test_script(command, tree)
    if found is None:
        return None
    script = npm_scripts.words(found[0].get("test"))
    if script is None or not _mocha([*script, *found[1]]):
        return None
    return words


def instrument(command, result_path, tree=None) -> str:
    """``command`` writing Mocha's JSON report to ``result_path``; other commands are returned as written."""
    words = command_words(command, tree)
    if not words:
        return command
    if Path(words[0]).name in ("npm", "npm.cmd") and "--" not in words:
        words = [*words, "--"]
    return shlex.join([*words, "--reporter", "json", "--reporter-option", "output=" + str(Path(result_path).resolve())])


def _relative(name, root) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return None
    posix_text = name.replace("\\", "/")
    if root is None:
        text = posix_text.removeprefix("./")
        parts = PurePosixPath(text).parts
        return text if text and not PurePosixPath(text).is_absolute() and ".." not in parts else None
    base = Path(root).resolve()
    raw = Path(name)
    if not raw.is_absolute():
        if ".." in PurePosixPath(posix_text).parts:
            return None
        resolved = (base / posix_text).resolve()
    else:
        resolved = raw.resolve()
    return resolved.relative_to(base).as_posix() if resolved.is_relative_to(base) else None


def _test_label(title: object, full_title: object) -> str | None:
    if not isinstance(full_title, str) or not full_title.strip():
        return None
    if isinstance(title, str) and title.strip() and full_title != title and full_title.endswith(" " + title):
        prefix = full_title[: -len(title) - 1].strip()
        if prefix:
            return f"{prefix} > {title}"
    return full_title


def results(path, tree=None, output_path=None) -> dict | None:
    """Complete per-test results from one Mocha JSON report, or None for absent or inconsistent evidence."""
    del output_path
    try:
        report = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(report, dict):
        return None
    stats = report.get("stats")
    tests = report.get("tests")
    passes = report.get("passes")
    failures = report.get("failures")
    pending = report.get("pending")
    if (
        not isinstance(stats, dict)
        or not isinstance(tests, list)
        or not isinstance(passes, list)
        or not isinstance(failures, list)
        or not isinstance(pending, list)
    ):
        return None
    for metric in ("tests", "passes", "failures", "pending"):
        value = stats.get(metric)
        if type(value) is not int or value < 0:
            return None
    if (
        stats["tests"] != len(tests)
        or stats["passes"] != len(passes)
        or stats["failures"] != len(failures)
        or stats["pending"] != len(pending)
    ):
        return None
    if not tests and not failures:
        return None

    key_to_id: dict[tuple[str, str], str] = {}
    seen_ids: set[str] = set()
    for item in tests:
        if not isinstance(item, dict):
            return None
        rel = _relative(item.get("file"), tree)
        label = _test_label(item.get("title"), item.get("fullTitle"))
        if rel is None or label is None:
            return None
        full_title = item["fullTitle"]
        identity = f"{rel}::{label}"
        key = (rel, full_title)
        if identity in seen_ids or key in key_to_id:
            return None
        seen_ids.add(identity)
        key_to_id[key] = identity

    passed: set[str] = set()
    failed: set[str] = set()
    skipped: set[str] = set()
    collection: set[str] = set()

    for bucket, target in ((passes, passed), (pending, skipped)):
        for item in bucket:
            if not isinstance(item, dict):
                return None
            rel = _relative(item.get("file"), tree)
            full_title = item.get("fullTitle")
            if rel is None or not isinstance(full_title, str) or (rel, full_title) not in key_to_id:
                return None
            target.add(key_to_id[(rel, full_title)])

    for item in failures:
        if not isinstance(item, dict):
            return None
        raw_file = item.get("file")
        full_title = item.get("fullTitle")
        if not isinstance(full_title, str) or not full_title.strip():
            return None
        rel = _relative(raw_file, tree)
        if raw_file and rel is None:
            return None
        key = (rel or "", full_title)
        if key in key_to_id:
            failed.add(key_to_id[key])
        else:
            label = _test_label(item.get("title"), full_title) or full_title
            hook_id = f"{rel}::{label}" if rel else f"{RUNTIME}::{label}"
            failed.add(hook_id)
            collection.add(hook_id)

    accounted = passed | (failed - collection) | skipped
    if len(accounted) != len(tests) and not collection:
        return None
    if passed & (failed | skipped) or (failed & skipped):
        return None
    return {
        "passed": sorted(passed),
        "failed": sorted(failed),
        "skipped": sorted(skipped),
        "collection_errors": sorted(collection),
        "uncollected": [],
        "total": len(tests) + len(collection),
        "complete": True,
    }
