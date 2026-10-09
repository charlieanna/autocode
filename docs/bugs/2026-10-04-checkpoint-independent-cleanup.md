# Process cleanup must not wait for checkpoint writes (#351)

A slow initial process checkpoint can hide a detached tool descendant from the
supervisor. The independent deadline kills the provider, the descendant is
reparented, and later discovery cannot reconstruct its lost ancestry. Before
this fix, `wait_for_stage` could then return successfully from cleanup while
that child continued writing.

The process owner now discovers and stops descendants in a dedicated worker.
It publishes complete identity snapshots in memory; the controller remains the
only thread that invokes persistence callbacks. A blocked save cannot suspend
process discovery or cleanup. The controller joins the owner before returning
or propagating an interruption, then persists the latest receipt. A failed
write is not blindly repeated, and uncertain cleanup takes precedence over a
concurrent checkpoint error. An interrupted worker start cannot transfer
ownership to both threads.

Root identity capture still precedes ordinary provider polling. Independent
hard/idle/tool deadline enforcement and birth-identity checks remain in place.
No provider-specific behavior, deadline extension, saved-state schema change,
or new runtime flag is involved.

## Fresh live proof

The before run used master `40297be3`; final qualification used master
`25bbe234` plus this change. The intervening merged files did not change the
process supervisor or its process-discovery dependencies. Native OpenCode
exports confirm actual `openai/gpt-6-sol` tool execution for every component
case. The model invoked the supplied local probe; it was not a fake provider.
Only the initial checkpoint delay was injected by the operator.

Each paired case used the same prompt and helper bytes, a 90-second hard cap,
and either no checkpoint delay or a 100-second initial delay. The helper
started a detached heartbeat writer and held its foreground tool open. An
independent observer recorded their ancestry and kernel birth identities
without supplying that information to AutoCode. It checked heartbeat growth
after the supervisor returned, then cleaned up only its verified fixture
workers if needed.

| Revision / case | Return after | Heartbeat after return | Operator cleanup needed |
| --- | ---: | --- | --- |
| Before, normal checkpoint | 90.10 s | 1686 → 1686 | No |
| Before, stalled checkpoint | 100.03 s | 1946 → 1958 | Yes |
| Final fix, normal checkpoint | 90.16 s | 1685 → 1685 | No |
| Final fix, stalled checkpoint | 100.03 s | 1678 → 1678 | No |

In the final stalled case, writes stopped at 90.03 seconds, while the controller
was still blocked in its 100-second checkpoint. The final saved receipt
included the detached child's verified identity. A transient process-table
entry alone is not considered evidence of continued execution.

A separate fresh public `TaskRun` review on the final candidate also completed
normally with actual OpenCode/Sol. It identified an intentionally planted
first-seen ordering defect and delivered a regression test. The runner proved
the finding, and an independent replay failed on the broken candidate and
passed on the original implementation. Original source and tests were
unchanged. Limits were 600 seconds total, 240 per stage, 120 idle and three
iterations.

The initial candidate passed the live component probes and a normal review,
but the affected-test gate caught an interruption receipt that omitted later
descendants. That failure was retained and corrected before repeating live
qualification. Event-driven tests cover a blocked checkpoint, cleanup-error
precedence and interrupted worker startup; existing SIGTERM coverage verifies
the complete final receipt. Runtime hashes stayed fixed through the final live
runs.

Evidence, native exports, executable probes and failed attempts are retained
under `.scenario-runs/cleanup-351-live/` in the `checkpoint-cleanup-live`
worktree. The `final/` directory is the final candidate's qualification. These
results establish this checkpoint-stall fix and one ordinary review flow; they
do not establish that every possible daemon escape or process-inspection
failure is solved. The earlier unrelated full-suite cleanup assertion failure
is not assumed to have the same cause.
