# Repair malformed draft guard selectors without a new question

A live `bugfix-trivial` qualification reached completion and passed its project,
hidden and regression checks, but exceeded the zero-question limit. Plan Reviewer
asked permission to replace a planner-generated `guard:` containing prose about
three tests with an ordinary suite command. The matched master run passed.

The existing revision guard correctly forbids losing the named-proof marker.
It already permits correcting an unapproved planner-generated selector while
preserving the criterion, human-review flag and named-proof requirements. Planning
now states that repair explicitly: use one supported test covering the complete
criterion, or plan an additional guard with independent assertions. Keep existing
tests, diagnosis bindings and coverage. Approved and user-authored proofs remain
protected. No revision-policy or scenario-oracle checks were relaxed.
