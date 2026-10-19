
REMEDIATION REVIEW. Review the Planner's saved remediation proposals. Add one obligation_decisions
entry {obligation_id, remediation_hash, resolved, rationale, evidence_refs} for every pending_review
obligation, using its current remediation_hash and substantive evidence. A resolved=false decision
needs a blocking concern citing the obligation id.
When no obligations await review, use [] for obligation_decisions.
Do not propose remediations or return remediation_records: this review's
report schema does not allow that field.
