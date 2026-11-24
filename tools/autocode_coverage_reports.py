"""Line coverage from the report formats most toolchains write: LCOV, Cobertura XML and JaCoCo XML.

One reader for the three, chosen by the report's own bytes, never by the tool's name: gcov (through gcovr
or lcov), coverlet, PHPUnit, cargo-llvm-cov, simplecov-lcov, Istanbul and JaCoCo write one of them. Each
names a source file in its own way (an absolute path, a path below one of the report's ``<source>``
directories, or a Java package path below a source root), so a reported path is made checkout-relative
where it lies inside ``root``, and otherwise matched to the one file of ``known`` (the changed files the
caller asks about) that it names. A path that matches no file, or more than one, is kept as it was reported
and so never measures a changed file.

Pure: reads bytes it is given and imports nothing from AutoCode.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

FORMATS = ("lcov", "cobertura", "jacoco")
_LCOV_RECORD = re.compile(rb"^(?:TN|SF):", re.M)


def detect(raw: bytes) -> str | None:
    """``lcov``, ``cobertura``, ``jacoco`` or None, from the report's own bytes."""
    text = raw.lstrip(b"\xef\xbb\xbf \t\r\n")
    if text.startswith(b"<"):
        try:
            tag = ET.fromstring(raw).tag
        except ET.ParseError:
            return None
        return {"coverage": "cobertura", "report": "jacoco"}.get(tag)
    return "lcov" if _LCOV_RECORD.search(text) else None


def read(raw: bytes, root, known=()) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    """(executed, missing) line numbers by checkout-relative path. Raises ValueError for a report in no
    known format or one whose line records are malformed."""
    kind = detect(raw)
    if kind is None:
        raise ValueError("not an LCOV, Cobertura or JaCoCo coverage report")
    rows = {"lcov": _lcov, "cobertura": _cobertura, "jacoco": _jacoco}[kind](raw)
    executed: dict[str, set[int]] = {}
    missing: dict[str, set[int]] = {}
    resolved: dict[tuple, str] = {}
    for bases, name, number, hit in rows:
        if type(number) is not int or number < 1:
            raise ValueError(f"line number {number!r} in a coverage report")
        key = (bases, name)
        if key not in resolved:
            resolved[key] = resolve(name, bases, root, known)
        (executed if hit else missing).setdefault(resolved[key], set()).add(number)
    for path in set(executed) | set(missing):
        executed.setdefault(path, set())
        missing[path] = missing.get(path, set()) - executed[path]
    return executed, missing


def resolve(name: str, bases, root, known=()) -> str:
    """``name`` relative to ``root``: as reported, below one of ``bases``, or the one ``known`` path it names."""
    text = name.replace("\\", "/")
    root = Path(root).resolve()
    for base in (*bases, "."):
        path = Path(text) if PurePosixPath(text).is_absolute() else root / base.replace("\\", "/") / text
        try:
            relative = path.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        if (root / relative).is_file():
            return relative
    tail = text.lstrip("/")
    matches = [path for path in known if path == tail or path.endswith("/" + tail) or tail.endswith("/" + path)]
    return matches[0] if len(matches) == 1 else text


def _number(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{value!r} is not a whole number in a coverage report") from None


def _lcov(raw):
    """``SF:path`` opens a file's record, each ``DA:line,count[,checksum]`` is one line, ``end_of_record`` ends it."""
    current = None
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line.startswith("SF:"):
            current = line[3:]
        elif line == "end_of_record":
            current = None
        elif line.startswith("DA:") and current is not None:
            fields = line[3:].split(",")
            if len(fields) < 2:
                raise ValueError(f"malformed LCOV line record {line!r}")
            yield (), current, _number(fields[0]), _number(fields[1]) > 0


def _cobertura(raw):
    """Each ``<class filename=...>``'s ``<line number= hits=>``, below the report's ``<source>`` directories."""
    tree = ET.fromstring(raw)
    bases = tuple(source.text.strip() for source in tree.iter("source") if source.text and source.text.strip())
    for item in tree.iter("class"):
        name = item.get("filename")
        if not name:
            continue
        for line in item.iter("line"):
            yield bases, name, _number(line.get("number")), _number(line.get("hits")) > 0


def _jacoco(raw):
    """Each ``<sourcefile>`` of a ``<package>`` (a slash-separated package path) and its ``<line nr= ci= mi=>``:
    executed when an instruction on it ran, missing when none did."""
    tree = ET.fromstring(raw)
    for package in tree.iter("package"):
        directory = (package.get("name") or "").strip("/")
        for source in package.findall("sourcefile"):
            if not source.get("name"):
                continue
            name = f"{directory}/{source.get('name')}" if directory else source.get("name")
            for line in source.findall("line"):
                covered, missed = _number(line.get("ci", 0)), _number(line.get("mi", 0))
                if covered or missed:
                    yield (), name, _number(line.get("nr")), covered > 0
