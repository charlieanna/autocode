# WIP handoff: #3868

Remove this file before merging.

This branch is a snapshot of an AutoCode run. Claude usage ran out partway, so the run was paused at a stage boundary for handoff. The run is `20261120-072506-…-a54fbfb1`, in a cloud container on 2026-11-20.

- Brief: `/mnt/project-files/issue-review/handoff/g3b/brief.md`.
- Approved plan (r5): `/mnt/project-files/issue-review/handoff/g3b/plan.md`.

## Done
- **Product change, implemented and proven** (the regression proof passes AC1 to AC10 and AC12). On resume, a pinned Tester report that authenticates but fails only the updated validator gets one report-only correction, with no new baseline, proof or full Tester launch.
  - New decision module: `tools/autocode_report_correction.py`, read by the resume dispatch and by the archived-repair recovery.
  - Wired in through `tools/autocode_action_recovery.py`, `tools/autocode_stage_recovery.py`, `tools/autocode_report_revalidation.py` and `tools/autocode_report_repair_queue.py`.
  - Tests: `tests/test_report_correction_resume_cli.py`.

## In progress
- **Changed-line evidence round** (tests only; tools/ unchanged). In-process tests kill mutants K01 to K32, and `tests/report_correction_mutation_probe.py` checks they are all killed.
  - At the last Tester round, 1 high finding was open (down from 3).
  - The run was re-checking changed-line mutations when it was paused.

## Left
- Close the last Tester finding (see `findings_ledger` in the run, or rerun the probe):

  ```sh
  python3 -B tests/report_correction_mutation_probe.py
  python3 -m unittest tests.test_report_correction_resume_cli
  ```

- Then open the PR (CHANGELOG under Unreleased → Fixed) and close #3868.
- `tests/report_correction_mutation_probe.py` is a validation helper. Either keep it, or drop it if the reviewers prefer not to commit it.

## To continue
- **Same container.** Same as the g4a handoff: resume the run, and at `PAUSED_BUDGET` switch the role's tool and model:

  ```sh
  autocode --run-dir RUN --workspace WS --answer route-ROLE=TOOL:MODEL --resolver-token -
  autocode --run-dir RUN --workspace WS --resume-paused
  ```

- **Elsewhere.** Check out this branch and finish by hand.
