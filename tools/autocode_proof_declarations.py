"""Reject unsupported explicit Python proof selectors before plan approval.

The runner binds bare test_* names and supported Go Test* paths. A dotted
Python selector is not a binding; never turn it into a guessed leaf name.
Ordinary prose, placeholders and multiple-test declarations retain ID fallback.
"""

import re

try:
    from .autocode_test_cases import mark
except ImportError:
    from autocode_test_cases import mark


_QUALIFIED_PYTHON = re.compile(
    r"(?P<selector>(?:[A-Za-z_][A-Za-z0-9_]*\.)+test_[A-Za-z0-9_]+)"
    r"(?:\s+(?:[—–-]\s+.+|\(.+\)))?",
    re.DOTALL,
)


def unsupported_python_selector(method):
    """An unequivocal dotted selector declaring one test, with optional annotation."""
    text = str(method or "").strip()
    found = mark(text)
    if not found:
        return None
    declaration = text[len(found) :].strip()
    wrapped = re.fullmatch(r"(?P<ticks>`+)([^`]+)(?P=ticks)(\s+.*)?", declaration, re.DOTALL)
    if wrapped:
        declaration = wrapped[2].strip() + (wrapped[3] or "")
    match = _QUALIFIED_PYTHON.fullmatch(declaration)
    return match["selector"] if match else None


def validate(criteria):
    """Refuse unbindable declarations without rewriting the criterion or its proof."""
    unsupported = [
        f"{row['id']} declares {selector!r}"
        for row in criteria
        if (selector := unsupported_python_selector(row.get("verification_method")))
    ]
    if unsupported:
        raise ValueError(
            "Unsupported qualified Python proof selector: "
            + "; ".join(unsupported)
            + ". After test: or guard:, declare the actual bare test_* name or a supported Go Test* path. "
            "AutoCode cannot bind dotted Python selectors and will not substitute another test."
        )
