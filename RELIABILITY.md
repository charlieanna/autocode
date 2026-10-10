# Reliable completion first

Product direction agreed on 2026-09-21: use Autocode as the default workflow for
defining tasks and completing projects. Prioritize making that existing workflow
dependable. Add features only when the user identifies a need and agrees to their
scope. This document records development priorities; it does not change runtime
approval rules or authorize implementation in another project.

## What to improve now

1. Preserve the agreed outcome, scope, and acceptance criteria through every handoff.
2. Finish the approved work with independent evidence from the current code. A
   successful provider exit, changed files, or elapsed time does not mean completion.
3. Recover from interruptions without losing accepted work, repeating completed
   actions, duplicating messages, or treating a resume as plan approval.
4. Show one understandable current state and the exact next action. Distinguish a
   user decision from an operational failure and explain what a retry will do.
5. Apply human feedback at the supported safe boundary, retain its receipt, and
   make any resulting plan revision explicit.
6. Stop bounded work with a useful explanation when it cannot progress. Do not hide
   failures or repeatedly spend the execution budget without new evidence.

Prefer reproducible bug fixes and regression coverage over new controls, new
providers, parallel scheduling, or additional integrations. Those are deferred
possibilities, not a committed feature backlog.

## Evidence required

Use the existing Autocode CLI and browser flows to exercise planning, approval,
implementation, failed verification and rework, feedback, interruption, resume,
human review, and completion. Test isolated fixtures first so failure cases do not
damage user projects. Keep fake-provider results separate from live-model results.

Then establish delivery quality through three bounded real-model cases: a small new
application, a feature in an existing project, and a bug fix. Each case needs a
concrete agreed scope before execution. Reuse existing projects when suitable;
do not start arbitrary work just to populate this checklist.

For each case record:

- The agreed outcome, run identity, code revision, and acceptance results.
- Whether the complete user flow worked, including relevant failure cases.
- Whether pause/restart/resume preserved work and kept approvals correct.
- Which interventions were product decisions and which were repairs to Autocode.
- Remaining blockers, elapsed execution time, and reported usage where available.

A case passes when its agreed criteria and complete user flow pass, required human
reviews are satisfied, and the dashboard reflects the saved result. Missing evidence
stays unverified. Claims of dependable project completion require these live trials;
passing fixture tests alone does not establish model effectiveness.

