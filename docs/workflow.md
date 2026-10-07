# Workflow: planning, approval, and handoffs

[← Back to README](../README.md)

This page covers the end-to-end conversation flow: how a rough idea becomes an
approved plan, how that plan is implemented and reviewed, and what Autopilot
coordinates along the way.

## The flow

Start with a rough idea and discuss it with the Requirements. It helps define the smallest
useful end-to-end product, asks focused questions, and drafts a versioned build brief.
The Plan Reviewer challenges that draft; the Requirements revises; the Plan Reviewer finalizes. You can revise the brief
in the same conversation. Implementation starts only after you explicitly approve it.

After approval, Autocode handles the handoffs:

```text
You → Autopilot: recognize the kind of job (build, bugfix, review, design, discuss); saved as `workflow`,
   printed with its reason and signals; `--workflow KIND` names it instead of recognizing it
   review → Reviewer only: findings written to review/findings.json, repository untouched, run complete
   bugfix → Investigator first: diagnosis written to docs/bugs/<name>.json, repository untouched;
            not reproduced → run complete;
            reproduced → the build pipeline below from the Planner on, planned from the
              diagnosis: no requirements gathering, but plan review and your approval
              (small or large; the short path for small fixes is off until the full path is dependable)
   design → Architect first: a design review is written to review/design-review.json (goals met,
            blocking/advisory concerns, questions for you), repository untouched, run complete;
            a request for a NEW design → the build pipeline below
   discuss → Analyst only: an answer with evidence tied to repository files (and the note the
            request asks for, written by the runner), repository untouched, run complete
   (a design review's or a discussion's questions do not hold the run: it completes, and you
    answer them with --follow-up, the next turn of the same run; a reply to a design review
    → the Architect revises that review in review/design-review.json as revision n+1, the same
    concern ids kept, settled ones marked resolved, the repository outside review/ untouched)
   build → the build pipeline below;
           implementing an APPROVED design document as written, or "Build it." after a design turn
             (the design that turn wrote) → Architect checks it against the repository first,
             repository untouched:
             conflicts (a frozen API, a documented invariant) → written to <design>.blockers.json,
               run stops (PAUSED_DESIGN_CONFLICT), nothing built, you decide;
             no conflicts → the design's binding decisions become a constraint and the pipeline starts
               at the Planner (no requirements gathering; the Planner may not redesign or ask);
               after a design turn the earlier turn's plan is archived, so the Planner drafts a new
               contract for the build instead of revising the design job's
You → Requirements Gatherer: rough idea → saved requirements report
Requirements Gatherer → Planner: draft task DAG
Planner → Plan Reviewer → Planner revision → Plan Reviewer final → your approval

Autopilot → AutoCode task scheduler → independent Builders → combined validation

Builder → Tester → Completion Reviewer
   ↑                       |
   └──────── REWORK ───────┘
```

The Plan Reviewer owns final planning decisions. On new joint runs, the runner-owned
Autopilot dispatches AutoCode to schedule independent milestone Builders after approval, falling back
to one Builder when work cannot safely run in parallel. Each Builder implements one bounded
task. A separate Tester independently inspects and tests the combined code, then the
Completion Reviewer proposes completion or rework for Autopilot to evaluate. They all work
from the same approved brief; you do not explain the product to each agent or relay
their prompts. The runner saves decisions, tasks and evidence so it can resume.

## The job kind and the older settings

Three settings decide "which process runs". They answer different questions and do not override
one another:

| Setting | Question it answers | Where it applies |
| --- | --- | --- |
| **Job kind** (`workflow.kind`: build, bugfix, review, design, discuss) | What you get back, and so which stages run at all. | Every run. Recognized from your request, or named with `--workflow`. Fixed once recognition has run. |
| **Workflow mode** (`settings.workflow.mode`: `glm_first_v1`, `glm_final_audit_v2`, or the legacy default) | Who checks the build: which role reviews each milestone and which audits the whole task. | Only inside the build pipeline, so only for `build` and a reproduced `bugfix`. `review`, `design` and `discuss` never reach it. |
| **Task-lane mode** (`ui` or `code` on an item of `autocode tasks`) | Which loop one lane task runs: the Figma design loop, or a normal code run. | Only in `autocode tasks`. A `code` item is an ordinary run and gets its own job kind. A `ui` item uses the Figma path and has no job kind. |

