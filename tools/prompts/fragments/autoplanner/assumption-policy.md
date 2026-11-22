
ASSUMPTIONS. Each proposed_assumptions entry is {id, text, kind, category, convention_ref,
rationale, supports}. Give it a stable id (A1, A2, ...) and keep ids across refreshes.
supports lists the requirement ids it underpins. An assumption is only an engineering
convention AutoCode can settle from code: category="technical", kind="inferable", with a
convention_ref citing the repository path:line you read in this request that establishes it
and a rationale saying how. The runner checks that you read the cited file; an unread or
free-text convention_ref is shown to the person for confirmation.
When an assumption claims the project's test suite already exercises certain examples (for
instance that source docstrings are covered because a runner flag such as --doctest-modules is
set), set its suite_claim to {kind, node_ids, collection_command}: kind is "all_suite_pass" or
"named_tests_pass", node_ids lists the exact collected example node IDs the claim covers, and
collection_command is the exact unfiltered "python -m pytest --collect-only -q" you ran. A runner
flag alone is not coverage: the runner binds the claim to what that command actually collects, and
a claim whose named examples are not collected is shown as unverified, never as established coverage.
Use suite_claim: null when the assumption makes no suite-coverage claim.
User-visible behavior (inputs and edge cases, errors and exit status, output and messages,
compatibility with existing callers, saved state) is never an assumption: ask it as an open
question with options and a recommended proposed_default, or, when the brief or a saved answer
already fixes it, carry it as a requirements row with its source_quote. A behavior or other
assumption is shown to the person at the single requirements confirmation unless its
convention_ref names the saved answer or feedback event id that decided it.
Never make cost, quota, permission, external_side_effect or requested_outcome an assumption of
any kind; ask a decision question instead. When previous_requirements_handoff exists and you
drop one of its requirements, list it in ignored_requirements as {requirement_id, reason, basis,
event_id} citing the saved user answer or feedback event that authorizes it; otherwise use [].
