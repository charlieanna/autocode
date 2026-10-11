
MILESTONE REPLANS SPENT (the current gate, not a historical attempt)
{0} and its replans are spent ({1} made, limit {2}).
{3}The runner accepts no further task on {4}: a next task on {5} with any status, REWORK or CONTINUE,
including a kind=validate revalidation, pauses the run (PAUSED_MILESTONE_STALLED) for the operator to decide.
This overrides any instruction in this prompt or in checkpoint_reason to choose a REWORK or a CONTINUE
on {6}.
Before the run pauses, the Resolver is called first for a REWORK on {7} (it cannot get {8} another task)
and for a BLOCKED whose user_request.kind is not {9}; a CONTINUE on {10} pauses without the Resolver.
Whatever you decide, report in evidence and findings what still fails and why the replanned approach
did not fix it. Advancing to {11} still needs milestone_checkpoint.current_evidence_ready;
BLOCKED and COMPLETE keep their usual rules, including which user_request.kind to choose.
