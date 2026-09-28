# Workflow: planning, approval, and handoffs

[← Back to README](../README.md)

This page covers the end-to-end conversation flow: how a rough idea becomes an
approved plan, how that plan is implemented and reviewed, and what Autopilot
coordinates along the way.

## The flow

Start with a rough idea and discuss it with the Requirements Planner. It helps define the smallest
useful end-to-end product, asks focused questions, and drafts a versioned build brief.
The Plan Reviewer challenges that draft; the Requirements Planner revises; the Plan Reviewer finalizes. You can revise the brief
in the same conversation. Implementation starts only after you explicitly approve it.

After approval, Autocode handles the handoffs:

```text
You → Autopilot: recognize the kind of job (build, bugfix, review, design, discuss); saved as `workflow`,
   printed with its reason and signals; `--workflow KIND` names it instead of recognizing it
   review → Reviewer only: findings written to review/findings.json, repository untouched, run complete
   bugfix → Investigator first: diagnosis written to docs/bugs/<name>.json, repository untouched;
            not reproduced → run complete;
            reproduced, small (and you did not ask to approve the plan) → one Builder task built from the diagnosis (invariant = the acceptance
              criterion, a regression test that fails before the fix), approved under a recorded policy
              instead of by you (approval actor "workflow_policy"), then Validator and Completion Owner;
            reproduced, large or you asked to approve the plan → the build pipeline below from the Planner on, planned from the
              diagnosis: no requirements gathering, but plan review and your approval
   design → Architect first: a design review is written to review/design-review.json (goals met,
            blocking/advisory concerns, questions for you), repository untouched, run complete;
            a request for a NEW design → the build pipeline below
   discuss → Analyst only: an answer with evidence tied to repository files (and the note the
            request asks for, written by the runner), repository untouched, run complete
   build → the build pipeline below;
           implementing an APPROVED design document as written → Architect checks it against the
             repository first, repository untouched:
             conflicts (a frozen API, a documented invariant) → written to <design>.blockers.json,
               run stops (PAUSED_DESIGN_CONFLICT), nothing built, you decide;
             no conflicts → the design's binding decisions become a constraint and the pipeline starts
               at the Planner (no requirements gathering; the Planner may not redesign or ask)
You → Requirements Gatherer: rough idea → saved requirements report
Requirements Gatherer → Planner: draft task DAG
Planner → Plan Reviewer → Planner revision → Plan Reviewer final → your approval

Autopilot → AutoCode task scheduler → independent Builders → combined validation

Builder → Validator → Completion Owner
   ↑                       |
   └──────── REWORK ───────┘
```

The Plan Reviewer owns final planning decisions. On new joint runs, the runner-owned
Autopilot dispatches AutoCode to schedule independent milestone Builders after approval, falling back
to one Builder when work cannot safely run in parallel. Each Builder implements one bounded
task. A separate Validator independently inspects and tests the combined code, then the
Completion Owner proposes completion or rework for Autopilot to evaluate. They all work
from the same approved brief; you do not explain the product to each agent or relay
their prompts. The runner saves decisions, tasks and evidence so it can resume.

## Four units controlled by Autopilot

The same repository contains four callable units:

| Unit | Owns | Handoff |
| --- | --- | --- |
| Autoplanner | Separate requirements, planner, and plan-reviewer sessions | User-approved contract and task DAG |
| Autocode | Next-task planning, parallel Builders, implementation and integration | Build candidate with source revision |
| Autoreview | Independent validation and completion-owner review | Evidence-backed review result |
| Autoresolver | Read-only diagnosis of reviewer-requested rework | Evidence-linked, bounded repair DAG and retests |

`autopilot` is the deterministic workflow controller in `tools/autopilot.py`. It calls
all four units, consumes their results, selects the next stage, coordinates recovery,
pauses for approval, and enforces the final completion gate. Unit reports are proposals;
Autopilot checks them against the approved plan and current evidence before advancing.
Planning revisions, implementation handoffs, review outcomes and repair routing are
applied by Autopilot. The shared runtime supplies provider calls, locking and persistence.

The existing `autocode` and `autocode-orchestrator` commands delegate to the same
controller, including dashboard launches. `tools/autocode_orchestrator.py` is a
compatibility import. The saved stage named `orchestrator` remains the milestone
scheduler inside Autocode so existing runs can resume. Autopilot owns the overall loop.

