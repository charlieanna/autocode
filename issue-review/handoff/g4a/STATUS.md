# WIP handoff: #3852, #3880, #3869 (+ the view half of #3954)

Remove this file before merging.

This branch is a snapshot of an AutoCode run. Claude usage ran out partway, so the run was paused at a stage boundary for handoff. The run is `20261120-151156-…-273c2bd5`, in a cloud container on 2026-11-20.

- Brief: `/mnt/project-files/issue-review/handoff/g4a/brief.md`.
- Approved plan (r12): `/mnt/project-files/issue-review/handoff/g4a/plan.md`, also on the issues.
- Shared cause: the status view decides each recovery offer with its own, shorter predicate instead of the executor's admission.

## Done
- **M1 is accepted by AutoCode** (fail-first proof, Tester and Completion Reviewer). It covers AC22, AC1, AC2, AC3 and AC20:
  - `tests/test_offer_admission_agreement.py` and `docs/bugs/offer-admission-agreement.md` survey every named offer.
  - #3852: the PAUSED_STAGE_ABANDONED resume offer and admission now share one contract-context predicate (`tools/autocode_operational_disposition.py`, `tools/autocode_operational_admission.py`, `tools/autocode_contract_identity.py`, `tools/autocode_resolver_human.py`).

## In progress, not yet accepted
- **M2 (#3869):** record the controller's full intervention capability at setup (`tools/autocode_run_setup.py`, `tests/test_pause_offer.py`, `tests/test_stop_now_runner_check_cli.py`). The Builder has run; the Tester had not checked it yet.
- **M3 source-edit offer (#3880):** the public offer now reads the same admission checks as the action. Added controls for source, settings, task, contract, scope, evidence and an existing diagnosis. The focused `tests.test_accepted_source.AcceptReviewedSourceTests` passes locally, and the new offer regression fails on master.
- **M3 stale check names (#3880):** failed dispatch bindings now record the named checks on the pending request and expose those names in the status view without bound values. Packet guard tests pass and fail on master. The compatibility path for saved settings bindings remains the same.
- The actual dispatch also retains those check names in a run event after the temporary request is removed. Read-only retry-authorization checks remain side-effect free; the existing owner test and the packet evidence tests pass. The offer survey and status contract documentation now describe the result.
- **M3 answered blocker (#3954 view):** the view now omits a plain resume at the same answered blocker frontier, explains the hold, and preserves feedback for an actual contract decision. The new regression fails on master and passes on the branch. The terminal chat also handles that existing feedback offer without requiring an unavailable resume command; 44 focused view and terminal tests pass.
- **M4 abandonment:** the status view and executor now read the same process-layer live-process check before offering or taking abandonment. A real-live-process regression fails on master and passes on the branch; a process that cannot be inspected stays held. The architecture check passes. The other M4 offers remain.
- **M4 job retry:** source and configuration checks moved to a lower-level shared admission module read by both the status view and executor. The stale-source and stale-settings regressions fail on master and pass on the branch. Existing changed-source tests now expect the withheld offer while still checking the executor's refusal. The focused job and admission suite passed 54 tests.
- **M4 repeated-failure retry:** the executor and view now share the same source, stage and recorded-failure frontier. Three stale variants fail on master and pass on the branch. The failure-retry, offer-agreement and recovery-view checks passed 37 tests, and the architecture check passed.
- **M4 serial Builder retry:** the view uses the executor's stopped-lane checks, approval guard and legacy-process check before naming either the card or next command. Draft contract, validation task, stale pending contract and already-retrying regressions fail on master and pass on this branch. The focused offer/view/architecture suite passed 60 tests; repository-scoped Ruff, formatting and mypy passed. The subprocess Builder suite passed five tests with process-enumeration access.
- **M4 report retry:** the executor's report retry admission is shared with the view. Source, contract, schema, pins and stage regressions failed on the original base and passed in the isolated implementation. Five offline CLI regressions passed there. The combined offer, report policy, failure retry, view and architecture suite passed 72 tests after integration; the combined offline CLI checks are running.
- **M4 parallel Builder retry:** the view and executor share worker, contract, saved-result and route admission. The approved-contract, completed-result, live-worker and mixed-member controls fail on master and pass on the branch. A refused combined selection has no fallback resume while other admissible member cards remain. The expanded offer, view, report policy, worker-quota, run-state, pause-command and architecture suite passed 149 tests. Ruff, formatting and mypy passed.
- In the actual VS Code Extension Development Host using this candidate extension and the offline real-core stopped-Builder fixture, the card displayed `TASK BLOCKED` and its admission reason with no invalid Resume action. After correcting the inconsistent status, the notification said the run was blocked and the status bar said “Task blocked” and “nothing needed from you”; no “Waiting for you” message appeared. Controls preserve genuine questions and feedback. The workspace’s original test CLI wrapper and previous host bundle were restored. The combined checks passed 61 Python tests and 763 extension tests (22 skipped); Ruff and mypy passed. The new regressions fail before the correction.
- On the master-merged branch, the handoff's eight-module suite passed 293 tests with one Linux-only skip; Ruff, formatting and mypy passed. The extension's automated suite passed 762 tests with 22 skips.
- The first master-merged CI run failed its extension-host setup waiting for a click on the History webview after it had navigated away. The failure screenshot shows the Runs list and requested run already visible. The setup now dispatches that view-switch action and waits for the resulting row; fresh CI must verify the correction.
- The eight-module suite named below passed 284 tests with one skipped before the stale-check change. The focused packet and source-offer tests passed after it. Ruff check and format and mypy for the two changed recovery modules passed.
- M2 setup and the controller writer now use one shared capability definition. Its unit and architecture checks pass. The resumed local-check Stop now test is Linux-only and was skipped on this Mac; Linux CI must establish its result.

## Left
- **M3 (#3880 and the #3954 view case):**
  - Complete independent review and integration verification of the shared source-edit admission and answered-blocker view. The failed check names and offer survey are recorded above.
  - Criteria: AC8 to AC19, AC21, AC23 to AC25.
- **M4:** the five offer families now use their executor's admission (AC26 to AC32); finish the combined branch verification and review.
- Current master was merged after the other requested PRs landed. Run the handoff's combined focused suite, repository Ruff and mypy on this merged head, confirm CI and review its exact diff before marking the PR ready.

## To continue
- **Same container, run intact.** Resume the run. If a stage hits the Claude quota, AutoCode pauses `PAUSED_BUDGET`. Switch that role's tool and model, then resume:

  ```sh
  autocode --run-dir RUN --workspace WS --answer route-ROLE=TOOL:MODEL --resolver-token -
  autocode --run-dir RUN --workspace WS --resume-paused
  ```

  See `docs/models.md#when-a-roles-quota-runs-out`.
- **Elsewhere.** Check out this branch. Finish M2 to M4 by hand or with another engine, following the plan's criteria, and run the plan's checks:

  ```sh
  python3 -m unittest tests.test_operational_disposition tests.test_pause_offer tests.test_accepted_source tests.test_stale_handoff_boundary tests.test_recovery_view tests.test_offer_admission_agreement tests.test_json_contract tests.test_taskrun
  ```

  Then run ruff, open the PR, and close the issues.

## Merged-tree follow-up

- PR #3972 merged as `4393e5ec98f9492e9b705fbc0a1ce94ddbb9e702`; its merged tree matched the independently integrated index. The ten-module focused suite passed 371 tests with one skipped in 326 seconds.
- The historical #3972 lint job encountered a baseline mypy failure introduced by #3982. Current-master corrected CI is still required; do not close related issues solely on that historical run.

## Live controller capability follow-up for #3869

The earlier #3972 Linux CLI job passed the resumed regression and immediate
pause test. Its overall required CI failed. The remaining cross-version case
now has a public-CLI regression on exact master `a6389bbb1`. It creates a real
birth-bound legacy owner, checkpoints it through the current generic writer,
and observes the false Stop Now action. The boundary Pause is correctly
available. The regression fails on master and passes with the follow-up.

The controller publishes immediate-interruption support on its own immutable
invocation receipt. The status projector checks the current live receipt
before offering Stop Now or Send Now. A legacy receipt, an uncertain process,
and a stale receipt each leave the boundary action available. Tests cover
invalid receipt values and keep the native CLI cancellation tests selected
when the controller changes.

Focused controller, intervention, architecture and state checks passed 116
with five Linux-only skips. CI selection passed all 181 tests. Ruff, format
and mypy passed (999 Python files). The existing resumed-check public CLI
acceptance also passed in a one-case native macOS run outside its default
Linux-only test selection. Log `/private/tmp/autocode-3869-native-mac-resumed-control.log`.

The actual macOS VS Code Extension Host opened the public run history for a
resumed held regression proof, clicked Pause, then clicked the still-offered
Stop Now. The disposable harness drove the original resume via the public
CLI, and VS Code sent both intervention requests. The real command and its
descendant exited, the status reached PAUSED_INTERVENTION, the attempt count
stayed at four, and no regression proof was accepted. The editor displayed
the stopped state and named continuation. The owned-process scan returned
empty. No model was used. The editor log is
`/private/tmp/autocode-3869-actual-editor.log`, and its owned artifact root is
recorded in `/private/tmp/autocode-3869-actual-editor-root.txt`. Hosted Linux
and required CI for the follow-up are pending.
