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

Only the first SIGTERM, SIGHUP or Ctrl-C raises a stage's interrupt (a
KeyboardInterrupt whose message is the signal's name). Later ones are absorbed
while its cleanup runs and, once the stage's own handler scope has closed, until
the CLI process exits: their default dispositions are not restored after the
pause is saved. A handler scope opened inside a stage's shares its one
interrupt. A program that calls `main()` in-process gets its own SIGTERM or
SIGHUP handler back when `main()` returns, and that handler then receives each
signal absorbed meanwhile, once. A closed terminal
sends SIGHUP twice (the kernel and the shell) and people press Ctrl-C again. A
second raise while the first unwound skipped the controller's own cleanup (the
keeper still stopped the provider, but no exit code was saved), turned the pause
into `PAUSED_INVALID_OUTPUT` "release unlocked lock", escaped as a bare
KeyboardInterrupt that left the run `RUNNING`, or, rarely, hung the controller on
a leaked threading lock with SIGINT and SIGTERM ignored. Of 351 fake scenario
runs given two signals 0 to 200 ms apart, 3 hung and 77 more did not pause as
`PAUSED_INTERRUPTED` with the provider's exit code saved; no provider outlived
its controller. A latch that ended with the stage's handler scope still left a
second signal 175 to 290 ms after the first, while the pause was being saved,
to escape or kill the controller (exit -2, -1 or -15, often `RUNNING`). Held
only until `main()` returned, a second signal 300 to 400 ms after the first could
still kill the CLI once its pause was saved (exit -2 or -15 instead of 2, which
a task-run caller rejects). Ctrl-C is taken over only from Python's default
handler, so an ignored (background job) or replaced SIGINT is left alone.

The stage cleanup itself (stopping the provider tree and joining its process
worker) defers these signals. One that arrives first during it, as a provider
ends on its own, reaches the stage's interrupt once the provider is collected,
so the run pauses as `PAUSED_INTERRUPTED` with the exit code saved. Raised
inside the cleanup it cut the cleanup short; ignored, it was lost and the
controller went on to the next stages. A first signal while the provider
launches (after its attempt is saved), after it launched but before its wait
began (the "started" line can block on a stalled stdout), or while its keeper is
discharged (after the provider was collected) pauses the same way instead of
escaping and leaving the run `RUNNING`; the attempt keeps its artifacts for
inspection and `--abandon-stage`. When the wait never began, the keeper stops
the provider as it is discharged and the exit code is collected then; it had
been saved as `None`. One during admission, before anything of the stage is
saved, still ends the CLI as an interrupt between stages does.

A first signal outside every stage's handler scope (between stages, during a
runner check, or as a finished stage's record is saved)
still ends the CLI as a killed controller would, and the run stays `RUNNING` for
the same recovery: status reports a retained attempt as stale, to be reconciled
before any provider call. Catching the interrupt there instead could save a
half-updated state, so these changes leave it as it was.
A job workflow's interrupted stage (review, discuss) pauses as
`PAUSED_JOB_FAILURE`, its pause for any stopped job attempt, with an exact retry
token and the interrupt named in the reason.

With these changes, fake scenario runs through the reviewers' probes all paused
as `PAUSED_INTERRUPTED` with exit 2, no traceback and no provider outliving its
controller: 38 runs given two signals (SIGHUP and SIGHUP 0 to 400 ms apart,
Ctrl-C twice 250 to 500 ms apart, SIGHUP then SIGTERM 300 to 450 ms apart;
before the CLI held them until it exited, one of 9 Ctrl-C pairs 300 to 400 ms
apart killed it),
20 given one SIGHUP 0 to 40 ms after the provider exited, 7 given one signal
during the launch, and a hangup injected into the cleanup of the first stage
that ended normally. Closing a real controlling terminal also exits 2 without a
traceback: the hung-up terminal fails writes with EIO, which the CLI's output
wrapper now discards as it does a closed pipe's EPIPE; the pause was already
saved, but its own message raised and the CLI exited 1.

Qualification on macOS uses fake providers only: controller SIGKILL, process-group
SIGKILL, SIGHUP, controlling PTY close, harness SIGKILL, keeper loss before/after
exec, blocked ownership persistence, a TERM-resistant detached child, unchanged
provider exit semantics, read-only status and no automatic response adoption or
replay. Faults fire at event barriers, not arbitrary sleeps. Full catalog,
harness and Linux CI results belong to the pull request's exact source revision.
This crash path uses the repository's fault-injection exception to live-model
qualification; no paid calls or larger allowances are involved.

