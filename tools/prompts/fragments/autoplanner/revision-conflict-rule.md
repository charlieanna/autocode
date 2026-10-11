
PROTECTED REVISION CONFLICTS: outside the narrow allowed draft proof/example corrections, a
reviewer's requested behavior change still needs a saved user answer or feedback event, even in
an unapproved model-written draft. Without that basis, retain the protected text and add a blocking decision
to contract.open_blocking_questions, explaining the conflict and proposed correction in the response/summary.
Set contract.initial_task.kind=none, technical_approach=[] and milestones=[] while blocked; do not claim the
conflict is resolved or the plan ready.
Do not use agent_proposed or original_request as authorization for a protected revision, or invent a user event.
Allowed proof-only corrections do not require a new question.
MALFORMED DRAFT GUARD SELECTORS: for an unapproved planner-generated guard whose selector contains
prose or several test names, repair only the selector to one supported test name proving the entire
unchanged criterion. If no existing test covers it, plan one additional named guard with independent
assertions for the entire preserved behavior; keep the existing tests and their assertions unchanged.
Retain guard: and every named diagnosis case binding. Do not convert it to an ordinary suite command,
drop coverage, or ask permission just to repair this draft selector. Approved, user-authored or otherwise
user-protected proofs still need their saved user basis for a change; this is no exception to that guard.
