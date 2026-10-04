# Where each stage may write: one owner instead of ten

A recurring bug class: a stage is told to write somewhere that another rule forbids, or a guard
rejects a write its prompt asked for. Seven recent bugs and fixes share it (#228, #301, #313, #320,
#327, #329, #332). Each fix changed one rule source and left, or created, a disagreement with
another: #320 added a second copy of the review prefix and the snapshot exclusions, which caused
#332; #337 and #309, both merged on 2026-10-04, disagree on the judging stages' sandbox.

`tools/autocode_stage_access.py` is the owner. Rules move into it one at a time, each with a test
that calls the code enforcing it (`tests/test_stage_access.py`).

## The rule sources (master at 25bbe23)

1. **Prompt text.** `autocode_support.COMMON`, `units/autoplanner.EVIDENCE_FACTS`, the receipt
   sentence in `providers/command.py` and `providers/opencode.py`, the Builder policy in
   `autocode_stage_context.py`, and each job's `PROMPT`.
2. **OS sandbox mode.** `units/common.launch_sandbox`; hard-coded modes in `autocode.py` (format
   correction), `autocode_format_correction.py` and `autocode_builder_worker.py`;
   `providers/codex_sandbox.py` (#309) for Codex.
3. **OpenCode permissions.** `providers/opencode.launch`: `edit` denied without write access;
   planning agents read-only; `external_directory` left at OpenCode's default (ask, which stops the
   run) for every other stage.
4. **After-stage snapshot.** `autocode.py` pauses a stage without write access whose revision
   changed; exclusions in `autocode_util.snapshot`.
5. **Native OpenCode snapshot guard.** `autocode_readonly_events` and
   `autocode_opencode_snapshots` (#354), keyed on role `terra` and the review stage.
6. **Per-job stray-write checks.** Each job's `check`, then `autocode_stray_writes.undo`.
7. **Handoff helpers.** `capture_command` is built in `units/autoplanner.py` (without `--mode`) and
   in `autocode_stage_context.py` (with it), and added again at the provider layer by
   `autocode_tool_handoff.py` (#309).
8. **Paths resolved against the current directory.** The capture CLI and `autocode_output_store`
   (#329).
9. **Recovery budget and advice.** `autocode_recovery_limits`, `autocode_resolver_runtime`,
   `autocode_run_setup`, `autocode_run_actions`.
10. **Permission answers.** `autocode_goals.resolve_permission` records a scoped permission
    answer; in #313 that answer never reached the launch sandbox.

## Disagreements found

| What | One side | Other side | Status |
| --- | --- | --- | --- |
| Design, design-check and discussion jobs told to make a scratch copy OUTSIDE the workspace | their `PROMPT`s | OpenCode stops on `external_directory`; the review and bug prompts had already been moved inside (`test_review_job`, `test_bug_job`) | **fixed here** |
| OpenCode guard's review prefix | `autocode_readonly_events.REVIEW_PERMITTED_ADDITIONS`, a hand copy (#345) | `autocode_review_job.ALLOWED_PREFIXES` | #354 replaced the copy with an import; **here** the guard and the job read `stage_access` |
| Judging stages' sandbox | #337: `workspace-write` for `sol`, `astra_review`, `astra_checkpoint` | #309: its narrow Codex write profile applies only to `read-only` launches, so it never reaches those three | both on master; open decision, below |
| `review_design` may change `review/` | `design_job.check` (now `JOB_WRITES`) | its prompt says never write; the OpenCode guard allows no additions for it | to decide |
| Resolver stages are handed `capture_command` | the `astra_review` handoff they reuse (`units/autoresolver.py`) | they launch `read-only`, so a Codex capture most likely cannot write its receipt | to confirm and fix |
| Where evidence goes | `COMMON`: `<run-directory>/evidence/`; providers: `.autocode/evidence/`; Builder: `run_dir/evidence`; `EVIDENCE_FACTS`: "the run's own directory under .autocode/" | #309's profile allows only `.autocode/evidence`, `.autocode/output` and the report | to unify after #309 |
| Snapshot exclusions | `autocode_util.snapshot`, `autocode_readonly_events`, `autocode_verify`, `autocode_investigation_workspace`, `autocode_job_source`, `units/autoplanner.RUNNER_OWNED_PARTS` | each other: six lists, four different | to unify (some differences are deliberate) |
| `docs/execution.md` | "The Plan Reviewer and Tester use the read-only sandbox" | `launch_sandbox` since #337 | to fix |
| `investigate_stuck`'s stray check | `stuck_job.check` | it launches without write access, so `autocode.py`'s revision pause fires first; the check never sees a repository change | to decide |

## Order of work

The open #347 owns `autocode_support.py`, `autocode_permission_recovery.py` and
`autocode_stage_recovery.py`; work that touches them waits until it merges.

1. **Done here:** the judging stages, each job's writable paths and OpenCode's permitted additions
   live in `stage_access`. `launch_sandbox`, `autocode_readonly_events` and the six job checks read
   them, and the three job prompts name `.autocode/scratch/<stage>/`.
2. Next: the judging stages' sandbox (the decision below), one `capture_command` (planning,
   execution and the provider layer), the resolver stages' helper or sandbox, `review_design`'s
   writes, `docs/execution.md`, then the evidence directory and the receipt sentence the providers
   add.
3. After #347 merges: the snapshot exclusions and the workspace-only scratch rule in `COMMON` and
   `autocode_stage_recovery` read `stage_access`.

## Open decision: the judging stages' sandbox

#337 launches the judging stages `workspace-write` and relies on the after-stage snapshot to keep
their source unchanged. #309 gives a read-only Codex launch a named profile that may write only the
report, `.autocode/evidence` and `.autocode/output`. The profile is the stronger guarantee: the OS
refuses a source write instead of the runner noticing it afterwards. But #309 applies it only to
`read-only` launches, and since #337 the three judging stages are not launched read-only, so on
master the profile does not cover the stages it was written for.

The options are to keep #337's model and drop the judging stages from #309's tests and docs, or to
launch the judging stages `read-only` again wherever the profile is available (Codex with
`codex_artifacts`) and keep `workspace-write` elsewhere. Either way, `stage_access` should say which,
so that the sandbox, the profile and the prompts read one answer.
