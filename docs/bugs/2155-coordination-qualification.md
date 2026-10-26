# Automatic TUI coordination: qualification for #2155

The terminal chat delegates multi-repository bugs to a core coordinator over
TaskRun and public status. The acceptance fixture starts with a checkout symptom,
discovers history and then tax, runs real Python code across those interfaces,
and publishes to an offline GitHub server backed by real Git remotes. Only the
provider answers and GitHub transport are fixtures. No live-model discovery
recall or real GitHub-account integration is claimed.

The user supplies `/repos` setup, the symptom and exact plan approvals. There is
no injected registry file, manually supplied integration command, model launch
from the client, or completion waiver. The second case injects a locally green
but incompatible history implementation and a lost response after PR creation.
Both cases also change the generated joint test after publication and require
the public completion claim to be withdrawn.

## Commands

From the repository root, using the project venv interpreter:

```sh
PY=.venv/bin/python
$PY scripts/run_linux_fake.py --unittest tests.test_coordination_tui_e2e.CoordinationTuiEndToEnd.test_symptom_discovers_transitive_repositories_and_publishes_only_jointly_verified_fixes
$PY scripts/run_linux_fake.py --unittest tests.test_coordination_tui_e2e.CoordinationTuiEndToEnd.test_locally_green_contract_mismatch_is_repaired_before_any_pr
$PY scripts/run_linux_fake.py --unittest tests.test_coordination_tui_e2e.CoordinationTuiEndToEnd.test_combined_suite_preserves_existing_cases_and_proves_the_joint_regression
$PY scripts/run_linux_fake.py --unittest tests.test_verify_task.VerifyCliTests
```

These run in the supported Ubuntu container with provider session ownership and
tool containment admitted before testing. Durations below are unittest wall
times, excluding the cached container build.

The final runs used the same `docker_run_prefix` and a separate preflight process,
copying the runtime and tests onto the container's local filesystem to reduce
bind-mount overhead. The copied bytes were checked against the source inventory
before launch. The actual launcher was
`$PY /private/tmp/2155-linux-local.py SELECTOR`; its scripts and file inventory
are retained under ignored `.scenario-runs/coordination/`. The tracked commands
above run the same selectors and boundaries directly against the checkout.

## Development failures retained

The failed attempts remain under ignored `.scenario-runs/coordination/`; no run
output is tracked. Attempts 1–7 used the module selector
`--unittest tests.test_coordination_tui_e2e` when it contained the normal-delivery
case. Attempts 8–10 use the first exact selector above. Attempt 11 uses the
second exact selector.

| Attempt | Seconds | Failure and correction |
| --- | ---: | --- |
| 1 | 14.570 | Fixture Investigator note used an invalid location; moved it to `docs/bugs/`. |
| 2 | 181.296 | Fixture counterexample used exact floating-point equality and omitted source references; corrected both. |
| 3 | 197.883 | Fixture finalizer omitted the retained-contract protocol. |
| 4 | 183.331 | Fixture finalizer sent a full contract where the schema requires its retained reference. |
| 5 | 175.813 | Fixture nested output contained a field outside its active schema. |
| 6 | 402.244 | All members completed; integration Requirements omitted instructions from its source quotation. |
| 7 | 273.061 | Invalidated development run: a new runtime module was added while the checkout was mounted. Subsequent full-flow runs freeze runtime files. |
| 8 | 451.968 | All members completed; integration Planner omitted requirement R1's trace. |
| 9 | 615.353 | Independent proof correctly refused copied local tests with invalid imports and an absent baseline test directory. Made fixture tests portable, added an empty baseline test package, and added bounded automatic integration replanning on independent failure. |
| 10 | 1053.217 | All three bounded joint attempts remained unverified: the root-only suite had no existing passing baseline tests. The coordinator now replays the approved combined suite; fixtures include existing behavior tests in the original repositories. No PR was published. |
| 11 | 705.325 | The mismatch was detected before publication, but the follow-up receipt compared submitted text with the augmented task. Reconciliation now checks the exact public chat message and turn. |
| 12 | 1055.163 | Copied tests with identical filenames collided during counterfactual pytest collection. The fixture now supplies a replayed `conftest.py` import-mode helper; all three attempts retained their failure and published nothing. |
| 13 | 748.415 | The history correction's trailing newline differed from the core's trimmed follow-up receipt. Compare the input's canonical trimmed text. |
| 14 | 740.997 | Independent proof passed; publication reread the returned receipt-file path through a directory-only API. The reader now accepts its own public `record` reference as well as a directory. |
| 15 | 949.254 | Interrupted after retaining the fixture, before reaching the already diagnosed publication-path refusal. |

Container-local development attempts subsequently found Git's rejection of the
broad staging pathspec (291.868 s; replaced with the shared issue adapter's
literal source inventory), a no-op implementation planned for correct peers
(456.096 s), and a fixture Planner reading the task reference instead of its
source-inventory feedback (459.581 s). Correct peers now use ordinary validation
tasks. No completion or publication guard was waived.
The corrected validation path then exposed a fixture that invented a new session
ID on native resume (446.655 s). The fixture now returns the requested resumed
session ID, matching the existing native protocol; the uncertain-call gate was
retained.

