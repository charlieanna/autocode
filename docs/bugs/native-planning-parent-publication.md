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

The launch freshness guard admits only the sole, genuine pending preparation
from the same owner and process, with unchanged source, runtime and evidence
pins. Generic idle checks remain strict; an unfinished historical launch cannot
be adopted. First approval preserves the assignment but does not enable native
execution or claim functional, image or Figma acceptance. Those later scopes
still require their own implementation and actual qualification.
