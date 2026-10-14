# Verification commands after owner loss (#454)

The shared verification command boundary retains its shell argv, working directory,
scrubbed test environment, streams, actual Popen child and collected exit status.
An independent keeper records native birth identities before releasing that child.
Its command admission and cleanup receipts stay beside retained output, outside
scratch trees removed after verification. Admission consumes the original command
time budget.

This is the portable runner-command guard, separate from the kernel-mediated
model provider boundary. Nested preparation/replay commands install no provider
notification listener and carry no kernel-qualified `session_boundary` marker.
Existing provider filters remain inherited when a command is launched inside
such a family. Standalone runner commands still cannot discover an unseen child
that creates a new session and reparents before observation; their sampled SID
guard does not claim universal process containment. Linux model provider adapters
require the kernel boundary and expose no CLI bypass. The owner-selected macOS
policy ([#1195](https://github.com/charlieanna/autocode/issues/1195), implemented
by [#1204](https://github.com/charlieanna/autocode/pull/1204)) uses portable
lifeline and sampled group/session ownership instead. Those provider receipts
carry no kernel-qualified `session_boundary` marker, and an unseen child that
creates a session and reparents between samples can escape. A Linux host that
cannot establish its required boundary still refuses before provider exec.

Command proof requires a collected integer exit and an intact, pinned normal
cleanup receipt. A normal nonzero exit remains valid baseline-failure evidence.
Owner loss, a deadline, missing collection, keeper failure or changed ownership
receipts cannot establish passing evidence or a fail-to-pass flip. Replay,
protected tests, original-brief checks, risk checks and final completion retain
and recheck the same ownership evidence. Older receipts without ownership fields
keep their previous behavior; a partial or malformed new extension fails closed.

Authenticated keeper cleanup and fresh native absence allow retirement of the
command ownership hold. They do not recover a lost exit status or reconcile a
scheduled pending obligation. A stopped or uncertain receipt the reader can load,
whose owner, keeper, provider and recorded processes are all confirmed gone, is
retired on the next resume so the check runs again. That retirement is an audit
event (`runner_check_retired`), not a passing result. An armed receipt, a live
process, unreadable bytes or denied inspection keeps the hold. The keeper will
not publish a receipt larger than the reader accepts (4 MiB, enough for the
20,000-process inventory).
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

Parallel Builder workers are kept too: each guards itself on a lifeline the
Orchestrator holds, with a kill window that covers a stage teardown, and a BUILT
result is adopted only after its keeper verified the cleanup
(`qualification-supervision-454.md`, "Parallel Builder workers"). The inherited
checkout lease and the harness's nested cleanup window are documented there too.
Builder retry admission still honors any command ownership hold within its own run.

Version-2 prerequisite probes and model-free worker sandbox/debug helpers now
retain portable command ownership as well. They preserve exact argv, worker
environment, original output and collected exits while publishing the same
runner-check admission before exec. Public TaskRun faults cover both local and
worker execution: caller loss stops the recorded probe and detached worker,
leaves an unrelated sentinel alive, and accepts no readiness or proof. Standalone
capture/oracle owners outside these guarded paths remain separate work; this
change does not qualify them or remove the portable sampling limit.

The original harness readiness event could occur during `codex login status`,
before a supervised model stage. Retained local argv proved that barrier gap;
the first Linux harness failure did not identify its surviving actor or establish
its exact cause. The harness now preserves separate startup and stage faults.
Startup faults retain #558's guarded CLI ownership and group cleanup; stage
faults complete login and require an armed receipt with fresh native
owner/keeper/provider identities immediately before either fault.

The integration preserves #555's enclosing keeper discovery barrier, #558's
survivor diagnostics, and all four fault cases with their original cleanup and
sentinel assertions and time limits. Guarded harness startup coverage does not
qualify standalone authentication/version probes. They and standalone capture/oracle
owners remain deferred, unqualified boundaries: the original #454 acceptance
covers active provider stages and their CLI/harness owners, not every standalone
helper subprocess. In particular, `providers/env_prep.preflight_run` still calls
`subprocess.run`; no independent owner-loss guarantee is claimed for it. Closing
that original acceptance does not qualify these helpers or the historical runs
with missing supervision receipts. The seven-point disposition and current
fault evidence are in [the qualification note](qualification-supervision-454.md).
