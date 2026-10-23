# Pytest functions sent to unittest (#1960)

The public recovery views for `20261023-115658-chat-81fc3a` in `small-totals`
and `20261023-122957-chat-85ba27` in `nobud-totals` show acceptance validation
running `python -m unittest -v test_totals.py` and collecting zero tests. Their
test files define module-level pytest functions. No pytest configuration or
dependency declaration was present, so automatic detection chose unittest.
The diagnostic added for #1567 explained the mismatch but did not prevent it.

Detection now considers real AST test declarations in Git-visible test modules.
Comments, strings and helper filenames do not select pytest. A unittest
`load_tests` hook keeps its existing collector; ordinary TestCase-only projects
remain unittest. Mixed ordinary TestCase and pytest functions can use pytest.
Rooted detection also reads the pinned base's test style, through its existing
bounded policy reader, so rewriting tests cannot silently downgrade collection.

Explicit test commands remain explicit. If inferred pytest is unavailable, its
execution fails honestly rather than falling back to a collector that skips the
tests. This does not install dependencies or turn collection errors into proof.

Focused deterministic coverage includes pure style selection, scoped/pinned
inputs, real failing pytest acceptance execution, explicit unittest refusal of
bare functions, and a public-CLI spec-first bug fix with no collection repair or
extra discovery. The negative reproduction check refuses a test that already
passes. Tester and Completion Reviewer remain separate.

The first selected CI run revealed a missing test dependency that the local
qualification image already provided. The `test` extra now declares pytest;
CI and the focused Linux image install it explicitly. A separate stale
multi-component fake also needed to read the complete latest rejection rather
than a historical inline delta. Its existing component-admission refusals pass
without weakening those guards.

Final CI also checked a stronger repair invariant: the fake must preserve the
original failed checks even when a newer rejected report incorrectly says PASS.
It now reconstructs the historical JSON overlay rather than choosing a newer
report by preference. The acceptance fixture selects the interpreter owning
the declared test dependency. A mixed pytest/unittest proof test now expects
pytest detection while continuing to refuse exit-code-only unittest evidence.

The feedback/replanning gap following an acceptance hold is tracked separately
by #1961. These old saved runs are preserved and are not post-fix live evidence.
