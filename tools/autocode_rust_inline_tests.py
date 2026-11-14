"""Sub-file extraction and base splicing for Rust inline ``#[cfg(test)]`` modules (#3467).

In Rust, unit tests commonly live beside product code in the same source file inside an
``#[cfg(test)] mod tests { ... }`` block (e.g. ``src/lib.rs``). File-level ``make_tree``
comparisons treat ``src/lib.rs`` as a product file only: it keeps the base version on
``base_with_tests`` and counts every line of ``#[cfg(test)] mod tests`` in changed-code
obligations.

This module provides a pure standard-library lexical scanner over UTF-8/raw Rust source
bytes that locates inline ``#[cfg(test)] mod <name> { ... }`` blocks while respecting line
and nested block comments, normal/byte/C/raw string literals, character literals vs
lifetimes, and non-test ``#[cfg(...)]`` attributes.

Pure stdlib helper; imports nothing from AutoCode.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_IS_CFG_TEST = re.compile(rb"^cfg\s*\(\s*test\s*,?\s*\)$")


@dataclass(frozen=True)
class ModuleSpan:
    name: str
    start: int
    brace_start: int
    end: int
    depth: int


def _ident_byte_start(b: int) -> bool:
    return (65 <= b <= 90) or (97 <= b <= 122) or b == 95 or b >= 128


def _ident_byte_cont(b: int) -> bool:
    return _ident_byte_start(b) or (48 <= b <= 57)


def _skip_ws(data: bytes, i: int) -> int:
    n = len(data)
    while i < n and data[i] in (32, 9, 10, 13):
        i += 1
    return i


def _skip_line_comment(data: bytes, i: int) -> int:
    n = len(data)
    i += 2
    while i < n and data[i] != 10:
        i += 1
    return i + 1 if i < n else n


def _skip_block_comment(data: bytes, i: int) -> int:
    n = len(data)
    i += 2
    depth = 1
    while i < n and depth > 0:
        if data[i : i + 2] == b"/*":
            depth += 1
            i += 2
        elif data[i : i + 2] == b"*/":
            depth -= 1
            i += 2
        else:
            i += 1
    return i


def _raw_string_prefix_len(data: bytes, i: int) -> tuple[int, int] | None:
    """Return ``(prefix_len, hashes)`` if ``data[i:]`` starts a raw/byte-raw/c-raw string."""
    n = len(data)
    if i >= n:
        return None
    if i == 0 or not _ident_byte_cont(data[i - 1]):
        if data[i : i + 2] in (b"br", b"rb", b"cr", b"rc"):
            p = 2
        elif data[i] == 114:  # 'r'
            p = 1
        else:
            return None
        j = i + p
        while j < n and data[j] == 35:  # '#'
            j += 1
        if j < n and data[j] == 34:  # '"'
            return p, j - (i + p)
    return None


def _skip_raw_string(data: bytes, i: int, prefix_len: int, hashes: int) -> int:
    n = len(data)
    j = i + prefix_len + hashes + 1
    closing = b'"' + b"#" * hashes
    found = data.find(closing, j)
    return found + len(closing) if found >= 0 else n


def _skip_quoted_string(data: bytes, i: int, quote_offset: int) -> int:
    n = len(data)
    j = i + quote_offset + 1
    while j < n:
        b = data[j]
        if b == 92:  # '\\'
            j += 2
        elif b == 34:  # '"'
            return j + 1
        else:
            j += 1
    return n


def _try_skip_char_literal(data: bytes, i: int) -> int | None:
    """Skip a char literal `'x'` / `'\\n'` or byte literal `b'x'` while leaving lifetimes `'a` alone."""
    n = len(data)
    start = i
    if data[i : i + 2] == b"b'" and (i == 0 or not _ident_byte_cont(data[i - 1])):
        j = i + 2
    elif data[i] == 39:  # '\''
        j = i + 1
    else:
        return None
    if j >= n or data[j] in (10, 13):
        return None
    if data[j] == 92:  # '\\'
        k = j + 2
        while k < n and k - start <= 16:
            if data[k] == 39:
                return k + 1
            if data[k] in (10, 13, 59, 123, 125):
                return None
            k += 1
        return None
    if data[j] == 39:
        return None
    # Advance over one UTF-8 scalar value.
    k = j + 1
    if data[j] >= 0x80:
        while k < n and (data[k] & 0xC0) == 0x80:
            k += 1
    if k < n and data[k] == 39:
        return k + 1
    return None


def _skip_non_code(data: bytes, i: int) -> int | None:
    n = len(data)
    if i >= n:
        return None
    two = data[i : i + 2]
    if two == b"//":
        return _skip_line_comment(data, i)
    if two == b"/*":
        return _skip_block_comment(data, i)
    if raw := _raw_string_prefix_len(data, i):
        return _skip_raw_string(data, i, *raw)
    if data[i : i + 2] in (b'b"', b'c"') and (i == 0 or not _ident_byte_cont(data[i - 1])):
        return _skip_quoted_string(data, i, 1)
    if data[i] == 34:  # '"'
        return _skip_quoted_string(data, i, 0)
    if data[i] == 39 or (data[i : i + 2] == b"b'" and (i == 0 or not _ident_byte_cont(data[i - 1]))):
        return _try_skip_char_literal(data, i)
    return None


def _skip_trivia(data: bytes, i: int) -> int:
    n = len(data)
    while i < n:
        if data[i] in (32, 9, 10, 13):
            i += 1
        elif data[i : i + 2] == b"//":
            i = _skip_line_comment(data, i)
        elif data[i : i + 2] == b"/*":
            i = _skip_block_comment(data, i)
        else:
            break
    return i


def _find_matching_delimiter(data: bytes, open_pos: int, open_byte: int, close_byte: int) -> int:
    n = len(data)
    i = open_pos + 1
    depth = 1
    while i < n and depth > 0:
        if skipped := _skip_non_code(data, i):
            i = skipped
            continue
        b = data[i]
        if b == open_byte:
            depth += 1
        elif b == close_byte:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n - 1


def _strip_comments_for_attr(inner: bytes) -> bytes:
    """Remove Rust comments inside ``#[ ... ]`` so ``#[cfg(/* comment */ test)]`` is recognized."""
    out = bytearray()
    i = 0
    n = len(inner)
    while i < n:
        two = inner[i : i + 2]
        if two == b"//":
            i = _skip_line_comment(inner, i)
            out.append(32)
        elif two == b"/*":
            i = _skip_block_comment(inner, i)
            out.append(32)
        else:
            out.append(inner[i])
            i += 1
    return bytes(out).strip()


