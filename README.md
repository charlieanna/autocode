# Autocode

Start with a rough idea and discuss it with GLM. GLM helps define the smallest
useful end-to-end product, asks focused questions, and drafts a versioned build brief.
Astra challenges that draft; GLM revises; Astra finalizes. You can revise the brief
in the same conversation. Implementation starts only after you explicitly approve it.

After approval, Autocode handles the handoffs:

```text
You ↔ GLM: rough idea → clarification → draft brief
GLM explores → Astra challenges → GLM revises → Astra finalizes → your approval

Astra assigns → Terra builds → Sol verifies → Astra decides
                    ↑                            |
                    └──── CONTINUE / REWORK ─────┘
```

Astra owns the final planning decisions and the completion decision. Terra implements
one bounded task. Sol independently inspects and tests the actual code. They all work
from the same approved brief; you do not explain the product to each agent or relay
their prompts. The runner saves decisions, tasks and evidence so it can resume.

Works against any committed Git workspace; no IdleCampus files or services are required.

Requires Python 3.11+, Git, authenticated OpenCode 1.x, and Codex signed in with ChatGPT.
macOS/Linux are supported; Windows needs WSL because the inherited process and lock
mechanisms use POSIX APIs. There are no Python runtime dependencies. Installation does
not change Codex or OpenCode settings. `--engine codex` still starts a Codex-only run.

## Run or install

Run directly from this checkout:

```sh
python3 /path/to/autocode/tools/autocode.py "Build a greeting CLI" \
  --workspace /path/to/project --reasoning-effort high
```

Install the command once with `pipx` to invoke it from any project:

```sh
pipx install --editable /path/to/autocode
```

Or install into your own virtual environment:

```sh
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/autocode "Build a greeting CLI" --workspace /path/to/project
```

New runs use joint GLM/Astra planning by default. `--joint-planning` is accepted and
redundant. `--engine codex` is the explicit single-CLI loop; it does not use joint planning.

| Role | CLI and billing route | Default model |
| --- | --- | --- |
| GLM — clarification, exploration, draft, evidence-backed revision | OpenCode / Z.ai Coding Plan | `zai-coding-plan/glm-5.3` |
| Astra — challenge, final planning decisions, implementation reviews | Codex / ChatGPT login | `gpt-6-astra` |
| Terra — implementation | OpenCode / Z.ai Coding Plan | `zai-coding-plan/glm-5.3` |
| Sol — independent validation, separate session | Codex / ChatGPT login | `gpt-5.6-sol` |

From inside any committed Git project, the normal invocation is simply:

```sh
autocode "Your rough idea"
```

## Browser dashboard

Autocode includes a local browser dashboard for planning work with GLM, approving
the Astra-reviewed plan, following active tasks, and sending feedback or a pause
request at a safe boundary. It reads the same local run registry as the command
line tool, so tasks started from a terminal appear automatically.

After installing this checkout, start it from any directory:

```sh
autocode-dashboard --port 8767
```

Or run it directly from a checkout:

```sh
python3 tools/dashboard/agent_console.py --port 8767
```

Open the printed loopback URL. New conversations do not require a project: GLM
can clarify the idea first, then the conversation can be attached to a Git
workspace for joint GLM/Astra planning. A task’s **Now** view is the source of
truth for its current stage, the exact user action required, plan-revision
approval, and output-review approval. Archiving tasks, conversations, or
projects only changes the dashboard’s local visibility; it never deletes source
files or runner checkpoints.

The dashboard uses `$AUTOCODE_HOME/dashboard` by default (or
`$AUTOCODE_DASHBOARD_HOME`) for conversations and reversible archive settings.
It binds only to `127.0.0.1`, starts no development server for task previews,
and uses documented Autocode commands for task changes.

## Browser registry API

Autocode automatically records every real new run and ordinary resumed run before a
provider can start. The registry is a small per-user pointer index, not a copy of
`state.json`, logs, prompts, credentials, or provider configuration. It is stored at
`$AUTOCODE_HOME/registry.json`; when `AUTOCODE_HOME` is unset, the storage root is
`~/.autocode`. Set `AUTOCODE_HOME` for isolated installations and tests.

The browser application should invoke these commands as argument arrays, without a
shell, using the same environment as the runner:

