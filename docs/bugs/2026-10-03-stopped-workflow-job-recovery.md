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

Verification on application commit `a0c18e558b7dcc9198f7c38487dd8adb8fc59d89`:

- All 25 new public TaskRun regressions pass; the changed gate passes 989 tests.
- On unchanged master `0a36c1e8`, the same selected behavior tests produce nine
  assertion failures, while the valid-review success control still passes.
- The full fake catalogue records 53 passes, one live-only skip and one
  `NOT_EXERCISED` case. Its missing Resolver coverage is not claimed as proof.
- The scenario harness passes 172 tests. Before the final transport-binding
  correction, the complete suite ran 2,901 tests: one unchanged grader process-
  cleanup assertion failed. Its 39-test module and focused reruns pass; no
  full-suite PASS is claimed. The final correction passes the changed gate,
  complete fake catalogue and fresh native checks.
- Ubuntu CI exposed automatic Git maintenance racing the copied seed. Only
  that disposable seed disables automatic maintenance; all 25 regressions and
  the 989-test changed gate pass again. No assertion or application code changed.

Native OpenCode checks use a synthetic integer-review fixture, one original
provider stage each, with unchanged source. GLM/OpenAI caps are 180 seconds;
MiMo has an explicit 45-second cap:

| Model | Observed result |
| --- | --- |
| GLM-5.3 | Schema-valid review; `TASK_COMPLETE` |
| MiMo-v2.6-pro | Actual 45-second timeout; paused as Reviewer with exact retry |
| GPT-6 Sol | Schema-valid review; `TASK_COMPLETE` |

All three delivered real model activity. The partial MiMo review is not an approval.
On the actual paused MiMo run, plain resume and a mismatched token add zero model
calls. A saved joint-transport change fails before the correction and is refused
before another provider call after it. Hash-bound runtime manifests and raw receipts remain in the ignored
`.scenario-runs/issue-277-autocode/` directory.

AutoCode's GLM Builder attempt timed out with an incomplete patch. Its OpenAI
Completion Owner requested rework; the corrective Resolver also timed out.
The operator completed and independently checked the correction, then used
AutoCode for the native model checks above. No complete native self-build is claimed.
