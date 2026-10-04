# Recovery grants and planning progress

Four failures were reproduced in an OpenCode run using real OpenAI models.
They affect recovery from a stopped run, not the application produced by the
Builder. Related issue: #288; stacked recovery boundaries also relate to #301.

## A corrected timeout invalidated its own recovery request

The run exhausted three automatic recoveries during Requirements. Its issued
request recommended `--resume-paused --grant-recovery 1`. Supplying corrected
stage and idle timeouts with that command changed the settings bound into the
request before the grant was checked. Persistence then normalized the old
request, and the advertised command was rejected.

The grant now validates against either the current settings or the settings
read at the start of that same locked invocation. All other request bindings
still apply. A source change cannot be excused as a settings correction.
Settings and the explicit grant are persisted together after validation.
Recovery history remains; the audit records the amount and the originating
request. A CLI regression fails on the old implementation and passes with the
fix, including a control that rejects a stale source binding.

## Planning timeouts consumed the Builder's progress allowance

After the grant, the live run completed planning and received approval, then
stopped before its first Builder: three Requirements recoveries had incremented
the counter for unchanged implementation batches.

Only Builder timeout/permission recoveries now increment that counter. The
separate automatic-recovery allowance and its history still apply to every
stage. For saved runs, an explicit plain resume can correct an issued
pre-build no-progress hold only when the recorded history proves that no
implementation was attempted and distinct planning recoveries account for the
count. Workflow recognition is an allowed pre-build step. Unknown histories,
implementation attempts, stale requests, absent approval and other recovery
actions retain their existing boundaries.

The correction records an audit event without granting any additional timeout
recoveries. CLI coverage proves a legacy run reaches Builder and checks that
an invalid retry command cannot apply the correction.

## The task-run client hid a rejected command

The CLI rejected a verification-command
change outside its supported checkpoint, but `TaskRun.resume_paused()` returned
the unchanged status as though it were a normal pause. The client recognized
`Input rejected:` but not the CLI's `autocode:` error prefix. It now raises
`TaskRunError` for either form. The regression failed before the fix; all 30
task-run tests passed afterwards. Repeating the exact rejected operation on the
saved live-model run raised the error and left the checkpoint bytes unchanged.

## Validation-only repair was forced back to Builder

The Completion Owner and Resolver both requested `REWORK` with a `validate`
task: the application passed its checks, but the runner proof needed to execute
again after an interpreter environment correction. The Resolver is restricted
to `REWORK` or `BLOCKED`, while its guard and the task-assignment guard required
every `REWORK` task to be `implement`. Report repair therefore rewrote the
intended validation task into implementation work. Builder could not perform
the operator-only environment correction at that stage, so the run cycled.

An evidence-backed `REWORK` may now assign either implementation or validation.
The existing controller dispatches validation directly to the independent
Validator. The Resolver prompt makes this choice explicit. Source freshness,
approved milestone/criteria scope, read-only diagnosis, evidence requirements,
iteration limits and the final completion gate remain enforced. The shortcut
for a first implementation repair still excludes validation-only tasks.

A public CLI regression fails before this fix and passes afterwards: one
Builder, Validator, Completion Owner, Resolver, fresh Validator and final
Completion Owner; no report repair, no second Builder and unchanged delivered
source during revalidation. Evidence-free validation repairs are still rejected.

## Verification scope

The same retained live run reached `TASK_COMPLETE` after the corrections,
using OpenCode with GPT-6 Luna for planning, GPT-6.1 Sol for Builder, and GPT-6
Sol for independent validation, resolution and completion review. The final
Resolver's `validate` task was accepted without report repair. At the stopped
Validator checkpoint, the supported CLI updated only the interpreter PATH
prefix on the same full-suite command. A fresh runner proof passed with ten
fail-to-pass cases, all thirteen criteria passed independent validation, and
both reviewers closed their own findings. The original protected test file
remained byte-identical to the base. An additional independent CLI oracle
passed 108 cases, including invalid inputs and 5,000-digit integer sums.

The original unverified proof and the old report repair remain retained. The
trial caller's missing Python PATH was a setup failure, corrected through the
public interface, not an application defect. One explicit recovery grant was
retained throughout. A final explicit extension of one iteration and 600 seconds
allowed the fresh verification to finish; no allowance was silently reset.
This was a recovered successful run, not a clean first-pass result.

The affected-module gate passed 1,293 tests across 88 modules on this revision.

Evidence remains in ignored `.scenario-runs/remaining-defect-proof/`. These
changes do not establish that every operational-exhaustion category has a
working recovery action; the rest of #288/#301 still requires qualification.