```text
["autocode", "registry", "location", "--json"]
["autocode", "registry", "list", "--json"]
["autocode", "registry", "import", "/selected/root", "--max-depth", "3", "--directory-budget", "10000", "--json"]
```

`location` and `list` always emit versioned JSON and are read-only: they do not create the
storage directory or lock file, start/resume a task, migrate a checkpoint, or alter a
task file. `location` returns `registry_version`, `storage_root`, `registry_path`, and
`exists`. `list` returns `registry_version`, `workspaces`, `runs`, and `diagnostics`.
Run records contain stable canonical-path-derived `id`, `workspace_id`, `workspace`,
`run_dir`, and `task_id` when the run-level checkpoint identity is available. Listings
derive only a
small current summary (`status`, `phase`, `next_stage`) from a valid referenced
checkpoint.

Each listed run has an `availability` value. `available` includes that summary;
`workspace_missing`, `workspace_invalid`, `checkpoint_missing`, `checkpoint_malformed`,
`containment_invalid`, `checkpoint_unsupported`, `inaccessible`, and
`malformed_record` retain an honest stale or invalid pointer
instead of pruning or repairing it. An absent registry is a successful empty result with
the `registry_absent` diagnostic. Corrupt or unsupported registry storage returns JSON
with an `error` object and exits 2 without replacing the file. Successful location/list
operations exit 0.

Registration resolves workspace and run aliases before deriving IDs. The run must be
contained by the canonical `<workspace>/.autocode/runs` directory and its direct
`state.json` must identify that same canonical workspace. The registry stores no alias
and repeated aliases deduplicate. Updates use fsync-backed atomic replacement under a
dedicated registry lock with a one-second bounded wait. A runner first holds its
workspace writer lock, then obtains the registry lock only for the central
read/update/write, releases it, and only then proceeds toward a provider stage.
Registration failures pause the preserved run as `PAUSED_REGISTRY`; fix storage
and explicitly resume the same `--run-dir` to retry its stable identity.

`registry import` is the only registry discovery operation that writes. It requires an
explicit selected directory, resolves that root canonically, and searches the root at
depth zero through depth 3 by default. `--max-depth` must be a nonnegative integer;
`--directory-budget` must be a positive integer and defaults to 10000. The budget counts
each unique canonical directory inspected, including the selected root and direct run
candidates; canonical aliases do not consume the budget twice. Repository internals
(`.git`), `.autocode` contents, and run contents are pruned from general workspace
discovery.

Import follows only canonical directories contained by the selected root, deduplicates
aliases and cycles, and reports aliases that escape it before reading their candidate
contents. It validates each discovered canonical workspace, run and direct `state.json`
using the same containment and readable checkpoint rules as registration. Valid legacy
checkpoints do not need an approved goal and their bytes are never migrated or changed.
Canonical path identities, rather than `task_id`, determine uniqueness: two distinct
runs with the same task ID remain distinct; repeated imports report `already_registered`.

The JSON result includes `selected_root`, effective bounds, `directories_inspected`,
`imported`, `already_registered`, `diagnostics`, and `complete`. Expected malformed,
inaccessible, escaped, duplicate, or out-of-bound candidates are diagnostics. Budget
exhaustion, inability to enumerate a workspace or its `.autocode/runs` candidates,
unreadable traversal branches, and escaped aliases set `complete` false and exit 1, so
retry with a narrower root or deliberately larger bound. Fully enumerated candidates
with invalid individual checkpoints remain diagnostics without making the traversal
incomplete. Invalid arguments/root and registry lock/storage/write failures emit an
`error` or `registry_error` object and exit 2; candidate registration validation failures
such as a non-Git workspace conservatively use that same `registry_error` exit and abort
the pass. Any earlier acknowledged imports remain durable and it is safe to retry. Import
never starts or resumes providers and does not modify imported task files.

## Queued interventions

The browser can submit a durable change request without competing for the workspace
writer lock or changing `state.json`:

```text
["autocode", "intervention", "submit", "--workspace", "/project", "--run-dir", "/project/.autocode/runs/run", "--request-id", "request-123", "--kind", "feedback", "--text", "Keep the partial implementation", "--json"]
["autocode", "intervention", "submit", "--workspace", "/project", "--run-dir", "/project/.autocode/runs/run", "--request-id", "pause-124", "--kind", "pause", "--json"]
["autocode", "intervention", "inspect", "--workspace", "/project", "--run-dir", "/project/.autocode/runs/run", "--json"]
```