def _line_indent_start(data: bytes, pos: int) -> int:
    line_start = pos
    while line_start > 0 and data[line_start - 1] in (32, 9):
        line_start -= 1
    return line_start if (line_start == 0 or data[line_start - 1] in (10, 13)) else pos


def _after_item_line_end(data: bytes, pos: int) -> int:
    n = len(data)
    j = pos
    while j < n and data[j] in (32, 9):
        j += 1
    if j < n and data[j] == 13:
        j += 1
    if j < n and data[j] == 10:
        return j + 1
    return pos


def _read_ident(data: bytes, i: int) -> tuple[str, int] | None:
    n = len(data)
    start = i
    if data[i : i + 2] == b"r#" and i + 2 < n and _ident_byte_start(data[i + 2]):
        i += 2
    elif i >= n or not _ident_byte_start(data[i]):
        return None
    i += 1
    while i < n and _ident_byte_cont(data[i]):
        i += 1
    return data[start:i].decode("utf-8", errors="replace"), i


def _parse_mod_after_attrs(data: bytes, pos: int) -> tuple[str, int] | None:
    """If ``pos`` starts ``[pub[(...)]] [unsafe] mod <ident> {``, return ``(name, brace_pos)``."""
    i = _skip_trivia(data, pos)
    word = _read_ident(data, i)
    if word is None:
        return None
    ident, j = word
    if ident == "pub":
        k = _skip_trivia(data, j)
        if k < len(data) and data[k] == 40:  # '('
            k = _find_matching_delimiter(data, k, 40, 41) + 1
        i = _skip_trivia(data, k)
        word = _read_ident(data, i)
        if word is None:
            return None
        ident, j = word
    if ident == "unsafe":
        i = _skip_trivia(data, j)
        word = _read_ident(data, i)
        if word is None:
            return None
        ident, j = word
    if ident != "mod":
        return None
    i = _skip_trivia(data, j)
    mod_word = _read_ident(data, i)
    if mod_word is None:
        return None
    mod_name, after_name = mod_word
    brace = _skip_trivia(data, after_name)
    if brace < len(data) and data[brace] == 123:  # '{'
        return mod_name, brace
    return None


