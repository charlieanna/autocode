"""Read workspace design or spec Markdown files explicitly named in user messages.

When a person asks to implement an approved repository design document (for example,
``Implement the approved design in docs/design/rate-limiter.md``), Requirements sets
``design_document`` and quotes sentences from that file in ``requirements[].source_quote``.
Recording the referenced file in ``source_log`` lets ``source_quote`` grounding,
``autocode report-check``, and ``source_trace`` verify verbatim quotes against the pinned
bytes without forcing every background sentence of the document into ``out_of_scope``.

Only regular, non-symlink ``.md`` files inside the workspace that the user's own text
explicitly names (excluding repository instruction/meta files) are read. Pure stdlib
module; imports nothing from AutoCode.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any

LINKED_DESIGN_KIND = "design document"
MAX_DESIGN_BYTES = 64_000
MAX_DESIGNS_PER_MESSAGE = 5

_EXCLUDED_BASENAMES = frozenset(
    {
        "agents.md",
        "claude.md",
        "gemini.md",
        "changelog.md",
        "reliability.md",
        "contributing.md",
        "license.md",
        "security.md",
        "code_of_conduct.md",
    }
)
_URL_SPAN = re.compile(r"https?://[^\s<>()\"'`]+")
_MD_PATH = re.compile(
    r"(?<![A-Za-z0-9_./:-])(?:\./)?(?P<path>(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.md|[A-Za-z0-9_.-]*(?:design|spec)[A-Za-z0-9_.-]*\.md)(?![A-Za-z0-9_./-])",
    re.IGNORECASE,
)


def source_id(rel_path: str) -> str:
    """Stable colon-delimited source identifier accepted by source_log and source_trace."""
    return "design:" + rel_path.replace("/", ":")


def _normalize_rel_path(raw: str) -> str | None:
    cleaned = str(raw or "").strip().removeprefix("./")
    if not cleaned or cleaned.startswith("/") or "\\" in cleaned:
        return None
    posix = PurePosixPath(cleaned)
    if not posix.parts or any(part in ("", ".", "..") for part in posix.parts):
        return None
    if posix.name.lower() in _EXCLUDED_BASENAMES:
        return None
    if not posix.name.lower().endswith(".md"):
        return None
    return posix.as_posix()


def extract_paths(text: str) -> list[str]:
    """Normalized relative ``.md`` design/spec paths explicitly named in ``text``."""
    if not isinstance(text, str) or ".md" not in text.lower():
        return []
    scrubbed = _URL_SPAN.sub(" ", text)
    found: list[str] = []
    seen: set[str] = set()
    for match in _MD_PATH.finditer(scrubbed):
        rel = _normalize_rel_path(match.group("path"))
        if rel is None or rel in seen:
            continue
        seen.add(rel)
        found.append(rel)
        if len(found) >= MAX_DESIGNS_PER_MESSAGE:
            break
    return found


def read_design_file(workspace: str | Path, rel_path: str) -> str | None:
    """Read UTF-8 content of a verified, bounded, non-symlink workspace design file."""
    rel = _normalize_rel_path(rel_path)
    if rel is None or not workspace:
        return None
    try:
        root = Path(workspace)
        if not root.is_dir():
            return None
        resolved_root = root.resolve()
        current = root
        for part in PurePosixPath(rel).parts:
            current = current / part
            if current.is_symlink():
                return None
        if not current.is_file() or not current.resolve().is_relative_to(resolved_root):
            return None
        size = current.stat().st_size
        if size <= 0 or size > MAX_DESIGN_BYTES:
            return None
        text = current.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return text if text.strip() else None


def fetch_linked_records(
    workspace: str | Path,
    parent_id: str,
    text: str,
    *,
    existing_ids: set[str] | frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Build authenticated source_log records for workspace design files named in ``text``."""
    seen = {item.lower() for item in existing_ids}
    records: list[dict[str, Any]] = []
    for rel in extract_paths(text):
        ident = source_id(rel)
        if ident.lower() in seen:
            continue
        content = read_design_file(workspace, rel)
        if content is None:
            continue
        seen.add(ident.lower())
        records.append(
            {
                "id": ident,
                "kind": LINKED_DESIGN_KIND,
                "parent_id": parent_id,
                "path": rel,
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "text": content,
            }
        )
    return records


def buildable_turn_paths(turns: Any) -> list[str]:
    """Approved or proposed design paths carried forward into a follow-up turn."""
    if not isinstance(turns, list) or not turns:
        return []
    last = turns[-1] if isinstance(turns[-1], dict) else {}
    prev_raw = last.get("previous")
    previous: dict[str, Any] = prev_raw if isinstance(prev_raw, dict) else {}
    design_raw = previous.get("design")
    design: dict[str, Any] = design_raw if isinstance(design_raw, dict) else {}
    raw_paths: list[str] = []
    if design.get("mode") == "propose":
        raw_paths = [str(item) for item in (design.get("documents") or []) if item]
    elif design.get("mode") == "review" and design.get("verdict") == "approve" and design.get("design_under_review"):
        raw_paths = [str(design["design_under_review"])]
    normalized: list[str] = []
    for raw in raw_paths:
        rel = _normalize_rel_path(raw)
        if rel and rel not in normalized:
            normalized.append(rel)
    return normalized
