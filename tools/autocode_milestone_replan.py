"""When a stalled milestone must be replanned, and how the Completion Owner is told.

After `stalled_reviews` validations of a milestone without a new passing criterion,
autocode_milestones.observe_validation sets the milestone's needs_replan. From then on
autocode_milestones.before_assignment accepts another task on that milestone only as an
evidence-backed REWORK whose approach differs from the stalled one, until max_replans is spent.

Once those replans are spent and the milestone stalls again, before_assignment accepts no
further task on it: any next task there pauses PAUSED_MILESTONE_STALLED for the operator.

The gate and the Completion Owner's prompt both read `pending` here, so they cannot disagree
again. They did in issue #459: the prompt still said to answer CONTINUE with a validate task when
existing work only needs revalidation, the Plan Reviewer did so three times, and the gate refused
each answer until the run paused PAUSED_MILESTONE_REPLAN with one replan still allowed. With the
replans spent, the prompt said nothing and its policy still asked for a changed REWORK, which can
send the Resolver to plan a task the gate then refuses.

Pure functions over one milestone progress row and the milestone checkpoint limits
(autocode_milestones.settings). Imports nothing from AutoCode.
"""
from __future__ import annotations

REQUIRED = "required"
EXHAUSTED = "exhausted"

# The general decision rule in autocode_support.ASTRA_DECISIONS that a pending replan overrides,
# and what the Completion Owner reads in its place while the replan is required or spent.
GENERAL_VALIDATE_RULE = "Use kind=validate\nwith CONTINUE when existing work only needs Validator revalidation."
REPLAN_VALIDATE_RULE = ("Use kind=validate\nwhen existing work only needs Validator revalidation; while MILESTONE "
                        "REPLAN REQUIRED below applies,\nthat task is a REWORK, never a CONTINUE.")
SPENT_VALIDATE_RULE = ("Use kind=validate\nwith CONTINUE when existing work only needs Validator revalidation, "
                       "except on the milestone\nnamed in MILESTONE REPLANS SPENT below, where no further task runs.")


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


def validate_rule(row, limits):
    """The rule that replaces GENERAL_VALIDATE_RULE in the Completion Owner's prompt, or None when no
    replan is pending."""
    return {REQUIRED: REPLAN_VALIDATE_RULE, EXHAUSTED: SPENT_VALIDATE_RULE}.get(pending(row, limits))


def constraint(row, limits):
    """The hard constraint for the Completion Owner's prompt, or "" when no replan is pending."""
    gate = pending(row, limits)
    if gate is None:
        return ""
    milestone = row.get("id") or "the current milestone"
    cap = limits.get("max_replans")
    stalled = (f"Milestone {milestone} has had {row.get('reviews_without_progress', 0)} validations without progress "
               f"(limit {limits['stalled_reviews']})")
    if gate == EXHAUSTED:
        return (
            "\nMILESTONE REPLANS SPENT (the current gate, not a historical attempt)\n"
            f"{stalled} and its replans are spent ({row.get('replans', 0)} made, limit {cap}).\n"
            f"The runner accepts no further task on {milestone}: a next task on {milestone} with any status, "
            "REWORK or CONTINUE,\nincluding a kind=validate revalidation, pauses the run (PAUSED_MILESTONE_STALLED) "
            "for the operator to decide.\n"
            f"A REWORK on {milestone} may first send the Resolver to plan a task the runner will refuse.\n"
            "This overrides any instruction in this prompt or in checkpoint_reason to choose a REWORK or a "
            f"CONTINUE\non {milestone}. Whatever you decide, report in evidence and findings what still fails and "
            "why the replanned approach\ndid not fix it. Advancing to another milestone still needs "
            "milestone_checkpoint.current_evidence_ready;\nBLOCKED and COMPLETE keep their usual rules.\n")
    if cap is not None and cap > 0:
        budget = (f"This REWORK uses replan {row.get('replans', 0) + 1} of {cap}; if {milestone} stalls again "
                  "after it, the run pauses PAUSED_MILESTONE_STALLED.")
    else:
        budget = "Replans are unbounded, but each one needs this changed REWORK."
    return (
        "\nMILESTONE REPLAN REQUIRED (the current gate, not a historical attempt)\n"
        f"{stalled}.\nThe runner now accepts a next task on {milestone} only "
        "with status REWORK, nonempty evidence and a changed approach:\na next_objective, requirements or "
        "validation_plan that differs from the current task, or a smaller batch.\n"
        f"A CONTINUE on {milestone}, including a kind=validate revalidation, is refused, and repeated refusals pause\n"
        "the run (PAUSED_MILESTONE_REPLAN). This overrides any instruction in this prompt or in checkpoint_reason\n"
        "to answer CONTINUE with a validate task. When existing work only needs revalidation, return REWORK with\n"
        "next_task.kind=validate: cite the evidence of what is still unverified and change how it is verified.\n"
        f"{budget}\nAdvancing to another milestone still needs milestone_checkpoint.current_evidence_ready;\n"
        "BLOCKED and COMPLETE keep their usual rules.\n")
