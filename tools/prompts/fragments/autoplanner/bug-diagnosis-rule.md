
BUG FIX: bug_diagnosis in the handoff data is the Investigator's technical evidence about a reproduced bug,
saved in the repository at its note_path. The task and saved user answers and events define the requested
outcome and constraints; the diagnosis does not replace, narrow or override them. Plan the correction of
its root_cause while preserving every applicable user obligation, including ones absent from the diagnosis.
Its fix_plan is a proposed approach, not a user decision. Check its invariant and test_cases against those
obligations before using them. A conflicting diagnosis-derived behavior is a blocking plan concern, not
authorization to change the requested outcome. The Plan Reviewer checks that comparison independently.
After that consistency check, every plan must uphold its invariant as an acceptance criterion, checked as exactly as the invariant states it
(never "to 2 decimal places" or "within 0.001" when the rule is exact), with a regression test that fails on the
original code and passes after the fix, and must keep the project's existing tests passing. Its test_cases
are those regression tests in plain English: make each one an acceptance criterion quoting its given, when
and then, and require one test per case named test_<id>_<what it checks> (T1 -> test_t1_...); the runner
refuses the fix unless every restore case (the default kind) has such a test that fails on the original code
because of the bug and passes after the fix. A preserve case describes behavior that already works: its test
must pass on the original code and after the fix. Keep each case's kind when planning its verification.
Reconcile technical_approach and validation_plan with every retained test_cases ID, including preserve cases.
Give each diagnosis case an acceptance criterion with verification_method="test: <its named test>" for a
restore case, or "guard: <its named test>" for a preserve case. Each named test must identify one diagnosis
case. These approved names bind cases to milestones: a milestone proves its own and previously accepted
cases, and final completion proves every case. Without a complete unambiguous binding all cases are due.
A fix_plan that omits a named guard is incomplete: plan its independent named assertions explicitly, even when
an existing differently named test already covers that behavior. Keep existing tests unchanged; do not rename
or remove them to satisfy the new case binding. The Plan Reviewer checks this coverage before approval.
Fix the cause,
not the symptom, and do not widen the change beyond what the root cause needs. Do not ask the user to repeat
a clear requested outcome. Ask only about a genuinely unresolved choice in the task and saved user answers;
missing or conflicting diagnosis text is not permission to redefine it.
Cite the diagnosis in code_refs as exactly its note_path; explanations go in summaries, never inside a path.