On the default profile (2026-10-07, master 4e7e6895, profile `glm53-openai`: GLM-5.3 producers, GPT-6
verifiers, one run of each case; details in docs/bugs/2026-10-07-default-profile-reliability-cases.md):
all three deliveries were correct and none completed falsely. The bug-fix and to-do cases completed
with no person (18 and 35 minutes, no Validator report sent back). The feature case stopped honestly:
the Requirements stage had derived an exact-output example from the brief's layout rule and miscounted
its padding, the Plan Reviewer approved it, and the Resolver proved the contradiction and asked (#676).
The person's approval of the Resolver's own recommendation then had no named path: the view pointed to
`--answer`, which the CLI refused; `--resolver-response` was accepted and held forever; `--edit-goal`,
which finished the run (oracle 6/6), was named nowhere (#675).

On Claude models (2026-10-04, master e8366ad, profile `claude-tiers`, three runs of each case;
details in docs/bugs/2026-10-04-claude-reliability-cases.md): 8 of 9 runs passed and none completed
falsely. The feature and bug-fix cases passed 3 of 3. The to-do case passed 2 of 3: one run stopped
with correct code because the Validator kept citing event IDs, which a report-file provider rejects;
its generic instructions said to, and they now defer to the provider's receipt rule. In three re-runs
of the to-do case on that fix, no report was rejected for event IDs and 2 of 3 passed. The third
stopped honestly because AutoResolver wrote a plan check with a note in parentheses that the runner
replayed as a shell command. The runner now leaves such a line to the Validator as prose; no live
re-run has confirmed it yet.

Re-run of the two failed cases (2026-10-01, master fba6e738, same profile, 90/130-minute
budgets; same note for details):

- Bug fix (bugfix-iso-weeks): **passed** — TASK_COMPLETE, oracle 5/5, one pass through the
  whole pipeline (investigate → plan → build → regression proof → validate → complete), no
  repairs, no rework, no interventions. The first attempt had only lacked time.
- Small new application (greenfield-todo-cli): **failed as a false completion** — the run
  reported TASK_COMPLETE, oracle 7/10. A second attempt had stopped earlier at requirements
  (OpenCode output-token truncation, nondeterministic). Root cause of the false completion:
  the Requirements stage's worked examples transcribed the brief's literal `ID TEXT
  [open|done]` output format without the brackets; the Builder, tests, Validator and
  completion gate then all worked from the corrupted criteria, each honestly. The
  brief-to-criteria transcription is the one handoff with no independent check.

After the fixes (prompt rules for criteria-vs-brief tracing and in-workspace scratch, the
bug-fix budget, all in commit f001e317), the greenfield case **passed** on the same profile:
TASK_COMPLETE, oracle 10/10, no permission recoveries, criteria carrying the brief's
literals. One passing sample does not prove the prompt rules caused it; the next sweep
should watch the same boundaries. Standing tally after the fixes: all three cases pass end
to end; the one observed false completion came through the requirements boundary, and its
guard is now model-dependent — verified by live runs, not mechanically. Since 2026-10-04 one part is
mechanical (`tools/autocode_brief_literals.py`): a planner draft that drops a literal the brief writes in
backticks goes back to the planner. Whether every worked example agrees with that literal is still the
Plan Reviewer's check.

First run of the three cases (2026-10-01, master 68e89aa4, profile glm53-openai:
GLM-5.3 producers on the Z.AI plan, GPT-6 verifiers on OpenCode's ChatGPT login;
details in docs/bugs/2026-10-01-reliability-live-cases.md):

- Feature in an existing project (feature-timesheet-by-project): **passed** —
  TASK_COMPLETE, oracle 6/6, two report repairs recovered automatically.
- Small new application (greenfield-todo-cli): **not passed** — the deliverable was
  correct (oracle 10/10) but the run never completed: OpenCode's external_directory
  permission stopped the validator three times, the recovery budget ran out, and the
  run paused for a person. Blocker recorded; validator evidence paths must stay
  workspace-contained.
- Bug fix (bugfix-iso-weeks): **not passed** — the fix was correct (oracle 5/5) but the
  scenario's 60-minute budget expired while AutoResolver's rework loop was still
  running. Budget, not correctness, ended it.


## First interaction latency target

The initial target applies only to the `native-glm53-latency` greeting workload:
a standard-library `greet.py` CLI with one blocking punctuation question, joint
and adaptive planning, native OpenCode 1.18.33 and all model-backed roles on
`zai-coding-plan/glm-5.3`. Requirements and Builder use medium effort; Planner,
Plan Reviewer and Resolver use high effort. Active/stage/idle budgets are
5,400/1,200/300 seconds and each public invocation has a 3,900-second timeout.
The qualification driver answers the question and approves the displayed plan;
those actions remain in wall time. Imports precede the equally measured CLI
main-entry boundary. Builder means owned job dispatch, not model acceptance or
first token.

For the same workload and profile, target first question within **360 seconds**,
approval-ready plan within **720 seconds**, and first Builder dispatch within
**840 seconds**. A later comparable sample above a target is a latency regression
to investigate as a bug, retaining provider, model, host and human-wait evidence.
These are initial observational targets from one paired sample, not a measured
population percentile, a guarantee, or a target for the default profile.

On 2026-10-10 the frozen #727 candidate on base `e18cf660` saved first question,
plan and Builder dispatch at **291.251412 / 563.112154 / 655.162113 seconds**.
The baseline's external observations were **415.238558 / 1016.650956 /
1111.220591 seconds**; legacy baseline state was not assigned new timing fields.
New-run native roster calls fell from two to one: the extra baseline call took
6.359634 seconds. Model responses and report repairs also differed, so the full
latency difference is not attributable to that one removed wait. Both trials
passed a real Builder, the greeting oracle and delivered tests, then stopped at
`PAUSED_REQUESTED`; neither is a completed-delivery sample. See the
[dated measurements](docs/bugs/2026-10-10-first-interaction-latency.md) and
[recorded candidate timestamps](docs/reliability-table.md).