`submit` writes only the canonical run's `.autocode/runs/<run>/interventions.json`
under a separate short-held inbox lock. It validates the Git workspace, canonical run
containment, direct regular `state.json`, checkpoint workspace pointer, and rejects
symlinked inbox or lock paths. The versioned receipt has `id`, `kind`, original `text`,
monotonic `order`, `submitted_at`, `observed_goal_token`, and
`boundary_pause_requested`. Both feedback and pause requests set that boundary intent;
they never start a provider or write `state.json` from the submitting process.

Request IDs are idempotency keys over `id`, `kind`, and original `text`: an identical
retry returns the original durable receipt without another record, while changed text or
kind under the same ID returns `request_conflict`. Concurrent submissions are serialized
by the inbox lock and receive durable order values. Corrupt, unsupported, unavailable,
locked, invalid, or failed-write inboxes return JSON errors and exit 2 without claiming a
receipt. `inspect` is read-only, creates neither inbox nor lock, and reports only pending
requests; it does not claim that a currently running older binary can consume them.

The workspace-lock owner checks the inbox after recovery and before every provider
admission, and after every saved stage result, including question, approval, review and
completion exits. Inbox acceptance and admission are serialized by the short inbox lock:
a request accepted before admission is consumed first; one accepted after admission waits
for that stage's saved boundary. The inbox lock is never held while a provider runs.
Consumption writes the applied receipt ledger and feedback event to authoritative
`state.json` before removing inbox records. A crash before that state write leaves the
request pending; a crash after it is recovered by recognizing the applied ID and retrying
only inbox acknowledgement. If interruption follows inbox acknowledgement but precedes
the final state write, owner recovery clears the obsolete acknowledgement marker only
when every marked ID is already applied. Receipts retain their original IDs, text and
order.

Applied feedback saves a `brief_feedback` provenance event, preserves stage artifacts and
partial edits, archives stale validation/review authorization, invalidates goal approval,
and pauses with `astra_discovery` selected. It never starts Astra automatically: invoke
the existing explicit Continue action as an argument array, for example
`["autocode", "--workspace", "/project", "--run-dir", "/project/.autocode/runs/run", "--resume-paused"]`.
The revised brief still requires its exact displayed approval token. Pending feedback also
blocks goal approval, artifact approval and completion until the owner consumes it.

A pause-only request preserves the selected next stage and any valid goal approval. It
pauses at the next safe boundary with a `pause_intent`; `--resume-paused` records its
acknowledgement and resumes that selected stage. `--pause-after-stage` and the existing
run-local `pause-requested` file continue to stop at saved boundaries. `--status` is
read-only and adds `interventions` with inspector versus recorded-runner capability,
pending IDs/count, pause intent, applied receipts, inbox errors and blocked conditions.

Use `--astra-model`, `--terra-model`, or `--sol-model` to explicitly override a
role. Resuming keeps the saved engine, provider mapping and limits unless overridden.

## Joint GLM + Astra planning

This is the new-run default. Saved runs keep their original routing.

```sh
autocode "Your rough idea"
# Or target another committed Git workspace:
autocode "Your rough idea" --workspace /path/to/project
```

```text
You ↔ GLM: clarify outcome, scope, constraints and definition of done
GLM explores and drafts → Astra challenges → GLM investigates and revises
    → Astra resolves and finalizes → you approve that exact plan
    → Terra builds → Sol verifies → Astra reviews
```

| Role in this mode | CLI and billing route | Default model |
| --- | --- | --- |
| GLM — clarification, exploration, draft, evidence-backed revision | OpenCode / Z.ai Coding Plan | `zai-coding-plan/glm-5.3` |
| Astra — challenge, final planning decisions, implementation reviews | Codex / ChatGPT login | `gpt-6-astra` |
| Terra — implementation | OpenCode / Z.ai Coding Plan | `zai-coding-plan/glm-5.3` |
| Sol — independent validation, separate session | Codex / ChatGPT login | `gpt-5.6-sol` |

