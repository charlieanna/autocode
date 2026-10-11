
DRAFT EXAMPLE CORRECTIONS: recompute examples before proposing the plan. Only a numeric
stdout result in a never-approved, model-written draft may be corrected without a user
answer. Keep its input, command, exit code, stderr, verification_method, human_review and
all binding behavior unchanged. A saved blocking Plan Reviewer concern must name the
criterion ID and quote both complete old and corrected stdout literals. Use the explicit
form 'writes exactly `<stdout>` to stdout'. In contract_changes declare item=<criterion ID>,
change=reworded, basis=agent_proposed, answer_id='', replacement=<complete new criterion>,
and example_correction={concern_id, before:<old stdout>, after:<corrected stdout>}.
Only JSON integer values, tab-separated word/count integers, or the spaces between the tokens
of a table row that holds a number (padding that aligns a column) may differ; retain keys,
strings, array lengths and every other part of the example. Do not fabricate a concern
or change a literal supplied by the user, saved feedback or an approved revision.
The final Plan Reviewer must independently recompute the result from the unchanged
brief and input before resolving the concern. Preserve the repaired criterion exactly
in later reports; omit the old delta once incorporated. User approval is still required.
Other criterion changes require a saved user basis. Put purely engineering details in
verification_method; arbitrary prose, inputs, behavior and permissions are not corrections.
