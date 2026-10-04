# Native OpenCode snapshots and delivered review tests (#332)

The Code Reviewer may deliver new tests under `review/tests/`. The native
OpenCode guard nevertheless rejected every snapshot change, so two real reviews
(Sol and GLM) delivered valid tests and stopped at `PAUSED_STALE_VALIDATION`.
Their existing application, tests and pinned proof runtime were unchanged.

The guard now attributes each changed native tree to files. A normal
`review_change` can add regular files under the review job's allowed prefixes,
provided none existed in its hash-bound pre-launch source capture. Changes to
existing captured files, deletions, renames, mode changes, symlinks and additions
outside those prefixes still reject the report. Every intermediate tree is
checked, so restoring a source edit does not excuse it. Report-only repair and
other read-only stages receive no addition exception; repair also checks the
original attempt's evidence.

OpenCode stores these objects in its own Git snapshot repositories. The runner
records only the effective data directory and workspace before launch, then
matches the native project's ID, `core.worktree` and recorded tree hashes. It
runs no extra provider command and changes no review exclusions. Missing or
invalid attribution keeps the pause. Older records may use the current data
directory as a lookup hint, with the same identity and source-capture checks.
This remains detection through available native snapshots, not a filesystem
sandbox; it does not add coverage for missing snapshots or ignored files.

## Verification

The original live reproductions used `openai/gpt-6-sol` and
`zai-coding-plan/glm-5.3`. Fresh after-runs retained the exact application,
prompt, pinned proof runtime and explicit limits: 600 seconds overall,
240 per stage, 120 idle, three iterations. Provider exports confirmed each
actually executed model:

| Model | Fresh result |
| --- | --- |
| `openai/gpt-6-sol` | `TASK_COMPLETE` in 158 seconds. Delivered a real failing regression test, demonstrated a scratch fix, and the runner accepted the blocking finding with its proving test. An independent rerun also reproduced the application defect. |
| `zai-coding-plan/glm-5.3` | 240-second hard timeout. Created a review test, but no accepted final review; the failed attempt and its artifact were archived. |
| `xiaomi-token-plan-sgp/mimo-v2.6-pro` | 240-second hard timeout. No accepted review. |

No limits were increased or attempts relabeled. The application and operator
inputs stayed byte-identical in all three runs. Replaying the *original* guard
against the successful fresh Sol event stream still produces
`PAUSED_STALE_VALIDATION`, confirming that this stream exercises the fix.
These results qualify the artifact acceptance path; they do not establish that
all three models complete the review within these limits.

Thirteen new tests use actual native-style Git repositories and tree objects
through report loading. They cover permitted additions and refinement, reverted
source/review edits, deletion/rename/mode/symlink changes, pre-existing untracked
review files, missing/corrupt evidence, wrong workspace attribution, legacy
records and report repair. Together with existing guard and architecture tests,
33 focused tests pass. The changed-file gate passes 877 tests in 61 modules.
The required fake catalog passed 54 cases, with one existing NOT_EXERCISED
and one live-Investigator SKIPPED. The full suite ran 3,320 tests in 239 modules
and failed one subprocess-cleanup assertion in
`test_live_trial.DrivingBoundsTest.test_timeout_stops_provider_in_separate_process_group`.
Its isolated 30-test module rerun passed. The original failure remains recorded;
this is not a clean full-suite pass.

Ignored evidence: `.scenario-runs/review-evidence-332/` in the
`review-evidence-snapshots` worktree; original live evidence remains in
`seam-proof-live-review/.scenario-runs/runtime-setup-proof-299/before-*`.
