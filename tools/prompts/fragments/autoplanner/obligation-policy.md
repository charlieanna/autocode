
REJECTED ASSUMPTIONS. deferred_obligations lists assumptions the user rejected; never rely on a
rejected assumption again, even reworded. An open obligation of kind human_decision needs the
user's answer; a draft stays clarification-only until then. In a contract, ask it as a
kind="decision" question whose id is the obligation id. In a concern-only review, raise a
blocking concern instead. Return only fields allowed by your report schema.
In a final contract, every unresolved obligation remains a blocking decision question under its
own id, and initial_task.kind must be "none".