def _scan_block(data: bytes, start: int, end: int, depth: int, out: list[ModuleSpan]) -> None:
    i = start
    pending_attrs: list[tuple[int, bool]] = []
    while i < end:
        if skipped := _skip_non_code(data, i):
            i = skipped
            continue
        b = data[i]
        if b in (32, 9, 10, 13):
            i += 1
            continue
        if b == 125:  # '}'
            return
        if data[i : i + 2] == b"#[":
            close = _find_matching_delimiter(data, i + 1, 91, 93)
            inner = _strip_comments_for_attr(data[i + 2 : close])
            attr_start = _line_indent_start(data, i)
            pending_attrs.append((attr_start, bool(_IS_CFG_TEST.match(inner))))
            i = close + 1
            continue
        if data[i : i + 3] == b"#![":
            pending_attrs.clear()
            close = _find_matching_delimiter(data, i + 2, 91, 93)
            i = close + 1
            continue
        if mod_match := _parse_mod_after_attrs(data, i):
            mod_name, brace_pos = mod_match
            brace_end = _find_matching_delimiter(data, brace_pos, 123, 125)
            has_cfg_test = any(flag for _, flag in pending_attrs)
            if has_cfg_test:
                span_start = pending_attrs[0][0]
                span_end = _after_item_line_end(data, brace_end + 1)
                out.append(ModuleSpan(mod_name, span_start, brace_pos, span_end, depth))
                pending_attrs.clear()
                i = span_end
                continue
            pending_attrs.clear()
            _scan_block(data, brace_pos + 1, brace_end, depth + 1, out)
            i = brace_end + 1
            continue
        pending_attrs.clear()
        if b == 123:  # '{' of fn/impl/struct/enum/macro body: skip without treating body macros as items
            i = _find_matching_delimiter(data, i, 123, 125) + 1
        elif word := _read_ident(data, i):
            i = word[1]
        else:
            i += 1


def _scan_modules(data: bytes) -> list[ModuleSpan]:
    if not data:
        return []
    out: list[ModuleSpan] = []
    _scan_block(data, 0, len(data), 0, out)
    return out


def test_modules(data: bytes) -> list[tuple[str, int, int]]:
    """Non-overlapping ``(mod_name, start_byte, end_byte)`` spans of inline ``#[cfg(test)] mod ... { ... }``."""
    return [(span.name, span.start, span.end) for span in _scan_modules(data)]


def test_lines(data: bytes) -> set[int]:
    """1-based line numbers whose content falls inside an inline ``#[cfg(test)] mod ... { ... }`` block."""
    spans = _scan_modules(data)
    if not spans:
        return set()
    # Map each line's [start, content_end) byte range and include only lines wholly within a test span.
    line_ranges: list[tuple[int, int, int]] = []
    pos = 0
    for lineno, raw_line in enumerate(data.splitlines(keepends=True), 1):
        line_len = len(raw_line)
        stripped_end = pos + len(raw_line.rstrip(b"\r\n"))
        if stripped_end > pos:
            line_ranges.append((lineno, pos, stripped_end))
        else:
            # Blank line inside a test module span is also owned by the module.
            line_ranges.append((lineno, pos, pos))
        pos += line_len
    lines: set[int] = set()
    for span in spans:
        for lineno, line_start, line_end in line_ranges:
            if line_start >= span.start and line_end <= span.end and (line_end > line_start or line_start < span.end):
                lines.add(lineno)
    return lines


