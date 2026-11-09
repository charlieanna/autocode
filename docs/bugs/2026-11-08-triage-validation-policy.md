# Keep triage validation out of the controller

The Requirements and report-format optimizations from PR #2974 reached master
through PR #2976 at `54930f9a0`. Their triage prerequisite also brought an
architecture failure: `autopilot.py` had 1,401 lines against its 1,397-line limit.
The size regression fails on that master revision.

Move the existing application-validation decision into
`autocode_requirements_triage.validate_application`, passing the schema lookup
and validator from the controller. Small answers remain subject to report-loader
validation and handoff checks. Drafts keep every required field and type; only
the triage dispatch allows the optional `ready` field. Other stages and saved
flows keep the strict full schema. The compatibility-normalized report is still
the value validated. No report, approval or evidence gate changes.

The controller is now 1,392 lines. Its ceiling is lowered to that value.

Verification on the candidate runtime:

- `python -m unittest tests.test_requirements_triage.ApplicationValidationTests
  tests.test_architecture`: 23 tests passed in 56.464 seconds on macOS. The
  subsequently added controller-rejection test and the four policy tests also
  passed together (5 tests).
- Supported Ubuntu container, ordinary UID 1000, no network or credentials:
  `python -m unittest -v tests.test_requirements_triage`: 24 tests passed;
  preparation and execution took 97.804 seconds. This included the five public
  fake-provider CLI paths. The later controller-rejection test is pure and was
  checked on macOS; no runtime changed after the Linux run.
- All eight changed executable lines were observed by a stdlib trace of the
  five validation tests. Eight targeted mutations were detected: stage and saved
  flow guards, small-answer routing, full-schema retention, validation removal,
  compatibility value, optional-ready handling, and controller delegation.
- Ruff lint and format checks passed over `tools/`, `tests/` and `scenarios/`.

No new live run was needed for this relocation. The earlier partial Kilo run and
its startup failure remain documented in
[the overhead qualification](2026-11-08-small-task-model-overhead.md). End-to-end
live delivery and a matched live cost comparison remain unverified.
