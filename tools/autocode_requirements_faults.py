"""The decidable faults of a Requirements report, listed together from one place (#3865, #3866).

Admission (``units.autoplanner.accept_handoff``) and `autocode report-check` ask the same pure
questions of a ``requirements_gather`` report:

- does each ``ambiguity_audit`` dimension's status hold to its refs (``ambiguity_audit.problems``);
- does each local ``source_refs`` entry name an existing workspace file and in-bounds lines, in
  ``require``'s line syntax (``code_citations.required_citation``), and, in admission, one the stage
  read (the read-aware ``verify_citation`` admission binds as ``cite``);
- is each requirement's ``source_quote`` the person's own text (``requirement_quotes.problems``);
- does a discussion Requirements answers itself answer what was asked, in findings rather than the
  request said back (``discussion_faults``, #4237); acceptance raises on the same list.

Before, report-check reimplemented only the quote check, so a report it called "OK" was refused
(#3865), and admission raised on its first failing check, so each repair round revealed one more
fault already in the report (#3866). ``faults`` lists every one of them; whether the stage read the
file it cites stays with admission, which passes its own ``cite``. ``context`` holds only public
inputs already shown to the stage, so report-check's spec can carry it.

Domain layer: pure over the report and that context; never mutates the report.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_examples as acceptance_examples
    from . import autocode_ambiguity_audit as ambiguity_audit
    from . import autocode_code_citations as code_citations
    from . import autocode_discussion_answer as discussion_answer
    from . import autocode_requirement_cues as requirement_cues
    from . import autocode_requirement_quotes as requirement_quotes
    from . import autocode_requirements_answer as requirements_answer
    from . import autocode_source_log as source_log
except ImportError:
    import autocode_acceptance_examples as acceptance_examples
    import autocode_ambiguity_audit as ambiguity_audit
    import autocode_code_citations as code_citations
    import autocode_discussion_answer as discussion_answer
    import autocode_requirement_cues as requirement_cues
    import autocode_requirement_quotes as requirement_quotes
    import autocode_requirements_answer as requirements_answer
    import autocode_source_log as source_log

# The report-check spec key holding ``context``.
SPEC_KEY = "requirements_faults"
FIELD = "source_refs"


def input_refs(state: RunState) -> set[str]:
    """The person's inputs a ``source_refs`` entry may name instead of a file: the task, answers, linked
    issue and design records, and user events."""
    refs = {
        "task",
        *(state.get("answers") or {}),
        *(row["id"] for row in source_log.linked_issue_records(state)),
        *(row["id"] for row in source_log.linked_design_records(state)),
    }
    refs.update(event["id"] for event in state.get("user_events") or [] if event.get("id"))
    return refs


def cited_source_refs(refs, inputs) -> list[str]:
    """``source_refs`` entries that cite something other than the person's inputs or a web page.

    External design/spec references may accompany local inspection evidence; they are not filesystem
    paths and do not satisfy the source-inspection gate."""
    return [
        ref
        for ref in refs or []
        if isinstance(ref, str) and ref not in inputs and not ref.startswith(("https://", "http://"))
    ]


def local_source_refs(refs, inputs) -> list[str]:
    """The ``source_refs`` entries admission checks as workspace files: not an input, a web page or ``catalog/``."""
    return [ref for ref in cited_source_refs(refs, inputs) if not ref.startswith("catalog/")]


def discussion_context(state: RunState) -> dict:
    """The public inputs ``discussion_faults`` needs: what was asked, the person's inputs, and whether the run
    would let Requirements answer the discussion itself (its job and the person's answer ids)."""
    return {
        "source_texts": [str(text) for text in requirement_cues.source_texts(state) if str(text)],
        "input_refs": sorted(input_refs(state)),
        "has_workflow": "workflow" in state,
        "workflow_kind": (state.get("workflow") or {}).get("kind"),
        "answered": sorted(str(key) for key in state.get("answers") or {}),
    }


def context(state: RunState) -> dict:
    """The JSON-serializable public inputs ``faults`` needs, taken from the run state."""
    return {
        **discussion_context(state),
        "coverage_checklist": [str(item) for item in requirement_cues.coverage_checklist(state)],
        "audit_source_ids": sorted(ambiguity_audit.source_ids(state, {})),
        "inherited": bool(state.get("current_task") or state.get("goal_contract")),
        "workspace": str(state.get("workspace") or ""),
        "has_inventory": not code_citations.greenfield(state),
    }


def discussion_faults(report: dict, ctx: dict) -> list[str]:
    """Why a discussion Requirements would answer itself does not answer what was asked (#4237).

    Empty unless the run would end the turn on this report's own answer
    (``requirements_answer.answers_itself``); then the findings must answer each requirement, with evidence
    from what the report read, never restating the request (``discussion_answer.problems``). A context
    without the run's job (an older report-check spec) decides nothing."""
    if "answered" not in ctx or not ctx.get("has_workflow"):
        return []
    if not requirements_answer.answers_itself(
        report, known_kind=ctx.get("workflow_kind"), answered=set(ctx["answered"])
    ):
        return []
    return discussion_answer.problems(
        report,
        request_texts=ctx.get("source_texts") or [],
        local_refs=cited_source_refs(report.get(FIELD), set(ctx.get("input_refs") or [])),
    )


def faults(report: dict, ctx: dict, *, cite=None, catalog_ok=None) -> list[str]:
    """Every decidable fault of ``report``: audit references, cited source files and lines, then quotes.

    ``cite(ref, field)`` is why a cited file or line is not valid, or None; by default the workspace-only
    ``code_citations.citation_problem``, which demands no read evidence. ``catalog_ok(ref)`` verifies a pinned
    ``catalog/...`` audit ref; by default such a ref is undecidable and passes. A class whose inputs ``ctx``
    lacks is skipped, and an empty workspace has no file to cite."""
    root = ctx.get("workspace") or ""
    readable = bool(ctx.get("has_inventory")) and bool(root)
    if cite is None:

        def cite(ref, field):
            return code_citations.citation_problem(root, ref, field)

    found: list[str] = []
    if "audit_source_ids" in ctx:
        # The same mechanical ref-kind relabel admission applies before its gate (#3975), so
        # report-check and a rejection's fault list give one answer with admission (#3865).
        audit_report, _ = ambiguity_audit.relabel(
            report,
            sources=set(ctx["audit_source_ids"]),
            cite=cite if readable else None,
            catalog_ok=catalog_ok or (lambda ref: True),
        )
        found += ambiguity_audit.problems(
            audit_report,
            sources=set(ctx["audit_source_ids"]),
            cite=cite if readable else None,
            catalog_ok=catalog_ok or (lambda ref: True),
        )
    if readable and "input_refs" in ctx:
        # source_refs are held to require's line syntax, as admission holds them; then to ``cite`` (read evidence).
        refs = local_source_refs(report.get(FIELD), set(ctx["input_refs"]))
        found += [
            problem
            for ref in refs
            if (problem := code_citations.required_citation(root, ref, FIELD)[0] or cite(ref, FIELD)) is not None
        ]
    if "source_texts" in ctx:
        found += requirement_quotes.problems(
            report.get("requirements"),
            ctx["source_texts"],
            ctx.get("coverage_checklist") or [],
            inherited=bool(ctx.get("inherited")),
        )
    # JSON acceptance examples must discriminate a duplicate-accepting or build-dropping decoder (#3912).
    found += acceptance_examples.problems(report.get("acceptance_tests"))
    found += discussion_faults(report, ctx)
    return found
