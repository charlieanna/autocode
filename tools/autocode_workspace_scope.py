"""Whether a request's work spans more than this repository (#2066, step 2: detect).

One run is bound to one workspace. A request whose work partly belongs to another repository
(a ticket that needs a producer and a consumer service) can at best deliver half while looking
complete. Requirements reports what it found in ``workspace_scope``; this module keeps only the
parts it can ground, so a model's claim never acts on its own:

- each external part names a ``target`` (the other repository, as the request or the code names it)
  and the ``work`` that belongs there;
- it is kept when its ``source_quote`` is verbatim in the task, brief feedback or a saved answer
  (autocode_requirement_quotes.ground), or when its ``evidence`` is a file this report's
  ``source_refs`` cite and that exists inside the workspace (for example a client of the other
  service). A part with neither is dropped and its target is returned as dropped;
- ``spans_repositories`` is true only when a part was kept.

The validated value replaces the report's own in the saved handoff. Asking the person what to do
about it (this repository only, link a run, stop) is step 3 and is not decided here.
Depends only on requirement quoting and shared utilities.
"""

from __future__ import annotations

import re
from pathlib import Path

try:
    from . import autocode_requirement_quotes as requirement_quotes
    from .autocode_workspace_scope_state import current as current
    from .autocode_workspace_scope_state import identity as identity
    from .autocode_workspace_scope_state import intent as intent
    from .autocode_workspace_scope_state import record as record
except ImportError:
    import autocode_requirement_quotes as requirement_quotes
    from autocode_workspace_scope_state import current as current
    from autocode_workspace_scope_state import identity as identity
    from autocode_workspace_scope_state import intent as intent
    from autocode_workspace_scope_state import record as record

STRING = {"type": "string"}
PART = {
    "type": "object",
    "properties": {"target": STRING, "work": STRING, "source_quote": STRING, "evidence": STRING},
    "required": ["target", "work", "source_quote", "evidence"],
    "additionalProperties": False,
}
SCHEMA = {
    "type": "object",
    "properties": {
        "spans_repositories": {"type": "boolean"},
        "external_parts": {"type": "array", "items": PART},
    },
    "required": ["spans_repositories", "external_parts"],
    "additionalProperties": False,
}


def _text(value) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _cited_file(reference: str, root: Path) -> Path | None:
    """Resolve a relative file citation, checking optional line/range bounds."""
    span = re.search(r":([0-9]+)(?:-([0-9]+))?$", reference)
    relative = reference[: span.start()] if span else reference
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        return None
    try:
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return None
        if span:
            first, last = int(span[1]), int(span[2] or span[1])
            if first < 1 or last < first:
                return None
            with path.open("rb") as source:
                if not any(number == last for number, _ in enumerate(source, 1)):
                    return None
        return path
    except (OSError, RuntimeError, ValueError):
        return None


def _workspace_file(evidence: str, source_refs, workspace) -> bool:
    """Ground in the same cited file, not one spelling of its line reference."""
    if not evidence or not workspace:
        return False
    root = Path(workspace).resolve()
    path = _cited_file(evidence, root)
    return path is not None and any(_cited_file(str(ref), root) == path for ref in source_refs or [])


def validated(scope, *, sources, source_refs, workspace) -> tuple[dict, list[str]]:
    """The grounded ``workspace_scope`` (still valid against SCHEMA, since the saved report is checked
    again later) and the targets of the parts dropped for want of a quote or a real cited file."""
    if not isinstance(scope, dict):
        return {"spans_repositories": False, "external_parts": []}, []
    kept, dropped = [], []
    for part in scope.get("external_parts") or []:
        if not isinstance(part, dict):
            continue
        target, work = _text(part.get("target")), _text(part.get("work"))
        if not target or not work:
            continue
        quote = (
            requirement_quotes.ground(_text(part.get("source_quote")), sources) if part.get("source_quote") else None
        )
        evidence = _text(part.get("evidence"))
        if quote is None and not _workspace_file(evidence, source_refs, workspace):
            dropped.append(target)
            continue
        kept.append({"target": target, "work": work, "source_quote": quote or "", "evidence": evidence})
    return {"spans_repositories": bool(kept), "external_parts": kept}, dropped
