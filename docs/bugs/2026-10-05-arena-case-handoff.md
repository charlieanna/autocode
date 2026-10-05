# Arena bug fixes lost the Investigator's test names

The Boltons starter case produced a correct repair in two live attempts: the
independent Arena oracle passed all five checks, but AutoCode could not accept
completion. The Investigator declared T1–T6, including separate guards for
zero-argument and single-iterable updates. The Builder received the diagnosis
as prose and wrote tests named after acceptance criteria instead. The runner's
regression proof correctly reported missing T1–T6 mappings.

The Builder now receives the same diagnosis cases that the proof requires,
with each case's exact test-name pattern and its before/after requirement.
Restore cases must fail on the original behavior and pass with the repair;
preserve cases must pass on both. The Investigator and small-fix planning
instructions use the same distinction. The case matcher continues to reject
tests named only after unrelated plan criteria.

Recovery from those unnecessary proof failures also exposed separate behavior.
One repair narrowed the failed task's criteria and paused on a stale handoff;
master already contains the admission fix in PR #487. The other proposal
targeted a protected test and changed the original discriminating command, so
the recovery gate correctly rejected it. Neither guard needs relaxation for
the diagnosis handoff correction.

An earlier Arena attempt exceeded its CLI deadline and left a provider running
in a separate session. Advancing TaskRun calls now use the existing process
supervisor to track owned descendants and verify cleanup before publishing a
timeout. Calls on the main thread use the existing interruption handler so
SIGTERM also enters cleanup; calls from worker threads preserve their supported
signal behavior. Status reads and saved approvals continue through their ordinary
CLI calls.

Original evidence is retained under `.autocode/arena/attempts/`: the stopped
candidate `5cc7c3c238c14151ab5bd3e9f499ef7e`, the master comparison
`2d80e692b83e472fbeb6078d8bad8776`, and the timed-out attempt
`22610d756b0b4fc6a81d9ba316deab18`. Their recorded results are unchanged.
