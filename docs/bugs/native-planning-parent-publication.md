# Native planning and first approval

Native planning uses the complete, explicit `VISUAL_REVIEW_PROFILE=` directive
in the original operator task. The existing CLI options must name an existing
Git checkout with `--in-place`; automatic checkout provisioning is unsupported.
A saved legacy run cannot gain native authority retrospectively. Ordinary runs
continue to use their existing provider and ownership paths.

The original run and checkout lock handles enter one `NativeRunContext`. The
original supervisor, process tree and handoff remain the launch owners. Native
planning requires the qualified OpenCode 1.18.33 transport; this does not change
the ordinary OpenCode 2.x transport. The final output and capture environment is
assembled before native isolation seals it. Unimplemented execution, recovery,
image review and completion routes stop before artifact or provider setup and
before an allowance is charged.

`NativeProtection` alone writes `activation_intent` in its original journal's
origin row. Its constructor reads the field on subsequent invocations and
requires equality of the complete original task and profile. `PlanningCalls`
also checks that task after the normal locked checkpoint reread. These are
journal fields, not new run-state keys or public flags.

The invocation-local `ParentSink` observes the existing normalized JSON writer.
Every parent write invalidates the prior acknowledgement before normalization
or any fallible file operation. The actual durable sink reports the final
candidate. Native publication occurs only after the complete existing action
and artifact boundary succeeds. Replacement uncertainty preserves the actual
candidate and planning files; it cannot be converted into a memory rollback.
Native preparation, acknowledgement and publication errors after the durable
save also use this typed disposition. The original outer stage and approval
callers cannot reject the report or overwrite that candidate with a pause. A
post-save intervention error is classified inside the artifact persistence
callback, before artifact rollback could occur. Errors before the new save
cannot borrow an acknowledgement or durable candidate from an earlier write.
Every native action and stage persistence boundary starts a new observation,
including nonfinal Requirements, Planner and Plan Reviewer saves. A neutral
observation supplies no planning or approval intent. A failure before its save
rolls back only its uncommitted handoffs; a failure after its durable callback
preserves only that boundary's actual candidate and bytes.
Retained `native-parent-outcomes` diagnostics hold subsequent native admission;
they grant no authority and this scope offers no automatic reconciliation.

Draft task feasibility is pure declaration checking. It creates no approved
contract, task assignment or native proof. The real lifecycle emits its actual
approval event before requesting the concrete first-assignment proof. Initial
validation inherits its approved milestone paths consistently in the lifecycle
and both proof normalizers. A legitimate absent `current_task` also remains
assignable. All original exact-token, current-input and independent-review
checks remain required.

## Rejected planning reports

A native Requirements, Planner or Plan Reviewer stage (`requirements`, `plan`,
`plan_review`, `plan_revise`) that finished but returned a report that fails
validation gets the ordinary bounded report-only repair: one more request on
the same stage, saved route, model and effort, read-only, with the rejected
response in its handoff, at most `report_repair.max_attempts` times. The
rejected attempt's protected launch must have closed, and the native journal
closes a launch only when it left its report file. An OpenCode event-stream
response that does not parse as one JSON report leaves no report file, so that
attempt keeps the original pause. The original bytes, events and the rejected
record stay in the run; no file is moved or deleted. The repair goes through
the same native launch owner and parent sink as the original, pins the same
planning inputs as the stage it repairs, and is refused if the contract,
source, pinned evidence, planning inputs or saved route changed. It is not charged
to the planning review allowance, which is neither refunded nor renewed.
`autocode resume` continues a queued repair without renewing its attempt count;
`--retry-report` and `--retry-format` stay refused. When the attempts are spent,
the run pauses `PAUSED_INVALID_OUTPUT` with every attempt retained. A plain
resume from that pause runs the stage again as a fresh attempt, as a resume
after a rejected native planning report did before this change. It first moves
the spent repair, with its attempt count, to `report_repair_archive` (no file is
moved or deleted). If the fresh attempt's report is rejected, it gets its own
bounded repair. A fresh Plan Reviewer call is still charged to the review
allowance.

The final review (`plan_finalize`) is not repaired yet: its repaired report
would need its own native reviewed-plan publication before approval, which this
scope does not provide. A rejected final review, an attempt whose launch did not
close, and any non-report error keep the original `PAUSED_INVALID_OUTPUT`
pause. A native run never sends a stage pause to the stuck-stage Investigator,
which native authority cannot run, so that pause keeps its original reason and
spends no investigation. After approval, a Builder failure that needs
classification still queues the Investigator; native authority refuses it, and
the run is restored to `PAUSED_BUILDER_CLASSIFICATION` at the Builder, as before.

A repair launch that stops (a timeout, a collected output limit, or a held
launch at a failed cleanup) pauses the run with that launch still active, as a
stopped original planning stage does; the ordinary automatic retry of a
timed-out repair does not run. `--abandon-stage` with that attempt closes it
through the original journal owner, as it closes an original planning stop: its
attempt stays counted and the run pauses `PAUSED_STAGE_ABANDONED` at the stage
it repairs. The resume that follows reconciles the abandoned repair the
ordinary way: the pending repair moves to `report_repair_archive` (no file is
moved or deleted) and the stage runs again as a new attempt.

The launch freshness guard admits only the sole, genuine pending preparation
from the same owner and process, with unchanged source, runtime and evidence
pins. Generic idle checks remain strict; an unfinished historical launch cannot
be adopted. First approval preserves the assignment but does not enable native
execution or claim functional, image or Figma acceptance. Those later scopes
still require their own implementation and actual qualification.
