# Status and failed-job retry observation

Draft; do not merge. Based on master `5de8cacfaee998c8cd7584d6664f5cf7032d43ee`.

## Done

- Failed-job status used to call the retry admission's Git/source snapshot directly. A slow Git or file read could hang the status request outside the existing shared observation deadline; a source-unavailable `Paused` exception could also escape status entirely. The status projection now asks the existing supervised, bounded status-observation worker to run the same admission, while the executor continues to revalidate independently. Timeout, uncertain cleanup, malformed output and source observation errors hold the retry.
- A blocked retry no longer contains an actionable model-answer hint when the same stale source would make the route answer fail. The route question remains visible and, on successful admission, actionable.
- Deterministic real-workspace timeout and missing-source regressions fail on the base and pass here. A real Git end-to-end retry test checks stale source, unchanged protected state, restored source and a successful retry. Existing worker tests exercise timeout and uncertain cleanup. Logs: `/private/tmp/autocode-status-retry-base-behavior.log`, `/private/tmp/autocode-status-retry-base-source.log`.
- Repository Ruff check and format (`tools/ tests/ scenarios/`) pass; mypy `tools` passes.

## Remaining before readiness

- Classify and correct the status/retry contract fixtures from #3991's historical CI. All 15 failures and three errors in eight modules reproduce on both its original base and current master; `/private/tmp/autocode-3991-fullcompare-{0,1}.log`. Full candidate suite is in progress at `/private/tmp/autocode-status-retry-candidate-supervised.log`. Do not weaken source/token gates to make synthetic fixtures pass.
- Verify older saved route corrections where the model configuration already differs: `job_failure.reroute` can accept a model change that ordinary retry admission refuses, so the source-and-route decision must preserve any genuinely actionable question without granting a stale retry.
- Verify actual VS Code Extension Development Host status timeout and recovery on the final head; a CLI/worker test alone is not editor verification. Restore the approved test workspace and wrapper afterward.
- Review exact diff, get exact-head `ci-required` green, and resolve any upstream merges before merge. Keep the follow-up draft until all checks and the editor observation pass.