So the kind decides whether the build pipeline runs, the workflow mode tunes who checks inside it,
and the lane mode picks between two loops before either applies. If two seem to disagree, the job
kind wins: a `review` run never builds, whatever the mode says.

The kind is shown where you approve a plan (`Job kind: build (recognized: ...)`), because the two
mistakes are not equally bad. Reading "build" as "discuss" costs a little time; reading "discuss" as
"build" writes code you never asked for. If the kind is wrong, do not approve: start a new run with
`--workflow KIND`. A follow-up message on a finished run (`--follow-up`) is recognized again and
still needs its own plan approval.

## Four units controlled by Autopilot

The same repository contains four callable units:

| Unit | Owns | Handoff |
| --- | --- | --- |
| Autoplanner | Separate requirements, planner, and plan-reviewer sessions | User-approved contract and task DAG |
| Autocode | Next-task planning, parallel Builders, implementation and integration | Build candidate with source revision |
| Autoreview | Independent validation and completion-owner review | Evidence-backed review result |
| Resolver | Read-only diagnosis of reviewer-requested rework | Evidence-linked, bounded repair DAG and retests |

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

In the standard workflow, a reviewer's `REWORK` decision routes to Resolver,
then back to AutoCode and independent review. Resolver inherits the Plan Reviewer model
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
admission. Relaunches after a timeout, capacity or startup failure or a denied path, new
blockers and new iterations consume new reservations. A diagnosis whose report was
rejected is not relaunched for the same failure: it holds as `PAUSED_NO_PROGRESS` without
another reservation. Reloading the same
attempt does not consume another, and an ambiguous launch is not refunded. The default
run-level diagnostic allowance is two; explicit resume does not reset it. Report-only format repair has its separate existing
allowance and remains subject to the parent's time/token limits. The diagnostic limit
is not a count of every internal model/tool step or separately budgeted report repair.

The model can recommend a bounded retry or escalation, not grant permissions, change
approved requirements, implement a repair, or declare completion. The controller records
the policy outcome before applying it. Escalation remains a durable pause; an admitted
retry returns to the original owner and normal independent validation/review. It admits
the Builder without a proposed source change, because a rejected report usually has none,
until one Builder attempt returns a result. An attempt that automatic recovery archives
without a report does not use it up; that recovery's own budget bounds the relaunches.
(`--retry-failed-stage` differs: the attempt it admits spends it even if it times out.)
Another attempt at the same unchanged failure needs new evidence or an explicit
`--retry-failed-stage`. The Builder's repair plan carries the diagnosis and the
recommendation. A proposed `recovery_change` the incident packet cannot attest goes in it
as `unattested_change`, with the reason: advice for the Builder that neither blocks the
retry nor counts as a new experiment.

## When a stage stops making progress

Before the runner pauses because a stage is not converging, it sends the run to the
read-only `investigate_stuck` stage (`tools/autocode_stuck_job.py`) instead of stopping at once:

| Pause | After the Investigator |
| --- | --- |
| `PAUSED_REPEATED_FAILURE`, `PAUSED_INVALID_OUTPUT` | retry runs the stage once more; its failure history stays, so the same failure again extends the run of identical failures and a different one starts a new run (a spent report repair is archived). A Resolver repair's Builder retry continues from the in-scope work the rejected attempt kept, and is the one attempt recovery novelty admits for it ([Evidence-bound repair](execution.md#evidence-bound-repair)) |
| `PAUSED_PLANNING_BUDGET` | retry grants one more review round (two calls from the challenge, one from the final review) |
| `PAUSED_NO_PROGRESS` | retry allows one more implementation batch |
| `PAUSED_COMPLETION_REVIEW` | retry asks the Completion Reviewer once more |
| `PAUSED_REPORT_REPAIR_LIMIT`, `PAUSED_BUILDER_RETRY_LIMIT`, `PAUSED_MILESTONE_STALLED`, `PAUSED_MILESTONE_REPLAN` | diagnosis only; these keep their operator resume flags |

