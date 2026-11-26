# Repair loops in a live build: mutants that never ran

A live Claude run on 2026-11-25 (master `cad2b99`, Builder `claude-haiku-4-5`, Tester
`claude-sonnet-5-5`, Completion Reviewer and Investigator `claude-opus-5-5`) built a FastAPI
service that turns a Kibana alert into a Slack message, from an empty repository. It finished
`TASK_COMPLETE` with 9 of 9 criteria, but took about 45 minutes and $13.31: one Builder send-back
for weak AC5 to AC8 tests, two Investigator calls, eight Tester report repairs, three Completion
Reviews and 26 `main.py` mutants accepted on reviewer reasons.

## The shared cause: symbol narrowing decided that no test reaches a route handler

The mutation check narrows each mutant's tests by static analysis
(`autocode_affected_tests`). Every line of `handle_alert`, the handler registered by
`@app.post("/alert")`, got **zero** tests: the tests reach it through `TestClient(app)` and never
name it, and the symbol closure follows names only. The check then recorded all 26 mutants as
`survived` without running any test ("proven unobserved"). They were false. Run against the
final tests, all 26 are killed; run against the tests the Builder first wrote, 25 are killed and
the one real survivor (`except Exception:` around the fallback post) is AC6's missing "every
Slack post fails" case, which the Completion Reviewer later found by reading.

What those false survivors cost:

- The first Tester verdict cited "26 of 31 mutants in alertbot/main.py survived" and sent the
  Builder back.
- Later Testers accepted the 26 as `instrumentation_unavailable` or `equivalent_mutant`, so the
  one real survivor was buried among 25 false ones.
- The second Investigator diagnosed exactly this ("the handler is reached only through
  TestClient(app), so the symbol-closure selector never linked a test to it") and sent the
  Tester back to write 26 reasons, with a Resolver call and a third Completion Review after it.

The same thing happens to any definition handed to another object when it is defined: a
decorator (Flask, FastAPI, Click, Celery), a base class whose `__init_subclass__` or metaclass
registers it, a registry filled at import. Listing those patterns would cover the ones someone
listed. The fix instead treats the symbol closure as what it is, a guess that shortens runs:

- a site the symbol closure finds no test for keeps the tests that import its file (the import
  closure, the only proof that a test cannot reach a line);
- a mutant that survives a symbol-narrowed run runs again on every selected test, which decides
  its outcome; the narrowed run is kept beside it as `symbol_selection`.

On this run's workspace the selector now gives each of the 26 handler sites all 13 tests and
reports `unobserved_mutants: 0` (it reported 26).

## The other repairs

| Repairs | Cause | Status |
| --- | --- | --- |
| 4 Tester report repairs (iterations 4 and 5) | A check cited `python3 -m pytest tests -q` that ran inside `... 2>&1 \| tail -5; grep ...` | Fixed on master after this run by #4195: re-verifying those saved reports with current `verify_checks` binds both checks through the hidden-exit path that the clean-copy replay confirms |
| 2 Tester report repairs (iterations 1 and 7) | Changed lines with no mutation site (imports, signature lines, closing brackets, field annotations) each need a Tester reason, 35 of them here | Policy as designed (`autocode_changed_code_evidence`); the largest remaining per-run cost, not changed here |
| 1 Tester report repair (iteration 6) | Cited `cd <workspace> && X` when the Bash call ran `X` | Still refused on master; one case |
| 1 Tester report repair (iteration 2) | The capture command's output went through `grep -o`, dropping its receipt line | Refused with a specific hint, as designed |
| 1 Builder report repair (iteration 4) | The Builder's final message was not JSON | Model formatting |
| 1 Builder send-back, 1 Investigator | AC5 to AC8 tests were weaker than their criteria | Real; the false survivors hid the one mutant that showed it |
