"""Source-level identities for traceback locations; no result grants proof authority.

The caller must independently bind the source bytes and the runner's traceback
to the exact execution before comparing these identities.
"""

from __future__ import annotations

import ast
import hashlib
import json

MAX_SOURCE_BYTES = 2_000_000
MAX_NODES = 100_000


def identity(source, line):
    """Identify one unique statement within an unambiguous lexical scope, or hold.

    Repeated identical statements can be distinguished only while the entire
    enclosing scope is identical; this prevents a shifted occurrence from being
    equated with another failure site. The caller retains all exception text.
    """
    if (
        not isinstance(source, str)
        or len(source.encode("utf-8")) > MAX_SOURCE_BYTES
        or type(line) is not int
        or line < 1
    ):
        return None
    try:
        root = ast.parse(source)
        scopes = {(): root}
        repeated_scopes = set()
        entries = []
        stack = [(root, ())]
        while stack:
            node, scope = stack.pop()
            if len(entries) > MAX_NODES:
                return None
            entries.append((node, scope))
            child_scope = scope
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                child_scope = (*scope, node.name)
                if child_scope in scopes:
                    repeated_scopes.add(child_scope)
                else:
                    scopes[child_scope] = node
            stack.extend((child, child_scope) for child in ast.iter_child_nodes(node))
        matches = [
            (node.end_lineno - node.lineno, scope, node)
            for node, scope in entries
            if isinstance(node, ast.stmt)
            and not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.lineno <= line <= node.end_lineno
        ]
        if not matches:
            return None
        matches.sort(key=lambda row: row[0])
        span, scope, statement = matches[0]
        if scope in repeated_scopes or (len(matches) > 1 and matches[1][0] == span):
            return None
        shape = ast.dump(statement, include_attributes=False)
        peers = [
            node
            for node, owner in entries
            if owner == scope and isinstance(node, ast.stmt) and ast.dump(node, include_attributes=False) == shape
        ]
        if not peers:
            return None
        value = {"scope": scope, "statement": shape}
        if len(peers) > 1:
            ordered = sorted(peers, key=lambda node: (node.lineno, node.col_offset))
            value.update(
                scope_shape=ast.dump(scopes[scope], include_attributes=False), occurrence=ordered.index(statement)
            )
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    except (SyntaxError, ValueError, TypeError, UnicodeError, RecursionError, MemoryError):
        return None
