"""Named proof from Node's built-in test runner, independent of its display reporters.

The bundled reporter consumes TestsStream events, never test stdout. Only a
complete, balanced stream with matching final counts can provide named proof.
Unsupported shell wrappers keep their normal exit-code-only behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import shlex
from pathlib import Path, PurePosixPath


VALUE_OPTIONS = {"--require", "-r", "--import", "--conditions", "-C", "--loader",
                 "--experimental-loader", "--test-concurrency", "--test-name-pattern",
                 "--test-skip-pattern", "--test-timeout", "--test-isolation",
                 "--experimental-test-isolation", "--test-shard", "--test-global-setup",
                 "--test-coverage-branches", "--test-coverage-functions", "--test-coverage-lines",
                 "--test-coverage-exclude", "--test-coverage-include", "--unhandled-rejections"}
FLAG_OPTIONS = {"--test", "--test-only", "--test-force-exit", "--no-warnings", "--no-deprecation",
                "--enable-source-maps", "--experimental-strip-types", "--no-strip-types",
                "--experimental-transform-types", "--experimental-test-coverage",
                "--experimental-test-module-mocks", "--experimental-vm-modules"}


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
    index = 1
    while index < len(words):
        word = words[index]
        if word == "--test":
            return words
        if word in VALUE_OPTIONS:
            if index + 1 == len(words) or words[index + 1].startswith("-"):
                return None
            index += 2
            continue
        if word in FLAG_OPTIONS or ("=" in word and word.split("=", 1)[0] in VALUE_OPTIONS):
            index += 1
            continue
        # An unknown option may consume --test as its value. A script or --
        # makes it a script argument, so neither supplies a test collector.
        return None
    return None


@dataclass(frozen=True)
class Invocation:
    node: str
    arguments: tuple[str, ...]
    command: str

    def targeted(self, files):
        """Retain the trusted runtime and options when selecting changed tests.

        Unknown option arity or options after a path cannot safely be rewritten.
        Keep the original suite in that case; it must still report named tests.
        """
        original = self.command
        files = ["./" + path if path.startswith("-") else path for path in files]
        kept, index, saw_path = [], 0, False
        while index < len(self.arguments):
            word = self.arguments[index]
            if word == "--":
                kept.append(word)
                break
            if word.startswith("-") and saw_path:
                return original
            if word in VALUE_OPTIONS:
                if index + 1 == len(self.arguments) or self.arguments[index + 1].startswith("-"):
                    return original
                kept.extend(self.arguments[index:index + 2])
                index += 2
                continue
            if word in FLAG_OPTIONS or ("=" in word and word.split("=", 1)[0] in VALUE_OPTIONS):
                kept.append(word)
            elif word.startswith("-"):
                return original
            else:
                saw_path = True
            index += 1
        return shlex.join([self.node, *kept, *files])


def parse(command):
    """Keep one direct Node test invocation; shell wrappers remain unsupported."""
    words = command_words(command)
    return Invocation(words[0], tuple(words[1:]), command) if words else None


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
        uncollected = set()
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
                        "cancelled" if row.get("failure_type") in
                        ("cancelledByParent", "testAborted", "testTimeoutFailure") else
                        "passed" if kind == "pass" else "failed")
            counts[category] += 1
            # Node reports an empty file as one passing test, or an import failure as one
            # failing test. Neither executed a named case, even if the file has its name.
            if row.get("file_wrapper"):
                if kind == "fail":
                    failed.add(file + "::[collection]")
                    collection.add(file + "::[collection]")
                    uncollected.add(file + "::[collection]")
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
                "collection_errors": sorted(collection), "uncollected": sorted(uncollected),
                "total": len(passed | failed | skipped), "complete": True}
    except (OSError, ValueError, KeyError, TypeError):
        return None
