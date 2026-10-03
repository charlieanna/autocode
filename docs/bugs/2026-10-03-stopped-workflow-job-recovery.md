# Stopped workflow jobs keep their owner (#277)

A read-only Reviewer timeout previously moved to the build Completion Owner
and asked for approval of a reconstructed goal. Failed nonterminal workflow
jobs now retain their own stage and publish a technical pause with the archived
attempt, fragments, usage and source diagnosis. They never retry automatically.

`TaskRun.retry_job(needs["job_retry_token"])` authorizes one fresh attempt bound
to that run, stopped attempt, original source, saved route and limits. Plain
resume and stale tokens do not launch providers. Original dirty/untracked bytes
and modes are captured before admission; restoration uses the immutable stopped
witness. Later user edits and missing/corrupt captures block the old retry.

Verification on application commit `13bbdd03a1635bdbd1e2d38a2ff7deba6d9f1788`:

- All 24 new public TaskRun regressions pass; the changed gate passes 988 tests.
- On unchanged master `0a36c1e8`, the same selected behavior tests produce nine
  assertion failures, while the valid-review success control still passes.
- The full fake catalogue records 53 passes, one live-only skip and one
  `NOT_EXERCISED` case. Its missing Resolver coverage is not claimed as proof.
- The scenario harness passes 172 tests. A separate immutable checkout runs
  the complete suite; no full-suite pass is claimed while it is pending.

Native OpenCode checks use a synthetic integer-review fixture, one original
provider stage each, with a 180-second stage cap and unchanged source:

| Model | Observed result |
| --- | --- |
| GLM-5.3 | External-directory failure; paused as Reviewer with exact retry |
| MiMo-v2.6-pro | Actual 180-second timeout; paused as Reviewer with exact retry |
| GPT-6 Sol | Schema-valid review; `TASK_COMPLETE` |

All three delivered real model activity. Neither partial review is an approval.
On the actual paused GLM run, plain resume and a mismatched token add zero model
calls. Hash-bound runtime manifests and raw receipts remain in the ignored
`.scenario-runs/issue-277-autocode/` directory.

AutoCode's GLM Builder attempt timed out with an incomplete patch. Its OpenAI
Completion Owner requested rework; the corrective Resolver also timed out.
The operator completed and independently checked the correction, then used
AutoCode for the native model checks above. No complete native self-build is claimed.
