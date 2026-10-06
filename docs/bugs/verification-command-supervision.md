# Verification commands after owner loss (#454)

The shared verification command boundary retains its shell argv, working directory,
scrubbed test environment, streams, actual Popen child and collected exit status.
An independent keeper records native birth identities before releasing that child.
Its command admission and cleanup receipts stay beside retained output, outside
scratch trees removed after verification. Admission consumes the original command
time budget.

Command proof requires a collected integer exit and an intact, pinned normal
cleanup receipt. A normal nonzero exit remains valid baseline-failure evidence.
Owner loss, a deadline, missing collection, keeper failure or changed ownership
receipts cannot establish passing evidence or a fail-to-pass flip. Replay,
protected tests, original-brief checks, risk checks and final completion retain
and recheck the same ownership evidence. Older receipts without ownership fields
keep their previous behavior; a partial or malformed new extension fails closed.

Authenticated keeper cleanup and fresh native absence allow retirement of the
command ownership hold. They do not recover a lost exit status or reconcile a
scheduled pending obligation. Uncertain cleanup retains runner activity and the
clean-replay pending launch.
Caught exceptions with confirmed cleanup still produce #414's failed attempt and
allow a fresh retry. Result application publishes command ownership from the
original controller state before exec; speculative result copies cannot erase
the hold. Status reads fresh supervision without changing saved state.

The unchanged-master fault at 72b7104d killed a disposable public run_command
owner after socket barriers identified its command and detached TERM-resistant
worker. Both workers survived, while the unrelated sentinel stayed alive and no
result was collected. Fixture cleanup used their captured native identities.
The changed-source fault requires keeper-owned cleanup and sentinel survival.
Public TaskRun faults cover both a prerequisite and result application after a
finished review provider. They check durable ownership, read-only stale status,
and refusal to launch again when cleanup is uncertain. Deadline normalization uses a controlled caller clock with an
actual zero-exit command; existing keeper fault tests cover physical deadline
termination. No paid models or larger run allowances are needed for these faults.

Independent supervision of Builder workers, version-2 preflight workers and
other standalone capture/oracle owners remains separate #454 work. Builder
retry admission still honors any command ownership hold within its own run.

The harness owner-loss fixture previously signalled readiness during `codex login
status`, before any supervised model stage. Retained local argv identified that
startup probe; one Linux harness run also failed its owned-process cleanup check,
without enough actor detail to establish its cause. The fixture now completes
login setup and requires an armed stage receipt with fresh native birth identities
before either fault. Startup authentication/version probes still need separate
owner-loss supervision and qualification under #454.