GLM can originate alternatives and push back on Astra using source evidence. Astra's
concerns have stable IDs; every concern requires a GLM response and an Astra decision,
including a concrete acceptance test. The final displayed brief includes the technical
approach, milestones, and **first bounded implementation task**, all covered by its
revision/hash. Approval dispatches that task directly, without a third Astra planning
call. Astra's later implementation reviews use the normal execution budget.

Planning is bounded to **two Astra request attempts per cycle**, including failed or
abandoned attempts. There is no automatic debate loop, retry or provider fallback.
Unresolved final decisions return to you as blocking questions. If the budget is
exhausted, the run pauses at `PAUSED_PLANNING_BUDGET`; inspect the exchange and explicitly
send `--feedback '...'` to request a new cycle. Answering final blockers, giving feedback,
or editing the goal starts fresh joint review and requires fresh approval. Old exchanges
remain archived. Ordinary resume preserves the cycle and its spent budget.

Both CLIs must be installed and authenticated. Codex must report a ChatGPT login;
API-key environment overrides, custom Codex provider selection and endpoint overrides
are rejected. Astra and Sol launch with the OpenAI provider and `forced_login_method=chatgpt`,
using separate sessions. GLM and Terra are pinned to the `zai-coding-plan/` provider.
You may select another Coding Plan model with `--glm-model` or `--terra-model`;
`--astra-model` and `--sol-model` take Codex model names. Usage/provider failures pause without silently switching to
separately billed API access. Actual subscription entitlements are managed by the CLIs.

Planning requests use fresh sessions and focused handoffs: the current brief, code
references, alternatives, concerns, responses and changes since review. Full reports
remain in the run's `iterations/` directory. Codex planning uses its read-only sandbox.
OpenCode planning uses a fresh, read/search-only agent with shell, edit, delegation,
external-directory access and unlisted tools denied; workspace snapshots are also
checked. These OpenCode restrictions are tool permissions, not an OS sandbox.

The existing `--chat`, `--answer`, `--feedback`, `--show-goal`, `--approve-goal`, status
and resume commands work in this mode. Intermediate drafts cannot be approved. The
saved `planning` object records the exchange, final approval token and Astra call count;
`planning_history` retains prior cycles. Existing runs keep their original routing,
including earlier OpenCode-only runs and joint-planning runs that used GLM for Sol.
Start a new run to use the current GPT Sol default in that case; saved sessions cannot
move between CLIs. No global OpenCode or Codex configuration is changed.

## OpenCode adapter

GLM and Terra use the connections already configured in OpenCode. `--engine opencode`
is accepted but is optional for new runs:

```sh
python3 tools/autocode.py "Your rough idea" --workspace /path/to/project
```

Override GLM or Terra with `--glm-model` or `--terra-model` using a `zai-coding-plan/`
ID from `opencode models`. Astra and Sol stay on Codex model names. OpenCode reasoning
variants can be selected with the existing role-specific reasoning-effort flags; no
variant is forced by default. Provider credentials remain with OpenCode: Autocode does
not read its auth file or change your global configuration.

The same approval, task, independent-evidence and completion gates apply. The adapter
uses OpenCode's [non-interactive JSON event interface](https://opencode.ai/docs/cli/#run),
validates the final report against the stage schema, and verifies command evidence
against actual completed bash events. Raw events, session IDs and stage-local
permission overrides are saved alongside the checkpoint. Token limits include cache
reads/writes and reasoning tokens. Malformed, truncated or uncertain results pause;
the runner does not automatically replay the provider request.

OpenCode has a different isolation boundary: GLM planning agents and other
read-only OpenCode roles have edit tools denied and their workspace snapshots
checked, but OpenCode tool permissions are **not an OS sandbox**. Shell commands
and configured external tools retain OpenCode's native permission policy. Autocode
does not enable `--auto` or override user-level permission rules with blanket allows.
A denied required operation is reported back as a blocker.
See OpenCode's [permission documentation](https://opencode.ai/docs/permissions/).

Resuming preserves the saved engine, models and separate role sessions. Start a new
run when switching between Codex and OpenCode; their session IDs cannot be reused
across engines. A response with an unexpected session ID pauses the run.
OpenCode version or configuration drift pauses the saved run, including changes to
custom config-directory files, agent definitions and local plugin/tool definitions.
This adapter was live-checked with OpenCode **1.18.31**; OpenCode 2.x is not supported.

## Codex provider overrides

