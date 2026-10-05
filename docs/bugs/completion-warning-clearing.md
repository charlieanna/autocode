# Clear obsolete completion warnings (#476)

A Completion Reviewer can return CONTINUE even though every required criterion
has current passing evidence. AutoCode sends that report back with a correction.
If the next report completes the run, the former correction remained in public
`stop_reason`, making a completed run appear to need action.

The shared stage-result cleanup now clears that current reason for TASK_COMPLETE
as well as RUNNING. Paused states retain their actionable reason; saved reports,
decisions and correction prompts remain available.

## Evidence

A public CLI regression failed before the fix and passed afterward. Its negative
control repeatedly returns CONTINUE and checks that the resulting
PAUSED_COMPLETION_REVIEW still exposes its reason. On the integrated candidate,
627 changed-file tests and four architecture tests passed. The supplemental fake
catalog recorded 60 PASS, one NOT_EXERCISED and one SKIPPED.

Fresh native run
`20261005-092040-fix-clamp-py-so-clamp-value-lower-upper-raises-v-49fb1e11`
used OpenCode 1.18.33, GLM 5.3 for planning, GPT-6 Luna for investigation,
plan review and implementation, and GPT-6 Sol for stuck investigation, testing
and completion. Eleven native calls used 1498.029 active seconds within the
unchanged 1800-second limit. Native output naturally contained CONTINUE followed
by COMPLETE; no model output or report was substituted. Public status finished
TASK_COMPLETE with no current stop reason, while its earlier decision and
corrective prompt were retained.

An independent oracle passed all 729 clamp inputs. The original test files stayed
unchanged, regression proof passed, inspected source and evidence matched, and
runtime/provider/driver hashes stayed pinned. Relay closure receipts and the
final process audit found no owned workers. The initial Investigator used a wrong
interpreter path and needed native recovery; this was a successful recovery, not
a claim of a frictionless run.

Run commands used the disposable public TaskRun driver under
`.scenario-runs/completion-warning-476/integrated-native-r1/drive.py`: `start
--i-authorize-live-model-spend`, inspected plan approval, then `advance
--i-authorize-live-model-spend`. Artifacts are ignored and retained locally.
This proof covers the completion-warning transition on candidate tree
`9d2d0700e8dd6f4dbfe916ec262f0ddebaca4411` based on `0021a748`; subsequent
parent-runtime changes require integration validation. It does not qualify all
providers or close other outstanding verification issues.
