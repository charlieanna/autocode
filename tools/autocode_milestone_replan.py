"""When a stalled milestone must be replanned, and how the Completion Owner is told.

After `stalled_reviews` validations of a milestone without a new passing criterion,
autocode_milestones.observe_validation sets the milestone's needs_replan. From then on
autocode_milestones.before_assignment accepts another task on that milestone only as an
evidence-backed REWORK whose approach differs from the stalled one, until max_replans is spent.

The gate and the Completion Owner's prompt both read `pending` here, so they cannot disagree
again. They did in issue #459: the prompt still said to answer CONTINUE with a validate task when
existing work only needs revalidation, the Plan Reviewer did so three times, and the gate refused
each answer until the run paused PAUSED_MILESTONE_REPLAN with one replan still allowed.

Pure functions over one milestone progress row and the milestone checkpoint limits
(autocode_milestones.settings). Imports nothing from AutoCode.
"""
from __future__ import annotations

REQUIRED = "required"
EXHAUSTED = "exhausted"

# The general decision rule in autocode_support.ASTRA_DECISIONS that a required replan overrides,
# and what the Completion Owner reads in its place while the replan is required.
GENERAL_VALIDATE_RULE = "Use kind=validate\nwith CONTINUE when existing work only needs Validator revalidation."
REPLAN_VALIDATE_RULE = ("Use kind=validate\nwhen existing work only needs Validator revalidation; while MILESTONE "
                        "REPLAN REQUIRED below applies,\nthat task is a REWORK, never a CONTINUE.")


def pending(row, limits):
    """REQUIRED while the next task on this milestone must be a changed REWORK, EXHAUSTED once the
    bounded replans are spent (every further task on it pauses PAUSED_MILESTONE_STALLED), else None.

    max_replans None (or 0) means replans are unbounded."""
    if not row or not row.get("needs_replan") or not limits or not limits.get("stalled_reviews"):
        return None
    cap = limits.get("max_replans")
    if cap is not None and cap > 0 and row.get("replans", 0) >= cap:
        return EXHAUSTED
    return REQUIRED


def constraint(row, limits):
    """The hard constraint for the Completion Owner's prompt, or "" when no replan is required."""
    if pending(row, limits) != REQUIRED:
        return ""
    milestone = row.get("id") or "the current milestone"
    cap = limits.get("max_replans")
    if cap is not None and cap > 0:
        budget = (f"This REWORK uses replan {row.get('replans', 0) + 1} of {cap}; if {milestone} stalls again "
                  "after it, the run pauses PAUSED_MILESTONE_STALLED.")
    else:
        budget = "Replans are unbounded, but each one needs this changed REWORK."
    return (
        "\nMILESTONE REPLAN REQUIRED (the current gate, not a historical attempt)\n"
        f"Milestone {milestone} has had {row.get('reviews_without_progress', 0)} validations without progress "
        f"(limit {limits['stalled_reviews']}).\nThe runner now accepts a next task on {milestone} only "
        "with status REWORK, nonempty evidence and a changed approach:\na next_objective, requirements or "
        "validation_plan that differs from the current task, or a smaller batch.\n"
        f"A CONTINUE on {milestone}, including a kind=validate revalidation, is refused, and repeated refusals pause\n"
        "the run (PAUSED_MILESTONE_REPLAN). This overrides any instruction in this prompt or in checkpoint_reason\n"
        "to answer CONTINUE with a validate task. When existing work only needs revalidation, return REWORK with\n"
        "next_task.kind=validate: cite the evidence of what is still unverified and change how it is verified.\n"
        f"{budget}\nAdvancing to another milestone still needs milestone_checkpoint.current_evidence_ready;\n"
        "BLOCKED and COMPLETE keep their usual rules.\n")
