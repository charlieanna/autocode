
REJECTED ASSUMPTIONS. deferred_obligations lists assumptions the user rejected; never rely on a
rejected assumption again, even reworded. An open obligation of kind human_decision must be asked
as a kind="decision" question whose id is the obligation id; the plan stays clarification-only
until the user answers it. For an open remediation obligation, the Planner may add a
remediation_records entry {obligation_id, assumption_id, approach, evidence_refs,
covered_requirements (exactly the obligation's supports, each covered in requirement_trace),
episode_id (clarification_episode.id)}. The Plan Reviewer must add one obligation_decisions entry
{obligation_id, remediation_hash, resolved, rationale, evidence_refs} for every pending_review
obligation, using its current remediation_hash; resolved=false in the first review needs a
blocking concern citing the obligation id. At final review, any obligation still unresolved is
asked as a decision question under its id, and initial_task.kind must be "none". Otherwise use
[] for remediation_records and obligation_decisions.
