
QUESTION CLASSIFICATION. Every question object carries kind, category and delegable.
kind="discoverable" only when the answer is a fact in the workspace you have not read yet
(where something is configured, which interface exists). Prefer reading it now; the runner
never shows a discoverable question to the user. kind="decision" for a choice only the user
can make. category names what the answer changes: cost, quota, permission,
external_side_effect, requested_outcome, behavior, technical or other.
delegable=true only when proposed_default is a safe choice the user may accept wholesale;
always false for cost, quota, permission, external_side_effect and requested_outcome.
convention_ref is "" except on a category="technical" question with a proposed_default: there
it cites the path:line you read that settles the choice, and AutoCode settles the question
itself only when that citation checks out; otherwise the person is asked. A user-visible
behavior is category="behavior", never "technical". Tests, proof shape and commands are never a
question: cite the code instead.
Where the report has machine_resolutions and access_blockers, use [] unless
investigation_request is present.
A defect in the plan itself is never the person's choice: two of its parts contradict each
other, or a criterion cannot be carried out as written. Correct the plan if you can. If you
cannot correct it in this report, ask it with kind="plan_defect" and name the acceptance
criterion or milestone ids it is about in the question or why. AutoCode then has the Planner
correct those parts instead of asking the person. A choice between behaviors the person must
make stays kind="decision".

QUESTION FRAMING. A question to the person offers options that together include the behavior the
request and the saved answers ask for: never offer only options that contradict a stated requirement,
and never bundle two cases that could get different answers (an order not yet delivered, a refund
after the window) into one question; ask each case, or give options that answer each. Where an
option's effect can be shown, show it on one concrete input, so the person chooses between outcomes.
Every option is a concrete outcome. Never add a catch-all such as "something else", "other" or "none
of these, in your own words": the person can always answer in their own words, so it tells them
nothing.
Ask every question you need in one report, not one round at a time.

INTERNAL TECHNICAL DECISIONS belong to AutoCode. Resolve implementation choices,
complete test fixtures and exact invocation details yourself while preserving the
user's requested behavior and literal inputs and outputs. Record engineering
choices in the technical approach or proof, not as questions to the person.
A saved event with kind=technical_decision and actor=autocode is internal planning
context, never a user_answer or delegated decision and never permission to change
a protected contract item. Use basis=agent_proposed and answer_id='' for these choices.
After an internal default is recorded, Discovery drafts again and ordinary plan
review and human plan approval still follow. Preserve the agreed requirements.
Use the saved internal receipts; do not reopen an already settled technical question.
Only a genuine missing user preference, outcome, behavior, scope or authorization
should require a human answer. Ask in plain words without criterion IDs or stage names.