Each unit command stops successfully before dispatching another unit. Clarification,
approval and blockers keep their existing explicit checkpoints. `autocode --unit
autoplanner|autocode|autoreview|autoresolver` provides the same selection; omitting it runs all units.

Unit implementation lives in `tools/units/`. All four share a run directory and its
authoritative `state.json`; they preserve separate model roles and sessions. Versioned
JSON handoffs are saved under `handoffs/<unit>/<hash>.json`, with paths in the state's
`unit_handoffs` field: approved plan, build candidate, and review result. These exports
are inspectable snapshots, not standalone authorization tokens. Execution still checks
the saved approval and current evidence; editing a handoff cannot approve a plan or pass
a review. Existing runs acquire handoffs when they next progress, without migrating
their approval or replaying completed stages.

In the standard workflow, a reviewer's `REWORK` decision routes to Autoresolver,
then back to AutoCode and independent review. Autoresolver inherits the Plan Reviewer model
configuration but has its own saved session. It cannot implement, approve or complete
a task. Its first version emits one bounded repair task (a one-node DAG with
`depends_on: []`), preserving integrated-batch accountability. It pins the reviewed
source, task, contract and validation evidence; stale inputs pause instead of launching
a writer. Requirements or permission changes still require a user decision and, where
applicable, a revised approved plan.

Transport/report-format recovery and safety pauses remain runner-owned. Existing
explicit alternate workflows retain their configured routing; this does not override
their final-audit-only policy or automatically retry failed integration operations.

## Operational diagnosis

An operator can request the separate read-only `astra_diagnose` stage after a
recorded repeated Builder report failure:

```sh
autocode --workspace /path/to/project --run-dir /path/to/run --resume-paused --diagnose-failed-stage
```

This is an alternative to `--retry-failed-stage`, not an automatic handler for every
pause. Admission requires the approved task, stopped workers, current source and
intact pinned evidence; unresolved user decisions and hard budgets still stop it.
Repetition may stop a repair path before its configured repair allowance is exhausted.

Each fresh diagnostic provider attempt receives a durable reservation at final launch
admission. Timeout replacements, invalid-output retries, new blockers and new iterations
consume new reservations. Reloading the same attempt does not consume another, and an
ambiguous launch is not refunded. The default run-level diagnostic allowance is two;
explicit resume does not reset it. Report-only format repair has its separate existing
allowance and remains subject to the parent's time/token limits. The diagnostic limit
is not a count of every internal model/tool step or separately budgeted report repair.

The model can recommend a bounded retry or escalation, not grant permissions, change
approved requirements, implement a repair, or declare completion. The controller records
the policy outcome before applying it. Escalation remains a durable pause; an admitted
retry returns to the original owner and normal independent validation/review.

## When a stage stops making progress

Before the runner pauses because a stage is not converging, it sends the run to the
read-only `investigate_stuck` stage (`tools/autocode_stuck_job.py`) instead of stopping at once:

| Pause | After the Investigator |
| --- | --- |
| `PAUSED_REPEATED_FAILURE`, `PAUSED_INVALID_OUTPUT` | retry runs the stage once more; its failure history stays, so another failure counts on top (a spent report repair is archived) |
| `PAUSED_PLANNING_BUDGET` | retry grants one more review round (two calls from the challenge, one from the final review) |
| `PAUSED_NO_PROGRESS` | retry allows one more implementation batch |
| `PAUSED_COMPLETION_REVIEW` | retry asks the Completion Owner once more |
| `PAUSED_REPORT_REPAIR_LIMIT`, `PAUSED_BUILDER_RETRY_LIMIT`, `PAUSED_MILESTONE_STALLED`, `PAUSED_MILESTONE_REPLAN` | diagnosis only; these keep their operator resume flags |