The short combined-suite regression first exposed pytest cache files left by
independent review (8.565 s). Disabling the cache exposed an overly specific
test assertion with a mixed-framework suite (15.066 s). Giving the composed
fixture an explicit pytest configuration produced complete per-test receipts;
the regression passed in 16.566 s.
Adding the copied member tests to that focused regression reproduced the
duplicate-module problem. With the replay helper, all seven cases collect and
the baseline/candidate proof passes (13.065 s).
The local-filesystem launcher's first setup check inherited its preflight
seccomp filter and failed before provider launch (1.044 s). Running preflight in
its own process, as the tracked launcher does, corrected the setup. A restored
fixture probe also refused inherited Git overrides; using the acceptance test's
clean Git environment allowed authenticated publication and lost-response
recovery to complete without another model call.

The verifier was not weakened. A missing/import-failing test cannot stand in for
a reproduced application defect, and a candidate test failure cannot publish.

## Focused checks

| Check | Result |
| --- | --- |
| Fresh TUI symptom → transitive discovery → three fixes → joint proof → three linked PRs, reopen and changed-input rejection | Passed, 356.441 s |
| Fresh TUI incompatible contract → member correction/reapproval → validation-only peers → second joint proof → lost PR response → reopened chat → three unique linked PRs | Passed, 638.308 s on master base `0b7c5a73b` |
| Coordination, architecture, run-state and report identity (`tests.test_coordinate tests.test_architecture tests.test_run_state tests.test_report_identity`) | 59 passed, 21.911 s |
| Chat conversations/history/setup/tabs, TUI and panel | 154 passed, 1.863 s |
| Standalone verification CLI in Linux | 8 passed, 103.682 s |
| Combined-suite baseline/candidate proof in Linux, including duplicated local module names | 1 passed, 13.065 s |
| Shared delivery staging, coordination guards and architecture | 52 passed, 19.537 s |
| After refreshing to master `0b7c5a73b`: coordination/delivery, architecture, run state, selection and check-receipt consumers | 115 passed, 25.015 s |
| Combined proof and returned-record reread on the refreshed base | Passed, 9.072 s |
| Existing GitHub client/review and secret-redaction consumers | 17 passed |
| Control-token transport and withholding | 8 passed |
| Suite and CI test selection | 99 passed, 2.458 s |
| Changed Python lint/format and focused mypy | Passed |
| Strict documentation build | Passed |

The final fault-flow runtime/test snapshot has SHA-256
`86e7583b28a30b3543a5af1b61a5953bc4ae9aa2c3ccf45ee1d9f3fdada17355`.
Both full flows require original product checkouts to remain unchanged, preserve
the selected role routes and limits, retain dependency merge order, and withdraw
completion after the generated joint test changes. Publication recovery was also
replayed from a retained, already verified fixture through the public TUI: three
authenticated delivery commits, a lost response and reopened chat, and no second
PR creation.

The master baseline introduced four unannotated run-state parameters in
`autocode_report_identity.py`; annotating them restores the existing ratchet
without changing behavior. An earlier macOS batch also selected a supervised
provider test, which host admission refused, and a platform-specific PATH
expectation. Those results are not Linux qualification; the relevant CLI and
full-flow checks run in Linux above.

PR #2169's first CI lint run found that the report loader can call `adopt` with
no run state. Its existing guard already handles that case; `adopt` and `bind`
now declare `RunState | None`. The exact CI type command (`mypy tools/`) passes
all 674 source files, and the report-identity/run-state regressions pass all 25
tests. This changes annotations only, so the behavioral flow evidence above
remains applicable.

The retained Ubuntu log also exposed `RunViewTests.test_contract_fields`: its
exact public-field inventory omitted the new `coordination` and `related_profile`
fields. The regression failed locally before updating that inventory; all 27
public status-view tests pass afterward. No runtime behavior changed.

The next Ubuntu gate on `05c560d26` ran 4,093 tests in 238 modules in 923 seconds
and exposed five remaining failing modules. The status JSON schema now requires
and describes the coordination/profile fields and scope identity; the scope CLI
assertion binds that identity to its displayed question. The CLI reference now
lists every new public option. A populated-view schema regression checks actual
coordination output, including the active member's string identity.

The terminal resize assertion previously accepted tmux's blank intermediate
frame before curses redrew it; it now waits for the header and composer as well
as the new dimensions. The empty-chat resize check passes in a private tmux
terminal without a provider launch (1.685 seconds).

CI invokes its venv interpreter without activating the venv. The multi-repository
fixture now puts that interpreter's bin directory on PATH so its actual
`python3 -m pytest` checks use the installed test dependency. Reproducing CI's
inactive-venv PATH made the short joint proof fail in 3.053 seconds before the
correction and pass in 8.559 seconds afterward. The supported Linux status/JSON
contract (3 tests, 39.076 seconds) and scope-question CLI (3 tests, 28.908 seconds)
also pass; the CLI-reference tests and changed-file Ruff checks pass locally.
The same-byte container-local launcher is `/private/tmp/2169-ci-linux.py` and
adds only the CI-like PATH to the existing Docker boundary. Initial snapshot
setup attempts omitted harness/schema resources and were unqualified; the
passing CLI checks use all tracked ordinary files. No runtime or provider gate
was weakened, and all source corrections were authored directly.

On `c2e6184b0`, Ubuntu ran 4,094 tests in 238 modules in 1,193 seconds. All
three multi-repository end-to-end tests passed together (601.7 seconds); the
only failing module was the existing terminal inventory test. It mistook a
restored tab's cached header/prompt for a fresh status and sent the next switch
before the current read arrived. Its controlled public-CLI fixture now emits a
per-read notice, and the test waits for that fresh displayed status before
switching again. The private-tmux regression passes in 1.159 seconds, retaining
the held-read, draft/cursor, two-opened-status and child-cleanup assertions.
