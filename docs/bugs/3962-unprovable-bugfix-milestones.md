# A bug-fix milestone the regression proof can never accept (#3962)

Observed on 2026-11-20 in an AutoCode run of this repository on the Claude engine (the fix for #3852, #3880
and #3869). The approved bug-fix plan began with M0, a survey milestone: one guard criterion (AC22),
restricted to test and doc files, required to change nothing under `tools/`. The Builder delivered it, the
guard passed on the original code and with the change, and the regression proof failed with an empty
`fail_to_pass`. The Resolver called the plan structurally unpassable and asked the person to choose between
folding M0 into M1 and staying paused, after a full Builder round, a proof and a Tester round.

## The shared cause

The plan check and the regression gate disagreed about one fact: whether a milestone checkpoint gives its
proof anything that can fail first. The gate always owes a proof for `task_kind` `bugfix`
(`autocode_regression.required`) and accepts a proof with nothing failing first only as coverage, when the
run is an approved, coordinated member and every due case is a guard (`preserve_only`). In the proof itself
(`autocode_verify_judgment`, the `not flipped and not preserve_only` branches) a bug fix whose proof flips no
test fails. The plan check knew none of this, so it approved any milestone shape, and nothing told the
Planner or the Plan Reviewer that one checkpoint could never pass.

The cause is not the guard: it is that a checkpoint's due cases (its own criteria and those of the milestones
it depends on, `autocode_test_cases.proof_cases` with `in_scope`) contain no `test:` case while the gate owes a
fail-first proof there. Every shape below that has that property fails for the same reason.

## What was observed, without a model

Fixture: a model-free `python3 -c` probe run from the repository root on the original code, before any
change. For each shape it built a bug-fix Planner draft from `goal_fixtures.body(task_kind="bugfix")` with two
milestones, M0 of that shape (`depends_on []`, the initial task) and M1 holding `C1` (`test:
test_c1_add_negative`, paths `calc.py`, `test_calc.py`, `depends_on [M0]`), and:

1. installed it with `goal_lifecycle.install_draft(..., origin="planner_draft")` (the plan check);
2. on a scratch Git project (`tests.test_verify.Project` with `calc.py` and an existing `test_calc.py`), applied
   the diff the M0 milestone allows, put the approved draft's `current_task` on M0 and read
   `regression.required`, `regression.preserve_only` and `regression.cases`, then ran the real
   `regression.prove`.