The Investigator runs at high effort on a model different from the stuck stage's: Claude Opus
5.5 (`kilo/anthropic/claude-opus-5.5`) in `kilocode` runs; otherwise GPT-6 Sol, or GLM 5.3 when
the stuck stage runs on Sol (GPT-6 Luna in native Codex runs, which have GPT models only). Never
Astra by default. It runs on a fresh route and session. It reads the task, the saved state and the stuck stage's attempts and returns
a diagnosis, then either guidance for one more attempt or the question only you can answer.
`--investigator-model MODEL` (and `--investigator-reasoning-effort`) pins the Investigator's model for
a run instead; a `provider/model` id such as `openai/gpt-6-sol` runs it through OpenCode even in a
Codex run. Guidance goes into the retried stage's prompt: for planning, every planning stage until the
plan is presented; otherwise that stage until it completes.

Bounds: one investigation per distinct stage and pause, three per run
(`settings.stuck_investigation.max_calls_per_run`; 0 turns it off). A second failure of the
same problem, a `pause` recommendation or a failed investigation restores the original
pause, with the diagnosis in its reason. It never approves anything, changes requirements or
criteria, weakens tests, grants permissions or extends budgets beyond that one attempt.
Permission, scope, goal, criteria and spend pauses go straight to you, and so does a pause a
guard re-raises on resume before any stage runs, or the outcome of a retry you authorized
with `--retry-failed-stage`. Only full Autopilot runs investigate; a
single-unit invocation (`--unit`) stops at its own boundary. `--diagnose-failed-stage`
below remains the operator's explicit, policy-ledgered alternative for a Builder.

## Joint requirements planning and review

This is the new-run default. Older mixed-CLI OpenCode runs move their Codex roles
to OpenCode when execution resumes at a recovered stage boundary. The runner saves
a checkpoint backup and archives those Codex session IDs; approvals, task history,
evidence, limits, and existing OpenCode sessions are preserved.

```sh
autocode "Your rough idea"
# Or target another committed Git workspace:
autocode "Your rough idea" --workspace /path/to/project
```

```text
You → Requirements Gatherer: clarify outcome, scope, constraints and definition of done
Requirements Gatherer → Planner: draft plan and task dependencies
Planner → Plan Reviewer → Planner revision → Plan Reviewer
    → you approve that exact plan
    → Builder → Validator → Completion Owner
```

Role defaults and escalation ladders are documented in [Models](models.md).

The Requirements Gatherer saves a separate structured report under the run's `iterations/`
directory. The Planner receives that artifact, originates alternatives and a task DAG,
and can push back on the Plan Reviewer using source evidence. Unanswered requirements
questions cannot silently disappear from the draft.

Each question is classified by `kind` (`discoverable`, `inferable`, or `decision`),
`category`, and `delegable`. A **discoverable** question, one the workspace can answer,
is never shown to you. The first time the Requirements Gatherer or Planner asks one in a
clarification episode, the runner holds that report back and re-runs the same stage once
with the questions, the report that raised them, and its hash. That single pass must
resolve each question from cited workspace files (a `machine_resolution`, accepted only
for technical facts, never for cost, quota, permission, side-effect or outcome choices),
keep it as a decision, or record an `access_blocker` when the source is missing.
Anything still discoverable afterwards is shown to you as a labelled decision; there is
no second pass. The episode, and so the pass, is renewed only by your own new input: a
non-delegated `--answer`, `--feedback`, or `--edit-goal`. Delegating a default, rejecting
an assumption, or the model regenerating question IDs does not renew it.

Whenever planning stops for your answers, the displayed brief starts with a **Plan Preview**
bound to that exact revision and requirements handoff. It lists what you said (quoted
requirements), what was read from the workspace, the assumptions the plan would rely on,
and the decisions only you can make. Readiness is shown as counts only (blocking decisions,
assumptions relied on, acceptance tests and criteria, open obligations), never as a score.
Answering nothing leaves execution blocked; the preview never approves anything.

