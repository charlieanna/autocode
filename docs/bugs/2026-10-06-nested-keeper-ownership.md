# Nested keeper ownership can be lost during CLI cleanup (#454)

A real-process scheduling control reproduces this on master e042daaf1 and
3b0f0340 (PR #550). Their supervision implementations are byte-identical.

The control pauses provider creation until the outer CLI keeper records the
inner stage keeper. It delays the outer keeper, lets the real provider launch
and the stage keeper arm, delays that stage keeper, kills the CLI, then resumes
the outer keeper. These delays are explicit SIGSTOP scheduling faults. They do
not claim to establish the trigger of an uninstrumented incident.

The outer keeper knows the CLI and stage keeper but not the subsequently born
provider. Once the CLI disappears, those processes are siblings reparented to
PID 1; ancestry cannot rediscover the missing ownership edge. The outer keeper
kills the stage keeper, reports stopped/owner_lost with no cleanup error, and
exits. The provider survives while its stage receipt remains armed. Both
controls explicitly cleaned up their birth-identified survivors afterward.

PR #550 Ubuntu CI also observed an owned process surviving a CLI SIGKILL, but
its assertion printed only the PID. That may have the same cause; it is not
proven. A local repeat of the unchanged test passed, which does not resolve the
CI failure.

## Repair requirements

Before provider exec, nested launch must transfer its exact provider and keeper
identities to the enclosing supervisor and receive durable admission. The
provider must remain blocked if that handoff fails. A cleanup observer cannot
kill a nested keeper first and assume a later ancestry scan will recover its
provider. Merely waiting longer or skipping keepers by executable name does not
establish ownership and is not an adequate repair.

The transfer must retain birth-identity checks, unchanged provider exit codes,
original deadlines, the pre-exec bootstrap, and independent cleanup after CLI
loss. Interrupted admission must fail closed. Concurrent inventory sampling
must not overwrite admitted identities. A dead CLI cannot revoke the outer
supervisor's receipt, and a normal completion must discharge without signalling
unrelated processes.

The repair uses a framed admission request before releasing the provider's
bootstrap. The outer keeper validates native birth identities and direct CLI
parentage, saves both children in its receipt, then acknowledges admission.
Admission writes and ordinary samples share one thread; the independent owner
watcher remains free to enforce EOF and the original deadline. Legacy textual
birth fallbacks are rejected at this new ownership boundary.

The same real-process wire observer reproduced the missing admission on the
old source and current master d3554125, and verified durable admission on the
candidate before provider execution. Under the same delayed-inner-keeper/CLI-loss ordering, the repaired
outer keeper stopped the provider. Normal threaded launch, interrupted
admission, wrong birth identity, and an unrelated sentinel have separate
controls. This composes with master #558's retained CLI group ownership:
detached stage providers still require the explicit admission edge.

The earlier candidate passed 4,789 tests across 324 modules, 251 harness tests,
and 60 fake scenarios (one catalog entry was not exercised and one live-only
entry was skipped). Those results predate the final master integration and
strict native identity validation. The final combined source passed 39 focused
checks, including all four harness owner-loss tests, and 53 affected tests in
five modules. Its complete fake catalog finished with 60 PASS, one
NOT_EXERCISED and one SKIPPED, with every CLI exit and source-pin check passing.
Ten completed serial results were retained; an intentionally interrupted
attempt remained ungraded, with no owned or scoped survivors. The remaining
52 entries ran through two isolated CLI workers with unchanged limits.

After merging master 19f6f270 as 9a31a8e8, the unchanged runtime passed
56 affected tests in six modules, six explicit command/verification ownership
tests, the nine-check wire control, and three supplemental fake scenarios
(bugfix-trivial, completion-rework-direct and parallel-diamond). Their 24
outer receipts discharged cleanly; all 517 recorded native identities were
gone, with no unknown liveness. The first affected attempt encountered socket
and process-inspection EPERM under the restricted execution substrate; that
failure is retained separately from the identical native-access passing run.

Fresh native OpenCode 1.18.33 qualification on 9a31a8e8 reached TASK_COMPLETE
in one iteration using Sol medium for Requirements, Planner and Builder,
Astra high for Plan Reviewer and Tester, and Astra medium for Completion.
The disposable greeting bugfix used the public TaskRun interface through the
production enclosing CLI lifeline. All six unfiltered native streams finished;
every provider and stage keeper appeared in its enclosing ownership receipt.
The runner proved four fail-to-pass and two pass-to-pass named cases. Public
evidence inspection marked all ten criteria current; the independent eight-case
functional oracle passed. Protected files, model routes and original limits
(1800 active seconds, 360 per stage, 120 idle/tool seconds, four iterations)
remained unchanged. Active time was 415.315 seconds. The strict terminal audit
found all 465 recorded native identities gone, without unknown liveness.

Evidence is retained in .scenario-runs/verification-454/openai-after-r2 and
ignored local validation logs; it is not committed. The earlier r1 completed
planning only before the master merge and remains partial evidence. A retained
empty-input aborted Builder tool event recovered without a report or scope
failure. Controlled process faults establish the crash-ordering repair; the
live campaign establishes ordinary native-provider integration. Neither
establishes the spontaneous CI trigger or closes every concern in #454.