A rejected report pauses as `PAUSED_REPEATED_FAILURE` only after three consecutive attempts of
the stage, at the same source, failed with the same error and left the same kind of output
(`tools/autocode_failures.py`). Different problems that happen to share an exception type,
such as missing responses to different concerns, pause as `PAUSED_INVALID_OUTPUT`.

The Investigator runs at high effort on a model different from the stuck stage's: Claude Opus
5.5 (`kilo/anthropic/claude-opus-5.5`) in `kilocode` runs; otherwise GPT-6 Sol, or GLM 5.3 when
the stuck stage runs on Sol (GPT-6 Luna in native Codex runs, which have GPT models only). Never
Astra by default. It runs on a fresh route and session. It reads the task, the saved state and the stuck stage's attempts and returns
a diagnosis, then either guidance for one more attempt or the question only you can answer.
`--investigator-model MODEL` (and `--investigator-reasoning-effort`) pins the Investigator's model for
a run instead; a `provider/model` id such as `openai/gpt-6-sol` runs it through OpenCode even in a
Codex run. Guidance goes into the retried stage's prompt: for planning, every planning stage until the
plan is presented; otherwise that stage until it completes.

A retry is checked, not trusted. The Investigator states the diagnosed cause as a
plain-English `example` ("Given the Planner's report cited `docs/bugs/cent-drift.json (see
note)`; when the runner validated `code_refs`; then it rejected the path as missing") and,
unless it says in `untestable` why no command can, a `probe`: a command that exits 0
exactly when the files it cites in `evidence_refs` show that cause. Every cited file must
exist, in the repository or in the run's directory, and a retry must cite at least one. The
runner copies only the cited run files into a scratch tree, each at `run/<name>`, and runs
the probe there: a probe that does not exit 0, or that needs a file the Investigator did
not cite, rejects the report, and the original pause is restored with the reason. The
probe's receipt is recorded with the investigation.

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
    → Builder → Tester → Completion Reviewer
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
including a concrete acceptance test. A row whose ID names no concern (a question ID, or an empty
placeholder) answers nothing: it is not checked, and the saved report keeps only the concern rows.
The final displayed brief includes the technical
approach, milestones, and **first bounded implementation task**, all covered by its
revision/hash. Approval dispatches that task directly, without a third Plan Reviewer
call. The separate Completion Reviewer's later decisions use the normal execution budget.

Planning defaults to **two Plan Reviewer reviews per cycle**. A review counts once it
returns a report, whether or not the runner then accepts that report. An attempt that
times out or whose provider fails returns no review, so it is given back before the next
attempt; the repeated-failure limit, not this allowance, stops a review that keeps failing.
Bounded recovery does not otherwise extend the allowance. Unresolved final decisions return
to you as blocking questions. Once you answer them, planning starts a new cycle, and its first
review receives the previous review's concerns, its decisions and your answers
(`previous_review`), so it checks your answers instead of reviewing the plan from scratch. If the
budget is exhausted, the run pauses at `PAUSED_PLANNING_BUDGET`. After inspecting a
reconciled checkpoint, an operator can permit one more attempt without discarding the
accepted challenge and revision (for example, increase a total allowance of 2 to 3):

```sh
autocode --workspace /path/to/project --run-dir /path/to/run --planning-review-call-limit 3
autocode --workspace /path/to/project --run-dir /path/to/run --resume-paused --unit autoplanner --no-chat
```

The first command only saves an audited, finite **total** allowance for this cycle;
repeating it does not add another attempt. Reviews that returned a report remain counted, unresolved
provider work must be reconciled first, and final review and exact user approval remain
mandatory. Unlimited allowances and decreases are rejected. A new cycle returns to the
default of two attempts; its predecessor's allowance and exchange remain in history.

Alternatively, explicitly send `--feedback '...'` to request a new cycle.
Answering final blockers, giving feedback,
or editing the goal starts fresh joint review and requires fresh approval. Old exchanges
remain archived. Ordinary resume preserves the cycle and its spent budget.

Before the plan is shown for approval, Resolver checks that it is the reviewed final plan and
that the workspace has not changed since that review. If the check fails, approval is deferred and
planning restarts with a new cycle. Planning restarts at most twice for the same reason since your
last input. After that, the run pauses at `PAUSED_APPROVAL_DEFERRED` with the reason, instead of
spending review calls on cycles that end the same way. `--resume-paused` runs one more cycle;
`--feedback` restarts from requirements (in an adaptive run, from the Planner when a plan is shown
for approval) and renews the allowance.

The default workflow uses OpenCode for every role. The Plan Reviewer, Builder, Tester,
and Completion Reviewer use OpenCode's current ChatGPT OAuth connection; the Requirements Gatherer
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
including earlier OpenCode-only runs and joint-planning runs that used the Requirements model for validation.
Start a new run to use the current GPT Sol default in that case; saved sessions cannot
move between CLIs. No global OpenCode or Codex configuration is changed.

## Bug fixes

A bug report goes through the same conversation and every stage as any other
task: requirements, planning, plan challenge, revision, final plan review, your
approval, orchestrator, Builder, Tester and Completion Reviewer.

- **Job type.** The Requirements Gatherer proposes `task_kind` (`bugfix` or
  `build`), the Planner writes it into the contract, and the Plan Reviewer
  confirms or challenges it. The brief you approve says "Job type: bug fix". It
  is part of the hashed contract, so changing it needs approval again. Saved
  runs without it are `build` and behave as before.
- **A defect-shaped plan.** For a bug fix the planning stages keep the plan to
  the reproduction, the root cause, the smallest correct fix and a regression
  test in the project's own suite. The same stages run; they review less.
- **Runner-owned proof, no model call.** Just before the Tester runs, the
  runner executes `regression_proof` against the run's base commit (the commit
  the run started from; for a run created before that commit was saved, the
  commit its first stage recorded, if the source still descends from it). An
  in-place run, including a new run in an earlier task's worktree, started with
  uncommitted or untracked files starts from a commit of HEAD plus those files
  (ignored files and untracked nested repositories left out), kept under
  `refs/autocode/launch/RUN`, so they count as original code rather than as the
  change; HEAD, the index and the files stay as they were. A run that finds the
  checkout busy records it once it holds the checkout. A later launch drops the
  ref of a run whose directory is gone, after a day's grace. Each
  suite run may take as long as the run's tool-call limit, at least 900 seconds
  and with no limit when the run turned that limit off;
  `settings.regression.test_timeout` overrides it. The new or changed tests
  must fail on the original code and pass on the current code, and every test
  that passed on base must still pass (not fail, be skipped or disappear). The
  checks run in clean scratch worktrees, never in the task workspace. The result
  is bound to the exact
  source revision and appears as a runner-owned step with zero tokens in the
  stage history.
- **Reviewers use it instead of repeating it.** The Tester and the Completion
  Owner receive `regression_proof`. With a passing proof the Tester runs the
  regression command once as its own check instead of the whole suite. With a
  failing proof the Tester reports FAIL and the Completion Reviewer returns
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
  - **"Reproduced" is checked.** A reproduced bug also carries `probe`, a
    command that exits 0 exactly when the bug is present on today's code, for
    example `python3 -c "from pager import page_count; assert page_count(5, 2) == 2"`.
    The runner runs it in a scratch copy and rejects the report if it does
    not exit 0, so the diagnosis rests on something the runner saw, not on
    the Investigator's word. A bug no command can show here (a live registry,
    a race, a device) says why in `untestable` instead. The note records the
    probe that showed the bug in `proven_by`.
  - **Contract criteria.** Each case becomes an acceptance criterion of the
    small-fix contract. For a large fix, it becomes one the Planner must carry
    into the plan you approve.
  - **Named tests.** The Builder writes one test per case, named after the case
    id: `test_t1_<what it checks>`.
  - **The runner's check.** The regression proof also requires every case to
    have a test with its id in the name. The link is by name, so no model is
    involved. Names match as whole words, so `T1` matches `test_t1_…` and
    `TestT1…` but not `test_t12_…`.
  - **Restore and preserve cases.** A case is `kind: restore` by default (and a
    case with no `kind` is one): behavior the fix restores, proven by a test
    that fails on the original code and passes after the fix. A case may
    instead be `kind: preserve`: behavior that already worked and must keep
    working — for example *"an exact multiple still gives the same page
    count"* — proven by a test that passes on the original code **and** after
    the fix. A preserve case without a test, or whose test fails on the
    original code (it describes restored behavior and is mis-tagged), fails
    the proof, which names the case. Nothing else is relaxed: a restore case
    whose test passes on the original code still fails, and every case needs
    its own named test.
  - **Without per-test results** (exit codes only), the cases cannot be matched
    and the proof is `UNVERIFIED`.
  - **The Tester's check.** The Tester reads each case's test
    (`case_tests` in the proof) and reports FAIL if the test does not assert
    what the English case says.
  - **Where the cases appear.** The status view's `evidence` carries the cases
    and the tests that prove them, and the pull-request body written by
    `autocode-issue` lists them.
  - **Compatibility.** Runs whose diagnosis has no cases, including bug fixes
    planned without an Investigator and older saved runs, behave as before.

### Features: tests in plain English

A feature gets the same kind of proof from its plan. The Planner writes every
acceptance criterion a test can check as one concrete example, and sets its
`verification_method` to name the test:

> C2: Given calc.py with add(); when sub(5, 3) runs; then it returns 2
> Verify: test: test_c2_subtracts

- **You approve the examples with the plan.** The plan display says which
  criteria the runner will prove.
- **Criteria a test cannot check keep an ordinary verification.** Examples are
  documentation, visual design and performance under real load; the Tester
  judges those as before.
- **Naming.** The Builder writes one test per marked criterion, named with its
  id (`test_c2_…`).
- **The runner's check.** Before the Tester runs, the runner checks each
  named test by the same regression proof as a bug fix, with one difference.
  The test must pass with the change and must not have passed without it. For a
  feature, "not passed" includes failing to import the new code on the original
  revision, because the code didn't exist yet. A bug fix still needs a test that
  runs and fails. As for bug fixes, no test that passed before may fail now.
- **Completion.** It is refused until every marked criterion has its test.
- **Several milestones.** A test criterion is listed under the milestone that
  delivers it. At each milestone checkpoint the runner proves:
  - the tests of that milestone, or of every member of a parallel batch
  - the tests of milestones already accepted under the approved plan

  It never proves a later milestone's tests, so a milestone's tests must not
  depend on a later one. A test criterion that belongs to no milestone is
  proven once every milestone is current or accepted, which is at final
  completion. A milestone with no test criteria due runs no proof step. A
  proof made for a smaller set of due tests is never reused after the set
  grows.

The matching and the rules are in `tools/autocode_test_cases.py`.

Test commands are detected (pytest, unittest, Go, Jest/Vitest/Mocha, RSpec,
Cargo, `make test`); `--test-command` and `--regression-command` set them for a
new run. With per-test results (pytest, unittest) a proof needs a named test
that ran and failed on base and passed on the fix; a test that only fails to
import on base is not a reproduction. Without per-test results exit codes decide.
When nothing can be proven (for example no test command is found), the proof is
`UNVERIFIED` and the bug fix cannot complete until a command is supplied.

A script-driven suite (`npm test`, Yarn, pnpm, a shell runner) runs each tree's
own definition, so a passing exit code alone cannot show that the old tests
still ran. The runner therefore also runs the base revision's suite definition
over the fix's product code, in a temporary folder outside the checkout. That
definition is the base's `package.json` scripts and other non-loading fields,
its package-manager, task-runner and test-runner configuration (`.npmrc`,
`.yarnrc*`, `.pnpmfile.*`, `bunfig.toml`, `turbo.json`, `jest.config.*`,
`.mocharc*` and the like, and whatever a link of that name points at), its
tests, and the runner files its scripts reach. The fix keeps its own product
code and the `package.json` fields that load it (`type`, `main`, `exports`,
`imports`, dependencies). That run happens whenever both suites ran to the end
and nothing has failed yet. If it fails, the proof is FAIL when the base suite
passed and `UNVERIFIED` when the base suite already failed. When both runs name
their tests, they are compared test by test. If a runner reaches its selector
through a variable or a computed path, the proof is `UNVERIFIED` only when the
fix also changed a file that is neither a test, product code the tests import
nor a definition file (docs/bugs/2026-10-07-base-definition-gaps.md).

The scratch worktrees use the project's own environment: its `.venv`, `venv` or
`node_modules` is linked in, and the Python tests run with the project's
virtualenv interpreter even though the task worktree has none. Build-generated
source files that git ignores but that sit next to tracked code (such as a
setuptools-scm or hatch-vcs `_version.py`) are copied from the project into every
scratch tree, base and fix alike, so the package imports there. Ignored build
output directories are not copied. Ignored files under `vendor/` are copied the
same way. An `--in-place` run's Builder edits that same checkout, so the run saves
these files before any prerequisite or provider starts, and clean checks use
only inputs that still match the launch bytes and modes (#529). Added ignored
files are left out of test copies and noted. A captured generated or vendored
file changed or removed since launch makes the proof `UNVERIFIED`; restore it,
or start a new run after preparing the intended inputs. Missing, damaged or
incompletely captured launch records also block proof. Older runs without a
record copy none of these files and remain `UNVERIFIED` if the checkout has
eligible ignored inputs. The older generated-only byte-hash record cannot
authenticate the complete launch inventory and requires a new run. Planning prerequisites apply the same policy before
provider dispatch, including on resume. Saved evidence is checked again before
stage results advance the run, artifact reviews are accepted, or completion is
accepted or reused. Status also marks completed evidence stale after a captured
input changes. Separate task worktrees retain their
project checkout dependency policy; shared virtualenvs and `node_modules` are
outside this ignored-source inventory.
In-place capture requires readable regular files with no symlink in their paths.
Materialize ignored vendor file links before starting, including links to tracked
files in the same checkout. An incomplete capture stays unverified after the
files are changed or removed; correct the inputs and start a new run.

Planning reports that omit only a provenance list (such as `code_refs` or
`source_refs`) now get an empty list instead of a report-repair model call; the
raw report is kept and every semantic check still runs. Reports that omit a list
carrying a decision (requirements, questions, concerns, responses) still go to
report repair.

## Reviews, discussions and designs: findings and claims shown by running code

The review and discuss workflows produce findings and answers, not code. The
same idea applies: a finding or a claim is stated as a plain-English example,
and the runner, not a model, runs something that shows it.

### Review: every blocking finding has a failing test

- **Example.** Every blocking finding carries `example`, the defect as one
  concrete case: "Given …, when …, then … (expected …)". A blocking finding
  without one is rejected.
- **Test.** The Reviewer delivers a test under `review/tests/`, named after the
  finding (`F1` → `test_f1_…`). The report names the change's patch file in
  `change_patch` (`""` when the change is already in the workspace).
- **The runner's check.** It applies the patch in a scratch copy of the
  repository, never in the workspace, and runs the delivered tests there. Each
  blocking finding's test must fail on the changed code. A finding whose test
  passes, or has no test named after it, rejects the report: a finding that
  cannot be shown is not reported. The failing tests are recorded per finding
  (`proven_by` in `review/findings.json`, `finding_tests` in the run state).
- **Untestable findings.** A blocking finding no test can show (a documented
  compatibility rule, a missing document) says why in `untestable` and needs no
  test. Advisory findings need none either.
- **Bad patches.** A patch that does not apply rejects the report with git's
  message.

### Discuss: a claim may be shown by a probe

- A claim about what the code does may carry `example` (the concrete case in
  plain English) and `probe`: a shell command, run from the repository root,
  that exits 0 exactly when the claim holds.
- The runner runs every probe in a scratch copy of the code as it is now, with
  a two-minute limit each. A probe that fails rejects the answer. A probe
  cannot change the workspace.
- Claims without a probe stay grounded by their source file only, as before.
- Probed claims are recorded in the run state (`answer.probes`) and shown in
  the answer's evidence list.

### Design: a concern about today's code carries a probe

The design workflow's Architect judges a document, so nothing is applied. The
same probe rule as discussions applies to what it says about the code.

- **Reviewing a design (`review_design`).** Every blocking concern carries
  `example`, the problem as one concrete case in plain English. A concern that
  rests on what the code does today (an ordering check, a charge per call)
  also carries `probe`, a command that exits 0 exactly when the code behaves
  that way. The runner runs every probe in a scratch copy and rejects the
  review if one fails. A concern about the design text alone (a missing
  rollback step) has no probe. Probed concerns are recorded in
  `design_review.probes`.
- **Checking an approved design (`check_design`).** Every conflict carries
  `example`. A conflict with something the code enforces today carries
  `probe`; a constraint that lives in prose (a README rule) has none. The
  blockers file written beside the design records each conflict's proving
  probe in `proven_by`.

The scratch runs are `autocode_verify.scratch_run`. A Python test delivered
into a project with no test suite of its own still runs under unittest.

## Conversation and approval

The first stage runs **read-only requirements gathering** and saves a structured JSON handoff
without milestones. A separate Planner uses it to propose the task DAG and any material
questions. Chat mode stays in the conversation; command
mode saves and exits at the checkpoint. Intermediate drafts cannot be approved;
the Plan Reviewer still has to challenge, the Planner revises, and the Plan Reviewer finalizes first. State, answers,
brief feedback, contract history, user events, prompts, schema files, evidence and
sessions remain in the target workspace's `.autocode/runs/<run>/`. No implementation
starts from the initial prompt.

Run the following from the project or the task worktree; each acts on its unfinished run.
With several unfinished runs, add `--run-dir /path/to/run` (see
[Which run a command acts on](cli.md#which-run-a-command-acts-on)):

```sh
autocode --answer 'Q1=CLI only'
autocode --no-chat
autocode --feedback 'Keep the first milestone local only'
autocode --no-chat
autocode --show-goal
autocode --approve-goal 'r3:<full displayed hash>'
autocode --no-chat
```

`--answer` is repeatable. `--feedback TEXT` saves a correction and returns to Requirements
discovery on the next invocation; a revised brief always needs fresh approval. In an
`--adaptive-planning` run, feedback on a plan shown for approval goes to the Planner instead,
which revises that plan ([Adaptive planning](adaptive-planning.md#feedback-on-a-plan-you-were-shown)).
`--delegate Q1` explicitly accepts that question's proposed
default. Saved answers are included in subsequent interviews; an answered question
ID cannot be requested again. Answers do not approve the task. The approval token
must exactly match the current displayed contract revision. At the approval stop the brief
says which plan revision (`r3`) waits and that approving it authorizes implementation,
explains the token as a SHA-256 lock on that exact plan (any revision changes it), and ends
with the limits in effect and the exact `--approve-goal` and `--feedback` commands. The full
brief can run to hundreds of lines, so just above the limits it summarizes the decision, quoting
the plan: its outcome and milestones, every permission boundary, the first three scope
exclusions, each acceptance criterion with how it is checked (and whether it also needs your
review), and what passing proves for this job. The last screen reads:

```text
Before you approve r3 (a summary of the plan above):
What it will do:
  Provide a deterministic greeting CLI
