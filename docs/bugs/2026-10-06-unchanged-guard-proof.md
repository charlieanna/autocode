# A validation-only re-check that changes nothing can never pass its regression proof

Fixed on `claude/elegant-planck-ynb2dq`; found by live run 7 of `program-notes-cli` for #22 and #23 on
2026-10-06 (Claude models, run directory `20261006T181128Z-program-notes-cli-claude-tiers-ad_aq2xf`).

After the change request on the `store` interface was accepted, the program re-checked the merged
skeleton (`M1`). Its re-check did what the `RE-CHECK:` brief line asks: it planned a validation-only
task (kind `validate`), marked the five testable criteria `guard: test_s1_...` to `guard: test_s5_...`
(tests the skeleton already had), left the sixth to the Validator, and changed nothing. The Validator
passed all six. The runner's regression proof (`autocode_verify.verify`, run because the plan marks
`guard:` criteria) failed it anyway:

- "No change: the candidate is identical to the base revision";
- "No regression test was added or changed";
- "No project test command was found", because the suite only ran when something changed.

The Completion Reviewer returned `REWORK`, the Resolver had already diagnosed the task, and the run
stopped at `PAUSED_INVALID_OUTPUT`. Search and export stayed `STALE` behind it, oracle 11 of 21. The
Investigator traced it to the "No change" rule. The person's answer (re-run the proof in pass-to-pass
mode) had no effect, because no code path reads it.

## Fix

`verify(..., preserve_only=True)` with an empty diff no longer fails as "No change". Nor does
a change that edits no test fail for lack of a new test. `preserve_only` holds only when every case
the proof covers is a `guard:`, or when the person granted the test-only exception, which the proof
already treats as coverage.

- **Suite run.** The suite runs on the candidate tree (the unchanged source, when nothing changed),
  and its result is judged against the base suite as before.
- **Proof lists.** `_held_guards` needs complete per-test results on both runs. It records three
  lists:
  - `fail_to_pass` as empty;
  - `pass_to_pass` as the tests that passed on base and on the candidate;
  - `failed_on_candidate` as the candidate's failures.
- **Case checks.** `autocode_regression.check_cases` then needs each guard's named test in
  `pass_to_pass`, as it does for a guard on a changed source. It also fails a guard when any test
  named after it is in `failed_on_candidate`. That covers one failing variant of a parametrized
  test, which the suite comparison only notes when it already failed on base.
- **Ignored test files.** A guard cannot rest on git-ignored test files that `make_tree` copies into
  both trees: when there are any, the proof is `UNVERIFIED`.

Everything else still needs a change. A `test:` criterion on an unchanged source still fails "No
change" (without the exception), and so does any run whose proof is not guard-only.

Tests in `tests/test_test_cases.py` go through `autocode_regression.prove` on a committed, unchanged
source:

- guards whose tests exist and pass: `PASS`, and `complete()` accepts it;
- a guard without its test: `FAIL`, naming the case;
- a guard whose test fails: `FAIL`, naming the test;
- a guard whose test passes in one class and fails in another (a failing variant): `FAIL`;
- a guard whose test file is excluded through `.git/info/exclude`: `UNVERIFIED`;
- a guard-only change to a source file that adds no test: `PASS`, and `FAIL` when the change
  breaks the guard;
- a `test:` criterion beside a guard: still "No change".

Every test but the last fails without the fix. The ignored-file, failing-variant and test-free-change
cases came from a review of the first version of this fix. The fake model's program mode (`scenarios/harness/fake_codex.py`)
now marks a re-checked workstream's criteria `guard:`, naming a test that workstream already has, so
`program-notes-cli` reaches this proof on the skeleton's re-check. Without the fix the fake run stops
the way the live run did (S `PAUSED_INVALID_OUTPUT`, T and U `STALE`, oracle 11 of 21); with it, it
passes 21 of 21.
