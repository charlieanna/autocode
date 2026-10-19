"""Authenticate owned review findings and construct fully anchored COMMENT reviews."""

from __future__ import annotations

try:
    from . import autocode_review_attribution as review_attribution
except ImportError:
    import autocode_review_attribution as review_attribution

import hashlib
import json
import math
import os
import re
import stat
from contextlib import ExitStack
from pathlib import Path, PurePosixPath


class ReviewError(ValueError):
    """The review/evidence or a GitHub comment anchor is unsafe or stale."""


REPORT_BYTES = 4 * 1024 * 1024


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_artifact(workspace):
    """Bounded exact bytes from one stable regular leaf in a pinned directory chain."""
    target = Path(workspace).absolute() / "review/findings.json"
    if ".." in target.parts:
        raise ReviewError("Review findings path contains parent traversal")
    directories = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        with ExitStack() as opened:
            parent = os.open(target.anchor, flags)
            opened.callback(os.close, parent)
            for name in target.parts[1:-1]:
                child = os.open(name, flags, dir_fd=parent)
                opened.callback(os.close, child)
                directories.append((parent, name, child, os.fstat(child)))
                parent = child
            leaf = os.open(target.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            opened.callback(os.close, leaf)
            before = os.fstat(leaf)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > REPORT_BYTES:
                raise ReviewError("Review findings must be a bounded, singly linked regular file")
            chunks, size = [], 0
            while chunk := os.read(leaf, min(64 * 1024, REPORT_BYTES + 1 - size)):
                chunks.append(chunk)
                size += len(chunk)
                if size > REPORT_BYTES:
                    raise ReviewError("Review findings exceed the artifact byte limit")
            after = os.fstat(leaf)
            bound = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
            if size != before.st_size or _identity(before) != _identity(after) or _identity(before) != _identity(bound):
                raise ReviewError("Review findings changed, redirected or short-read during admission")
            for anchor, name, child, info in directories:
                observed = os.stat(name, dir_fd=anchor, follow_symlinks=False)
                current = os.fstat(child)
                if any(
                    (row.st_dev, row.st_ino, row.st_mode) != (info.st_dev, info.st_ino, info.st_mode)
                    for row in (observed, current)
                ):
                    raise ReviewError("Review findings parent directory changed during admission")
            return b"".join(chunks)
    except OSError as error:
        raise ReviewError("Review findings artifact is missing, inaccessible or redirected") from error


def strict_json(raw):
    def pairs(rows):
        result = {}
        for name, value in rows:
            if name in result:
                raise ReviewError("Review findings contain duplicate JSON keys")
            result[name] = value
        return result

    def finite(text):
        value = float(text)
        if not math.isfinite(value):
            raise ReviewError("Review findings contain a nonfinite JSON number")
        return value

    def constant(text):
        raise ReviewError("Review findings contain a nonfinite JSON constant")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_float=finite, parse_constant=constant)
    except (ValueError, UnicodeError) as error:
        raise ReviewError("Review findings contain invalid or ambiguous JSON") from error


def findings(run, checkout):
    """Read one public bounded artifact after authenticated completed-review admission."""
    view = run.status(inspect_evidence=True)
    # Review completion is not build delivery: preserve the authenticated pair
    # as recorded, and independently bind findings to this exact fetched head.
    canonical = run.evidence_report(require_current=False)
    result = (view.get("evidence") or {}).get("workflow_result") or {}
    if (
        view.get("workflow") != "review"
        or not view.get("done")
        or canonical.get("availability") not in {"current", "recorded"}
        or (view.get("evidence_report") or {}).get("availability") not in {"current", "recorded"}
        or (view.get("evidence_report") or {}).get("binding") != canonical.get("binding")
        or (canonical.get("document") or {}).get("workflow_result") != result
    ):
        raise ReviewError("Review is not complete with matching authenticated canonical evidence")
    digest = result.get("artifact_sha256")
    if (
        result.get("artifact") != "review/findings.json"
        or not isinstance(digest, str)
        or not re.fullmatch(r"[a-f0-9]{64}", digest)
    ):
        raise ReviewError("Review findings have no authenticated artifact hash; legacy reports must be revalidated")
    raw = read_artifact(checkout.workspace)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ReviewError("Review findings changed after their authenticated result")
    value = strict_json(raw)
    if not isinstance(value, dict) or not isinstance(value.get("findings"), list):
        raise ReviewError("Review findings artifact is invalid")
    if value.get("change_patch") != "":
        raise ReviewError("PR review proof must use the fetched head directly with an explicit empty change_patch")
    if value.get("verdict") != result.get("outcome"):
        raise ReviewError("Review verdict differs from authenticated canonical evidence")
    checkout.unchanged()
    return value, canonical


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)$")


