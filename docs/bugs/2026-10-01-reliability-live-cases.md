# The three RELIABILITY.md live cases (first run, GLM/OpenAI profile)

Run 2026-10-01 from a clean worktree of master `68e89aa4` (after #196 and #198), profile
`glm53-openai` (producers GLM-5.3 on the Z.AI coding plan; Plan Reviewer/Builder/Resolver GPT-6
on OpenCode's ChatGPT login), provider `kilocode`, `--joint-planning`. Evidence directories are
under `.scenario-runs/` in the run worktree (not committed).

| Case | Scenario | Verdict | Oracle | Runner | Model time | CLI calls / answers |
| --- | --- | --- | --- | --- | --- | --- |
| Small new application | greenfield-todo-cli | HONEST_BLOCKER | 10/10 | WAITING_FOR_USER | 2205 s | 7 / 4 |
| Feature in an existing project | feature-timesheet-by-project | PASS | 6/6 | TASK_COMPLETE | 2362 s | 5 / 1 |
| Bug fix | bugfix-iso-weeks | ERROR (time budget) | 5/5 | PAUSED_INTERRUPTED | 3319 s | 4 / 1 |

No FALSE_COMPLETE in any case: all three deliverables were correct in the workspace when the
harness stopped. Judged by RELIABILITY.md's definition (agreed criteria **and** the complete
user flow), one case passed and two did not: the feature case completed; the greenfield case
never reached its completion gate (validator permission blocker); the bugfix case was
interrupted by the time budget mid-rework.

## What happened, per case

- **greenfield-todo-cli** built the correct CLI (oracle 10/10), answered two clarification
  questions and a plan approval, then the validator stage was stopped three times by OpenCode's
  `external_directory` permission (the validator referenced paths outside the workspace, e.g.
  `/tmp` evidence paths). AutoResolver exhausted the automatic-recovery budget and paused with
  the cause named and `--resume-paused --grant-recovery N` offered. Honest stop, operational
  cause, not model quality. Fix candidate: keep validator evidence paths workspace-contained
  (the stop message already states the rule), or run the validator through a provider whose
  sandbox allows the evidence directory.
- **feature-timesheet-by-project** completed end to end: plan approved, built, validated,
  completion gate satisfied, TASK_COMPLETE; oracle 6/6 with two report repairs along the way
  (planner and validator reports, both recovered automatically).
- **bugfix-iso-weeks** investigated, planned, built and validated the fix correctly (oracle 5/5
  at timeout), but after the first completion review AutoResolver scheduled a second builder
  task (rework loop), and the scenario's 60-minute harness budget expired during it. The fix
  itself was already correct in the workspace. The run needed roughly one more cycle; budget,
  not correctness, ended it.

## Interventions

- Two clarification answers and one plan approval per run were driven by the scenario driver
  (product decision boundaries, working as designed).
- The first launch attempt used the harness's committed `glm53` profile (all roles GLM-5.3) and
  paused at `PAUSED_CROSS_MODEL` on every scenario: the cross-model check compares model
  families, so no Z.AI-plan-only arrangement can satisfy it (GLM-5.3 vs GLM-5.2 is still family
  `glm`). That profile works only for 3-step routing checks and should be fixed or marked as
  such in `scenarios/harness/profiles.py`.

## Re-run of the two failed cases (2026-10-01, master fba6e738, same profile)

Evidence kept in this checkout's `.scenario-runs/` (durable this time).

- **bugfix-iso-weeks: PASS.** TASK_COMPLETE, oracle 5/5, one pass through the whole
  pipeline (investigate → plan → build → regression proof → validate → complete), 0
  report repairs, 0 rework, 3 CLI calls with no answers, ~23 min of model time. The
  130-minute budget was ample; the first attempt had only lacked time.
- **greenfield-todo-cli, attempt 2: HONEST_BLOCKER at requirements.** OpenCode exhausted
  its output token limit (finish reason: length) during `requirements_gather`; the
  provider normalization keeps the usage but refuses completion evidence
  (`providers/opencode.py`, `output_token_limit`), and AutoResolver paused with zero
  recoveries spent. Nondeterministic: the first attempt cleared this stage on the same
  profile. There is no output-token knob in the provider config.
- **greenfield-todo-cli, attempt 3: FALSE_COMPLETE.** TASK_COMPLETE, oracle 7/10 — the
  worst outcome, first one observed in these trials. Diagnosis (evidence
  `20261001T030616Z-greenfield-todo-cli-glm53-openai-tybvyqvb`):

  1. The brief pins the list format literally: ``ID TEXT [open|done]``.
  2. The Requirements stage's worked examples transcribed it without brackets:
     "stdout is exactly `1 buy milk open`". One transcription slip at the first handoff.
  3. The Builder implemented the criteria faithfully; `test_todo.py` asserts the
     criteria's format and passes.
  4. The Validator (different model family, per the cross-model rule) ran real commands
     against the criteria — 31/31 PASS, honestly: its contract is the criteria.
  5. The completion gate saw independent PASS on every criterion with pinned evidence
     and accepted. The oracle, which reads the brief, found `add_then_list`,
     `complete_marks_done` and `ids_stable_across_restarts` all failing on the missing
     brackets (`1 buy milk open` vs `1 buy milk [open]`).

  Every handoff preserved the criteria exactly as RELIABILITY.md priority 1 demands; the
  criteria themselves betrayed the brief, and **the brief-to-criteria transcription is
  the only handoff in the system with no independent check**. Plan review checks examples
  against their own rule (`EXAMPLE_CHECK_RULE`), the Validator checks code against the
  criteria, the completion gate checks evidence against the criteria — nothing re-derives
  the criteria's literals from the brief. Fix candidates: extend requirement tracing so
  every literal in the brief's I/O-format sentences must appear verbatim in some
  criterion's example (a mechanical check the runner can run), or give the Plan Reviewer
  a rule to re-derive each worked example from the brief text rather than from the
  criterion it is checking.

## Validation run after the fixes (2026-10-01, master f001e317, same profile)

greenfield-todo-cli: **PASS** — TASK_COMPLETE, oracle 10/10, all three previously failing
checks passing (`add_then_list`, `complete_marks_done`, `ids_stable_across_restarts`),
zero permission recoveries (the validator stayed in-workspace), zero report repairs,
5 CLI calls with 2 answers, ~44 min of model time, evidence
`20261001T061511Z-greenfield-todo-cli-glm53-openai-aisqo2ir`. The critical list-format
criterion carried the brief's literals ("stdout is exactly the two lines `1 buy milk
[open]` and `2 walk dog [open]`"). Caveat recorded honestly: one passing sample does not
prove the prompt rules caused the correct transcription; the failure mode was absent, not
provably prevented. The next sweep should watch both boundaries (criteria literals,
validator scratch) across repeats.

## Follow-ups worth doing

1. ~~Criteria-vs-brief verification~~ **Fixed 2026-10-01**: `BRIEF_TRACE_RULE` now reaches the Plan
   Reviewer (`astra_challenge`/`astra_finalize`), requiring every brief literal to appear verbatim in
   some worked example (`units/autoplanner.py`, test in `tests/test_test_cases.py`). Model-dependent:
   the next greenfield live run checks whether the reviewer catches it.
2. ~~Validator scratch paths~~ **Fixed 2026-10-01**: `VALIDATOR_NOTE` and the Reviewer's prompt now
   require scratch under `.autocode/` inside the workspace and forbid `/tmp`/`mktemp`
   (`autocode_check_replay.py`, `autocode_review_job.py`, tests in `test_check_replay.py` and
   `test_review_job.py`). The Reviewer's prompt previously said the opposite — "OUTSIDE the workspace".
3. `scenarios/harness/profiles.py`: `glm53` cannot pass a full run's cross-model check; either
   split families across providers or document it as routing-only.
4. Output-token truncation (`finish reason: length`) on OpenCode pauses the run with no retry and no
   configured ceiling; consider a capped automatic retry for it. Not fixed: the failure paths live in
   `autocode.py`, which is at its recorded line limit, and an identical-request retry may truncate
   again — the right shape (retry policy, output ceiling) needs its own decision.
5. ~~bugfix budget~~ **Fixed 2026-10-01**: `bugfix-iso-weeks` now carries `[run] timeout_minutes = 90`
   in its `scenario.toml`.
