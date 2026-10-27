# Full-master fixture contract regressions (#2223)

Master run 40492351191 at `51728f73b` failed four test modules because scripted
providers and mutation inputs no longer followed the contracts they exercise.
This is separate from the Windows import regression fixed in #2222.

The black-box Builder and configured-tool fixtures omitted Investigator's
`counterexample` in their prescribed execution-failure diagnoses. Strict output
validation rejected those reports, and report-only repair copied the same invalid
report. The retry/recovery cases therefore never reached their original policy
assertions. Both fixtures now provide a single scratch-only edit to their pinned
report evidence that makes their existing probe fail. Unknown diagnoses retain
an empty counterexample and cannot claim causal proof. No product proof, retry,
role-isolation, report-validation or host-admission guard changes.

M09's old mutation anchor stopped matching when the evidence guard gained an
additional call. It now targets the current code and still removes the guard in
a disposable copy; the original detector must kill that mutation. CI mappings
also retain the slow Builder, recovery and configured-tool consumers when their
scripted provider changes, instead of leaving these paths only to master's full
suite.

## Evidence

A fresh supported-Linux baseline reproduced the serial-noop and configured-tool
escalation failures: two tests failed in 15.977 seconds on missing counterexample.
The changed selection ran 505 tests in 22 modules in 249 seconds. All behavior
and mutation controls passed except SYS-07: the source-only test checkout lacked
the historical Git object that replay reads. Importing the exact commit/tree/blob
objects for `49b3a7b70c86398a1e484571a484e099c6ff733b` restored that environment
requirement; all eight tests in `tests.test_catalogue_t14` then passed in 123.067
seconds. The original failed log is retained outside version control. This was
not a source fix, retry-policy change or weakened historical replay assertion.

The final evidence includes all 70 builder-policy tests, 27 build-recovery tests,
28 build black-box tests and 13 configured-tool tests, plus all eight mutation
suite tests. The CI mapping controls and Ruff checks pass. Commands:

```sh
/tmp/home/sweep-venv/bin/python tools/run_suite.py --changed --jobs 4
/tmp/home/sweep-venv/bin/python -m unittest tests.test_catalogue_t14
```

These supervised fake-provider checks use no model accounts. They establish the
repaired mechanics, not live delivery speed. Any subsequent requested TUI/Kilo
review is a separate, bounded integration comparison and must report its actual
scope and timings.


## Cold provenance lookup in the denied-admission fixture

The subsequent full-master run [40553999894](https://github.com/charlieanna/autocode/actions/runs/40553999894)
on `a38957d18` passed the original failures above, but failed one remaining module
out of 790 (11,575 tests): `tests.test_dashboard_integration`. Its denied-planning
admission test patches the shared `subprocess.Popen`, already pinning the retry
Git-index seams. New launch provenance also asks for the runtime Git identity;
with a cold identity cache this reaches the unrelated mock and raises before the
expected admission refusal. Running the single test in a fresh interpreter
reproduces that error.

The fixture now pins the public runtime-identity seam too. Its assertions still
require no Popen launch, no spent Planner call and no active stage. Runtime
provenance, admission and provider behavior are unchanged. CI explicitly maps the
launch-provenance producers to this dynamic admission consumer, with a selection
regression that also keeps it when classified slow. No model run is needed for
this fixture/CI-only correction.

## JSON refusal after unused-worktree cleanup

The later integrated master run [40575058953](https://github.com/charlieanna/autocode/actions/runs/40575058953)
on `51b164da1` exposed a separate stale assertion in `tests.test_json_output`.
Merged #2247 deliberately removes a launch-created task worktree and branch
when configuration refuses before the first run save. The JSON action test
still required that unused directory to remain. A fresh Linux checkout of
`34c3f6f12` reproduces that exact failure in 0.136 seconds.

The regression now requires the unused directory and Git branch to be gone,
while retaining the existing single-object JSON, exit/error, creation-message
and saved-state preservation checks. It also requires the cleanup notice on
stderr. Existing task-workspace controls retain saved runs and edited trees.
No production cleanup or refusal behavior changes.

The three launch/cleanup modules now explicitly select this dynamic JSON CLI
consumer. The new selector regression fails for all three producer paths before
the mapping and also exercises retention when the consumer is classified slow.
This closes the changed-test selection gap that let the stale assertion reach
master; no model run is necessary for this test/CI-only correction.

The final focused Linux command passed 83 checks in 19.283 seconds:

```sh
/tmp/home/sweep-venv/bin/python -m unittest tests.test_json_output tests.test_task_workspaces tests.test_ci_test_selection -q
```

Ruff lint and formatting checks also pass. The original failed assertion and
three missing-mapping failures were retained before the corresponding edits.
