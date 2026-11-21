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
- **M3 answered blocker (#3954 view):** the view now omits a plain resume at the same answered blocker frontier, explains the hold, and preserves feedback for an actual contract decision. The new regression fails on master and passes on the branch. The terminal chat also handles that existing feedback offer without requiring an unavailable resume command; 44 focused view and terminal tests pass.
- The eight-module suite named below passed 284 tests with one skipped before the stale-check change. The focused packet and source-offer tests passed after it. Ruff check and format and mypy for the two changed recovery modules passed.

## Left
- **M3 (#3880 and the #3954 view case):**
  - Verify the saved stale check names reach the final evidence and update the offer survey.
  - Complete independent review and integration verification of the shared source-edit admission.
  - Finish integration verification of the answered-blocker view and update the offer survey.
  - Criteria: AC8 to AC19, AC21, AC23 to AC25.
- **M4:** abandon, retry_job, retry_report, retry_builder (serial and parallel member) and retry_failed_stage each read their executor's admission (AC26 to AC32).

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
