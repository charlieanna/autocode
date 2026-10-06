# Qualification supervisor loss (#454)

The original live qualification loss has no established trigger. Offline fault
controls reproduce two separate gaps: a provider in its own session can outlive
a killed CLI, and a killed harness can leave no final result. A saved RUNNING
checkpoint or terminal provider event does not prove that its owner survived or
that a provider exit was collected.

Provider launches now start behind a blocked exec bootstrap. An independent
keeper durably records the controller, keeper and provider birth identities
before the controller saves ownership metadata and releases exec. The actual
provider remains the controller's Popen child with its own session, original
streams and checkout lock. The keeper inherits none of those streams or locks.
Owner pipe EOF and the existing stage deadline trigger independent, scoped
cleanup; the keeper's receipt observes cleanup and never manufactures an exit
code or delivery verdict. Cleanup excludes the exact keeper identity before
ancestry/group expansion and rejects PID zero.

The harness durably admits an attempt and each CLI call before launch. Its sole
lifeline writer bounds the CLI if the harness disappears. Missing final results
and interrupted calls remain INTERRUPTED_UNGRADED, with unknown usage retained;
rebuilding statistics does not run an oracle, resume a task or claim PASS.
Comparison arms preserve admitted unfinished attempts too.

CLI status supplies fresh observations through the additive public
`view.liveness` field: supervised, unsupervised, interrupted, stopped or unknown.
Unavailable inspection or missing native ownership stays unknown. Status does
not change the saved run.
New active-stage `owner` and `supervision` records have one writer, the
pre-exec ownership callback in `run_role`; the status and reconciliation paths
consume them. The existing interrupted flag is written when the stage handler
retains an interruption. An unfinished or interrupted supervised response is
held for inspection and explicit abandonment, even when its event file contains
a terminal response. A stopped receipt cannot replace a collected integer exit,
and a known finished deadline follows the existing bounded timeout recovery
route instead of accepting a late terminal response. SIGHUP uses the retained interrupt path while inherited
SIGHUP ignore is respected.

Qualification on macOS uses fake providers only: controller SIGKILL, process-group
SIGKILL, SIGHUP, controlling PTY close, harness SIGKILL, keeper loss before/after
exec, blocked ownership persistence, a TERM-resistant detached child, unchanged
provider exit semantics, read-only status and no automatic response adoption or
replay. Faults fire at event barriers, not arbitrary sleeps. Full catalog,
harness and Linux CI results belong to the pull request's exact source revision.
This crash path uses the repository's fault-injection exception to live-model
qualification; no paid calls or larger allowances are involved.

This fixes provider-stage and scenario-harness ownership. Independent parallel
build workers and runner-owned verification commands need their own lifelines;
this change does not claim those launch paths are covered. Portable ancestry
sampling also cannot recover an arbitrary child that detaches and reparents
before any observer discovers it. Those limits keep #454 open for the remaining
ownership work rather than equating these passing controls with universal
process containment.
