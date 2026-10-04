# Original internal test links must not block review startup

Issue: https://github.com/charlieanna/autocode/issues/307

A real AutoCode review of etcd commit `7a68a7c` stopped before any model
started: the protected-test inventory rejected
`client/v3/concurrency/example_election_test.go`, an existing tracked relative
link into `tests/integration/`. The repository has eleven such test links.
The existing blanket rejection prevented both legitimate startup and capture
of the original test gate.

The correction binds each original file link, every intermediate file link and
its terminal file, including targets whose names do not themselves classify as
tests. All targets must belong to the source snapshot. The retained bundle and
scratch replay preserve relative link text and file resolution semantics.
Directory links, external/absolute targets, cycles, missing targets and ignored
unbound targets remain refused. Candidate retargeting and target edits trigger
execution of the original gate; they cannot silently replace it. Scratch
restoration removes candidate destination links and parent aliases before
writing retained files. Regular-file version-one bindings remain unchanged.

A CLI regression failed on the original startup path. After the correction,
33 focused checks passed, including a full workflow with linked tests, actual
original-gate failures against weakened/retargeted candidates, target hash and
link tampering, safe restoration with an external replacement parent, original
`__file__.resolve()` semantics, and old regular binding compatibility. The
first post-fix CLI attempt correctly rejected a fixture edit to an undeclared
link target; the fixture now explicitly declares both affected paths.
169 affected tests across 15 modules also passed.

Fresh live qualification completed through AutoCode/OpenCode on the real etcd
checkout with GLM 5.3: all eleven links and 596 protected inputs were captured,
the Reviewer executed the actual Go tests and correctly reported the planted
missing-fsync defect and inadequate log-only oracle. Original source/test/link
identities were unchanged; the review added four permitted reproduction/report
artifacts. This is a completed review with findings, not a clean bill of health
for the deliberately broken etcd candidate.

A separate GPT-6 Sol AutoCode review executed 25 focused checks and approved
the fix with one documentation advisory: describe link entries and target
closure in user-supplied gate revisions. `docs/protected-tests.md` now does so.
An earlier fix review accidentally routed to GLM and reached its 480-second
hard limit without a verdict. That failure is retained; no saved budget was
reset. Both completed reviews used the same runtime/test hashes as these
local checks. The fake catalogue passed 54 cases, with one existing
NOT_EXERCISED case and one live-Investigator SKIPPED case.

The full suite ran 3,030 tests in 225 modules: 224 modules passed; three errors
in `test_scenario_oracles` came from `PermissionError` in the unchanged
`task_scenarios._run` process-group cleanup. Its isolated 41-test rerun passed
with one existing skip. The original failure remains recorded as
https://github.com/charlieanna/autocode/issues/308; this is not a clean full-suite
pass and no fix for that separate cleanup defect is included here.
