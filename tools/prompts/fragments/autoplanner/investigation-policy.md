
INVESTIGATION PASS. investigation_request lists discoverable questions from your previous
report (prior_report), bound to handoff_hash. This is the only investigation pass in this
clarification episode. For each question, do exactly one of:
- read the workspace and add a machine_resolutions entry {question_id, resolution,
  source_refs (existing repository files you read, optional :line), handoff_hash}, and remove
  the question from your questions (only for category technical or other);
- keep it as a kind="decision" question when it is really the user's choice;
- if the source needed is missing or unreadable, keep it as a kind="decision" question and add
  an access_blockers entry {question_id, reason}.
Anything still discoverable after this pass is shown to the user as a decision. Otherwise
return the complete report as before.