This fixes provider-stage and scenario-harness ownership. Runner-owned
verification commands (`verification-command-supervision.md`) and parallel
Builder workers (below) have their own lifelines now. Portable ancestry
sampling still cannot recover an arbitrary child that detaches and reparents
before any observer discovers it. That limit keeps #454 open for the remaining
ownership work rather than equating these passing controls with universal
process containment.

Provider-stage keepers now retain the provider's dedicated session group too,
not only a harness-guarded CLI's group. Previously a provider that spawned a
child and exited before the first keeper sample could be reaped by its caller;
the child then belonged to PID 1, and the keeper published a clean `stopped`
receipt without discovering it. The controller's keeper-loss fallback had the
same gap. A keeper killed just after the provider was reaped could also evade
the watchdog's next poll; normal and abnormal context exits now invoke the same
once-only fallback, preserve a caller's original error and leave an unsettled
cleanup uncertain. Both paths retain the admitted leader's group until it is
observed empty or its leader PID has a different birth identity, and signal only
freshly birth-verified members. No cleanup signal targets a numeric process group.
Focused fake-provider regressions hold the first ancestry sample at an event
barrier, witness the live parent/child identities, reap the provider, then
require cleanup of its reparented child, an unchanged provider exit 0 and an
untouched unrelated sentinel. A second control kills only that launch's keeper
at the same barrier and requires the controller fallback to stop the child,
including when the caller raises before its context can discharge normally.
The existing empty-group and reused-leader controls still end group ownership.
These controls cover children that keep the recorded group. An arbitrary child
that leaves this group (for example with `setsid()`) and reparents before any
observer discovers it is still outside portable sampling; this change does not
close that remaining #454 limit.

A CLI killed between two keeper samples used to leave its newest child behind.
The harness's CLI shared the harness's process group, and its keeper finds
children only by polling. A child started since the last sample (in the
owner-loss tests, the unsupervised `codex login status` settings check) was
reparented when the CLI died, so the keeper's cleanup found nothing and still
recorded a clean `stopped` receipt. The harness now starts a guarded CLI in its
own session. The CLI's keeper keeps owning that session's group after the CLI is
killed and reaped, until a sample finds the group empty or finds a different
process at the CLI's PID; the kernel cannot reuse that PID before then. A group
that empties and is re-created at a reused PID between two samples, which the
keeper takes every 50 ms, is not detected; that needs the PID space to wrap in
between. The harness owner-loss tests kill the harness or the CLI at two
barriers: that settings check, and the first supervised provider stage.

The CLI's group also widens the keeper's discharge check. A CLI that finishes
normally while a child it started still runs in its group, including one
reparented after its parent exited, is not discharged: the keeper stops that
child and the CLI. The call ends with exit -9, and the keeper receipt that the
call record names under `receipt` is `uncertain`, with cleanup error `CLI
discharged with live child processes`. A direct child still running at discharge
already ended this way; a reparented one used to escape and leak.

## Parallel Builder workers

A parallel Builder worker used to outlive the Orchestrator that started it. It
runs in its own session, and its stage keeper watches the worker, not the
Orchestrator. After a controller SIGKILL (or a kill of its process group) the
worker, its stage keeper and its provider ran on, and a later resume adopted
whatever BUILT result they wrote. A controller SIGTERM froze and killed each
worker together with its stage keeper while the worker was still cleaning up:
its result was `PAUSED_PROCESS_CLEANUP`, its stage receipt stayed `armed`, and
resume asked for `--abandon-stage`. A worker launched just before an interrupt
reached the list the cleanup used was never stopped at all.

Each worker now guards itself the way a CLI under the scenario harness does.
Before launch, the Orchestrator saves the worker row's `supervision` record and
writes a lifeline declaration into a pipe; the worker inherits the read end
(`--owner-lifeline-fd`, appended after its usual arguments) and the Orchestrator
holds the only writer for as long as the worker lives
(`autocode_worker_lifeline`). The worker's keeper (`supervision_cli.guard`,
`protect_owner`) reads that pipe. The declaration names no deadline: both
`deadline` and `timeout_seconds` are null, so each provider attempt keeps its
own stage limit, as before. The worker's stage keepers and providers are
admitted to its keeper before they exec.

