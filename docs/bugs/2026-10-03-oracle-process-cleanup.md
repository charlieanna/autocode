# Scenario oracle process cleanup (#308)

A full macOS suite produced three real `PermissionError` exceptions in
`task_scenarios._run`: `communicate()` had completed, then cleanup signaled
`killpg(proc.pid, SIGKILL)`. The exception replaced the oracle result. The same
41-test module passed on an isolated rerun. PID reuse and a persistent process
leak were not demonstrated; the intermittent OS error was not reproduced by
the initial live-model probes.

The old cleanup is invalid independently of that uncertainty: after reaping a
leader, its numeric PID is insufficient evidence of process-group ownership.
The retained live investigation also demonstrated result loss by injecting
EPERM; that is a deterministic control, not an actual OS reproduction.

The correction captures stdin and output in temporary files and delegates
completion to the existing process supervisor. It keeps the direct child
unreaped until descendants are recorded, checks birth identity before signals,
and returns a result only after owned cleanup succeeds. Uncertain cleanup
still raises. The program-scenario launcher likewise observes and terminates
its root without an implicit `Popen.poll()` before supervised cleanup.

The first candidate was rejected: its existing timeout regression took
13.7045 seconds against the unchanged eight-second bound, and 91 focused tests
took 777 seconds. Profiling a trivial command attributed most of 3.24 seconds
to psutil's macOS whole-table parent scan. Its live-model review also reached
the 480-second cap without a verdict; it is not counted as an approval.

The revised candidate uses Apple's scoped `proc_listchildpids` API on macOS.
It checks count-versus-byte semantics, grows full buffers, rejects errors, and
rechecks each parent's identity and each child's parent/birth. These are only
candidate IDs; the supervisor still checks process identity before cleanup.
Other platforms retain psutil discovery. The ABI was checked against the local
SDK and [Apple's implementation](https://github.com/apple-oss-distributions/xnu/blob/main/libsyscall/wrappers/libproc/libproc.c).

The corrected revision passed 20 initial focused checks and 299 affected tests
across 24 modules. A fresh GPT6Sol AutoCode review completed with `approve`,
executing 21 focused checks, the 43-test oracle module (one existing Chromium
skip), the exact injected-EPERM regression through unittest discovery, and
real fast-exit, detached-child, timeout, nonzero-output and timing probes. The
recorded children stopped before return and an unrelated sentinel survived.
Three trivial-command probes took 0.063, 0.068 and 0.070 seconds on this host;
these timings are observations, not portable performance guarantees. All seven
changed runtime/test files matched their pins after the review.

The fake catalog passed 54 scenarios, with one existing NOT_EXERCISED and one
live-Investigator SKIPPED. The full suite ran 3,038 tests across 226 modules:
224 modules passed; dashboard-consumer readiness and a browser accessibility
timeout failed. The unchanged consumer rerun passed, and the browser suite
passed on its later in-module retry; neither erases the original failure.
These gate failures remain under [#314](https://github.com/charlieanna/autocode/issues/314).
The relevant test/browser files are unchanged from master, but causality is
not established because shared process discovery changed. This is not a clean
full-suite pass. The original #308 EPERM did not recur in the revised run.

Local, ignored evidence is under
`.scenario-runs/remaining-defect-proof/oracle-cleanup-308/`. The original full
failure is retained in `../links-307/full.log`; candidate revisions and live
fixtures have separate source pins. A direct invocation of the retained test
file only defines tests; qualification requires unittest discovery and an
actual nonzero test count.