`--reject-assumption A1` turns a structured assumption into an **obligation** the runner
tracks until it is discharged; the rejected assumption may not reappear. If the assumption
carried policy weight (cost, quota, permission, external side effect, or the requested
outcome), it becomes your decision: the plan stays clarification-only and you are asked a
question under the obligation's id. Otherwise the Planner may propose a remediation that
still covers every requirement the assumption supported, and only a Plan Reviewer decision
bound to the hash of that exact proposal discharges it; a revised proposal needs a new
decision. Anything still open at final review comes back to you as a question, and the
plan has no executable first task. Only your own answer to that question discharges it;
feedback that merely mentions the obligation does not, and such a question cannot be
delegated. A rejected assumption can never be restored, even after its obligation is
resolved. Proposing a remediation under one intent does not carry over a change of intent:
a new answer or feedback returns it to be proposed and reviewed again. Approval is refused
while any obligation is open. `--delegate-all` and `--reject-assumption` act only on the
revision you were shown, so both take its token with `--review-token`.
Reviewer concerns have stable IDs; every concern requires a Planner response and a reviewer decision,
including a concrete acceptance test. The final displayed brief includes the technical
approach, milestones, and **first bounded implementation task**, all covered by its
revision/hash. Approval dispatches that task directly, without a third Plan Reviewer
call. The separate Completion Owner's later decisions use the normal execution budget.

Planning defaults to **two Plan Reviewer request attempts per cycle**, including failed or
abandoned attempts. Bounded recovery does not refund attempts or automatically extend
the allowance. Unresolved final decisions return to you as blocking questions. If the
budget is exhausted, the run pauses at `PAUSED_PLANNING_BUDGET`. After inspecting a
reconciled checkpoint, an operator can permit one more attempt without discarding the
accepted challenge and revision (for example, increase a total allowance of 2 to 3):

```sh
autocode --workspace /path/to/project --run-dir /path/to/run --planning-review-call-limit 3
autocode --workspace /path/to/project --run-dir /path/to/run --resume-paused --unit autoplanner --no-chat
```

The first command only saves an audited, finite **total** allowance for this cycle;
repeating it does not add another attempt. Failed attempts remain counted, unresolved
provider work must be reconciled first, and final review and exact user approval remain
mandatory. Unlimited allowances and decreases are rejected. A new cycle returns to the
default of two attempts; its predecessor's allowance and exchange remain in history.

Alternatively, explicitly send `--feedback '...'` to request a new cycle.
Answering final blockers, giving feedback,
or editing the goal starts fresh joint review and requires fresh approval. Old exchanges
remain archived. Ordinary resume preserves the cycle and its spent budget.

The default workflow uses OpenCode for every role. The Plan Reviewer, Builder, Validator,
and Completion Owner use OpenCode's current ChatGPT OAuth connection; the Requirements Gatherer
and Planner use separate sessions on the Z.ai connection by default. Changing
the account in ChatGPT's browser or desktop app does not change OpenCode's login.
To switch this workflow's ChatGPT account, reconnect OpenAI in OpenCode.
Every role can select any available provider/model
from `opencode models`. The role names describe responsibilities, not mandatory models.

Planning requests use fresh sessions and focused handoffs: the current brief, code
references, alternatives, concerns, responses and changes since review. Full reports
remain in the run's `iterations/` directory. Codex planning uses its read-only sandbox.
OpenCode planning uses a fresh, read/search-only agent with shell, edit, delegation,
external-directory access and unlisted tools denied; workspace snapshots are also
checked. These OpenCode restrictions are tool permissions, not an OS sandbox.

The existing `--chat`, `--answer`, `--feedback`, `--show-goal`, `--approve-goal`, status
and resume commands work in this mode. Intermediate drafts cannot be approved. The
saved `planning` object records the exchange, final approval token and Plan Reviewer call count;
`planning_history` retains prior cycles. Existing runs keep their original routing,
including earlier OpenCode-only runs and joint-planning runs that used the Requirements Planner model for validation.
Start a new run to use the current GPT Sol default in that case; saved sessions cannot
move between CLIs. No global OpenCode or Codex configuration is changed.

## Bug fixes

A bug report goes through the same conversation and every stage as any other
task: requirements, planning, plan challenge, revision, final plan review, your
approval, orchestrator, Builder, Validator and Completion Owner.

- **Job type.** The Requirements Gatherer proposes `task_kind` (`bugfix` or
  `build`), the Planner writes it into the contract, and the Plan Reviewer
  confirms or challenges it. The brief you approve says "Job type: bug fix". It
  is part of the hashed contract, so changing it needs approval again. Saved
  runs without it are `build` and behave as before.
- **A defect-shaped plan.** For a bug fix the planning stages keep the plan to
  the reproduction, the root cause, the smallest correct fix and a regression
  test in the project's own suite. The same stages run; they review less.
