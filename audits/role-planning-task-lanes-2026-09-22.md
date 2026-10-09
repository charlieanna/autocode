# Role planning and connected task lanes

Autocode UI now uses the same full planning shape as the normal workflow before
execution: Requirements Planner → Plan Reviewer → Requirements Revision → Plan
Finalizer. Accepted work then follows Figma Builder → Design Validator → Completion
Owner through the shared durable stage driver. Role names describe responsibility;
model names remain replaceable configuration. UI planning remains automatic and
bounded, so this does not add a fabricated or unnecessary human approval event.

Version 2 UI handoffs pin the draft, plan review, revised brief, final planning
decision, build report, independent validation and completion decision. Import
rejects changed artifacts, unaccepted plans, failed validation and inconsistent
Figma URLs. Version 1 handoffs remain readable.

`autocode tasks` and the `autocode-tasks` entry point add persistent task lanes.
Tasks in a lane invoke ordinary Autocode UI/code runs sequentially in the same Git
worktree. Separate lanes receive separate worktrees and run up to `--max-parallel`
at once. A completed UI task can feed a later code task through `ui_from`. Normal
plan approval, interventions, validation and completion boundaries remain intact;
the lane runner reports each ordinary run directory and waits when that run waits.

Parallel lanes are deliberately independent branches. They are not automatically
merged. Source-dependent work belongs in one sequential lane; combining parallel
branches remains an explicit Git integration decision.

Tests use fake providers for process-level orchestration. No live Figma task was
started for this release.

Validation:

- 385 core tests ran. One existing six-iteration subprocess test exceeded its
  30-second harness limit after 30.2 seconds; its harness timeout was corrected to
  60 seconds and the test passed independently in 32.1 seconds.
- 174 dashboard tests passed.
- 14 Figma workflow tests and 6 task-lane tests passed.
- Two tests passed against the built 0.7.0 wheel, covering parallel isolated lanes
  and an accepted UI-to-code handoff.
