
MILESTONE REPLAN REQUIRED (the current gate, not a historical attempt)
{0}.
{1}The runner now accepts a next task on {2} only with status REWORK, nonempty evidence and a changed approach:
a next_objective, requirements or validation_plan that differs from the current task, or a smaller batch.
A CONTINUE on {3}, including a kind=validate revalidation, is refused, and repeated refusals pause
the run (PAUSED_MILESTONE_REPLAN). This overrides any instruction in this prompt or in checkpoint_reason
to answer CONTINUE with a validate task. When existing work only needs revalidation, return REWORK with
next_task.kind=validate: cite the evidence of what is still unverified and change how it is verified.
{4}
Advancing to {5} still needs milestone_checkpoint.current_evidence_ready;
BLOCKED and COMPLETE keep their usual rules.