def _excise(data: bytes, spans: list[tuple[int, int]]) -> bytes:
    if not spans:
        return data
    parts: list[bytes] = []
    cursor = 0
    for start, end in sorted(spans):
        if start > cursor:
            parts.append(data[cursor:start])
        cursor = max(cursor, end)
    if cursor < len(data):
        parts.append(data[cursor:])
    return b"".join(parts)


def product_bytes(data: bytes) -> bytes:
    """``data`` with every inline ``#[cfg(test)] mod ... { ... }`` module removed."""
    return _excise(data, [(span.start, span.end) for span in _scan_modules(data)])


def inline_test_text(data: bytes) -> str:
    """Concatenated UTF-8 source text of inline ``#[cfg(test)]`` modules in ``data``."""
    return "\n".join(data[span.start : span.end].decode("utf-8", errors="replace") for span in _scan_modules(data))


def inline_test_changed(before: bytes | None, after: bytes) -> bool:
    """Whether ``after`` defines inline ``#[cfg(test)]`` modules differing from ``before``."""
    after_spans = _scan_modules(after)
    if not after_spans:
        return False
    after_sig = [(span.name, span.depth, after[span.start : span.end].strip()) for span in after_spans]
    if before is None:
        return True
    before_spans = _scan_modules(before)
    before_sig = [(span.name, span.depth, before[span.start : span.end].strip()) for span in before_spans]
    return after_sig != before_sig


def _leading_product_bytes(data: bytes, pos: int) -> bytes:
    return product_bytes(data[:pos]).rstrip(b"\r\n")


def splice_bytes(base_data: bytes, candidate_data: bytes) -> tuple[bytes, list[dict]]:
    """Splice ``candidate_data``'s inline ``#[cfg(test)]`` modules onto ``base_data``.

    Matching ``(depth, name)`` test modules in ``base_data`` are replaced in place; new
    top-level test modules are appended at EOF, and new nested test modules require an
    identical leading product prefix so they land inside the matching base scope. Raises
    ``ValueError`` if ``candidate_data`` has no inline tests or if product bytes drift.
    """
    cand_spans = _scan_modules(candidate_data)
    if not cand_spans:
        raise ValueError("Candidate Rust file contains no inline #[cfg(test)] module")
    base_spans = _scan_modules(base_data)
    cand_keys = {(span.depth, span.name) for span in cand_spans}
    replaced = [span for span in base_spans if (span.depth, span.name) in cand_keys]

    first_pos: int | None = replaced[0].start if replaced else None
    if first_pos is None and any(span.depth > 0 for span in cand_spans):
        first_nested = next(span for span in cand_spans if span.depth > 0)
        prefix = _leading_product_bytes(candidate_data, first_nested.start)
        if not base_data.startswith(prefix):
            raise ValueError(f"Nested #[cfg(test)] mod {first_nested.name} has no matching base scope")
        first_pos = len(prefix)
        if base_data[first_pos : first_pos + 2] == b"\r\n":
            first_pos += 2
        elif base_data[first_pos : first_pos + 1] == b"\n":
            first_pos += 1

    # Remove replaced spans from base_data, adjusting insert position accordingly.
    removed_spans = [(span.start, span.end) for span in replaced]
    if first_pos is None:
        base_cleaned = base_data
        insert_at = len(base_cleaned)
    else:
        insert_at = first_pos - sum(min(end, first_pos) - start for start, end in removed_spans if start < first_pos)
        base_cleaned = _excise(base_data, removed_spans)

    chunks = [candidate_data[span.start : span.end] for span in cand_spans]
    payload = b"".join(chunk if chunk.endswith(b"\n") else chunk + b"\n" for chunk in chunks)
    before_insert = base_cleaned[:insert_at]
    after_insert = base_cleaned[insert_at:]
    if before_insert and not before_insert.endswith(b"\n"):
        payload = b"\n" + payload
    if before_insert and insert_at == len(base_cleaned) and not before_insert.endswith(b"\n\n"):
        if before_insert.endswith(b"\n"):
            payload = b"\n" + payload
        else:
            payload = b"\n" + payload
    if after_insert and not after_insert.startswith(b"\n") and not payload.endswith(b"\n"):
        payload = payload + b"\n"
    spliced = before_insert + payload + after_insert

    if product_bytes(spliced).rstrip(b"\r\n") != product_bytes(base_data).rstrip(b"\r\n"):
        raise ValueError("Rust inline test splice altered product bytes")

    details = [
        {
            "name": span.name,
            "depth": span.depth,
            "candidate_span": [span.start, span.end],
        }
        for span in cand_spans
    ]
    return spliced, details


