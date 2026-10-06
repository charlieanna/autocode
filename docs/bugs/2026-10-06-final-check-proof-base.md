# A program's final check is proven against the seed, and its inherited criteria cannot pass

Fixed on `claude/elegant-planck-ynb2dq`; found by live run 8 of `program-notes-cli` for #22 and #23 on
2026-10-06 (Claude models, run directory `20261006T185139Z-program-notes-cli-claude-tiers-hiv481je`).

The three code workstreams were planned, built, merged and verified. That includes the skeleton's
re-check after the accepted change to the `store` interface, which went through the unchanged-guard
proof ([note](2026-10-06-unchanged-guard-proof.md)). The final check then planned, built and
validated. Its regression proof failed:

> Preserve case AC6 ... has a test that fails on the original code, so it describes behavior the fix
> restores

The run stopped at `PAUSED_INVALID_OUTPUT`, with oracle 19 of 21.

## Causes

Three things were wrong, and fixing any one alone would still have stopped the run.

1. **The final check's run was proven against the seed.** A new run takes its `base_commit` once,
   from its worktree's `.autocode/task-workspace.json` (`autocode_run_setup`). Every program child
   runs `--in-place` in a worktree the program made. The final check shares the integration
   worktree, which `ensure_integration` made from the project's HEAD when the program started: the
   README-only seed. Nothing rewrote the file. So the proof compared the merged product with a
   project that had no `notes` package. The program's own `record["base_commit"]` held the right
   head, but the child never reads it.
2. **Its plan marked delivered behavior `test:`.** The brief lists each inherited criterion with the
   parent plan's own verification method word for word, such as `(verify: test: test_ac1_...)`. The
   planner kept those marks. A `test:` criterion needs a test that did not pass before the change.
   On the correct base, the merged workstreams' tests already pass, so each one fails the proof as
   "no test named ... that passes with the change and did not pass without it".
3. **Guards on untouched files were never matched.** When a change edits any test file, the proof
   runs only the changed test files (`verify.select_commands`) and matches guards against that run.
   A guard naming a merged workstream's test in a file the final check leaves alone, such as
   `tests/test_skeleton.py`, was never matched. Correct marks would still have failed.

## Fix

- `autocode_program.launch`, for the integration workstream only: before a fresh run starts (the
  record has no `run_dir`), the program writes the current integration head as `base_commit` in
  the integration worktree's `task-workspace.json` with `util.atomic_json`. The other fields are
  unchanged, so `autocode_workspaces.metadata` still accepts the file.
  - A resumed run keeps the base saved in its own state.
  - The program's delivery checks keep `record["base_commit"]`: the head the first run on the
    record started from, set with `setdefault`. A retire with a new worktree record renews it:
    after an upstream re-check, or once the final check merged. After a retire in place, these
    checks still cover commits made on the branch since.
  - `autocode_unattended`'s report now prefers a run's own `state["base_commit"]` to the shared
    file.
- `compose_brief` adds a "Proof marks" paragraph for the final check. The merged product already
  delivers the criteria it inherits, so it marks them `guard:`, naming the tests the merged
  workstreams already have, and keeps `test:` for an integration defect it repairs.
- The `RE-CHECK:` line asks for the same marks, but only when the new run is proven against the
  head it starts from (`from_head`): the final check, or a code workstream in a fresh worktree from
  the integration head. A code workstream retired in place keeps its worktree's original base,
  which lacks its earlier work, so `guard:` would refuse that work as mis-tagged.
- Neither new sentence carries a requirement cue word, so neither adds an obligation the
  Requirements stage has to quote.
- `verify.verify` takes the exact test names the plan's guards give (`guards`). When there are any
  and the change edits test-path files, it also runs the whole suite on the base with the change's
  test files (`base_with_tests`: tests, fixtures, goldens and helpers under test directories). It
  reuses that run where the proof already makes it.
  - `verify._suite_guards` (it replaces `_held_guards`) keeps the tests that pass there and on the
    candidate (`suite_pass_to_pass`), and the candidate's failures (`failed_on_candidate`).
  - Comparing with the pristine base instead would let a change rewrite a guard test's
    expectation in a fixture, golden or helper that never names the test, so the guard passes
    with its behavior broken. A second review found exactly that in a first rework.
  - It records the git-ignored test files the proof copies into its trees (`ignored_test_files`).
    With any, it builds no such list.
  - All three stay in `verification.json`, out of the stage prompts.