| Shape (M0's only criterion, its paths, the diff) | Plan check before the fix | Gate before the fix | Covered by the shared answer now |
| --- | --- | --- | --- |
| guard-only: `guard: test_keep_is_nine`, paths `test_keep.py`, `docs/`; diff adds `test_keep.py`, which passes on the original code | installed M0 and M1 unchanged | required true, preserve_only false, due `[C0 preserve]`; proof FAIL, "Only test files changed; a fix must change product code" (the incident's run, with a product file allowed, failed with an empty `fail_to_pass` instead) | yes: merged into M1 (AC1), or refused when no milestone has a `test:` criterion (AC2) |
| preserve-only, a command guard: `` guard: run `python3 -m unittest test_calc` ``, paths `docs/survey.md`; diff adds that doc | installed unchanged | required true, preserve_only false, due `[]` (a command guard names no test, so it is no case); proof FAIL, "No regression test was added or changed, so the bug is not shown to be reproduced" | yes: no due `test:` case, so merged or refused like guard-only |
| doc-only: "Read docs/survey.md and confirm it names the cause", paths `docs/survey.md`; diff adds that doc | installed unchanged | required true, preserve_only false, due `[]`; proof FAIL, "No regression test was added or changed, so the bug is not shown to be reproduced" | yes (AC5). A checkpoint whose paths are docs adds no test, so nothing can flip, and a bug fix whose proof flips nothing fails unless `preserve_only`; with no due case `preserve_only` is false. That is the observation the AC5 merge rests on |
| only `test:` criterion supersedes nothing new: `test: test_add`, a test the project already has and that passes on the original code; paths `calc.py`, `test_calc.py`; diff edits `calc.py` | installed unchanged | required true, preserve_only false, due `[C0 restore]`; proof FAIL, "No regression test was added or changed, so the bug is not shown to be reproduced" | no: the plan names a `test:` case, and whether that test already exists and passes is known only from the workspace. Left to the gate; a spec-first run's acceptance-tests correction already relabels a `test:` case the runner saw pass (`autocode_acceptance_tests_job`) |
| paths that hold no test: `test: test_c0_add_floats`, paths `calc.py` only; diff edits `calc.py` | installed unchanged | required true, preserve_only false, due `[C0 restore]`; proof FAIL, "No regression test was added or changed, so the bug is not shown to be reproduced" | no: the checkpoint names a `test:` case, so the plan alone does not show it cannot be proven; where the Builder may write that test is a scope question the gate decides from the diff |

## The fix: one answer, read from one place

`tools/autocode_fail_first.py` holds the decision both sides read:

- `coverage_allowed(task_kind, coordinated, due)` is the bug-fix branch of `preserve_only`: every due case is
  a guard and the run is a coordinated member. `regression.preserve_only` now calls it, keeping its own
  approval check; `required()` is unchanged, so the gate is exactly as strict.
- `owes_fail_first` is a bug fix that is not such coverage.
- `checkpoint_cases(state, body, milestone)` asks the gate's own case selection (`proof_cases`, `in_scope`)
  what is due at that checkpoint, with the plan treated as approved, that milestone current and its transitive
  `depends_on` accepted. A guard-only milestone after a fail-first prerequisite is therefore kept (AC6).
- `unprovable(state, body)` lists the milestones whose checkpoint owes a fail-first proof and has no `test:`
  case. It does not know any shape: guard-only, a command guard and doc-only are caught because their due
  cases hold no `test:` case.

Exemptions the shared answer leaves to the gate, which decides them from the diff:

- **the checkpoint at which every criterion is due** (a one-milestone plan, AC12, or the milestone every other
  milestone is a prerequisite of): an unloadable-code repair under `autocode_first_suite` or an unnamed
  fail-first test may still pass there;
- **a test-files-only plan** under `autocode_test_support` (`test_support.planned`, with the path role rule
  of `autocode_project_kinds.is_test_path`): the tests that already fail are the proof (AC13).

A coordinated member keeps guard-only acceptance (AC4, AC8), an uncoordinated or unapproved bug fix still owes
a fail-first proof (AC9), and a feature build is not judged at all (AC7).

## Where the answer is applied

- **At install.** `goal_lifecycle.install_draft` applies `fail_first.correct` to Planner drafts (the
  `PLANNER_DRAFT_ORIGINS`) before `validate_body`: each unprovable milestone is merged into the next milestone,
  in plan order, among those that depend on it and then among the later ones, whose own criteria include a
  `test:` criterion. Criteria (the merged milestone's first), paths and `depends_on` are joined, other
  milestones' `depends_on` are rewritten and an initial task on the merged milestone moves to the target. A
  merged checkpoint holds a superset of the criteria and paths, so nothing is lost. The merge is recorded in
  `fail_first_corrections`. This is the deterministic, zero-model-call correction path.
- **Refusal.** Where the merge is not safe (no milestone with a `test:` criterion, a dependency cycle, an
  initial task left on a milestone with prerequisites, or a progressive plan), `install_draft` raises a
  `ValueError` naming the milestone and saying it gives its proof "nothing that can fail first", with the
  correction: merge it into a milestone with a `test:` criterion, or plan the work as one milestone. That text
  goes back to the Planner as the correction through the ordinary report repair of the refused draft.
  `validate_body` refuses any remaining unprovable milestone of a plan with milestones and no open questions,
  so the Plan Reviewer and the person can never approve one, and approval of such a saved draft is refused.
- **report-check.** `autocode report-check` exposes the same refusal: `build_spec` adds `fail_first
  {coordinated}` for the planning stages from the run's coordination context, and `_check_planning` reports
  the unsafe-merge refusal for a bug-fix contract, so a shell-capable Planner sees it before finishing.
- **A plan approved before this check.** `completion_result.before_review`, before
  `regression.before_review` and before any stage that is not Requirements, the Planner or the Plan Reviewer
  (so before the next task Planner, Builder, Tester or Resolver launch), calls `fail_first.send_back`: an
  approved, uncoordinated bug-fix contract with an unaccepted unprovable milestone is installed corrected
  (origin `fail_first_correction`, no person question) and goes to plan review (AC3); when the merge is not
  safe, AutoCode records the correction as its own `technical_decision` (`settled_as: fail_first`) and the
  Planner drafts again (AC14). Either way the stage is skipped, no Resolver person request is published, and
  it happens once per contract revision.

## Rejection rate

The new check can reject a completed Planner answer (architecture rule 8). Against the saved plan answers in
this checkout it rejected none: of 4 distinct saved plan bodies read directly (the #3962 run's own Planner
draft, `iterations/001/discovery-01.json`, a one-milestone bug fix, and the 3 approved-plan bodies in
`tests/fixtures/old-names-run/approved-plan.json`, one-milestone builds), 0 were refused and 0 were merged,
a rejection rate of 0/4. The run's revision and final-plan answers (`requirements-revise-01`,
`plan-finalize-01`) are retention deltas of the same one-milestone plan. The incident's own plan would be
merged, not refused: its M1 carried `test:` criteria.

## What remains open

- The sample is small and holds no multi-milestone bug fix besides the incident, which is described in the
  issue but not saved in this checkout; the rate should be re-measured against the owner's saved responses
  (`autocode usage report`), which this run cannot read.
- A milestone whose only `test:` criterion names a test that already passes, and a milestone whose paths
  hold no test file, are still accepted by the plan check: the plan alone cannot show they are unprovable,
  and the gate keeps deciding them from the diff.
- A progressive bug-fix plan with such a milestone is refused rather than merged, since its slices are the
  Planner's to size.
- A draft saved before this fix and still awaiting approval is refused at approval with the correction text;
  only an approved plan is sent back to planning automatically.