def partition_tests(workspace, base, tests, sources, changes) -> tuple[list[str], list[str]]:
    """Promote modified/added ``.rs`` source files with changed inline tests to ``runnable_tests``.

    When a non-test-named ``.rs`` file changed ONLY inside its inline ``#[cfg(test)]`` blocks
    (its product bytes outside those blocks match ``base``), remove it from ``sources`` so
    bugfixes with test-only inline edits cannot pass the product-change gate.
    """
    runnable = [path for path in tests if changes.get(path) != "deleted"]
    kept_sources = list(sources)
    root = Path(workspace)
    for path in sources:
        if not path.endswith(".rs") or changes.get(path) == "deleted" or path in runnable:
            continue
        candidate_file = root / path
        if not candidate_file.is_file() or candidate_file.is_symlink():
            continue
        after = candidate_file.read_bytes()
        if changes.get(path) == "added":
            before = None
        else:
            shown = subprocess.run(["git", "-C", str(root), "show", f"{base}:{path}"], capture_output=True)
            before = shown.stdout if shown.returncode == 0 else None
        if inline_test_changed(before, after):
            runnable.append(path)
            if before is not None and product_bytes(before).rstrip(b"\r\n") == product_bytes(after).rstrip(b"\r\n"):
                kept_sources.remove(path)
    return runnable, kept_sources


def splice_tree(base_tree, candidate_tree, test_changes, changes, evidence_dir) -> dict | None:
    """Splice changed inline Rust test modules from ``candidate_tree`` onto ``base_tree``.

    Validates all modified ``.rs`` files in memory before touching ``base_tree`` and writes a
    provenance ``receipt.json`` into ``evidence_dir``.
    """
    rs_paths = sorted(
        path
        for path, status in changes.items()
        if path.endswith(".rs") and status == "modified" and path not in test_changes
    )
    if not rs_paths:
        return None
    staged: list[tuple[Path, bytes, dict]] = []
    files_receipt: dict[str, dict] = {}
    base_root, cand_root = Path(base_tree), Path(candidate_tree)
    for path in rs_paths:
        base_file = base_root / path
        cand_file = cand_root / path
        if base_file.is_symlink() or cand_file.is_symlink() or not base_file.is_file() or not cand_file.is_file():
            continue
        base_data = base_file.read_bytes()
        cand_data = cand_file.read_bytes()
        if not inline_test_changed(base_data, cand_data):
            continue
        spliced, modules = splice_bytes(base_data, cand_data)
        entry = {
            "base_sha256": hashlib.sha256(base_data).hexdigest(),
            "candidate_sha256": hashlib.sha256(cand_data).hexdigest(),
            "spliced_sha256": hashlib.sha256(spliced).hexdigest(),
            "modules": modules,
        }
        staged.append((base_file, spliced, entry))
        files_receipt[path] = entry
    if not staged:
        return None
    for target, spliced, _ in staged:
        target.write_bytes(spliced)
    evidence = Path(evidence_dir)
    evidence.mkdir(parents=True, exist_ok=True)
    receipt = {"files": files_receipt}
    (evidence / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt
