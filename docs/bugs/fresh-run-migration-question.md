# Fresh runs must not inherit legacy migration questions (#366)

A new live Luna/Sol build stopped to ask whether it could reconstruct the goal
from the request it had just received. The initializer called `migrate(fresh=True)`,
which installed a legacy draft containing `migration-context`. Discovery treated
that synthetic question as a real unresolved user decision. The operator had to
reconfirm the existing request before planning could continue.

Fresh initialization now leaves contract creation to planning. It still sets the
run version and starts workflow recognition; the first real draft must pass the
normal contract checks and receive explicit approval. Actual legacy saved runs
still receive the conservative reconstruction draft, with their existing work,
answers, sessions and execution history retained. No schema, guard or approval
rule has been relaxed. A fresh draft is not made valid by bypassing its checks.

The original live stop is retained under the follow-up #364 qualification on
`25bbe23464b222ae3b7867689204105d40aada3a`. The migration module on the fix's base,
`83271dcdb13c780889b6fc58beeb46b688b6adc1`, is byte-identical to that baseline.
The stopped run predates this fix and is not counted as an after-test.

The CLI regression uses a scripted Planner that carries unresolved handoff
questions into a valid clarification-only draft. On unchanged master, a fresh
run stops asking `migration-context`; the genuine-question and legacy controls
pass. On the fix, the fresh run reaches normal plan approval, cannot accept a
forged approval token, and has not written the implementation. A separately
introduced planning question still reaches the user, and legacy migration still
shows its reconstruction question. Initial test-fixture import/schema errors
were corrected and retained before recording the intended before-fix failure.

The initial candidate live run on the original fix base reached `TASK_COMPLETE` in one
build iteration: all ten criteria verified, regression proof and check replay
passed, and an independent oracle passed eight checks. Native OpenCode exports
confirm Luna and Sol. Its only operator action was ordinary plan approval; there
were no clarification answers, report repairs, retries, or limit changes. The
original tests and all runtime source hashes remained unchanged during the run.

Focused checks passed 123 tests, and the affected-file gate passed 664 tests.
The supplemental fake catalog passed 54 scenarios, with its existing one
`NOT_EXERCISED` and one live-Investigator `SKIPPED`. The wider suite found two
fixtures that assumed the synthetic contract existed: the parallel provider
read its metadata before producing discovery, and the stop test manufactured an
approval token. Discovery now runs before that fixture metadata is read. The
stop test obtains a real displayed token through the public CLI and verifies
that Stop still prevents approval and implementation. All refusal and approval
assertions remain enforced.

After integration onto master `6a8c0065238eeaaea7b17e06d57dcc0d3f1f9903`, a
second fresh live run of the same brief also reached `TASK_COMPLETE` in one
iteration. Its eight criteria, regression proof, replay and independent eight-check
oracle passed. Six native exports confirm Luna and Sol; only normal plan approval
was supplied. There were no clarifications, report repairs or retries. Saved
limits remained 1800 total seconds, 300 per stage, 120 idle seconds and six
iterations. Runtime hashes and original tests remained unchanged.

The integrated affected-file gate passed 673 cases across 39 modules, and the
supplemental catalog returned 54 PASS, one existing NOT_EXERCISED and one
live-Investigator SKIPPED. A further native Codex compatibility check exposed a
missed assumption: pause after workflow recognition, then enable joint planning.
That supported boundary has no draft yet, but upgrade setup read its token and
crashed with `KeyError('goal_contract')`. A saved native rollout confirms Sol;
the original checkpoint, limits and single completed recognition stage remain.

Upgrade setup now records a null prior-contract token at that boundary, and only
changes approval fields if a contract exists. It retains the backup, requires
normal planning and approval, and preserves existing approved-run upgrades. A
public CLI regression fails before this correction and completes after it;
eleven upgrade and architecture checks pass. A fresh native Luna/Sol campaign
on the corrected code reached `TASK_COMPLETE`: nine verified criteria, eight
independent oracle checks, passing regression proof and six verified replay logs.
Ten native rollouts confirm the actual models. The early-upgrade backup preserves
the original recognition stage, and all 350 runtime file hashes, original tests
and limits stayed unchanged. The Planner initially omitted coverage of two
requirements; normal report repair succeeded. Completion took a second review,
ending at iteration two. The only user event was normal plan approval; this is
a successful recovery run, not a no-repair claim. The refreshed affected-file
gate passed 674 cases across 39 modules.

The full integrated gate was deliberately interrupted when the native check
proved that another runtime correction was necessary; cleanup confirmed no owned
processes remained. The final supplemental catalog passed 54 scenarios, with
one existing NOT_EXERCISED and one live-only SKIPPED; the final full suite is
still running. The original full run covered
3398 cases across 246 modules and failed five modules: the two fixture assumptions
above, two stale fixtures now fixed upstream, and a browser test that exceeded its
420-second timeout. A later browser invocation passed, but that does not turn the
original full gate into a pass. No timeout was raised. Evidence is ignored under
`.scenario-runs/fresh-migration-366/`.
