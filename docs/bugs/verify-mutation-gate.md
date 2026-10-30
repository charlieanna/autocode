# Should `autocode verify` refuse a PASS on a surviving mutant? (#2506)

A decision note for the owner. It changes no behavior.

## Today

On branch `fix/2506-verify-mutation-check`, `autocode_verify_task.mutation_check`
clones `--base`, keeps the change as `mutation-base/reviewed-change.patch` and runs
`autocode_review_mutation.collect` on it once the external-change proof passed.
`autocode_verify_record.publish` records the result as `workflow_result.mutation_check`
in `evidence.json`, renders it under "Review test mutation check" in `evidence.md`
with the line "Report only; this does not change the review verdict", and pins its
command captures. The verdict comes only from `reasons`: a blocking review, a failed
proof, a failed check, human-review criteria, changed source or a refused completion
predicate. Survivors never enter `reasons`, so a weak test that reproduces the bug
(`assertNotEqual(3, double(3))`, which `return None` also passes) gets `PASS` and
exit 0 with the survivor listed beside it.

A task run differs in one respect. Its mutation check (#699) is report-only too, but
`autocode_changed_code_proof.collect` runs beside it, `autocode_changed_code_evidence.assess`
turns every changed executable line without a demonstrated kill, measured execution
or independently checked reason into a `reason_required` obligation, and
`autocode_regression.complete` refuses completion (`tests/test_mutation_check.py`,
"keeps its pass and survivors but cannot complete"). `verify` runs neither, so it
passes a change a task run would send back to the Builder.

## Option A: keep report-only

No change; consistent with #699 and the review workflow, where the study "changes
neither the review verdict nor the finding-test requirement" (`docs/named-test-proof.md`).
The cost: "AutoCode delivers no untested code" (AGENTS.md) stops short of `verify`,
and a caller who trusts the exit code never reads the survivor.

## Option B: a survivor becomes a reason

A nonempty `survivors` appends a reason; the verdict is `UNVERIFIED` (not `FAIL`:
nothing demonstrated a defect) and exit is 2. The weak test above is refused although
the proof shows the change fixes the bug, and the caller has no way to say the line
is equivalent or defensive: `verify` has no Builder, no Tester reason field and no
exception path, so the only remedy is a stronger test and a rerun. The study's scope
makes the refusal uneven: it covers only Python test files the patch adds or
modifies, six mutants per file, and records `not_run` for Go, `node:test`, Vitest, a
change with no new test file or an exhausted budget. If `not_run` passes, a change
with no new tests passes while one with a weak test fails; if it refuses, every
non-Python change is `UNVERIFIED`. The shared "Report only" wording in
`autocode_evidence_document` becomes false for one caller.

## Option C: reuse the changed-code evidence gate (#2474)

After the proof passes, `verify` calls `autocode_changed_code_proof.collect` on the
candidate tree and coverage report the proof runner already leaves for it. A survivor
is then one `reason_required` line among coverage and mutation obligations, decided
as in a task run: enforcement-mode mutation over every eligible site, the recorded
deterministic fallback where a project has no coverage tool, no dependency installed.
Per-line reasons arrive with the criteria the caller already writes, an optional
`changed_code_exceptions` list bound by file, bytes and line (`autocode_replay_bindings`
re-derives the `gap_id`, whose digest the caller cannot know). A `reason_required`
line is terminal: there is no Builder to send it to, so the verdict is `UNVERIFIED`
with the exact lines in `reasons`, and the caller strengthens the tests or supplies
a reason and reruns. Open inside C: a caller-supplied reason is a self-approval of
the kind `autocode_workflow.implementation_schema` strips from the Builder; the
alternative offers the field to the independent Code Reviewer, which needs the
review to run after the proof. Either way the reason is requirements-level input,
like `human_review`, never a mid-run question or a flag.

## Tests that encode report-only

- `tests/test_verify_task.py`, `test_a_surviving_mutant_is_reported_without_changing_the_verdict`
  (fix branch): flips under B and C.
- `tests/test_review_mutation_evidence.py:275` asserts the rendered "Report only; this
  does not change the review verdict." The line is shared with the review workflow;
  under B or C it depends on the record's kind.
- `tests/test_evidence_report.py:386` ("a survivor is not a verification gap") and
  `tests/test_mutation_check_catalog.py:83` ("the proof still passes") concern a task
  run's proof: unchanged when the change is scoped to `verify`.
- `tests/test_mutation_check.py` ("cannot complete") is already the model for C.
- Docstrings and docs to update: `autocode_review_mutation`,
  `autocode_verify_task.mutation_check`, `docs/verify.md`, `docs/named-test-proof.md`.

## Recommendation

Option C. B refuses without an explanation path and only for Python; A lets
`verify` pass what a task run refuses for the same reason. Keep #2506 as it is (the
study is correct, only incomplete) and add the gate in its own change, keeping the
review mutation study as the record it is today.

**Owner decision needed:** does "AutoCode delivers no untested code" apply to
`autocode verify`, so a changed executable line without current behavioral evidence
or a checked reason makes the verdict `UNVERIFIED`? And who supplies the per-line
reason: the caller through criteria, or the independent Code Reviewer?
