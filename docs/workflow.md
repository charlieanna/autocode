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
You → Requirements first, on every run and follow-up turn: it names the kind of job (build, bugfix,
   review, design, discuss), saved as `workflow`; `--workflow KIND` names it instead (a pinned build
   still runs Requirements, any other kind starts its own first stage). A flow without Requirements
   (v2 planning, no Requirements role) has the Job recognizer name it first
   review → Reviewer only: findings written to review/findings.json, repository untouched, run complete
   bugfix → Investigator first: diagnosis written to docs/bugs/<name>.json, repository untouched;
            not reproduced → run complete;
            reproduced → the build pipeline below from the Planner on, planned from the
              diagnosis and the Requirements handoff: no second requirements gathering, but plan
              review and your approval
              (small or large; the short path for small fixes is off until the full path is dependable)
   design → Architect first: a design review is written to review/design-review.json (goals met,
            blocking/advisory concerns, questions for you), repository untouched, run complete;
            a request for a NEW design → the build pipeline below; its plan may not change a file an
            earlier turn of the same conversation wrote (a discussion's decision record) unless your
            newest message names it, so the design job writes its design and only reads the rest
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
               at the Planner (no second requirements gathering; the Planner may not redesign or ask);
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

## Reading a stop

A stopped run shows one category: **Your decision**, **Your environment**,
**Model or provider**, or **AutoCode**. Its exact saved state and stop reason
remain visible. The next command appears beneath the category; it uses the
existing request, approval or recovery control and does not grant authority on
its own. Resolve the stated prerequisite and supply any `ANSWER`, `MODEL`, `N`
or `GOAL_FILE` value before executing it. Read `autocode status` again after the
first step of a recovery; earlier request tokens cannot authorize a newer one.

`autocode --help` lists public commands and common options on one screen.
`autocode --help-all` shows all accepted task/run options, and
`autocode COMMAND --help` shows a subcommand's options. Compatibility console
scripts remain internal; use `autocode` in new instructions.

## The job kind and the older settings

Three settings decide "which process runs". They answer different questions and do not override
one another:

| Setting | Question it answers | Where it applies |
| --- | --- | --- |
| **Job kind** (`workflow.kind`: build, bugfix, review, design, discuss) | What you get back, and so which stages run at all. | Every run. Named by Requirements from your request, or with `--workflow`. Fixed once named. |
| **Workflow mode** (`settings.workflow.mode`: `glm_first_v1`, `glm_final_audit_v2`, or the legacy default) | Who checks the build: which role reviews each milestone and which audits the whole task. | Only inside the build pipeline, so only for `build` and a reproduced `bugfix`. `review`, `design` and `discuss` never reach it. |
| **Task-lane mode** (`ui` or `code` on an item of `autocode tasks`) | Which loop one lane task runs: the Figma design loop, or a normal code run. | Only in `autocode tasks`. A `code` item is an ordinary run and gets its own job kind. A `ui` item uses the Figma path and has no job kind. |

So the kind decides whether the build pipeline runs, the workflow mode tunes who checks inside it,
and the lane mode picks between two loops before either applies. If two seem to disagree, the job
kind wins: a `review` run never builds, whatever the mode says.

The kind is shown where you approve a plan (`Job kind: build (Requirements understood: ...)`), because the two
mistakes are not equally bad. Reading "build" as "discuss" costs a little time; reading "discuss" as
"build" writes code you never asked for. If the kind is wrong, do not approve: start a new run with
`--workflow KIND`. A follow-up message on a finished run (`--follow-up`) starts at Requirements
again, which names its job, and still needs its own plan approval.

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

The public `autocode` command and the internal `autocode-orchestrator` compatibility
alias delegate to the same controller, including dashboard launches. `tools/autocode_orchestrator.py` is a
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

The Investigator runs at high effort on a model different from the stuck stage's: in `kilocode`
runs the first of the run's own direct routes (Plan Reviewer, Planner, Requirements, Completion,
Tester, Builder) outside the stuck stage's model family, never a `kilo/` gateway route; otherwise
GPT-6 Sol, or GLM 5.3 when the stuck stage runs on Sol (GPT-6 Luna in native Codex runs, which have
GPT models only). Never Astra by default. A gateway model needs `--investigator-model`. It runs on a fresh route and session. It reads the task, the saved state and the stuck stage's attempts and returns
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
(`settings.stuck_investigation.max_calls_per_run`; 0 turns it off). A Builder failure the
Investigator classifies does not count toward these three: it has its own budget per
milestone ([Models](models.md#builder-retry-policy)). A second failure of the
same problem, a `pause` recommendation or a failed investigation restores the original
pause, with the diagnosis in its reason. It never approves anything, changes requirements or
criteria, weakens tests, grants permissions or extends budgets beyond that one attempt.
Permission, scope, goal, criteria and spend pauses go straight to you, and so does a pause a
guard re-raises on resume before any stage runs, or the outcome of a retry you authorized
with `--retry-failed-stage`. Only full Autopilot runs investigate; a
single-unit invocation (`--unit`) stops at its own boundary. A native visual run
(`VISUAL_REVIEW_PROFILE=`), whose scope cannot run the Investigator, never sends a stalled
stage to it, so that pause keeps its original reason and spends no investigation. A Builder
failure that needs classification still queues the Investigator there; native authority
refuses it, and the run pauses `PAUSED_BUILDER_CLASSIFICATION` at the Builder with that
classification spent, as before. `--diagnose-failed-stage`
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
questions cannot silently disappear from the draft. A handoff that has only questions so
far, with no behaviors or acceptance tests yet (a vague request), stops the run for your
answers before any Planner call, on every flow; Requirements then runs again with them.

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

The final review returns a reference to the revised plan rather than a copy of it. Inside
that reference it may correct **acceptance criteria only**, as a delta on the retained rows
(changed or new rows, removed IDs, corrected proofs); every other row is kept byte for byte.
Each correction passes the same change-receipt, source-coverage and proof guards as a
Planner revision, so a removed or reworded approved criterion still needs your saved answer
or feedback. Milestones, the technical approach and permissions stay the Planner's: a
problem there comes back to you as a blocking question.

A Planner revision that answers the Plan Reviewer's blocking concern may change one of its
own lines without you while nobody has approved or edited the plan: a constraint it may
reword or drop, a permission boundary it may replace. The line must be the Planner's (it
repeats no sentence of your brief, your feedback or your answers), the change is declared
as the Planner's own with its replacement, and the approval screen lists it under "Declared
contract changes" with the concern that asked for it; the final review judges it. Required
behaviors, scope exclusions, failure cases and any line that repeats your words still need
your saved answer or feedback, and so does every line once a plan was approved or edited.

Planning defaults to **two Plan Reviewer reviews per cycle**. A review counts once it
returns a report, whether or not the runner then accepts that report. An attempt that
times out or whose provider fails returns no review, so it is given back before the next
attempt; the repeated-failure limit, not this allowance, stops a review that keeps failing.
Bounded recovery does not otherwise extend the allowance. Unresolved final decisions return
to you as blocking questions. Once you answer them, planning starts a new cycle, and its first
review receives the previous review's concerns, its decisions and your answers
(`previous_review`), so it checks your answers instead of reviewing the plan from scratch. A
technical question that AutoCode settles with the Planner's proposed default (a
`technical_decision`, not an answer of yours) does not start a new cycle: the redraft that
follows continues the cycle that asked, with its spent calls, its first review and its
revision carried over (`planning.inherited_review`). The reviewer is told which answers were
AutoCode's, and with one call left the final review decides on the redraft; what it cannot
settle comes back to you as blocking questions. Only when the final review itself asked that
question with no call left does the allowance grow by one call, once per run and never past a
limit you set. Model output never renews the allowance; your answers and feedback do. If the
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
remain archived. Ordinary resume preserves the cycle and its spent budget. With `--planning-v2`,
the new cycle begins when Requirements hand over a changed handoff after your feedback, answer or
follow-up (or a program's plan guidance); the same handoff again, or a Requirements report retried
with nothing new from you, keeps the cycle and its spent reviews.

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
    `autocode issue` lists them.
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

For a feature with several milestones and named test criteria, a saved literal
pytest or verbose unittest file/module list includes the run's cumulative new
or changed Python test files at each proof. Earlier selectors, the interpreter,
environment assignments and collection options remain in the executed command;
the saved setting stays unchanged. A selected `--test-root` still bounds added
paths. Filters such as `-k` and `-m` remain authoritative, so a filter that omits a
required case still fails the proof. A missing, failing or deleted acceptance
test also blocks completion. This accumulation does not apply to an explicit
bug-fix regression command. Shell programs, unknown option arity, `--pyargs`
and discovery commands retain their existing selection; correct an unsupported
saved command at the reconciled pre-validation pause with
`--resume-paused --regression-command CMD`.

### Scoped Python proof

Start a new run with `--test-root components/api/` to detect and run the Python
suite inside that workspace-relative directory. The caller chooses a literal
directory, not an absolute path, parent path or glob. The root is saved at launch
and cannot be changed on resume. Through `TaskRun`, pass it in `start_options`,
not the options repeated on every advance ([Task-run interface](task-run.md)).
Omitting it keeps ordinary whole-project detection unchanged.

Scoped detection honors current and pinned-base pytest policy within the root
and its ancestors, including `pytest.ini`, `.pytest.ini`, `pytest.toml`,
`.pytest.toml`, `conftest.py` and conditional configuration in `pyproject.toml`,
`setup.cfg` and `tox.ini`. It never substitutes unittest because configured
pytest is unavailable. Pinned policy links are resolved from Git blobs; current
policy reads use non-following directory descriptors. Both use bounded,
repository-local link resolution. Current policies over one MiB are unknown;
unknown, unreadable, special-file or unsafe policy leaves
scoped detection unavailable. Unittest package hooks and relative imports remain
part of collection.

A first feature can prove its tests when the pinned base holds nothing under
the root and the independently detected complete rooted suite passes. This is
evidence for a first suite, not a claim that a baseline passed. Explicit suite or
regression commands and arbitrary filtered caller-supplied frameworks get no such
exemption. Existing passing tests still cannot fail, be skipped or disappear;
source identity, command ownership and completion checks are unchanged.

Changes outside the selected root remain `UNVERIFIED`, even with explicit test
commands. For an ordinary task this is test selection, not permission to write
only that directory or a filesystem sandbox. An identified generated component
brief also binds its declared affected paths and file deliverables to the
caller's owned root. Its mandatory flow must be executable locally by that
child; deferred container builds, Compose and cross-service checks belong to
the separate integration owner, not the child's mandatory flow. A retained
conflicting plan stops at `PAUSED_COMPONENT_PLAN`: use `--edit-goal FILE` or
`--feedback TEXT` and freshly approve the corrected draft. An unchanged resume
cannot repair it or invent missing caller scope for a legacy run.

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

### Monorepo packages

Start a new run with `--package packages/api/` when the task belongs to one
package of a larger repository. The path is a plain directory relative to the
repository root, in the same form as `--test-root`. It must hold files in the
checkout the run uses: committed files for a new task worktree, which starts
from `HEAD`, or files Git does not ignore for an `--in-place` run. The package is
saved with the run and cannot be changed on resume. Through `TaskRun`, pass it in
`start_options` ([Task-run interface](task-run.md)). `--status` shows it as
`view.package`.

What the package changes:

- Test detection reads only the package, as if it were the project root: its own
  `pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml` and test files choose
  the runner. The detected suite runs from the package directory, and receipts
  show the command that ran, for example `cd packages/api && go test -json ./...`.
  The regression command derived from the changed tests, and the repeated runs
  of the proving tests, run there too, with per-test results as for a whole
  project. A test's name is relative to the directory its runner treats as the
  root, normally the package (`test_greet.TestGreet.test_x`), and a node:test file there that runs another test runner is still not named
  proof (#380). A feature that adds the first Go module in the package, on a
  base with no Go project, is not held to a base Go suite that cannot exist,
  as at the root ([#685](bugs/2026-10-07-first-go-suite.md)).
- Only the package's existing tests are retained as
  [original test gates](protected-tests.md), with the package suite as their
  command unless `--test-command` gives one, so a large repository does not bind
  thousands of unrelated tests. The replay of the package suite is read in the
  package as the same suite is read at the root.
- In a bug fix, the Investigator's handoff names the package
  (`package_scope`) and, when the run has one, `suite`: the command the run
  tests the package with (the suite detected there, or `--test-command`). A
  reproduced diagnosis must name only files or directories inside the package in
  `affected_paths` and `test_paths`. One that names anything else (another
  package, a file at the repository root, a shared lockfile) is refused as
  invalid output and takes the path of any refused report: the Investigator is
  asked to repair it with the reason, and the run pauses rather than plan from a
  report that still names such a path. The diagnosis note stays under
  `docs/bugs/` at the repository root, where the runner writes it. A bug whose
  fix is outside the package needs a run without `--package`: the Investigator
  is told to name the file it would change there anyway, so its report is
  refused and the run pauses rather than plan the fix.
- The package is the Builder's write scope, and its handoff says so
  (`write_scope`). Apart from the Investigator, no other stage is told the
  package. Before each Tester and
  Completion Reviewer dispatch, in every package run, whether or not it owes a
  regression proof, the runner compares the candidate with the run's base, as
  the regression proof reads it. The Code Reviewer, Designer and Design
  Reviewer dispatches are not checked. A file added, modified (its content or
  its mode) or deleted outside the package (another package, a file at the
  repository root, a shared lockfile or workspace file; a rename counts on both
  sides) opens one blocking finding with source `runner` that names each path
  and what happened to it. A file restored with the same content but a
  different mode still counts as modified. The completion gate and a
  milestone's acceptance refuse while the finding is open; every stage sees it
  in `open_findings`, and a correction task is assigned it. Only the runner
  closes it, when a later check finds no change outside the package; no
  reviewer report can. Closing it with `--close-finding` accepts nothing:
  `--accept-completion` runs the same check once its own checks pass (a
  finished run, an unapproved plan or missing evidence is refused for that
  reason first) and refuses while the change is there, and the next check
  before review opens a new finding. A task that must change files outside the
  package, such as a root lockfile, needs a run without `--package`.
- What the check does not count outside the package:
  - what the regression proof does not count either: AutoCode's own
    `.autocode/` files, bytecode, the runner's dependency links, and an
    untracked file Git ignores, unless the approved plan names its path;
  - an untracked Git repository or a submodule's uncommitted work that the
    checkout already held when the run started, which the run's base cannot
    hold. Every new package run records what is there when its base is taken
    (none, in a new task worktree). While such a path is still a Git
    repository, it and everything under it are left out, so a later change
    inside it is not seen. An untracked repository that is later removed
    entirely was the person's own and names no change of the candidate: it
    does not hold the run. A deleted submodule, or a path that is no longer a
    Git repository (replaced by a file, or its `.git` removed), is named
    apart from the Builder's changes, and the finding tells the Builder not
    to delete, move or reset anything there: the person restores it. A
    package run saved before this record existed has none, so its check
    leaves out every nested Git repository and submodule work it finds
    outside the package rather than report the person's own as the Builder's;
  - the files the runner wrote for this run, while they are as the runner
    wrote them: a bug fix's diagnosis note under `docs/bugs/` (compared with
    the investigation the run saved, whether or not it reproduced the bug),
    a review's or design review's report (its saved hash) and a design
    check's blockers file (its saved hash). Once changed, the finding names
    them apart and tells the Builder to put back what the runner wrote, not
    to delete them. A check of the same design that later passes removes the
    blockers file an earlier, stopped check wrote, so no stale one is left
    behind;
  - what an earlier, finished turn of the run left (a discussion's note, a
    design's documents, the other files the turn changed), and a blockers
    file a run saved before it recorded the hash, are recorded by path only:
    they are left out by path, so an edit to one is not seen.

  The regression proof's list of changes outside the package leaves out the
  same paths, apart from the person's Git work that is no longer as it was.
- A runner regression proof (a bug fix, or a plan whose criteria name tests)
  also leaves a change outside the package `UNVERIFIED`, because the package
  suite never ran it.

The `feature-monorepo-package` scenario
([scenarios/README.md, Catalog](../scenarios/README.md#catalog)) runs a build in
one package of a two-package repository with `--package` and the scripted
model. Its reference completes; its broken variant, which also edits the other
package, stops with each of those files named in the open `runner` finding.
`bugfix-monorepo-package` does the same for a bug fix: the Investigator runs
first, the proof runs in the package, and the runner's diagnosis note under
`docs/bugs/` is not counted as a change outside it. Its scripted Investigator
names only the package's files, as its handoff asks. Its broken variant also
fixes the other package's copy of the bug and stops with that file named.

An explicit `--test-command` or `--regression-command` still runs from the
repository root, exactly as given; write `cd packages/api && …` to run it in the
package. A `--test-command` in exactly that form for the run's own package, with
a runner AutoCode reads per test (`python3 -m unittest`, `python3 -m pytest`,
`node --test` or `go test`), is read with that runner inside the package, as the
proof reads such a command at the root. The proof, the named-proof preflight and
the protected-test replay all read it that way. Another command written for the
package, such as `cd packages/api && make test`, is read by the runner detected
there in the proof and the named-proof preflight; the protected-test replay judges
it by its exit code. `--package` cannot be combined with `--test-root`.

The learned test command in [project memory](project-memory.md) belongs to the
whole repository: a package run does not take it as a default, and does not save
its own test command or framework there.

In every run, with or without `--package`, a command that AutoCode replays in a
scratch copy (a check, a probe or the protected-test replay) and that has exactly
the form `cd DIR && RUNNER`, with a plain relative directory (no quotes or
trailing slash) and one of those runners, runs with that runner's per-test
results read inside `DIR`. A run of zero tests, of only skipped tests or with
incomplete results is then refused, as for the same runner at the root. A direct
node:test command in that form always runs fresh and is never reused from the
baseline cache, as at the root.

Not covered yet (#711):

- Nothing limits what the Builder writes while it works: a change outside the
  package is found when the candidate is checked before review, not refused when
  it is written.
- Detection in the package does not read pytest configuration in parent
  directories (a root `pyproject.toml` `[tool.pytest]` section, `pytest.ini` or
  `conftest.py`), so a package that relies on it is detected as unittest. Give
  `--test-command 'cd packages/api && python3 -m pytest'` instead. pytest then
  takes the repository root as its root, so test names stay relative to the
  repository (`packages.api.test_basic::test_adds`).
- Detection in the package finds a Go suite only from the package's own
  `go.mod`, so it finds no suite in a package of a single Go module (one `go.mod`
  at the repository root, packages in its subdirectories). Give
  `--test-command 'cd packages/svc && go test ./...'`. A root-form command such
  as `go test ./packages/svc/...` is read per test by the proof, but the
  named-proof preflight of a `--task-preflight` manifest judges it against the
  suite detected in the package, finds none, and refuses it.
- Native visual checks cannot run a command in the form `cd DIR && RUNNER`:
  such an approved check pauses with `PAUSED_VISUAL_EVIDENCE` ("foreign suite
  instrumentation"), in a run with or without `--package`. A package run's own
  suite has that form, so its native visual checks cannot run it.
- The test environment still comes from the repository root: the Python
  interpreter is the root's, and only the root's `node_modules`, `.venv` or
  `venv` are linked into scratch copies. A package's own `node_modules` or
  `.venv` is not used.
- A package run never reuses an earlier passing Python proof; it runs again.
- The first-Go-module rule (#685) still looks for a Go project in the whole
  repository. A Go module, workspace or Go source in another package counts, so
  a first Go module in this package is then held to a base suite that cannot
  run, and the proof stays `UNVERIFIED`.
- The Tester's acceptance tests, written before the Builder starts, still run
  with the suite detected at the repository root.
- Diff coverage and the mutation report, which never change a verdict, are not
  narrowed to a package's commands and may be skipped.

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
- A probed claim also carries `counterexample`: a unified diff of the cited
  code under which the claim would be false. The runner applies it in a fresh
  scratch copy, and the probe must exit non-zero there. A probe that passes
  whatever the code says, such as `python3 -c 'assert 2 + 2 == 4'`, shows nothing
  and rejects the answer. So does a counterexample that does not apply. A
  rejected answer gets the usual bounded report repair; the claim is never
  quietly shown as checked (#1769).
- Claims without a probe stay grounded by their source file only, and the answer
  shows them as read from the code, not as a check that ran.
- Probed claims are recorded in the run state (`answer.probes`, with each
  counterexample's exit code) and shown in the answer's evidence list.

### Design: a concern about today's code carries a probe

The design workflow's Architect judges a document, so nothing is applied. What it
says about the code carries a probe that must exit 0 on the code as it is. It
needs no counterexample yet (#1769).

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
`READY_TO_EXECUTE`; the next ordinary invocation begins execution. The approval records who gave it:
the Git identity, host and command, plus an optional `--as NAME` on a shared machine
([Who approved](execution.md#who-approved)). A project can name the Git identities allowed to
approve; any other approver is refused at the same stop ([Required approver](execution.md#required-approver)). User-input commands
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

### Reviewing a plan in a pull request

On a team, what will be built is worth reviewing before any model builds it.
`autocode plan --export PATH` writes the run's current plan, the one shown for approval or
the approved one, as a Markdown file someone can add to a pull request:

```sh
autocode plan --export plans/refunds.md        # or: --run-dir RUN when the project has several runs
autocode plan --check plans/refunds.md         # prints its goal hash; exits 2 if the file cannot be read
```

The file starts with a front matter between two `---` lines. It is the plan: the contract
body exactly as the run holds it (each criterion with how it is verified and the `test:` or
`guard:` name it cites, the request's exact-output examples, milestones, what is out of scope,
the runner's records), its goal hash, and the plan revision it came from and whether that was
approved. The front matter is JSON, which YAML front-matter readers also accept. The text under
it is generated from the front matter so reviewers can read the plan.

The goal hash is the SHA-256 of the contract body: what the plan says, whatever run or revision
holds it. A plan read back from the file has the same goal hash as the run's. The scenario runner
(`scenarios/run.py`) checks this at every plan approval its catalog runs reach: it exports the plan
and reads the file back with `--check` before it approves the plan
([scenarios/README.md](../scenarios/README.md)). The approval token is not in the file, so
committing it approves nothing. Text that looks like a credential is masked before the file is
written, with the same policy as `autocode bundle`; the export then says that the file's goal hash
differs from the run's.

A reviewer edits the front matter, then runs `autocode plan --render FILE` to regenerate the text.
`--check` then prints the new goal hash and the one it was exported as. It exits 2 when the text no
longer matches the front matter, for example when only the prose was edited, and names the line.
It checks the file, not the plan: whether a run accepts the plan is decided in that run.

Once the pull request is merged, load the file into the run that drafted the plan:

```sh
autocode --goal-file plans/refunds.md          # or: --run-dir RUN
```

The file's plan becomes the run's next draft revision, as a body given to `--edit-goal` does, and
any earlier approval token approves nothing. AutoCode first checks it as it checks a planner's
draft and refuses it, leaving the run as it was, when:

- the file cannot be read, or its text no longer matches its front matter;
- it still holds `[REDACTED]` where the export masked credential-shaped text: loading it would make
  the mask the plan's text. Restore the masked values (or reword that text) and render the file again;
- the plan drops a literal the request states, such as `todo.py complete ID` or a format like
  `ID TEXT [open|done]` (the message is the one the planner gets);
- a Go test the request names is not proved by a criterion under that name;
- a check names `git status`, or plans a command the shell cannot parse;
- the runner's records in it (`brief_acceptance`, `risk_acceptance`, `source_trace`) are not the
  run's current ones, because the file came from another run or from a plan revision the run has
  since replaced. Export again, or delete those keys from the front matter to keep the run's own.

It is refused too at an operational pause that does not offer feedback, as `--edit-goal` is.

Then the plan goes through ordinary approval. In joint planning the Plan Reviewer reviews it first,
as it reviews any edit, so resume the run (`autocode --run-dir RUN`) to get to the approval stop.
That stop names the file and its goal hash, and says whether the plan it asks you to approve is
still the plan loaded from it. A review of an unchanged plan records the new review in the runner's
records, so that plan's goal hash differs from the file's; any other change is named. The approval
token locks the revision on the screen, as always. `--goal-file` revises a saved run; it does not
start a new one.

When the run completes, its evidence report (`evidence.md`, and so the pull request body
`autocode-issue pr` writes around it) gives the approved plan's goal hash, the one `--check` prints
for a file holding exactly that plan. When the approved plan came from a plan file, the report also
names the file, its goal hash and the plan revision it was loaded as, and says how the two differ:
the approved plan is the file's plan unchanged (the two goal hashes are equal), differs from it only
in the runner's records of the plan's review, or differs from it in the parts it names. A plan
approved, changed and approved again after the load is the later approval's plan, so the report
names no file for it. For example:

```text
Plan goal hash: 3f1c… (what `autocode plan --check FILE` prints for a file holding exactly this plan).
Plan file: `/repo/plans/refunds.md` (goal hash 9a07…), loaded as plan revision 2; the approved plan differs from that file's plan only in the runner's records of the plan's review.
```

### A team's plan review

Put together, a team reviews a plan like this:

1. **AutoCode drafts the plan.** A run stops with its plan waiting for approval. For a GitHub issue,
   `autocode-issue start` gets it there.
2. **A person opens a pull request with the file.** `autocode-issue plan ISSUE --open` exports the
   plan, commits it as `plans/issue-N.md` on a branch of its own and opens a draft pull request
   ([GitHub issues](issues.md#reviewing-the-plan-in-a-pull-request)). Without an issue, run
   `autocode plan --export FILE`, then commit, push and open the pull request yourself.
3. **Reviewers edit it.** They change the front matter, regenerate the text with
   `autocode plan --render FILE` and check it with `autocode plan --check FILE`. Merging approves nothing.
4. **The merged file goes back to the run that drafted it.** `autocode --goal-file FILE --run-dir RUN`
   loads it as that run's next revision, and a person approves it at the approval stop as usual.

Not available yet: starting a new run from the merged file (`autocode "TASK" --goal-file FILE`), for
example on another machine or once the drafting run is gone. A plan file carries the exporting run's
records of the plan's review, and how a new run should treat them is still to be decided (#720). Until
then, `--goal-file` revises a saved run; it does not start one.

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

## The run's own worktree

A new task runs in its own Git worktree under `.autocode/worktrees/<run>/` on a
branch `autocode/<run>`, started from the current `HEAD`. The person's checkout
is never read after the start: snapshots, protected tests and the check replay
all operate on the worktree (#724). `--in-place` keeps the old behaviour and
runs in the checkout you started from.

When the run reaches `TASK_COMPLETE`, the delivered source is committed to the
task branch. Bring it back into the branch you started from with:

```sh
autocode merge            # the only task worktree; or name it
autocode merge --into main
```

`merge` fast-forwards when it can and makes a merge commit otherwise. If the
base branch moved after the run started, it refuses and tells you to re-validate
on the current source first (the same rule `--resume-paused` applies). Then
remove the worktree with `autocode clean-worktrees --yes`.

## Project conventions across tasks

A completed run can record reusable conventions that the person actually
answered. The next task includes them as historical Requirements context, so it
can avoid repeating an unchanged question. It still defines its own criteria
and requires its own approvals. See [Project memory](project-memory.md) for the
learned file, explicit-setting precedence, doctor and forget.