Pass `--engine codex` to start a Codex-engine run. Roles can use different
**Responses-compatible Codex providers** in one run, e.g. planning on the ChatGPT
subscription while Sol audits and Terra codes via another compatible provider:

```sh
python3 tools/autocode.py "Build a greeting CLI" --workspace /path/to/project \
  --engine codex \
  --astra-model gpt-6-astra \
  --sol-model audit-model --sol-provider other_provider \
  --terra-model implementation-model --terra-provider other_provider --reasoning-effort high
```

Each role can also have its own reasoning effort. For example, use Astra at
extra-high (`xhigh`) for read-only discovery and planning:

```sh
python3 tools/autocode.py "Build a greeting CLI" --workspace /path/to/project \
  --engine codex --astra-model gpt-6-astra --astra-reasoning-effort xhigh
```

Role-specific effort overrides the shared `--reasoning-effort` value. All selected
models and providers are saved in the run checkpoint, so resumed runs retain this
assignment.

An interactive terminal starts in chat mode by default. Astra presents a few material
questions at a time; type replies directly or `/default` to accept a proposed default.
When the build brief is displayed, type feedback to revise it, `y` to approve that exact
revision, or `/pause` to save and exit. After approval the implementation, validation
and review loop runs automatically within the configured limits. During questions,
`/feedback TEXT` sends a broader correction to Astra.

Use `--chat` to select this mode explicitly, or `--no-chat` for one command per turn.
Non-interactive invocations default to the command-per-turn interface.

`--<role>-provider` is saved per role like models and is passed to Codex as a
`model_provider` override; roles without a provider keep the local Codex login
(ChatGPT auth). The named provider must implement the OpenAI **Responses** API.
For the connected Z.ai Coding Plan, use the OpenCode engine above. Changing the global
provider/auth in local Codex config still pauses a saved Codex-engine run.

## Conversation and approval

The first stage runs **read-only GLM discovery**, presenting a small batch of
material questions or a draft. Chat mode stays in the conversation; command
mode saves and exits at the checkpoint. Intermediate drafts cannot be approved;
Astra still has to challenge, GLM revise, and Astra finalize first. State, answers,
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

`--answer` is repeatable. `--feedback TEXT` saves a correction and returns to Astra
discovery on the next invocation; a revised brief always needs fresh approval.
`--delegate Q1` explicitly accepts that question's proposed
default. Saved answers are included in subsequent interviews; an answered question
ID cannot be requested again. Answers do not approve the task. The approval token
must exactly match the current displayed contract revision. Approval saves
`READY_TO_EXECUTE`; the next ordinary invocation begins execution. User-input commands
never launch an agent. This command-per-turn interface also works from scripts and
other frontends; no continuously attached terminal is required.

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

## Execution and completion

New runs enforce a milestone checkpoint in the runner. Each task names an outcome,
affected paths, requirements, acceptance criteria and a validation plan. Terra can
implement, test and repair within the task; every completed handoff goes to Sol and
then Astra. A switch to a different milestone requires Sol's passing evidence for
**all criteria in the current milestone**, current source/contract/task identities,
intact evidence, no blocking findings, and its required human reviews. A writer's
self-assessment cannot authorize that switch. Later milestones may still have
`NOT_VERIFIED` results; full-task completion still requires all contract criteria
and the complete flow to pass on the current source.

Three independent reviews without any new passing criteria require an evidence-backed
`REWORK` with a changed approach or a smaller batch inside the same milestone. One
automatic replan is allowed; another three reviews without progress pause the run.
Changing files, renaming task IDs, or oscillating between previously passing checks
does not reset progress. The runner compares task fields; Astra remains responsible
for judging whether the changed approach is substantively useful.

Milestones have a 5,400-second active-time budget by default. This includes writer,
reviewer and report-repair attempts after the milestone is assigned (or after an
existing run adopts checkpoints). The budget is checked at stage boundaries and
prevents further writing; Sol/Astra can still validate finished work. It does not
replace per-stage timeouts. Use `--max-milestone-seconds N` to change the saved
budget or `0` to disable this time limit. `--status` includes `milestone_checkpoint`
with the current milestone, evidence progress, budget, replans, completed stage hours
per role, and separately estimated elapsed time for any recorded active stage.
These are elapsed stage durations, not billed model-compute hours or proof a recorded
process is still alive. Ordinary stage updates also print milestone time and progress.

