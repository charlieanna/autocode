# T03 — Sessions and model-report handling (SES-01..SES-12)

Executed 2026-09-24. 12/12 executed, 12 PASS, 0 environment blocks.
Command: `env -u AUTOCODE_TEST_CLI .venv/bin/python -m unittest tools.test_catalogue_t03`
Bundles: `.tmp-autopilot-testkit/artifacts/SES-*/NN/`.

| ID | Status | Before | Executed by / existing |
|---|---|---|---|
| SES-01 | PASS | full | `test_ses01...`; `test_planning` session assertions |
| SES-02 | PASS | partial | `test_ses02...` (provenance new); resolver flow in `test_findings_controller` |
| SES-03 | PASS | partial | `test_ses03...` (task/hash variants new); contract variant existing |
| SES-04 | PASS | partial | `test_ses04...` explicit prose-vs-structure case new |
| SES-05 | PASS | partial | `test_ses05...` (reconcile case new); repair queue in `test_report_repair` |
| SES-06 | PASS | full | `test_ses06...` via the real schema gate + unknown stage; existing `test_autocode.test_validated_final_rejects...` |
| SES-07 | PASS | full | `test_ses07...`; existing incomplete-turn/timeout tests |
| SES-08 | PASS | full | `test_ses08...`; FND-4 bundle + account_stage idempotency |
| SES-09 | PASS | full | `test_ses09...`; `test_report_repair.*` + FND-09 |
| SES-10 | PASS | gap | `test_ses10...` new: exit-0 report without evidence rejected transactionally |
| SES-11 | PASS | full | `test_ses11...`; `test_runtime_reports` + `test_command_flow` provider modes |
| SES-12 | PASS | gap | `test_ses12...` new: foreign session refused at reconcile; correct context resumes |

No product defects. Note (SES-06): `apply_result` trusts schema-validated
input by design; the enum gate runs in `load_stage_report`, so the case drives
rejection through the supported reconcile path, which is transactional.
