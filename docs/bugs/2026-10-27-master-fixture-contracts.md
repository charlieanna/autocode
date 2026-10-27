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

## Proof detector and returning-tab test contracts

The final verdict of run 40575058953 is 11,614 tests / 794 modules in
4,554 seconds, with three failing modules. The JSON refusal failure is fixed
in #2254. Two additional failures need separate fixture corrections.

`ProvenanceDefaults` supplied a two-argument framework-detector stub even
though regression proof now forwards its base revision. The exact TypeError
reproduces without launching a provider. The test now uses a real fixture
commit and requires the complete detector call: task worktree, selected project
venv (or explicit interpreter), and the bound base. It does not ignore unknown
arguments. Both proof producers explicitly retain this slow workflow consumer
in PR selection; the selector regression fails without that mapping.

The inventory terminal check already awaited a fresh public status after
switching left, but after switching right it waited only for a restored draft.
That cached display can precede the new question, which correctly archives
unbound input rather than treating it as an answer. The observed CI failure
never reached either intended held inventory read. A fast local baseline passed;
that is not evidence that the asynchronous path is safe. The fixture now holds
the returning tab's fresh status behind a signal gate, proves its cached draft
is visible while held, releases the response, and observes its unique new
status before sending commands. Cursor/draft preservation, responsive key/help
handling, no unauthorized task action and child cleanup assertions remain.
The signal is blocked before publishing an atomic ready marker, avoiding a
lost-wakeup or partially written PID race. No fixed sleep, timeout increase,
production authority or model change is introduced.

Focused final evidence: 64 pure/terminal/selector checks pass on macOS in
2.022 seconds, including the forced status hold (key 14.9 ms, help 23.5 ms).
The full affected bug-fix workflow and selector pass 68 checks on supported
Linux in 42.157 seconds. The existing Linux container lacks tmux and its
credential-switching restrictions prevent package setup, so Linux terminal
qualification is left to the required Ubuntu PR check, not claimed locally.
Ruff lint and formatting pass. No provider call or sandbox restriction changed.

## Adversarial scenario contracts after #2135, #2189 and #2065

Integrated master `49e3ecdde` passed all 11,629 unit tests (794 modules,
2,755 seconds), then failed seven scenario assertions in run
[40593561776](https://github.com/charlieanna/autocode/actions/runs/40593561776).
A fresh Linux checkout reproduces the three adversarial failures in 59.575
seconds; the four proof-order failures are retained in the CI log.

The evidence attacks invented a hash/revision and a never-dispatched task ID.
Since #2135 a current, admitted launch supplies runner-owned identity; arbitrary
copy errors are corrected and their raw reports retained. Those inputs no longer
exercise the stale-identity boundary. The replacement captures a real Tester
identity from a completed first turn, commits its delivered source, and starts a
scoped documentation edit in the same run. The second Tester replays that earlier
contract or task. The tests still require that injection reaches Tester, that the
old and current identities differ, and that the run cannot complete. No private
state is edited and no production identity guard changes.

The clarification test expected an earlier Investigator diagnosis to remain
current after a new answer. #2189 intentionally binds advice to its diagnosed
inputs, including saved answers. The fixture now produces a fresh source citation
after clarification and asserts the old guidance is absent, delivery completes,
and only one investigation was purchased. Its original bad citation still causes
the initial investigation, so a skipped recovery cannot satisfy the test.

The stock-refusal and hybrid scoring checks expected one failed proof before the
repair. #2065 deliberately re-executes a failed proof once to distinguish a flake.
The tests now require exactly FAIL, FAIL, PASS; both failures must use identical
source and the passing proof must use changed source. Existing exact stage-route,
Resolver-call, vacuous-test diagnosis and correct/incorrect scoring assertions
remain. No arbitrary duplicate allowance or extra retry is introduced.

These corrections exercise existing runtime decisions rather than changing
verification, approval, provider, timeout or sandbox policy. Live model calls
cannot establish deterministic fake attack coverage and are unnecessary for this
patch.

Final focused results on supported Linux: both historical-identity attacks pass
in 88.482 seconds; both stock-refusal scoring cases with source-identity assertions
pass in 80.010 seconds. The clarification case and both hybrid provider/environment
cases also pass. An intermediate two-turn fixture was correctly rejected by its
mandatory target-reached assertion (first missing tracked source, then a no-op
Builder); those failed attempts are retained rather than counted as protection.
The final fixture tracks the first delivery and supplies a genuinely changed
module docstring before replaying the historical identity. On macOS, all 22
launch-identity and planning-guidance unit controls pass in 0.694 seconds. Lint
and formatting pass. The broad scenario harness remains the required PR gate.
