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
Reviewer concerns have stable IDs; every concern requires a Planner response and a reviewer decision,
including a concrete acceptance test. The final displayed brief includes the technical
approach, milestones, and **first bounded implementation task**, all covered by its
revision/hash. Approval dispatches that task directly, without a third Plan Reviewer
call. The separate Completion Owner's later decisions use the normal execution budget.

Planning is bounded to **two Plan Reviewer request attempts per cycle**, including failed or
abandoned attempts. There is no automatic debate loop, retry or provider fallback.
Unresolved final decisions return to you as blocking questions. If the budget is
exhausted, the run pauses at `PAUSED_PLANNING_BUDGET`; inspect the exchange and explicitly
send `--feedback '...'` to request a new cycle. Answering final blockers, giving feedback,
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
