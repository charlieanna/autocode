# Autocode

Start with a rough idea and discuss it with Astra. Astra helps define the smallest
useful end-to-end product, asks focused questions, and drafts a versioned build brief.
You can revise that brief in the same conversation. Implementation starts only after
you explicitly approve it.

After approval, Autocode handles the handoffs:

```text
You ↔ Astra: rough idea → clarification → build brief → your approval

Astra assigns → Terra builds → Sol verifies → Astra decides
                    ↑                            |
                    └──── CONTINUE / REWORK ─────┘
```

Astra owns planning and the final completion decision. Terra implements one bounded
task. Sol independently inspects and tests the actual code. They all work from the
same approved brief; you do not explain the product to each agent or relay their
prompts. The runner saves decisions, tasks and evidence so it can resume.

Works against any committed Git workspace; no IdleCampus files or services are required.

Requires Python 3.11+, Git, and an authenticated Codex CLI or OpenCode 1.x. macOS/Linux are supported;
Windows needs WSL because the inherited process and lock mechanisms use POSIX APIs.
There are no Python runtime dependencies. Installation does not change Codex settings.

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

New runs use OpenCode by default with your selected provider split:

| Role | Default model |
| --- | --- |
| Astra — discovery, planning, and review | `openai/gpt-6-astra` |
| Terra — implementation | `openai/gpt-5.6-terra` |
| Sol — independent validation | `zai-coding-plan/glm-5.3` |

From inside any committed Git project, the normal invocation is simply:

```sh
autocode "Your rough idea"
```

Use `--astra-model`, `--terra-model`, or `--sol-model` to explicitly override a
role. Resuming keeps the saved engine, provider mapping and limits unless overridden.

## Use your OpenCode providers

Autocode uses the connections already configured in OpenCode. `--engine opencode`
is accepted but is optional for new runs:

```sh
python3 tools/autocode.py "Your rough idea" --workspace /path/to/project
```

The OpenCode defaults use the requested provider split:

| Role | OpenCode model |
| --- | --- |
| Astra — discovery, planning, final review | `openai/gpt-6-astra` |
| Terra — implementation | `openai/gpt-5.6-terra` |
| Sol — independent validation | `zai-coding-plan/glm-5.3` |

Terra and Sol use separate sessions and providers, so Sol can audit implementation
from a different model perspective. Each role can be overridden with `--astra-model`,
`--terra-model` or `--sol-model`, using the exact `provider/model` ID from `opencode
models`. OpenCode reasoning variants can be selected with the existing role-specific
reasoning-effort flags; no variant is forced by default. Provider credentials remain
with OpenCode: Autocode does not read its auth file or change your global configuration.

The same approval, task, independent-evidence and completion gates apply. The adapter
uses OpenCode's [non-interactive JSON event interface](https://opencode.ai/docs/cli/#run),
validates the final report against the stage schema, and verifies command evidence
against actual completed bash events. Raw events, session IDs and stage-local
permission overrides are saved alongside the checkpoint. Token limits include cache
reads/writes and reasoning tokens. Malformed, truncated or uncertain results pause;
the runner does not automatically replay the provider request.

OpenCode has a different isolation boundary: Astra and Sol have edit tools denied
and their workspace snapshots checked, but OpenCode tool permissions are **not an
OS sandbox**. Shell commands and configured external tools retain OpenCode's native
permission policy. Autocode does not enable `--auto` or override user-level permission
rules with blanket allows. A denied required operation is reported back as a blocker.
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

The first stage runs **read-only Astra discovery**, presenting a small batch of
material questions or a build brief. Chat mode stays in the conversation; command
mode saves and exits at the checkpoint. State, answers, brief feedback, contract history,
user events, prompts, schema files, evidence and sessions remain in the target
workspace's `.autocode/runs/<run>/`. No implementation starts from the initial prompt.

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
with a checkpoint. They never cause completion. Each provider stage has a five-minute
limit by default; use `--max-stage-seconds` to change it or `0` to disable it.
`--pause-after-stage` and a run-local `pause-requested` file stop at a saved boundary.
No automatic retries are used for uncertain provider requests. `--resume-paused`
acknowledges operational pauses only. Saved limits persist unless you explicitly
override them. For example, resume a run paused at its iteration ceiling with
`--resume-paused --max-iterations 25` to set the total ceiling to 25. Changing a limit
does not approve a draft brief.

Provider stages track their subprocesses, including detached tool processes. On normal
exit, timeout or interruption, Autocode stops tracked workers before taking the final
snapshot and releasing the workspace lock. Saved process identities also block a new
runner while known workers remain alive. This is process supervision for trusted local
tools; it does not replace an OS sandbox or contain deliberately hidden daemons.
Rejected and recovered attempts count toward the active-time budget.

A completed response with invalid JSON or an invalid report is archived and pauses;
`--resume-paused` starts a new explicit attempt. Timeouts and uncertain responses need
inspection first. To retain partial edits and set aside a stopped attempt:

```sh
autocode --workspace /path/to/project --run-dir /path/to/run --status
# Copy the exact attempt_id from that status, after inspecting its events and edits:
autocode --workspace /path/to/project --run-dir /path/to/run --abandon-stage '001/terra-01'
autocode --workspace /path/to/project --run-dir /path/to/run --resume-paused
```

Abandoning a stage preserves its logs, source snapshots and partial edits, clears that
role's uncertain session and invalidates previous validation. It launches no agent.
On explicit resume, Astra inspects the retained work and chooses the next step.
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