- **Runner-owned proof, no model call.** Just before the Validator runs, the
  runner executes `regression_proof` against the run's base commit (the commit
  the run started from). The new or changed tests must fail on the original
  code and pass on the current code, and every test that passed on base must
  still pass (not fail, be skipped or disappear). The checks run in clean scratch
  worktrees, never in the task workspace. The result is bound to the exact
  source revision and appears as a runner-owned step with zero tokens in the
  stage history.
- **Reviewers use it instead of repeating it.** The Validator and the Completion
  Owner receive `regression_proof`. With a passing proof the Validator runs the
  regression command once as its own check instead of the whole suite. With a
  failing proof the Validator reports FAIL and the Completion Owner returns
  REWORK, which sends the proof's reasons back to the Builder.
- **The completion gate requires it.** A bug fix cannot reach `TASK_COMPLETE`
  unless the proof passed for the current source. A refusal names the reason.
- **Regression tests in plain English.** When the Investigator reproduces a
  bug, it also writes the regression tests the fix must pass as `test_cases`
  in its diagnosis. Each case has an `id` (`T1`, `T2`, …) and `given`, `when`
  and `then` fields with exact values, for example:

  > T1: Given the timesheet entries for 2024-12-30 and 2025-01-02; when the
  > weekly report runs; then both appear in one row for week 2025-W01.

  You can check these without reading test code. They are saved in the
  diagnosis note under `docs/bugs/`.
  - **Contract criteria.** Each case becomes an acceptance criterion of the
    small-fix contract. For a large fix, it becomes one the Planner must carry
    into the plan you approve.
  - **Named tests.** The Builder writes one test per case, named after the case
    id: `test_t1_<what it checks>`.
  - **The runner's check.** The regression proof also requires every case to
    have a test with its id in the name, among the tests that fail on the
    original code and pass after the fix. A case without one fails the proof,
    which names the case. The link is by name, so no model is involved. Names
    match as whole words, so `T1` matches `test_t1_…` and `TestT1…` but not
    `test_t12_…`.
  - **Without per-test results** (exit codes only), the cases cannot be matched
    and the proof is `UNVERIFIED`.
  - **The Validator's check.** The Validator reads each case's test
    (`case_tests` in the proof) and reports FAIL if the test does not assert
    what the English case says.
  - **Where the cases appear.** The status view's `evidence` carries the cases
    and the tests that prove them, and the pull-request body written by
    `autocode-issue` lists them.
  - **Compatibility.** Runs whose diagnosis has no cases, including bug fixes
    planned without an Investigator and older saved runs, behave as before.

### Small features: tests in plain English

A small feature gets the same kind of proof from its plan. Here "small" means
a plan with one milestone. The Planner writes every acceptance criterion a test
can check as one concrete example, and sets its `verification_method` to name
the test:

> C2: Given calc.py with add(); when sub(5, 3) runs; then it returns 2
> Verify: test: test_c2_subtracts

- **You approve the examples with the plan.** The plan display says which
  criteria the runner will prove.
- **Criteria a test cannot check keep an ordinary verification.** Examples are
  documentation, visual design and performance under real load; the Validator
  judges those as before.
- **Naming.** The Builder writes one test per marked criterion, named with its
  id (`test_c2_…`).
- **The runner's check.** Before the Validator runs, the runner checks each
  named test by the same regression proof as a bug fix, with one difference.
  The test must pass with the change and must not have passed without it. For a
  feature, "not passed" includes failing to import the new code on the original
  revision, because the code didn't exist yet. A bug fix still needs a test that
  runs and fails. As for bug fixes, no test that passed before may fail now.
- **Completion.** It is refused until every marked criterion has its test.
- **Several milestones keep ordinary criteria.** A later milestone's tests
  cannot pass at an earlier milestone's checkpoint, so plans with more than one
  milestone are not proven this way.

The matching and the rules are in `tools/autocode_test_cases.py`.

Test commands are detected (pytest, unittest, Go, Jest/Vitest/Mocha, RSpec,
Cargo, `make test`); `--test-command` and `--regression-command` set them for a
new run. With per-test results (pytest, unittest) a proof needs a named test
that ran and failed on base and passed on the fix; a test that only fails to
import on base is not a reproduction. Without per-test results exit codes decide.
When nothing can be proven (for example no test command is found), the proof is
`UNVERIFIED` and the bug fix cannot complete until a command is supplied.