Saved runs keep their existing review routing until explicitly upgraded. At an idle,
reconciled boundary, enable checkpoints without launching a provider:

```sh
autocode --workspace /path/to/project --run-dir /path/to/run \
  --milestone-checkpoints --show-goal
```

For an active run, queue a safe boundary pause and the upgrade:

```sh
autocode --workspace /path/to/project --run-dir /path/to/run \
  --request-milestone-checkpoints
```

The queued request does not edit `state.json`, signal workers, or acquire the writer
lock. The old runner observes the pause marker at its next boundary. Its next launch
reconciles the saved stage and applies the request under the writer lock. An already
running loop continues without an extra resume gate; saved pauses still require
their usual explicit resume. Use `--show-goal` to apply and inspect without launching
a provider. Preexisting operator pause
markers are preserved. Migration keeps models, sessions, the approved contract and
artifacts; final-only or combined-review overrides are archived in the user event,
and existing bounded work goes to Sol first. Legacy briefs retain their current
task's criterion scope without rewriting or implicitly approving a new contract.
If an old stage first needs report-only repair, the upgrade remains queued. An
explicit `--resume-paused` can finish that bounded read-only repair under the old
schema, then apply the upgrade and continue to Sol. It never repeats implementation to migrate.

Astra plans/reviews, Terra implements one bounded task, and Sol independently validates
actual source with evidence per criterion. Each task records its milestone, requirements,
applicable criteria and validation plan. Every role receives the complete approved
contract and current task and echoes the contract revision/hash and task ID. Sol gets
Terra's full implementation report, workspace/revision and actual changes. Astra gets
both reports, milestone status and references to prior validation evidence.

Astra chooses `CONTINUE`, `REWORK`, `BLOCKED` or `COMPLETE`. The first two require a
concrete next task; rework describes the smallest correction for a verified defect.
A `CONTINUE` task with `kind=validate` sends existing work directly to Sol when it only
needs revalidation. Required behaviors and success cannot be changed by a plan.

A material ambiguity, contradiction, infeasible constraint, scope change or extra
permission need is reported to Astra at a safe stage boundary. Astra presents a
`BLOCKED` decision in `WAITING_FOR_USER` when user input is required, with
the discovery, impact, smallest decision, options and proposed delta. Answers return
to discovery; a revised goal needs new approval. Useful partial work is retained.

Completion requires the current approved revision, Sol PASS on the current artifact,
passing evidence for every required criterion, no blocking findings, intact evidence
hashes, actual human approvals where required, and Astra's completion request against
that same revision. New build briefs also require Sol's explicit end-to-end flow result
and its pinned evidence on the current source revision. Completion prints each criterion
with its evidence and any agreed limitations. Unknown, skipped and untested results cannot pass. Green tests
cannot substitute for missing criterion results.

For a human-review criterion, inspect the displayed validation and the actual artifact,
then record your decision in chat or using the displayed artifact-specific token:

```sh
autocode --workspace /path/to/project --run-dir /path/to/run \
  --approve-review C1 --review-token 'r3:<goal hash>@<source hash>:<validation hash>'
autocode --workspace /path/to/project --run-dir /path/to/run
```

Changing the source or validation invalidates reuse of human approval. Goal approval
does not count as approval of a subsequently produced artifact. Resuming a completed
run rechecks its source and evidence before reporting success. Stale completion pauses
for fresh Sol validation; `--status` reports `completion_current` without changing state.
Source snapshots include executable modes and changes inside initialized submodules.

Logical phases are `DISCOVERING`, `AWAITING_GOAL_APPROVAL`, `READY_TO_EXECUTE`,
`EXECUTING`, `WAITING_FOR_USER`, `COMPLETE`, and `PAUSED_OR_BLOCKED`. While an initial
interview awaits answers its phase is DISCOVERING and its status is WAITING_FOR_USER.
`--status` and `--dry-run` are read-only. Exit 0 means an action was saved or the task
completed; exit 2 means user input/pause/error (inspect status, not exit code alone).

Iteration, active-time, per-stage, reported-token and no-progress limits still pause
with a checkpoint. They never cause completion. New runs distinguish inactivity,
tool execution and total stage runtime:

