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
- a coordinated member run (``member_names``) must additionally name a kept part's repository:
  a role-noun label ("Checkout display member") that is neither a canonical member name nor
  literally grounded in the part's authorized quote or cited lines is refused with a ValueError,
  so the normal bounded report repair attempts the correction; a repair that does not converge
  stays a visible failure and nothing is dropped silently (#2420);
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

# The core's own coordinated-member context calls coordinated repositories
# "members", so member reports name external owners "<repository> ... member".
ROLE_NOUN = re.compile(r".+\s+member", re.IGNORECASE)


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


def _cited_text(reference: str, workspace) -> str:
    """The text of a cited workspace file, clipped to the lines the citation
    names, so a grounded literal stays inside the citation's own bounds."""
    if not reference or not workspace:
        return ""
    path = _cited_file(reference, Path(workspace).resolve())
    if path is None:
        return ""
    try:
        with path.open("rb") as handle:  # bound the read before splitting lines
            lines = handle.read(1_048_576).decode(errors="replace").splitlines()
    except OSError:
        return ""
    span = re.search(r":([0-9]+)(?:-([0-9]+))?$", reference)
    if not span:
        return "\n".join(lines)
    first, last = int(span[1]), int(span[2] or span[1])
    return "\n".join(lines[first - 1 : last])


def validated(scope, *, sources, source_refs, workspace, member_names=()) -> tuple[dict, list[str]]:
    """The grounded ``workspace_scope`` (still valid against SCHEMA, since the saved report is checked
    again later) and the targets of the parts dropped for want of a quote or a real cited file."""
    if not isinstance(scope, dict):
        return {"spans_repositories": False, "external_parts": []}, []
    kept, dropped = [], []
    canonical = {str(name).casefold() for name in member_names}
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
        if canonical and target.casefold() not in canonical and ROLE_NOUN.fullmatch(target):
            # A coordinated member's role noun is not a repository name. The exact
            # name may still be genuine when the authorized quote or the cited
            # workspace lines contain it literally; anything else is refused so the
            # normal report repair names the owner, never silently dropped.
            grounded_name = (quote and target.casefold() in quote.casefold()) or (
                bool(evidence) and target.casefold() in _cited_text(evidence, workspace).casefold()
            )
            if not grounded_name:
                raise ValueError(
                    f"workspace_scope target {target!r} is a role label, not a repository name; "
                    f"coordinated members are: {', '.join(str(name) for name in member_names)}. "
                    "Name the owning repository exactly as that list names it, or by its actual "
                    "repository name for one outside the set, grounded like the rest of the report; "
                    "if the owner cannot be named from evidence, ask the person in questions."
                )
        kept.append({"target": target, "work": work, "source_quote": quote or "", "evidence": evidence})
    return {"spans_repositories": bool(kept), "external_parts": kept}, dropped