The scratch worktrees use the project's own environment: its `.venv`, `venv` or
`node_modules` is linked in, and the Python tests run with the project's
virtualenv interpreter even though the task worktree has none. Build-generated
source files that git ignores but that sit next to tracked code (such as a
setuptools-scm or hatch-vcs `_version.py`) are copied from the project into every
scratch tree, base and fix alike, so the package imports there. Ignored build
output directories are not copied.

Planning reports that omit only a provenance list (such as `code_refs` or
`source_refs`) now get an empty list instead of a report-repair model call; the
raw report is kept and every semantic check still runs. Reports that omit a list
carrying a decision (requirements, questions, concerns, responses) still go to
report repair.

## Conversation and approval

The first stage runs **read-only requirements gathering** and saves a structured JSON handoff
without milestones. A separate Planner uses it to propose the task DAG and any material
questions. Chat mode stays in the conversation; command
mode saves and exits at the checkpoint. Intermediate drafts cannot be approved;
the Plan Reviewer still has to challenge, the Planner revises, and the Plan Reviewer finalizes first. State, answers,
brief feedback, contract history, user events, prompts, schema files, evidence and
sessions remain in the target workspace's `.autocode/runs/<run>/`. No implementation
starts from the initial prompt.

Use the printed run path in the following commands (keep the same `--workspace`):

```sh
autocode --workspace /path/to/project --run-dir /path/to/run --answer 'Q1=CLI only'
autocode --workspace /path/to/project --run-dir /path/to/run --no-chat
autocode --workspace /path/to/project --run-dir /path/to/run --feedback 'Keep the first milestone local only'
autocode --workspace /path/to/project --run-dir /path/to/run --no-chat
autocode --workspace /path/to/project --run-dir /path/to/run --show-goal
autocode --workspace /path/to/project --run-dir /path/to/run --approve-goal 'r3:<full displayed hash>'
autocode --workspace /path/to/project --run-dir /path/to/run --no-chat
```

`--answer` is repeatable. `--feedback TEXT` saves a correction and returns to Requirements
discovery on the next invocation; a revised brief always needs fresh approval.
`--delegate Q1` explicitly accepts that question's proposed
default. Saved answers are included in subsequent interviews; an answered question
ID cannot be requested again. Answers do not approve the task. The approval token
must exactly match the current displayed contract revision. Approval saves
`READY_TO_EXECUTE`; the next ordinary invocation begins execution. User-input commands
never launch an agent. This command-per-turn interface also works from scripts and
other frontends; no continuously attached terminal is required.

Execution roles must consult saved answers before asking for permission. An exact
repeated permission request under the same approved contract is returned once to the
Completion Owner with its authenticated answer, including any refusal or conditions.
It is not automatically granted or extended to a broader request. If the owner repeats
it again, the runner pauses for reconciliation rather than creating another question
or spending indefinitely. Older answers without the original request remain available
in the prompt but are not automatically matched.

A technical report blocked solely on one required human acceptance criterion can be
presented for that review. Completion still requires the explicit review event, current
source and evidence hashes, passing automated criteria and checks, and a passing full
flow. Human acceptance does not rewrite the independent report or waive other failures.

`--edit-goal body.json` loads the full contract body (the `goal_contract.body` shape
from state), creates a new draft revision and displays its delta. It invalidates goal
approval, validation and human reviews. Previous contracts/evidence stay archived.
Approval can never be inferred from a model response or a generic `--resume-paused`.

The contract records outcome/user, the ordered end-to-end user flow, deliverables,
behaviors and failures, exclusions,
constraints/permissions, assumptions and their provenance, delegated decisions,
stable criterion IDs with verification methods, human-review flags and open questions.
It also includes a minimal technical approach and small implementation milestones,
each linked to existing acceptance criteria; every required criterion is covered.
All listed acceptance criteria are required. Optional enhancements go in the deferred
backlog and do not participate in the completion gate.

See also: [Execution and completion](execution.md) · [Models](models.md) · [Providers](providers.md)