Built in 1 milestone: M1.
What it may change:
  - May edit only: greet.py, test_greeting.py
Out of scope:
  - Web service
Done when:
  [C1] `greet.py Ada` prints exactly "Hello, Ada" and exits 0
    Checked by: test: test_c1_greets_ada
What passing proves:
  - An independent check of the final source must pass every criterion above, and the runner itself re-runs that check's commands in a clean copy: each must exit 0.
  - The runner also runs the test each of these criteria names: C1 (test:) must pass with the change and must not have passed without it. No test that passed before may fail now.
  - Not proven: behavior no criterion describes, or inputs no check exercises.

Limits in effect: 12 h of active time for the run, 1 h per stage, no iteration ceiling, one Builder at a time.
To approve this plan: autocode --run-dir RUN --approve-goal r3:<hash>
To change it instead: autocode --run-dir RUN --feedback 'WHAT TO CHANGE' (the revised plan gets a new token)

State: AWAITING_GOAL_APPROVAL / AWAITING_GOAL_APPROVAL
```

The proof lines name the cases the runner's regression proof will require a test for
(`autocode_test_cases.proof_cases`, the same list `autocode_regression` checks): in a bug fix, the
diagnosis's test cases when the bug was reproduced, else the plan's `test:` and `guard:` criteria,
each with what its test must show; a guard whose test cannot load on the original code is called
out, because the proof accepts it with only a note. When no criterion is marked `test:` or `guard:`
(or in a design job) it says that nothing shows a check would fail without the change. Approval saves
`READY_TO_EXECUTE`; the next ordinary invocation begins execution. User-input commands
never launch an agent. This command-per-turn interface also works from scripts and
other frontends; no continuously attached terminal is required.

Execution roles must consult saved answers before asking for permission. An exact
repeated permission request under the same approved contract is returned once to the
Completion Reviewer with its authenticated answer, including any refusal or conditions.
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

## Task chat message intent

Task chat uses deterministic, context-first rules; it makes no model call to
guess a destructive action. Each saved message includes its kind and rule
version. The rules are evaluated in this order:

| Context or text | Saved kind | Effect |
| --- | --- | --- |
| Reply sent with **Send this answer** inside a current question card | Answer | Saves the literal text, including words such as “Continue?”. |
| A standalone control phrase such as “stop”, “pause after this step”, or “continue” | Control | Explains the composer buttons; executes no control. |
| A question, a status request, or a question-word prefix | Question | Replies from saved status. It does not change the plan, queue feedback, or start a worker. |
| Other reply explicitly linked to a current question by the composer | Answer | Uses the existing answer/delegate operation and current request token. A typed “yes” here is only an answer, never plan approval. |
| Unscoped “yes”, “approve”, “go ahead”, or equivalent approval wording | Approval guidance | Points to the exact reviewed-plan approval card; does not approve or start work. |
| Other prose, including “actually use SMS instead” and ambiguous instructions | Proposed change | Saves the text and displays a confirmation card. Nothing is submitted to the runner yet. |

“Yes, change the plan” confirms that saved text against the same plan token.
Only then is a correction queued through the existing durable feedback operation.
During execution it waits for the next safe boundary; fresh planning, independent
review and explicit approval remain required. “No, keep as a question” records
that decision and replies from saved status. If the plan token changed, the old
confirmation is refused and the user must submit a new message.

Replay uses the original request ID and saved decision. Failed or uncertain
delivery retains the receipt and requires the existing explicit retry/reconciliation
path. Historical feedback already submitted before these rules keeps its original
authority on retry. Pre-task planning conversations remain draft discussions and
cannot approve or control a task.


### Question cards and saved progress

Each pending question has its own answer field and suggested-answer button in
chat. Drafts survive refresh and reload. A draft from an earlier request is
labelled for review; submitting always requires the current request identity.
Accepted adjacent answers collapse into a history summary that distinguishes
written answers from accepted defaults. Failed deliveries stay visible. Re-asked
question IDs begin a separate history group. Suggestions are accepted one at a
time; there is no implicit bulk acceptance or plan approval.

The task header links to Work with saved milestone acceptance, requirement
results and open problems. Unknown and prior-plan results stay unchecked.
Requirement and problem links open their read-only details in Checks. On narrow
screens the checklist opens in the existing details drawer. None of these reads
can approve a plan, answer a question or continue a run.
