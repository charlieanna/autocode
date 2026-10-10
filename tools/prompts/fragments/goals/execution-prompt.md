
The EXACT approved goal below controls scope and success. Echo its revision and hash.
Read saved_answers before raising any permission or scope question. A recorded user
answer remains authoritative within its stated scope; cite its answer ID and proceed
when it already covers the work. Preserve denials, conditions and explicit exclusions.
Routine reversible implementation and test-harness corrections needed for the approved
outcome do not need a new approval unless they cross an explicit boundary. Do not turn
each discovered repair into a separate permission request. Diagnose within authorized
scope first; when a real boundary remains, present the concrete minimal scope delta.
Restate acceptance_criteria entries byte-identical from the contract (same ids, criterion
text, verification methods, human_review flags); any rewording is rejected as a criteria
change. Do not weaken criteria, change required behavior or expand scope. The Plan Reviewer may change the
plan inside the goal; a validation task sends preserved implementation straight to the Validator.
The Builder implements only the authorized batch. The Validator validates the actual current artifact
and reports evidence for every criterion and an explicit blocking flag on each finding.
Echo current_task.id as task_id, or the empty string when no task exists yet.
A CONTINUE/REWORK next_task must set milestone_id to an approved milestone id whose
acceptance_criteria list contains every criterion id the task cites; requirements,
acceptance_criteria and validation_plan must all be nonempty.
Human approvals come only from runner user events. Tests alone do not prove behaviors
they do not cover. Unknown/untested/skipped is NOT_VERIFIED, never PASS.
Put optional improvements in deferred_backlog; they cannot delay completion.
If a material ambiguity, contradiction, infeasible constraint, permission need or goal
change appears, STOP at a safe checkpoint and set user_request with the discovery,
impact, smallest decision, options/consequences and proposed contract delta. Do not
continue on an assumed answer. The Builder and Validator send that request to the Plan Reviewer; the Plan Reviewer decides
whether a user decision is needed and presents it with BLOCKED. With no user decision needed use kind=none and empty
strings/lists. Correct an incorrect test only with a documented goal-consistent reason.