- `autocode_regression.check_cases` (with `untouched` and `_variant`) uses that list only within
  limits:
  - It drops a test whose own name appears in any changed file. A changed test file is the
    targeted run's to decide, and a changed product module may define a test (a mixin) that ran
    its old content on base.
  - It drops a test whose own name appears in an ignored test file.
  - It never matches a case by its id alone, such as a diagnosis's T4.
  - It sends the list through the node:test wrapper refusal (#380), like every other candidate.
  - One failing variant of a matched test breaks a guard: the same function in the same file or
    module, run with other parameters, as a Go subtest, or by another class. A failing test that
    only shares the guard's name, elsewhere, does not.
  - With ignored test files, a guard left without a test says the whole suite was not consulted
    and why.
- A guard-only change that edits no test-path file has no `base_with_tests`. The base suite ran
  the same tests, so its pass-to-pass is the proof's own `pass_to_pass`, matched by id as before
  (a plan guard naming two tests holds). With ignored test files in the trees, that run is
  `UNVERIFIED`.

## Tests

- `tests/test_program_agreement_runs.py`
  (`test_each_new_final_check_run_starts_from_the_integration_head`) runs a program with a
  scripted child. At each fresh final-check launch it reads the base in `task-workspace.json` and
  checks that it equals the worktree's HEAD at three points:
  - the first run, after every merge (not the seed);
  - a run after an upstream re-check moves the head;
  - a run after a retire in place, following a commit on the branch. The program's own base stays
    the earlier one.
- Two revision tests check the `RE-CHECK:` guard sentence:
  - it appears for a merged workstream re-checked in a fresh worktree;
  - it is absent for one retired in place.
- `tests/test_test_cases.py`, through `regression.prove`:
  - a guard on an existing test in a file the change leaves alone, beside a `test:` case for new
    behavior: `PASS`;
  - a guard whose test the change rewrote to assert new behavior, with an explicit regression
    command that skips that file: `FAIL`;
  - a guard whose untouched test reads a fixture the change rewrote along with the behavior:
    `FAIL`;
  - on an unchanged source, a guard whose test a second class also runs and fails: `FAIL`, and a
    guard naming two tests: `PASS`.
- `tests/test_test_cases.py`, through `check_cases`:
  - a failing parameter variant, Go subtest or second class breaks a guard, and a failing
    namesake in another file does not;
  - an id-only case is never proven by the suite;
  - ignored test files keep the suite out, and the failure says so;
  - `untouched` drops the tests a changed test file or product module names.
- `tests/test_wrapped_runner.py`: a node:test wrapper in a file the change leaves alone does not
  prove a guard.

The final-check base test and the untouched-file guard test fail without the fix. The other
proof tests come from two reviews of earlier versions of this fix, which passed those cases
wrongly or failed them without reason.

The fake model's final check (`scenarios/harness/fake_codex.py`, `integration_method`) marks each
inherited id `guard:`, naming a merged workstream's test or its own journey test.
`program-notes-cli` passes 21 of 21. Without the base fix, or without the suite-level guards, it
stops the way live run 8 did: the final check at `PAUSED_INVALID_OUTPUT`, oracle 19 of 21.

## Left alone

- A `test:` case whose test already passes on the original code still fails with "has no test named
  ... that passes with the change and did not pass without it". A message telling the plan to mark
  it `guard:` was tried and dropped. Such a test can also pass on the original code for the wrong
  reason, as `feature-stock-refusals` shows: there argparse refuses a command that does not exist
  yet, and the right repair is a test that tells the two apart. The brief's guidance covers the
  final check.
- A revision retires the final check only when it changes the final check's own scope. A
  revision that re-checks only an upstream workstream (its brief, say) leaves a waiting or running
  final check on its run, proven against the head it started from, while the upstream workstream
  merges again under it. A merged final check is run again only when its scope changes.
- A fresh run in a code workstream's own worktree, after a retire in place, keeps that worktree's
  original base. That is the base the workstream's whole change is measured from, and its brief
  leaves out the `guard:` advice for that reason.
