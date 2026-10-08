# Interrupted clean verification remained blocked after abandonment

Issue: [#682](https://github.com/charlieanna/autocode/issues/682).

A bounded TypeScript qualification stopped while a clean verification command
was running. Its independent supervision receipt later authenticated stopped
ownership and all recorded process identities were absent, but the scheduler
had not published its completed pointer. Public
abandonment of the finished report-repair attempt succeeded. The subsequent
public resume immediately returned `PAUSED_VERIFICATION_UNCERTAIN` again.

The pre-review scheduler guard ran before runner-check retirement. Its pending
admission could be cleared only by an existing completed scheduler receipt;
model abandonment did not create that receipt. Native cleanup alone could not
prove an exit code, test success or an unchanged post-execution context.

Explicit resume after model abandonment now authenticates the exact owned
admission hierarchy, actual command and output, supervision identities and
terminal receipt, then checks native absence for the owner, keeper, provider
and complete recorded inventory. It preserves the original pending admission
in an immutable interrupted receipt with unknown exit and duration, publishes
the durable receipt pointer, and only then retires the pending pointer. This
failed receipt cannot be reused as validation. Already published completed
evidence is preserved rather than rewritten. Inspection of the exact owned
command receipt later showed that the original Node result had been collected
and saved before interruption, while its completed pointer was still missing.
This additional boundary now requires the original receipt's exact attempt,
identity, admission time, command, output hash and pinned normal cleanup before
publishing the missing pointer without changing the receipt. It neither invents
an exit nor substitutes that old result for fresh-context validation.

Missing, conflicting, corrupt, live, unknown, unfinished, symlinked and
nonregular evidence stays blocked. Status and ordinary dispatch retain the
same guard. Tests cover public resume after abandonment, fresh execution after
recovery, and interruption at both publication boundaries. Original Arena and
operator timeout outcomes remain separate from any later qualification.
## Preparation custody and the current obligation

Scheduled clean replay now records a separate immutable preparation admission
before publishing its pending marker. One owned worker encloses scratch-tree
preparation, project environment/framework discovery, the genuine inner test
capture and cleanup under the existing native keeper. Its exact command custody
is durable before the first preparation or discovery Git child is released.
The parent constructs only JSON arguments and a process-free scrubbed environment.
In-place launch-input policy uses an explicit bounded `Supply` transport record.
It preserves checkout and capture-store roots, generated/vendor hashes and modes,
uncertainty, notes, recorded status and the derived identity. Only the admitted
worker reconstructs that policy; its existing no-follow copy and source-hash
checks still apply. The record neither re-inventories inputs nor upgrades an
unrecorded or uncertain supply. Other objects are not converted with `str()`.
Worker requests use compact JSON with a separate 12 MiB envelope limit. This
leaves 4 MiB framing headroom for the existing 8 MiB launch-input record. The
publisher checks the exact serialized bytes before publishing a request or
starting a child, then uses atomic replacement with file and directory fsync.
The worker and recovery reader use the same envelope bound. Admissions, command
receipts and worker answers retain their 4 MiB default reader limit. Deterministic
coverage runs a valid input record larger than 4 MiB through the actual worker,
checks recovery of that request, and rejects an oversized envelope before launch.
The inner command retains its configured timeout. The whole worker has that
timeout plus a separate 900-second preparation/discovery/cleanup allowance;
existing client and stage deadlines still bound the complete invocation.
An uncertain inner test skips scratch removal; it cannot launch a cleanup child
or overwrite the current pending custody. The outer keeper must stop and collect
all its children before any subsequent attempt can be admitted.
An older saved runner check cannot stand in for a different current obligation.

Public status adds `verification_obligation`: the current attempt/identity,
phase, admission, native owner and current command custody. This is read-only
ownership information, never execution evidence or permission to retire a hold.
Attempt identifiers, admission time/nonce/owner, phase and current command's
output/admission/supervision hierarchy bind before native liveness is observed.
Malformed records are `unavailable`; legacy markers without preparation custody
are `legacy_unbound` and remain held. Missing directories prove neither no launch
nor native absence.

Explicit resume can record an interrupted failed receipt only after exact
preparation/request/worker/command pins, genuine terminal cleanup and fresh
absence of every bound native identity. It retains unknown exit/duration and is
never reusable. A pending marker before keeper admission, or during the caller's
post-execution context measurement, conservatively remains held without an exact
current native custody record. If the normal scheduler receipt was actually
published, explicit recovery instead requires the exact returned worker result,
closed outer custody and a collected inner receipt whose owner appears in that
outer inventory. Both receipt-publication boundaries preserve the original
command values, bytes and finish time. An outer worker's zero exit is never a test
PASS. Genuine collected nonzero/error results and the scheduler's exact
context-changed error extension may be preserved at those boundaries, but remain
failed and non-reusable. A post-context exception can add only the scheduler's
bounded failure message to the exact returned result; no native field or extra
key may change. Setup-only, unknown or uncollected inner receipts stay
held. This change supplies no waiver for earlier ownerless markers.