def right_lines(patch):
    """GitHub RIGHT lines, refusing incomplete or oversized old/new hunk counts."""
    if not isinstance(patch, str) or not patch:
        return set()
    lines: set[int] = set()
    current = None
    old_remaining = new_remaining = 0
    for row in patch.splitlines():
        match = _HUNK.fullmatch(row)
        if match:
            if old_remaining or new_remaining:
                raise ReviewError("GitHub patch has a truncated hunk")
            current = int(match[3])
            old_remaining = int(match[2]) if match[2] is not None else 1
            new_remaining = int(match[4]) if match[4] is not None else 1
        elif current is not None:
            if row == "\\ No newline at end of file":
                continue
            prefix = row[:1]
            if prefix not in {" ", "+", "-"}:
                raise ReviewError("GitHub patch has an invalid hunk row")
            if prefix in {" ", "-"}:
                old_remaining -= 1
            if prefix in {" ", "+"}:
                new_remaining -= 1
                lines.add(current)
                current += 1
            if old_remaining < 0 or new_remaining < 0:
                raise ReviewError("GitHub patch exceeds its old/new-side hunk length")
        else:
            raise ReviewError("GitHub patch has an invalid hunk header")
    if current is None or old_remaining or new_remaining:
        raise ReviewError("GitHub patch has no complete old/new-side hunk")
    return lines


def comments(report, files):
    """Require every finding to fit its exact changed file/new-side diff anchors."""
    indexed = {}
    for row in files:
        name = row.get("filename")
        if not isinstance(name, str) or name in indexed:
            raise ReviewError("GitHub changed-file inventory is invalid or duplicated")
        indexed[name] = row
    result, ids = [], set()
    for finding in report["findings"]:
        if not isinstance(finding, dict):
            raise ReviewError("Invalid finding")
        name, span, identifier = finding.get("file"), finding.get("lines"), finding.get("id")
        if (
            not isinstance(name, str)
            or not name
            or "\\" in name
            or any(ord(c) < 32 for c in name)
            or PurePosixPath(name).is_absolute()
            or any(p in {"", ".", ".."} for p in name.split("/"))
            or not isinstance(span, list)
            or not 1 <= len(span) <= 2
            or any(type(n) is not int or n < 1 for n in span)
            or span[-1] < span[0]
            or not isinstance(identifier, str)
            or not identifier
            or identifier in ids
        ):
            raise ReviewError("Every finding needs a unique id and a safe file/positive new-side line span")
        ids.add(identifier)
        changed = indexed.get(name)
        if not changed or changed.get("status") == "removed":
            continue  # Retained in the review summary; no invented inline anchor.
        allowed = right_lines(changed.get("patch"))
        if any(line not in allowed for line in range(span[0], span[-1] + 1)):
            continue  # Keep observations outside the diff in the summary.
        severity = finding.get("severity")
        if severity not in {"blocking", "advisory"} or not all(
            isinstance(finding.get(k), str) for k in ("summary", "evidence")
        ):
            raise ReviewError(f"Finding {identifier} has invalid report text")
        body = f"**{identifier} [{review_attribution.label(finding)}]: {finding['summary']}**\n\nEvidence: {finding['evidence']}"
        for label, key in (("Example", "example"), ("Test limitation", "untestable")):
            if finding.get(key):
                body += f"\n\n{label}: {finding[key]}"
        if finding.get("disposition"):
            body += "\n\nAttribution: " + str(finding["disposition"].get("reason", ""))
        if finding.get("proven_by"):
            body += "\n\nShown by: " + ", ".join(finding["proven_by"])
        if len(body) > 60_000:
            raise ReviewError(f"Finding {identifier} exceeds GitHub's comment size limit")
        comment = {"path": name, "side": "RIGHT", "line": span[-1], "body": body}
        if span[-1] > span[0]:
            comment.update(start_line=span[0], start_side="RIGHT")
        result.append(comment)
    return result


def summary(report, canonical, url, head):
    text = (
        f"AutoCode review of {url} at `{head}`.\n\n"
        f"Recorded review verdict: {report.get('verdict')}. {report.get('summary', '')}\n\n"
        "This GitHub review is COMMENT only; AutoCode neither approves nor requests changes.\n\n"
        + canonical["markdown"]
    )
    for finding in report.get("findings", []):
        text += (
            f"\n\n**{finding['id']} [{review_attribution.label(finding)}]: {finding['summary']}**"
            f"\n\nLocation: {finding.get('file')}:{finding.get('lines')}\n\nEvidence: {finding.get('evidence', '')}"
        )
        if finding.get("disposition"):
            text += "\n\nAttribution: " + str(finding["disposition"].get("reason", ""))
    if len(text) > 60_000:
        raise ReviewError("Review and canonical evidence exceed GitHub's review size limit")
    return text
