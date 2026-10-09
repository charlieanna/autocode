"""Compare operator instrumentation with candidate edits at the same original locations.

Git applies the patch to a private index, so paths and deletions come from Git rather
than parsed display headers. Text comparison is exact (including whitespace), with
line anchors refined to tokens so a surrounding refactor need not copy whole lines.
This establishes syntactic containment, never that instrumentation preserves behavior.
"""
from __future__ import annotations

import collections
import difflib
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Keep newlines separate: token boundaries must agree across line-anchored regions.
TOKEN = re.compile(r"\w+|[^\S\r\n]+|\r\n|[^\w]")
# SequenceMatcher without popular-token suppression can be quadratic. A large,
# repetitive replacement is ambiguous evidence; refuse it rather than hang or guess.
MAX_TOKEN_MATCHES = 2_000_000


@dataclass(frozen=True)
class FileChange:
    name: str
    before_mode: str
    after_mode: str
    before: bytes
    after: bytes


def applied_files(workspace, base, patch):
    """Apply captured patch bytes in a temporary index; return the actual changed blobs.

    The caller's index and worktree are untouched. ValueError means no usable patch.
    """
    with tempfile.TemporaryDirectory(prefix="autocode-base-patch-") as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}

        def git(*args, data=None):
            try:
                result = subprocess.run(["git", *args], cwd=workspace, env=env, input=data,
                                        capture_output=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ValueError(f"cannot inspect the patch: {exc}") from exc
            if result.returncode:
                raise ValueError(result.stderr.decode(errors="replace").strip()[:1000])
            return result.stdout

        git("read-tree", base)
        git("apply", "--cached", "--whitespace=nowarn", "-", data=patch)
        raw = git("diff", "--cached", "--raw", "--no-ext-diff", "--no-renames", "--no-abbrev", "-z", base, "--")
        fields, changes = raw.split(b"\0"), []
        for header, name in zip(fields[0::2], fields[1::2], strict=False):
            old_mode, new_mode, old_id, new_id, _ = header.decode("ascii").lstrip(":").split()
            if any(mode not in ("000000", "100644", "100755", "120000") for mode in (old_mode, new_mode)):
                raise ValueError(f"unsupported base-patch file type: {os.fsdecode(name)}")
            before = git("cat-file", "blob", old_id) if old_mode != "000000" else b""
            after = git("cat-file", "blob", new_id) if new_mode != "000000" else b""
            changes.append(FileChange(os.fsdecode(name), old_mode, new_mode, before, after))
        if not changes:
            raise ValueError("not a patch (no file changes)")
        return changes


def _edits(before, after):
    """Ordered (original start, original end, added tokens), with exact character offsets."""
    left, right = before.splitlines(keepends=True), after.splitlines(keepends=True)
    offsets = [0]
    for line in left:
        offsets.append(offsets[-1] + len(line))
    for tag, i, j, k, l in difflib.SequenceMatcher(None, left, right).get_opcodes():
        if tag == "equal":
            continue
        a, b = TOKEN.findall("".join(left[i:j])), TOKEN.findall("".join(right[k:l]))
        counts_a, counts_b = collections.Counter(a), collections.Counter(b)
        if sum(count * counts_b[token] for token, count in counts_a.items()) > MAX_TOKEN_MATCHES:
            raise ValueError("the changed region is too repetitive to locate patch edits reliably")
        positions = [offsets[i]]
        for token in a:
            positions.append(positions[-1] + len(token))
        for kind, start, end, start_b, end_b in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if kind != "equal":
                yield positions[start], positions[end], b[start_b:end_b]


def contains_edits(original, instrumented, candidate):
    """Whether every patch edit occurs in an ordered candidate edit at that original span.

    A candidate edit may cover a larger span (e.g. removing try/except around a
    newly injectable call). Its added tokens cannot satisfy two patch additions.
    Deletions also require a candidate edit covering the deleted original tokens.
    """
    candidate_edits = list(_edits(original, candidate))
    cursor, consumed = 0, 0
    for start, end, added in _edits(original, instrumented):
        while cursor < len(candidate_edits) and candidate_edits[cursor][1] < start:
            cursor, consumed = cursor + 1, 0
        matched = False
        while cursor < len(candidate_edits):
            cstart, cend, cadded = candidate_edits[cursor]
            if cstart > start:
                break
            if cstart <= start and end <= cend:
                for index in range(consumed, len(cadded) - len(added) + 1):
                    if cadded[index:index + len(added)] == added:
                        consumed, matched = index + len(added), True
                        break
                if matched:
                    break
            cursor, consumed = cursor + 1, 0
        if not matched:
            return False
    return True


def candidate_problem(change, workspace):
    """Empty on containment, otherwise a useful refusal without following candidate links."""
    root = Path(workspace).resolve()
    target = root / change.name
    if not target.is_relative_to(root) or ".." in Path(change.name).parts:
        return "the patch path leaves the workspace"
    if any(parent.is_symlink() for parent in target.parents if parent != root and parent.is_relative_to(root)):
        return "a candidate parent directory is a symlink"
    try:
        mode = target.lstat().st_mode
    except FileNotFoundError:
        mode = None
    if change.after_mode == "000000":
        return "" if mode is None else "the patch deletes a file retained by the candidate"
    if mode is None:
        return "the patched file is absent from the candidate"
    if change.after_mode == "120000":
        return "" if stat.S_ISLNK(mode) and os.fsencode(os.readlink(target)) == change.after else "symlink differs"
    if not stat.S_ISREG(mode):
        return "the patched file is not a regular candidate file"
    candidate_mode = "100755" if mode & 0o111 else "100644"
    if change.before_mode != change.after_mode and candidate_mode != change.after_mode:
        return "the patch's file mode change is absent from the candidate"
    candidate = target.read_bytes()
    if change.before_mode in ("000000", "120000") or b"\0" in change.before + change.after + candidate:
        return "" if candidate == change.after else "new, binary or type-changed file differs"
    try:
        texts = [text.decode("utf-8") for text in (change.before, change.after, candidate)]
    except UnicodeDecodeError:
        return "" if candidate == change.after else "non-UTF-8 file differs"
    return "" if contains_edits(*texts) else "patch edits are absent at the corresponding original source locations"