- Controller SIGKILL or process-group kill: the pipe reaches EOF, the keeper
  sends the worker SIGTERM, the worker saves `PAUSED_INTERRUPTED` and stops its
  provider through its stage keeper, and the keeper kills anything it recorded
  that is still alive 15 s later. Its receipt ends `stopped`, cause
  `owner_lost`. That window is the declaration's `kill_after_seconds`
  (`autocode_worker_lifeline.KILL_AFTER_SECONDS`; every other declaration keeps
  the keeper's 2 s): the SIGTERM interrupts the worker's running stage, whose
  provider guard waits up to 8 s for the stage keeper
  (`autocode_supervision.launch`), and that keeper allows its provider 2 s
  between freeze and kill before it verifies the tree, so 10 s plus a margin
  for the worker to save its pause and result on a loaded host.
- Controller SIGTERM, SIGHUP or Ctrl-C: the Orchestrator sends each live worker
  SIGTERM, waits up to 8 s for them to exit, closes their pipes, and waits for
  their keepers (`autocode_worker_lifeline.SETTLE_SECONDS`: the 15 s window the
  workers declared plus 5 s for the keeper to verify and publish; a keeper that
  is gone is not waited for) before falling back to freezing what is left. The
  freeze reaches the worker's own keeper, so doing it inside the declared window
  killed a worker still tearing down a stage, and its keeper, before its pause
  was saved. The worker saves `PAUSED_INTERRUPTED` with its stage receipt
  `stopped`, and `--retry-builder` resumes it without `--abandon-stage`.
- A crash right after launch: the writer dies with the Orchestrator, so the
  worker refuses its lifeline (`Builder lifeline: ...` in its `worker.log`) and
  does no work. Resume then launches the member once, as if it never started.
  Only the lifeline's own failures carry that label; a failure in the worker's
  own work keeps its traceback in `worker.log`.
- A normal exit discharges the keeper (`discharged`, `controller_finished`).

The row's `supervision` record has one writer, `run_workers`; `collect`, the
retry and supersede checks and the raw batch in `--status` read it. A BUILT
result is adopted only when its receipt is `discharged` or `stopped` with no
cleanup error, or when no receipt exists because the worker never armed.
Otherwise the batch pauses as `PAUSED_ORCHESTRATOR_WORKER` naming the receipt,
for a person to inspect, and the member is marked stopped. A plain resume holds
it again. Once nothing it recorded is alive, `--retry-builder <id>` (also
offered as "Retry Builder task") archives the held result and relaunches the
member under a new lifeline: the worker rechecks its saved implementation
against its worktree and reports BUILT again, or pauses on any drift. The held
result is archived first so that a relaunch that dies before arming cannot leave
it looking adoptable. Every check that refuses to launch or collect while a
saved worker runs also counts that worker's keeper. A keeper still stopping its
worker or verifying it (receipt `stopping`) is a wait, not a Builder to retry:
the Orchestrator waits for it after a normal exit too, and if it is still
working the batch pauses as `PAUSED_ORCHESTRATOR_WORKERS` saying so, and a plain
`--resume-paused` adopts the result once the receipt reads `discharged` or
`stopped`. `--retry-builder` is for a receipt that is `uncertain` or errored, or
unsettled with its keeper gone. Batches saved before this change have no record
and keep their previous behavior.

The worker's interrupt handler covers its whole run, the pause it writes
included: a stage failure that was no interrupt used to leave SIGTERM at its
default there, and the keeper's owner-loss SIGTERM killed the worker with no
result. The handler's one interrupt can land on the pause write; the write is
repeated once with later signals absorbed. Only the state read and the workspace
lock precede the handler, where the worker has nothing to save.

A keeper used to publish a clean `provider_stopped` shutdown as `uncertain`
(cleanup error `Lifeline monitor remains active after cleanup`) whenever its
lifeline watcher thread had not been joined within 1 s; on a loaded host a
thread merely not yet scheduled tripped it, and the adoption rule above would
then have held a finished Builder's BUILT result. The keeper now holds one lock
for the whole of a trigger (its gate check through its receipt write) and takes
that lock before publishing, once `done` is set, so no trigger is in flight and
none can start; the watcher is woken through a self-pipe in its select set.
`uncertain` is reserved for a cleanup the keeper could not verify: a trigger
that did not finish within 3 s of publication. What that trigger recorded while
the publication waited for it (a signal batch or receipt write that failed) is
read after the lock is held, so a cleanup that partly failed is never published
as clean; the lock is released after the final save, so a watcher reaching its
trigger late finds `done` and returns. `tests/test_supervision.py` holds the
watcher inside its select past the join bound while the provider exits 0 and
requires `stopped`/`provider_stopped` with no cleanup error, fails a signal batch
that is still running at publication, and ends a late trigger.

Cleanup layering and remaining limits:

- The Orchestrator's parent checkout lock now survives its death through
  explicitly inherited worker keeper leases. Each worker closes its transient
  copy after the keeper admits it, before provider or command work; ordinary
  stage keepers still inherit no checkout descriptor. All keeper copies must
  close before the kernel unlocks, after verified cleanup is published. The
  controller closes its own copy without `LOCK_UN`, as before.
  `tests/test_checkout_lock_lifetime.py` kills the real dispatcher at a pipe
  barrier, holds two keepers before publication, and probes the flock from an
  independent process: busy after owner loss, still busy after one keeper
  retires, available after the last verified cleanup retires. The unchanged
  source fails this probe because the lock becomes available immediately.
  A keeper's uncertain cleanup still exits within its existing bounds rather
  than becoming an indefinite lease process. Its authenticated receipt remains
  in `<parent-run>/checkout-workers.json`; `checkout_lock.retain_worker` is
  the sole writer, before worker launch, and `orphaned_workers` reads it from
  the stable `writer.lock` holder. This sidecar uses atomic replacement without
  changing the lock inode or rewriting its holder during admission. A new
  AutoCode writer remains refused while retained birth identities are live or
  receipt/process inspection is unknown. This admission safeguard applies to
  AutoCode; the raw flock can release on an uncertain keeper exit. The same
  native regression proves that distinction with a surviving owned worker,
  then proves admission succeeds once that worker exits. A guard that never
  armed admits no work before its durable receipt, so an absent receipt after
  all inherited lock copies close does not permanently hold the checkout.
- Under the scenario harness two keepers cover a worker: the CLI's (by sampling)
  and the worker's own, which cannot be admitted to the CLI's because it is not
  the CLI's direct child. On a normal exit this is now correct for any number of
  workers: each worker's keeper is discharged and gone before its worker exits
  (`protect_owner` waits for the discharge), and the CLI keeper's discharge
  check counts only live recorded processes, so no worker keeper is "a live
  child process" at the CLI's discharge; the fake `parallel-diamond` scenario is
  the check. On harness loss the harness now declares a 33 s cleanup window:
  the Orchestrator's first 8 s interrupt grace, the workers' 20 s settlement
  bound, and 5 s for retaining the CLI pause. This is teardown time, not an
  extension of the task deadline or provider limits. The outer keeper waits
  for its live owned tree, including worker keepers, even if the CLI itself
  was SIGKILLed, and stops waiting as soon as that tree is gone. A surviving
  harness also gives captured descendants up to 38 s (that window plus 5 s for
  the outer receipt) before its ancestry-based fallback; closed CLI streams
  alone no longer trigger an immediate kill of nested keepers. Generic owner
  guards retain their declared window, or the 2 s default when omitted.
  A host crash between a worker's BUILT result and its keeper's discharge can
  still leave that result held as unverified until `--retry-builder` reruns it;
  no in-memory cleanup grace can qualify a result after a host crash.
- A worker killed before it arms (it imports the runner first) leaves no result
  and no receipt. The Orchestrator reads that as `never_armed` and resume
  launches the member once more, as if it never started: the same path as the
  crash right after launch above, which `tests/test_build_recovery_blackbox.py`
  test 11 drives (no receipt, no result, one relaunch). The pre-arm kill itself
  has no test of its own; it has no barrier to fire at.

`tests/test_builder_worker_lifeline.py` kills or interrupts the real controller
at an event barrier (M1's provider held and retained by its keeper, M2 and M3
built) and requires no survivor within 12 s, M1's own `PAUSED_INTERRUPTED`
result and an untouched sentinel. It also interrupts the Orchestrator while a
stand-in worker, guarded by the real lifeline, spends 14 s on its SIGTERM
teardown, longer than the 8 s grace, and requires its pause saved and its
keeper's receipt `stopped`.
`tests/test_build_recovery_blackbox.py` covers a crash right after launch and a
live guarded worker (its keeper frozen) blocking a second writer.
`tests/test_dispatch.py` holds a BUILT result whose keeper never discharged,
resumes twice without adopting it, and reruns it with an explicit retry; it
pauses, without offering a retry, on a keeper that is still finishing, and adopts
the result once the keeper has published.
`tests/test_supervision_cli.py` declares a 6 s window to a guarded owner that
ignores SIGTERM and requires it alive after 3 s and killed by the keeper after
the window; `tests/test_worker_lifeline.py` checks the worker's declaration.
`tests/test_harness_nested_lifeline.py` drives two real nested guarded offline
workers at socket barriers, kills the harness or CLI, and requires retained
pauses, clean stopped receipts, no owned survivors and an untouched sentinel.
Its fake-clock check reproduces a dead root killing a nested keeper before a
14 s teardown on the previous code. Focused harness clock tests check a retained
nested keeper settling at 14 s, a stuck keeper's bounded 38 s fallback, and a
second interruption during settlement without waiting those durations in real
time.
