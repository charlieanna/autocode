"""Source positions for Python expressions and physical Git lines.

The compiler can fold an expression onto one bytecode line even though its
individual source tokens change behavior. This module observes source only;
it neither executes the file nor gives those lines test authority.
"""

from __future__ import annotations

import ast
import bisect
import io
import tokenize


def physical_lines(data: bytes) -> list[str]:
    """Decode physical LF-delimited lines, without splitting Unicode characters in string literals."""
    if not data:
        return []
    lines = data.decode("utf-8", errors="replace").split("\n")
    if data.endswith(b"\n"):
        lines.pop()
    return [line.removesuffix("\r") for line in lines]


def expression_lines(data: bytes, tree: ast.AST) -> set[int]:
    """Physical lines occupied by meaningful tokens inside runtime expressions."""
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    text = data.decode(encoding)
    # Git numbers LF-delimited physical lines; Python also accepts a bare CR.
    # An unsupported mapping must leave all changed source lines reviewable.
    if "\r" in text.replace("\r\n", ""):
        raise ValueError("Python source contains a bare carriage return")
    source_lines = text.split("\n")
    future = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in getattr(tree, "body", ())
    )
    excluded: set[int] = set()
    for node in ast.walk(tree):
        excluded_nodes: list[ast.AST] = []
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            excluded_nodes.append(node.value)  # docstrings and other inert bare constants
        if future:
            if isinstance(node, ast.arg) and node.annotation is not None:
                excluded_nodes.append(node.annotation)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
                excluded_nodes.append(node.returns)
            elif isinstance(node, ast.AnnAssign):
                excluded_nodes.append(node.annotation)
        for excluded_node in excluded_nodes:
            excluded.update(id(item) for item in ast.walk(excluded_node))
    ranges = sorted(
        ((node.lineno, node.col_offset), (node.end_lineno, node.end_col_offset))
        for node in ast.walk(tree)
        if isinstance(node, ast.expr)
        and id(node) not in excluded
        and node.end_lineno is not None
        and node.end_col_offset is not None
    )
    merged: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = previous_start, max(previous_end, end)
        else:
            merged.append((start, end))
    starts = [start for start, _ in merged]
    meaningful: set[int] = set()
    ignored = {
        tokenize.ENCODING,
        tokenize.ENDMARKER,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.NEWLINE,
        tokenize.NL,
        tokenize.COMMENT,
    }

    byte_offsets: dict[int, list[int]] = {}

    def byte_position(position):
        line, column = position
        source_line = source_lines[line - 1]
        if source_line.isascii():
            return line, column
        if line not in byte_offsets:
            offsets = [0]
            for character in source_line:
                offsets.append(offsets[-1] + len(character.encode("utf-8")))
            byte_offsets[line] = offsets
        return line, byte_offsets[line][column]

    for token in tokenize.tokenize(io.BytesIO(data).readline):
        if token.type in ignored:
            continue
        start, end = byte_position(token.start), byte_position(token.end)
        index = bisect.bisect_left(starts, end) - 1
        if index < 0 or merged[index][1] <= start:
            continue
        final = token.end[0] - (token.end[1] == 0 and token.end[0] > token.start[0])
        meaningful.update(range(token.start[0], final + 1))
    return meaningful
