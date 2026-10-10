Contract reference rules:
Define acceptance_criteria as objects with stable IDs (for example AC1, AC2).
For each covered requirement_trace entry, evidence may be an exact required_behaviors
string, an exact acceptance criterion ID, or a short explanation citing a defined
criterion ID as a case-sensitive whole token (for example 'AC1 verifies this').
'AC1' does not match 'AC10', 'XAC1', or 'ac1'. Unknown IDs from a defined ID
family, including a mixture such as 'AC1 and AC99', do not establish coverage.
For superseded requirements, evidence must cite an exact saved answer or feedback
event ID as a whole token; an explanation around that ID is allowed. Retain the
replacement requirement as covered. Do not mark both sides superseded just to
resolve a conflict. A question-free plan may resolve a two-sided conflict by
superseding the old side with a saved correction and covering the replacement.
A superseded trace does not neutralize a contradictory active required_behaviors entry:
reword that entry using the saved correction and an exact contract_changes record.
Keep the old wording in the historical handoff, not as an unconditional active rule.
If saved user feedback or an answer already settles a handoff conflict, record it
in conflict_resolutions: the exact requirement_ids of that conflict, basis
(user_answer or user_feedback), its saved answer_id, a source_quote copied verbatim
from that event, and a substantive resolution explaining how it settles the conflict.
Keep valid requirements covered in requirement_trace; they need not all be superseded.
Preserve these resolutions in later planner/reviewer reports. Use [] when none apply.
An explicit resolution may cite a conflict recorded in requirements_history after a
refreshed handoff removes it, but only when all its requirement IDs still have the
same verbatim source quotes. Do not transfer a resolution to reused or changed IDs.
If no matching current or historical conflict exists, retain the saved decision in
accepted_assumptions instead; an empty conflict_resolutions list then is valid.
Agent assumptions and unrelated user events cannot resolve a conflict. Carry genuinely
unresolved conflicts into open_blocking_questions; do not ask again for a saved decision.
When revising, copy required_behaviors, scope_exclusions, constraints, important_failure_cases, acceptance_criteria (including verification
methods), and permission_boundaries verbatim from goal_contract.body. In a draft without an approval receipt,
you may correct a planner-generated verification_method that was never approved or user-set, retaining
exact behavior, ID and human_review. Test:/guard: proofs cannot become prose or suite commands without a saved user basis.
An unapproved planner draft may add human review. Approved/user-set review changes and removals need a saved basis.
Use contract_changes=[] only for allowed draft corrections. Add new
items when review identifies a gap; revise technical_approach, milestones, paths,
tests and dependencies as needed. Do not rewrite an existing protected item for
style or detail. Except numeric draft stdout repairs with a reviewer receipt, changes need a saved user answer
or feedback event and an exact contract_changes entry naming the previous item.
Use contract_changes=[] when those protected fields are unchanged. Reviewer
concerns and agent proposals are not saved user authorization.
Each milestones[].acceptance_criteria must contain ONLY those existing ID strings,
for example ["AC1", "AC2"], never descriptions of checks or shell commands.
Every milestone needs a nonempty objective and at least one acceptance criterion ID;
together the milestones must cover all defined acceptance criteria.
Exception for clarification-only discovery: while blocking questions remain,
technical_approach=[] and milestones=[]; retain known requirements and questions.
Do not invent an implementation to fill those arrays before scope is settled.
Set depends_on on EVERY milestone. Use [] when it can start independently from
the same approved contract, and IDs of prerequisite milestones otherwise.
Check shared interfaces, ownership and validation boundaries before declaring
milestones independent. The dependency graph must have no cycles.
Declare affected_paths for every milestone as literal repository-relative files or
directories covering all writes, including tests. Do not use globs, parent paths,
or repository-wide '.'. Shared writes or interface/read dependencies need ordering
edges; disjoint writes alone do not establish semantic independence. If ownership
cannot be established, use [] for affected_paths; the Orchestrator will run it serially.
An implement initial_task still lists in affected_paths the files or directories it writes;
a validate initial_task, which writes nothing, may leave [] and checks its milestone's paths.
initial_task must target a milestone with depends_on []; later tasks may start a
milestone only after all of its depends_on milestones are accepted.
Put the check descriptions in acceptance_criteria[].criterion and verification_method.