| Setting | New-run default | Meaning |
| --- | --- | --- |
| `--max-idle-seconds` | `300` | Stop a provider with no new recognized activity while no tool is running. |
| `--max-tool-seconds` | `1800` | Stop a tool that exceeds its fixed deadline, including a quiet test command. Output and repeated starts do not extend this deadline. |
| `--max-stage-seconds` | `0` (off) | Optional hard cap for the entire stage, enforced even while activity continues. |

Each flag accepts `0` to disable that limit. Saved stage limits are preserved,
including an existing five-minute cap or an explicit zero; use
`--max-stage-seconds 0` to deliberately remove a saved hard cap. Saved runs gain
the inactivity and tool defaults at their next configured launch. Running worker
processes keep the code/settings they started with until the runner is relaunched.

The watchdog reads raw Codex/OpenCode events incrementally. Completed tools and
new provider text count as activity; repeated event IDs, repeated text, tool-output
updates, malformed lines and arbitrary log chatter do not buy more time. A tracked
tool uses its own clock, so a healthy quiet command can exceed the provider's idle
limit. OpenCode versions that report tools only after completion use a bounded
descendant-process interval instead. Process identity alone cannot distinguish a
tool from a persistent provider wrapper: status labels this fallback as inferred.
Only a newly completed tool can renew that interval; output, duplicate completions
and descendant churn cannot. Without start events, concurrent unreported tools
cannot each have a precise deadline; an explicit stage cap remains available.
Explicit tool starts supersede the fallback. These observations measure liveness, not acceptance progress;
independent milestone evidence remains mandatory.

CLI updates and `--status`'s `active_stage.activity` show provider/tool activity,
elapsed and idle time, active tool time, applicable limits and an observation
timestamp. A saved observation does not prove a recorded worker is still alive.
Timeout records and recovery context distinguish `idle`, `tool` and `stage` causes.
Deadline enforcement runs independently of process-table sampling, state writes
and event-file reads. A blocked observer cannot leave a worker unsupervised.

`--pause-after-stage` and a run-local `pause-requested` file stop at a saved boundary.
For a timed-out provider stage with no terminal response, Autocode confirms its
tracked workers are gone, archives the incomplete request and preserves its partial
edits/logs, clears the uncertain role session, and continues from a fresh recovery
checkpoint. It never replays that timed-out request. Each automatic recovery consumes
the existing no-progress budget. Consecutive timeouts without an accepted stage also
pause at that configured limit for every role, including Astra and Sol. An accepted
stage resets this consecutive-timeout counter. Inspect the saved cause and adjust
limits as needed; explicit `--resume-paused` acknowledges `PAUSED_TIMEOUT_RECOVERY`
and resets that counter while retaining recovery history. Setting `--no-progress-limit 0`
disables this recovery cap as well as the existing unchanged-batch limit.
A terminal response, live worker or requested pause remains paused for inspection.
Other uncertain provider requests still require explicit reconciliation.
`--resume-paused` acknowledges operational pauses only. Saved limits persist unless you
explicitly override them. For example, resume a run paused at its iteration ceiling with
`--resume-paused --max-iterations 25` to set the total ceiling to 25. Changing a limit
does not approve a draft brief.

Provider stages track their subprocesses, including detached tool processes. On normal
exit, timeout or interruption, Autocode stops tracked workers before taking the final
snapshot and releasing the workspace lock. Saved process identities also block a new
runner while known workers remain alive. This is process supervision for trusted local
tools; it does not replace an OS sandbox or contain deliberately hidden daemons.
Rejected and recovered attempts count toward the active-time budget.

A completed response with invalid JSON or an invalid report is archived and pauses;
`--resume-paused` starts a new explicit attempt. Timeouts with no terminal response
receive the bounded automatic recovery above; other uncertain responses need inspection
first. To retain partial edits and set aside a stopped attempt manually:

```sh
autocode --workspace /path/to/project --run-dir /path/to/run --status
# Copy the exact attempt_id from that status, after inspecting its events and edits:
autocode --workspace /path/to/project --run-dir /path/to/run --abandon-stage '001/terra-01'
autocode --workspace /path/to/project --run-dir /path/to/run --resume-paused
```

Abandoning a stage preserves its logs, source snapshots and partial edits, clears that
role's uncertain session and invalidates previous validation. It launches no agent.
On explicit resume, Astra inspects the retained work and chooses the next step, except
in final-audit-only routing where GLM/Terra receives the recovery context directly.
It does not approve an unapproved brief or stop an already-running worker.

