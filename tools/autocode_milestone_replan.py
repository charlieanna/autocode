"""When a stalled milestone must be replanned, and how the Completion Owner is told.

After `stalled_reviews` validations of a milestone without a new passing criterion,
autocode_milestones.observe_validation sets the milestone's needs_replan. From then on
autocode_milestones.before_assignment accepts another task on that milestone only as an
evidence-backed REWORK whose approach differs from the stalled one, until max_replans is spent.

Once those replans are spent and the milestone stalls again, before_assignment accepts no
further task on it: any next task there pauses PAUSED_MILESTONE_STALLED for the operator.

The gate and the Completion Owner's prompt both read `pending` and `members` here, so they cannot
disagree again. They did in issue #459: the prompt still said to answer CONTINUE with a validate task when
existing work only needs revalidation, the Plan Reviewer did so three times, and the gate refused
each answer until the run paused PAUSED_MILESTONE_REPLAN with one replan still allowed. With the
replans spent, the prompt said nothing and its policy still asked for a changed REWORK, which can
send the Resolver to plan a task the gate then refuses.

Pure functions over one milestone progress row and the milestone checkpoint limits
(autocode_milestones.settings). Imports nothing from AutoCode.
"""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


REQUIRED = "required"
EXHAUSTED = "exhausted"

# The general decision rule in autocode_support.ASTRA_DECISIONS that a pending replan overrides,
# and what the Completion Owner reads in its place while the replan is required or spent.
GENERAL_VALIDATE_RULE = prompts.get("fragments/milestone-replan/general-validate-rule.md")
REPLAN_VALIDATE_RULE = prompts.get("fragments/milestone-replan/replan-validate-rule.md")
# The user_request kinds whose BLOCKED review waits for the user at once. Every other kind, like a REWORK,
# first calls the Resolver: autopilot queues it for blocker and clarification, and the AutoResolver human
# gate (autocode_goal_lifecycle.wait_for_user, scope "blocker") defers contradiction and infeasible to it.
ASKS_USER_DIRECTLY = ("permission", "goal_change")
SPENT_VALIDATE_RULE = prompts.get("fragments/milestone-replan/spent-validate-rule.md")


def pending(row, limits):
    """REQUIRED while the next task on this milestone must be a changed REWORK, EXHAUSTED once the
    bounded replans are spent (every further task on it pauses PAUSED_MILESTONE_STALLED), else None.

    max_replans None (or 0) means replans are unbounded."""
    if not row or not limits:
        return None
    if row.get("builder_reassessment"):
        cap = limits.get("max_replans")
        return EXHAUSTED if cap is not None and cap > 0 and row.get("replans", 0) >= cap else REQUIRED
    if not row.get("needs_replan") or not limits.get("stalled_reviews"):
        return None
    cap = limits.get("max_replans")
    if cap is not None and cap > 0 and row.get("replans", 0) >= cap:
        return EXHAUSTED
    return REQUIRED


def members(row):
    """The milestone IDs a next task may name (next_task.milestone_id) and still be on this row: an integrated
    batch's members, else the milestone itself. before_assignment applies the gate to exactly these."""
    return list(row.get("milestone_ids", [row.get("id")]))


def validate_rule(row, limits):
    """The rule that replaces GENERAL_VALIDATE_RULE in the Completion Owner's prompt, or None when no
    replan is pending."""
    return {REQUIRED: REPLAN_VALIDATE_RULE, EXHAUSTED: SPENT_VALIDATE_RULE}.get(pending(row, limits))


def _either(ids):
    return ids[0] if len(ids) == 1 else ", ".join(ids[:-1]) + " or " + ids[-1]


def constraint(row, limits):
    """The hard constraint for the Completion Owner's prompt, or "" when no replan is pending."""
    gate = pending(row, limits)
    if gate is None:
        return ""
    milestone = row.get("id") or "the current milestone"
    cap = limits.get("max_replans")
    counts = (
        f"{row.get('reviews_without_progress', 0)} validations without progress (limit {limits['stalled_reviews']})"
    )
    if row.get("builder_reassessment"):
        counts = prompts.get("fragments/milestone-replan/constraint-06.md") + str(row["builder_reassessment"])
    if row.get("milestone_ids"):
        # An integrated batch's id (batch:<digest>) is no milestone a next task can name; the gate refuses its members.
        on = _either(members(row))
        stalled = f"Integrated batch {milestone} has had {counts}"
        scope = prompts.get("fragments/milestone-replan/constraint-02.md").format(on)
        subject, other = "the batch", "a milestone outside the batch"
    else:
        on = subject = milestone
        stalled, scope, other = f"Milestone {milestone} has had {counts}", "", "another milestone"
    if gate == EXHAUSTED:
        return prompts.get("fragments/milestone-replan/constraint-03.md").format(
            stalled, row.get("replans", 0), cap, scope, on, on, on, on, on, " or ".join(ASKS_USER_DIRECTLY), on, other
        )
    if cap is not None and cap > 0:
        budget = prompts.get("fragments/milestone-replan/constraint-04.md").format(
            row.get("replans", 0) + 1, cap, subject
        )
    else:
        budget = prompts.get("fragments/milestone-replan/constraint-05.md")
    return prompts.get("fragments/milestone-replan/constraint.md").format(stalled, scope, on, on, budget, other)
