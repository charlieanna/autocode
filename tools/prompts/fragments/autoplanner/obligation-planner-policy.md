
PLANNER REMEDIATIONS. For an open remediation obligation, you may add a remediation_records entry
{obligation_id, assumption_id, approach, evidence_refs, covered_requirements (exactly the
obligation's supports, each covered in requirement_trace), episode_id (clarification_episode.id)}.
Only the independent Plan Reviewer can discharge that obligation by judging the exact proposal.
Otherwise use [] for remediation_records. Do not return obligation_decisions: the Planner's
report schema does not allow that field.
