"""Named proof from Node's built-in test runner, independent of its display reporters.

The bundled reporter consumes TestsStream events, never test stdout. Only a
complete, balanced stream with matching final counts can provide named proof.
Unsupported shell wrappers keep their normal exit-code-only behavior.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path, PurePosixPath


def command_words(command):
    """A direct node --test invocation whose reporter the runner can safely own."""
    if not isinstance(command, str) or re.search(r"[;&|<>`$()\n\r]", command):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if len(words) < 2 or Path(words[0]).name not in ("node", "node.exe") or "--test" not in words:
        return None
    if any(word.startswith(("--test-reporter", "--watch", "--eval", "--print"))
           or word in ("-e", "-p", "-i", "--interactive") for word in words[1:]):
        return None
    # --test after the end-of-options marker or a script name is just a script argument.
    before_test = words[1:words.index("--test")]
    if any(not word.startswith("--") or word == "--" for word in before_test):
        return None
    return words


def instrument(command, result_path):
    words = command_words(command)
    if not words:
        return command
    reporter = Path(__file__).with_name("autocode_node_reporter.cjs").resolve()
    return shlex.join([words[0], "--test-reporter=spec", "--test-reporter-destination=stdout",
                       f"--test-reporter={reporter}", f"--test-reporter-destination={Path(result_path).resolve()}",
                       *words[1:]])


def test_files(root, paths):
    """Recognize node:test imports in candidate test files; never execute discovery code."""
    selected = []
    for name in paths:
        if PurePosixPath(name).suffix not in (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts"):
            continue
        try:
            source = (Path(root) / name).read_text(errors="replace")
        except OSError:
            continue
        if re.search(r"(?:\bfrom\s*|\b(?:require|import)\s*\(\s*)['\"]node:test['\"]", source):
            selected.append(name)
    return sorted(selected)


def results(path):
    """Return complete per-test results, or None for absent/invalid/ambiguous evidence."""
    try:
        rows = [json.loads(line) for line in Path(path).read_text().splitlines()]
        if len(rows) < 3 or rows[0] != {"protocol": "autocode-node-tests", "version": 1} \
                or rows[-1] != {"type": "end"}:
            return None
        starts, outcomes, parents, identities = {}, {}, {}, set()
        summary = None
        counts = dict(tests=0, suites=0, passed=0, failed=0, skipped=0, todo=0, cancelled=0)
        passed, failed, skipped, collection = set(), set(), set(), set()
        for row in rows[1:-1]:
            if not isinstance(row, dict) or summary is not None:
                return None
            kind = row["type"]
            if kind == "summary":
                if not isinstance(row.get("counts"), dict):
                    return None
                summary = row
                continue
            file, name, depth = row["file"], row["name"], row["nesting"]
            if not isinstance(file, str) or not file or PurePosixPath(file).is_absolute() \
                    or ".." in PurePosixPath(file).parts or not isinstance(name, str) or not name \
                    or type(depth) is not int or depth < 0:
                return None
            key = (file, name, depth, row["line"], row["column"])
            if kind == "start":
                if key in starts:
                    return None
                parent = parents.get((file, depth - 1), ()) if depth else ()
                if depth and not parent:
                    return None
                names = (*parent, name)
                identity = file + "::" + "::".join(names)
                if identity in identities:
                    return None
                identities.add(identity)
                starts[key] = identity
                parents[file, depth] = names
                continue
            if kind not in ("pass", "fail") or key not in starts or key in outcomes:
                return None
            outcomes[key] = kind
            identity = starts[key]
            if row["test_type"] == "suite":
                counts["suites"] += 1
                continue
            if row["test_type"] != "test":
                return None
            counts["tests"] += 1
            category = ("skipped" if row.get("skip") else "todo" if row.get("todo") else
                        "cancelled" if row.get("failure_type") == "cancelledByParent" else
                        "passed" if kind == "pass" else "failed")
            counts[category] += 1
            # Node reports an empty file as one passing test, or an import failure as one
            # failing test. Neither executed a named case, even if the file has its name.
            if row.get("file_wrapper"):
                if kind == "fail":
                    failed.add(file + "::[collection]")
                    collection.add(file + "::[collection]")
            elif category in ("skipped", "todo"):
                skipped.add(identity)
            elif category == "passed":
                passed.add(identity)
            else:
                failed.add(identity)
                if row.get("failure_type") != "testCodeFailure":
                    collection.add(identity)  # a failed hook/cancellation is not a reproduction
        if summary is None or starts.keys() != outcomes.keys() \
                or any(type(summary["counts"].get(key)) is not int or summary["counts"][key] != value
                       for key, value in counts.items()):
            return None
        return {"passed": sorted(passed), "failed": sorted(failed), "skipped": sorted(skipped),
                "collection_errors": sorted(collection), "total": len(passed | failed | skipped), "complete": True}
    except (OSError, ValueError, KeyError, TypeError):
        return None