Astra/Sol use the read-only sandbox with the Codex engine; Terra uses workspace-write.
OpenCode uses the native permissions and snapshot checks described above. The runner
does not pass blanket auto-approval. Contract permission text is a role instruction,
not a general-purpose OS policy compiler. The Codex sandbox/approval system remains
responsible for individual tool permissions; custom MCP/connector write permissions
should be configured accordingly. This is intended for trusted local repositories.

## Legacy migration — opt-in only

Existing v3 approved contracts retain their exact content, hash and approval. New
discovery drafts include the expanded build-brief fields. Older `TASK_COMPLETE` and
`VALIDATE` stage results remain readable during recovery; newly requested Astra
decisions use the four statuses above.

This extraction does not modify or attach to any existing project/run. Keep a live
legacy process running until it reaches a natural saved exit; it cannot hot-load these
gates. Do not launch a second writer or restore old state over newer work.

At a confirmed idle boundary, an operator may deliberately use this standalone tool:

```sh
autocode --workspace /path/to/project --run-dir /path/to/existing/run --migrate-only
autocode --workspace /path/to/project --run-dir /path/to/existing/run
```

Migration retains the original task, sessions, completed artifacts, stage history,
criteria, plan, iteration and limits. It backs up `state.pre-v2.json` where applicable
and `state.pre-v3.json`, then reconstructs an **unapproved** draft. Old evidence is
archived for inspection and must be revalidated. Astra uses known answers/artifacts
to ask only material unresolved questions. User decisions are never backdated.
Completed interrupted stages reconcile before migration; live/uncertain stages
refuse migration. No migration was applied to the original IdleCampus run.

## Tests and evidence

```sh
python3 -m unittest tools/test_autocode.py tools/test_goals.py tools/test_subprocess.py tools/test_opencode.py tools/test_process.py
```

The unit suite uses isolated Git fixtures. The subprocess test drives the actual CLI,
runner, schemas, snapshots and approval commands with a deterministic **fake Codex**
provider. That provider writes a tiny greeting program, then executes both success and
failure cases in its Sol stage. It never calls a real model or reads credentials.
Set `AUTOCODE_TEST_CLI=/path/to/installed/autocode` to test the installed package.
These tests prove runner behavior, not a model's interviewing quality or semantic
review accuracy. See `VALIDATION.md` for measured results and remaining limits.

The OpenCode subprocess tests use a fake OpenCode executable with native event shapes,
including separate resumed sessions, command evidence and usage. An optional tiny
live check is available via `python3 tools/opencode_smoke.py --run-live`; it makes one
request to each selected provider in a temporary workspace and saves raw evidence.
Process tests require local `ps` access and exercise detached-worker cleanup, timeouts,
interruption and checkpoint-write failure. The repair audit under
`audits/opencode-repair-2026-09-19/` also verifies the original defects with native
OpenCode metadata and a loopback provider fixture, without hosted model requests.

Codex launch compatibility was checked against installed exec/resume help and
[official non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

### Intervention ordering and recovery

Each request ID retains its original receipt after application. An identical explicit retry returns it without requeueing; changed text or kind under that ID is a conflict. Receipt order increases across consumed batches. The observed goal token is read under the inbox lock when accepting the request.

Submission and provider admission share the short inbox lock. Preparation occurs before admission; a request accepted first prevents the next provider launch. A request accepted after admission is queued while that stage finishes. The lock is released before waiting for the provider or asking for terminal input. Pending requests also prevent a prepared answer, goal approval, artifact approval or operator completion from committing. Stage results and recovery preserve completed work and consume earlier requests before committing any completion authorization.

The owner commits pause effects, feedback invalidation and applied receipt IDs in one authoritative state write before removing inbox requests. Restart recovers both acknowledgement crash windows without applying an ID twice. Pause confirmation requires the saved paused state, not merely a receipt. Explicit `--resume-paused` acknowledges a saved pause; feedback still requires Astra discovery and a newly displayed exact-token brief approval before implementation can resume.

Applied receipts include `applied_at`; explicit continuation adds `resumed_at` to previously applied receipts. Status exposes these durable timestamps. Submission retries omit these owner-only fields and continue to return the original acceptance receipt.
