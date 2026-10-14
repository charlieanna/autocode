"""Fresh, captured JUnit XML outcomes for an explicitly selected report.

JUnit supplies runner-reported outcomes, not portable setup/body provenance.
An assertion ``failure`` is an ordinary failure; an ``error`` is conservatively
also uncollected because the XML cannot establish whether setup or a test body
raised. Skips never establish a pass. Neither messages nor exception names are
used to guess execution phases.

``prepare`` removes the old report before execution; timestamps are not freshness
authority. ``capture`` copies the exact bounded bytes into caller-owned evidence.
This module does not run commands, inspect Git or import application modules.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import stat
import tempfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

MAX_REPORT_BYTES = 8 * 1024 * 1024
MAX_TESTS = 100_000
MAX_DEPTH = 64
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_NUMBER = re.compile(r"\+?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


class MissingReport(ValueError):
    """The contained source report was absent, not malformed or unsafe."""


def normalize(spec):
    """Normalize ``junit:relative/file.xml`` without allowing path traversal."""
    if not isinstance(spec, str) or not spec.startswith("junit:"):
        raise ValueError("Test report must use junit:relative/file.xml")
    relative = spec.removeprefix("junit:")
    path = PurePosixPath(relative)
    if (
        not relative.strip()
        or any(character in relative for character in "\\\x00\r\n:")
        or path.is_absolute()
        or ".." in path.parts
        or not path.parts
    ):
        raise ValueError("JUnit report must name a contained relative file")
    return "junit:" + path.as_posix()


def _root(tree):
    tree = Path(tree).absolute()
    if tree.is_symlink() or not tree.is_dir():
        raise ValueError("JUnit report root must be a non-symlink directory")
    return tree.resolve()


@contextmanager
def _parent(tree, parts, *, missing=False, report=False):
    """Open each descendant directory without following symlinks."""
    descriptors = []
    try:
        current = os.open(tree, _DIRECTORY_FLAGS)
        descriptors.append(current)
        for part in parts[:-1]:
            try:
                current = os.open(part, _DIRECTORY_FLAGS, dir_fd=current)
            except FileNotFoundError:
                if missing:
                    yield None
                    return
                raise
            descriptors.append(current)
        yield current
    except OSError as error:
        if report and isinstance(error, FileNotFoundError):
            raise MissingReport("JUnit source report is missing") from error
        raise ValueError(f"Unsafe or missing JUnit report path: {error}") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _source(tree, spec):
    normalized = normalize(spec)
    tree = _root(tree)
    parts = PurePosixPath(normalized.removeprefix("junit:")).parts
    return tree, parts, normalized


def prepare(tree, spec, *, tracked=False):
    """Refuse tracked/nonregular output and remove any pre-command report.

    The caller supplies tracked-path authority from its Git inventory. Missing
    output directories are allowed; only the selected command creates them.
    """
    tree, parts, _ = _source(tree, spec)
    if tracked:
        raise ValueError("JUnit report output must not be tracked source")
    with _parent(tree, parts, missing=True) as parent:
        if parent is not None:
            try:
                mode = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False).st_mode
            except FileNotFoundError:
                pass
            else:
                if not stat.S_ISREG(mode):
                    raise ValueError("JUnit report output must be a non-symlink regular file")
                os.unlink(parts[-1], dir_fd=parent)
    return tree.joinpath(*parts)


def _read(tree, parts):
    with _parent(tree, parts, report=True) as parent:
        descriptor = None
        try:
            descriptor = os.open(parts[-1], _FILE_FLAGS, dir_fd=parent)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_REPORT_BYTES:
                raise ValueError("JUnit report must be a nonempty bounded regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(MAX_REPORT_BYTES + 1)
            after = os.fstat(descriptor)
            named = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            identity = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
            if (
                len(data) != before.st_size
                or identity(before) != identity(after)
                or identity(after) != identity(named)
                or not stat.S_ISREG(named.st_mode)
            ):
                raise ValueError("JUnit report changed during capture")
            return data
        except FileNotFoundError as error:
            if descriptor is None:
                raise MissingReport("JUnit source report is missing") from error
            raise ValueError("JUnit report changed during capture") from error
        except OSError as error:
            raise ValueError(f"Unsafe or missing JUnit report: {error}") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)


def _artifact(path, *, root=None):
    path = Path(path)
    if root is not None:
        root = _root(root)
        path = path if path.is_absolute() else root / path
        try:
            parts = path.relative_to(root).parts
        except ValueError as error:
            raise ValueError("JUnit artifact is outside its evidence root") from error
        if not parts or ".." in parts:
            raise ValueError("JUnit artifact must name a contained file")
        return root, parts
    # The caller owns the evidence parent. Reject a symlinked parent as well as
    # the leaf, while allowing platform aliases outside that supplied parent.
    path = path.absolute()
    return _root(path.parent), (path.name,)


class _NoDoctype(ET.TreeBuilder):
    def doctype(self, name, public_id, system_id):
        raise ValueError("JUnit reports must not contain a DOCTYPE or entities")


def _tag(node):
    return node.tag.rsplit("}", 1)[-1]


def _duration(node):
    if "time" not in node.attrib:
        return None
    value = node.attrib["time"].strip()
    if not _NUMBER.fullmatch(value):
        raise ValueError("JUnit durations must be finite nonnegative numbers")
    duration = float(value)
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("JUnit durations must be finite nonnegative numbers")
    return duration


def _auxiliary_content(node):
    """Diagnostics are text; properties cannot hide structural outcomes."""
    tag = _tag(node)
    if tag == "properties":
        if any(_tag(child) != "property" or len(child) for child in node):
            raise ValueError("JUnit properties must contain only leaf property elements")
    elif len(node):
        raise ValueError(f"JUnit {tag} must contain text or CDATA only")


def _parse(data):
    try:
        root = ET.fromstring(data, parser=ET.XMLParser(target=_NoDoctype()))
    except (ET.ParseError, LookupError) as error:
        raise ValueError(f"Malformed JUnit XML: {error}") from error
    if _tag(root) not in ("testsuite", "testsuites"):
        raise ValueError("JUnit report root must be testsuite or testsuites")
    groups = {name: [] for name in ("passed", "failed", "skipped", "collection_errors", "uncollected")}
    identities, durations = set(), {}

    def suite(node, inherited="", depth=0):
        if depth > MAX_DEPTH:
            raise ValueError("JUnit report nesting exceeds its bound")
        _duration(node)
        name = node.get("name") or inherited
        counts = dict.fromkeys(("tests", "failures", "errors", "skipped"), 0)
        for child in node:
            tag = _tag(child)
            if tag in ("testsuite", "testsuites"):
                nested = suite(child, name, depth + 1)
                for key in counts:
                    counts[key] += nested[key]
            elif tag == "testcase" and _tag(node) == "testsuite":
                test_name, owner = child.get("name"), child.get("classname") or name
                if not test_name or not test_name.strip() or not owner or not owner.strip():
                    raise ValueError("Every JUnit test needs a name and classname or suite name")
                identity = owner + "::" + test_name
                if identity in identities or len(identities) >= MAX_TESTS:
                    raise ValueError("JUnit test identities must be unique and bounded")
                identities.add(identity)
                duration = _duration(child)
                if duration is not None:
                    durations[identity] = duration
                # Some XML dialects encode disabled/non-executed tests only
                # in these attributes. Unsupported states must not look like
                # the ordinary absence of a failure/skipped child.
                for attribute, allowed in (("status", "run"), ("result", "completed")):
                    if attribute in child.attrib and child.attrib[attribute] != allowed:
                        raise ValueError(f"Unsupported JUnit testcase {attribute}")
                outcomes = []
                for detail in child:
                    detail_tag = _tag(detail)
                    if detail_tag in ("failure", "error", "skipped"):
                        _auxiliary_content(detail)
                        outcomes.append(detail_tag)
                    elif detail_tag in ("properties", "system-out", "system-err"):
                        _auxiliary_content(detail)
                    else:
                        raise ValueError(f"Unsupported JUnit testcase element: {detail_tag}")
                if len(outcomes) > 1:
                    raise ValueError("JUnit testcase has conflicting or repeated outcomes")
                outcome = outcomes[0] if outcomes else "pass"
                counts["tests"] += 1
                if outcome == "skipped":
                    groups["skipped"].append(identity)
                    counts["skipped"] += 1
                elif outcome in ("failure", "error"):
                    groups["failed"].append(identity)
                    counts["failures" if outcome == "failure" else "errors"] += 1
                    if outcome == "error":
                        groups["collection_errors"].append(identity)
                        groups["uncollected"].append(identity)
                else:
                    groups["passed"].append(identity)
            elif tag in ("properties", "system-out", "system-err"):
                _auxiliary_content(child)
            else:
                raise ValueError(f"Unsupported JUnit suite element: {tag}")
        for key, count in counts.items():
            if key in node.attrib:
                declared = node.attrib[key]
                if not re.fullmatch(r"[0-9]+", declared) or int(declared) != count:
                    raise ValueError(f"JUnit declared {key} does not match testcase outcomes")
        if "disabled" in node.attrib:
            declared = node.attrib["disabled"]
            # Disabled tests can be a subset of skips; a runtime skip need not
            # be disabled. Every declared disabled test must still be named
            # among the observed non-passing cases.
            if not re.fullmatch(r"[0-9]+", declared) or int(declared) > counts["skipped"]:
                raise ValueError("JUnit declared disabled exceeds skipped outcomes")
        return counts

    suite(root)
    if not identities:
        raise ValueError("JUnit report contains no test identities")
    parsed = {key: sorted(value) for key, value in groups.items()}
    parsed.update(total=len(identities), complete=True)
    if durations:
        parsed["durations"] = dict(sorted(durations.items()))
    return parsed


def results(path):
    """Parse one bounded regular report, rejecting ambiguous/incomplete XML."""
    tree, parts = _artifact(path)
    return _parse(_read(tree, parts))


def capture(tree, spec, destination):
    """Copy a fresh report into an existing caller-owned evidence directory."""
    tree, parts, normalized = _source(tree, spec)
    data = _read(tree, parts)
    parsed = _parse(data)
    destination = Path(destination).absolute()
    parent = _root(destination.parent)
    destination = parent / destination.name
    if destination == tree.joinpath(*parts):
        raise ValueError("JUnit evidence must be separate from the command report")
    temporary = None
    with _parent(parent, (destination.name,)) as directory:
        try:
            existing = os.stat(destination.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(existing.st_mode):
                raise ValueError("JUnit evidence must be a non-symlink regular file")
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".junit-", dir=parent)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(Path(temporary).name, destination.name, src_dir_fd=directory, dst_dir_fd=directory)
            temporary = None
        except OSError as error:
            raise ValueError(f"Could not capture JUnit evidence: {error}") from error
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
    return {
        "format": "junit",
        "spec": normalized,
        "path": str(destination),
        "sha256": hashlib.sha256(data).hexdigest(),
    }, parsed


def _same_type(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_type(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_type(a, b) for a, b in zip(left, right, strict=True))
    return left == right


def intact(receipt, *, root=None):
    """Authenticate captured XML and the exact typed outcomes retained with it."""
    try:
        report = receipt["test_report"]
        if not isinstance(report, dict) or set(report) != {"format", "spec", "path", "sha256"}:
            return False
        if (
            report["format"] != "junit"
            or normalize(report["spec"]) != report["spec"]
            or not isinstance(report["path"], str)
            or not isinstance(report["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", report["sha256"])
        ):
            return False
        tree, parts = _artifact(report["path"], root=root)
        data = _read(tree, parts)
        return hashlib.sha256(data).hexdigest() == report["sha256"] and _same_type(_parse(data), receipt["results"])
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False
