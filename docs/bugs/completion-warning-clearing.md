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
control repeatedly returns CONTINUE and checks that PAUSED_COMPLETION_REVIEW
still exposes its reason. Candidate `583ec8ce`, integrated over master
`eb004089`, passed four architecture tests and 690 affected tests in 34 modules.

The current 63-entry fake catalog recorded 59 PASS, one NOT_EXERCISED, one
SKIPPED and two cleanup failures. One failed before workflow recognition; the
other completed its task and passed its oracle before an enclosing keeper wait
timed out. Both passed separate unchanged-source, unchanged-cap targeted reruns.
The original non-green catalog and its receipts remain preserved; the reruns
are not one green full catalog. All 8,105 original and 265 recheck identities
were gone, with no unknown liveness. These keeper concerns remain separate from
the warning-clear fix and do not establish a root cause for broader #454.

Fresh BEFORE r6, frozen at master `a834a6a3`, naturally completed through native
CONTINUE, the runner correction, then COMPLETE on the same source, while public
status retained the obsolete warning. Current regression proof recorded three
genuine fail-to-pass tests, one preservation guard and all eight suite tests
passing; the independent oracle passed 729 integer inputs. A separate supported
`--status --inspect-evidence` inspection, supplemental 30-check audit and full
completion guard passed, with all 720 recorded identities gone. The first
auditor's two observation-assumption failures remain preserved. BEFORE did not
capture the final merged inline provider policy: its saved overrides, actual
argv, bounded read-only events and unchanged snapshots were audited instead.
That observation limitation is explicit, not full effective-policy capture.

Fresh AFTER r8 used the same seed and brief on candidate `583ec8ce`. OpenCode
1.18.33 ran GLM 5.3 for planning, GPT-6 Luna for investigation, plan review and
implementation, and GPT-6 Sol for testing and completion. Nine actual native
calls used 896.400 active seconds, within unchanged 1800/360/120/120-second
run/stage/idle/tool limits. Its independently reviewed actual plan required
three restoration tests, five preservation guards and the four unchanged
original tests. Runner proof passed and all twelve candidate tests passed;
the independent oracle passed all 729 inputs and the protected original suite.

The AFTER Completion Owner naturally returned CONTINUE, received the runner
correction, then COMPLETE on the same current source. Public status ended
TASK_COMPLETE with no stop reason; earlier reports, decisions and the correction
prompt remained. Actual public evidence inspection covered all eleven criteria
and passed the full completion gate. All thirty sealed terminal checks and the
separately frozen full-completion guard passed. Selected inline permission/tools
policies were observed directly; raw and delivered native streams were identical
and complete. Kernel execution boundaries and nested ownership receipts passed,
with all 682 recorded identities gone, no unknown liveness and no survivors.

The disposable public TaskRun drivers used live-spend acknowledgement for
`start` and `advance`, with actual plan inspection and exact-token approval in
between. No forced decision, substituted report, corrective user feedback,
route fallback, unsafe containment option or cap increase was used. Artifacts
are ignored and retained locally. This proof covers the warning transition;
it does not qualify every provider or close other verification obligations.

Earlier eleven-call qualification on tree `9d2d0700` / base `0021a748` remains
historical: its original artifact root is currently unavailable. The fresh
BEFORE/AFTER above supplies the current independently inspectable evidence.
