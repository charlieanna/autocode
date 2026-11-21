# Status and failed-job retry observation

Draft; do not merge. Based on master `5de8cacfaee998c8cd7584d6664f5cf7032d43ee`.

## Done

- Failed-job status used to call the retry admission's Git/source snapshot directly. A slow Git or file read could hang the status request outside the existing shared observation deadline; a source-unavailable `Paused` exception could also escape status entirely. The status projection now asks the existing supervised, bounded status-observation worker to run the same admission, while the executor continues to revalidate independently. Timeout, uncertain cleanup, malformed output and source observation errors hold the retry.
- Repeated-failure retry status also performed an unbounded source frontier scan. It now uses the same supervised budget and avoids reintroducing a refused retry through the pause's fallback flags. Real Git positive/timeout controls pass and the timeout assertion fails on master (`/private/tmp/autocode-repeat-status-base.log`). The execution path still revalidates independently.
- A blocked retry no longer contains an actionable model-answer hint when the same stale source would make the route answer fail. The route question remains visible and, on successful admission, actionable.
- Deterministic real-workspace timeout and missing-source regressions fail on the base and pass here. A real Git end-to-end retry test checks stale source, unchanged protected state, restored source and a successful retry. Existing worker tests exercise timeout and uncertain cleanup. Logs: `/private/tmp/autocode-status-retry-base-behavior.log`, `/private/tmp/autocode-status-retry-base-source.log`.
- Repository Ruff check and format (`tools/ tests/ scenarios/`) pass; mypy `tools` passes.

## Remaining before readiness

- All 15 failures and three errors in eight modules from #3991 reproduce on both its original base and current master (`/private/tmp/autocode-3991-fullcompare-{0,1}.log`). The first candidate run retained 15 failures and two errors (`/private/tmp/autocode-status-retry-candidate-supervised.log`). Projection-only fixtures now explicitly supply a successful admission observation; real source/token and route integration controls still execute their real checks. The corrected 31-test projection subset and two real Git integration cases pass. Full revised suite is in progress at `/private/tmp/autocode-status-retry-revised-suite.log`; inspect the outcome and fix any remaining regressions without weakening admission.
- Verify older saved route corrections where the model configuration already differs: `job_failure.reroute` can accept a model change that ordinary retry admission refuses, so the source-and-route decision must preserve any genuinely actionable question without granting a stale retry.
- Verify actual VS Code Extension Development Host status timeout and recovery on the final head; a CLI/worker test alone is not editor verification. Restore the approved test workspace and wrapper afterward.
- Review exact diff, get exact-head `ci-required` green, and resolve any upstream merges before merge. Keep the follow-up draft until all checks and the editor observation pass.
