"""Bounded navigation hints: explicit task inputs precede lexical suggestions.

Names are not evidence or authority. Never import modules or read source contents.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts

RUNNER_OWNED_PARTS = (".autocode", ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")
IGNORED = {".venv", "venv", "node_modules", ".next", "dist", "build", ".cache", *RUNNER_OWNED_PARTS}
# Quoted paths may contain spaces. Bare tokens retain slash/colon prefixes so an
# external path or URL cannot accidentally become a relative path by substring.
TOKENS = re.compile(r"""[`'"]([^`'"\n]+)[`'"]|([^\s`'"(),;<>\[\]{}]+)""")


def _allowed_name(relative: Path) -> bool:
    return (
        not relative.is_absolute()
        and ".." not in relative.parts
        and not any(part in IGNORED for part in relative.parts)
        and not relative.name.endswith((".pyc", ".png", ".jpg", ".lock"))
    )


def _existing_file(root: Path, name: str) -> str | None:
    relative = Path(name)
    if not _allowed_name(relative):
        return None
    current = root
    try:
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return None
        if current.is_file():
            return relative.as_posix()
    except OSError:
        pass
    return None


def _explicit(root: Path, task: str, bound: int) -> list[str]:
    selected = []
    seen = set()
    for match in TOKENS.finditer(task):
        token = (match[1] or match[2]).rstrip(".!?")
        token = re.sub(r":\d+(?:-\d+)?$", "", token)
        if not token or ":" in token or "\\" in token or token.startswith("/"):
            continue
        candidates = [token]
        # Resolve unittest dotted selectors from longest module prefix to shortest,
        # without importing the module (which could execute arbitrary test code).
        if re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", token):
            parts = token.split(".")
            candidates += ["/".join(parts[:end]) + ".py" for end in range(len(parts), 0, -1)]
        for candidate in candidates:
            name = _existing_file(root, candidate)
            if name is not None:
                if name not in seen:
                    selected.append(name)
                    seen.add(name)
                break
        if len(selected) >= bound:
            break
    return selected


def inventory(workspace, task: str, limit: int = 40, scan_limit: int = 5000) -> dict:
    """List safe explicit inputs even when the fallback walk cannot reach them."""
    root = Path(workspace)
    limit, scan_limit = max(0, limit), max(0, scan_limit)
    # One extra explicit input lets the output report truncation correctly.
    explicit = _explicit(root, task, limit + 1)
    words = set(re.findall(r"[a-z]{3,}", task.lower()))
    candidates = []
    truncated = scan_limit == 0
    if scan_limit:
        for directory, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED and not Path(directory, d).is_symlink())
            for name in sorted(files):
                path = Path(directory, name)
                relative = path.relative_to(root)
                if not _allowed_name(relative) or path.is_symlink():
                    continue
                text = relative.as_posix()
                score = sum(word in text.lower() for word in words)
                candidates.append((-score, text))
                if len(candidates) >= scan_limit:
                    truncated = True
                    break
            if truncated:
                break
    candidates.sort()
    names = list(dict.fromkeys([*explicit, *(name for _, name in candidates)]))
    return {
        "files": names[:limit],
        "truncated": truncated or len(names) > limit,
        "instruction": prompts.get("fragments/autoplanner/workspace-inventory.md"),
    }
