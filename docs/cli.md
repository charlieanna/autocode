# CLI reference

[← Back to README](../README.md)

Use `autocode` for tasks and its subcommands for related work. `autocode --help`
shows the short command overview; `autocode --help-all` lists every run option,
and `autocode COMMAND --help` shows that command's options. This reference lists the accepted public flags, including advanced run
controls. Role/model selection is in [Models](models.md); provider setup is in
[Providers](providers.md). Every command accepts `-h` / `--help`.

## Entry points

| Command | What it does |
| --- | --- |
| `autocode "Your rough idea"` | The normal entry point. Runs the full plan → approve → build → validate → complete loop (or stops at the next required checkpoint). |
| `replay RUN --into WORKSPACE/.autocode/runs/NAME` | Re-drive a saved run offline with recorded provider events and fresh verification checks; see [Replay](replay.md). |
| `replay RUN --export DIR` | Export a bounded recording; `--recorded-checks` on replay simulates historical checks without current completion evidence. |
| `autocode resume` | Continue the unfinished run of this project or task worktree (see [Which run a command acts on](#which-run-a-command-acts-on)). Never starts a new task. On a paused or blocked run (`PAUSED_*`, `BLOCKED_*`, `*_REWORK_REQUIRED`, `RESOLVER_PENDING`) it also acknowledges the stop, as `--resume-paused` does, so the run goes on: no new budget or recovery allowance, though the per-cycle report-repair and resolver attempt counts restart. A design conflict (`PAUSED_DESIGN_CONFLICT`) is only shown until you edit the design and pass `--resume-paused`. Its companions (`--retry-failed-stage`, `--grant-recovery N`, ...) need no `--resume-paused` after it. Plain `autocode` with no task relaunches a running run the same way but only shows a stop. |
| `autocode status` | The same as `autocode --status`: read-only status of that run. Command words are recognized wherever they stand among the options; a task whose whole text is `resume`, `status` or `explain` goes after `--` (`autocode -- status`). |
| `autocode explain` | The same as `autocode --explain`: explain the saved run's stop and the offered next command. Reads only; no model call, run lock or workspace changes. |
| `autocode demo [--directory PATH]` | Watch an offline sample plan, demo approval, build, rejected evidence and completion acceptance with the bundled fake provider. No account or model spend. Creates a new temporary directory by default, keeps its run and evidence records for inspection, and refuses an existing `--directory`. See [Offline demo](#offline-demo). |
| `autocode verify` | Check an existing working-tree change, branch or patch against caller-supplied criteria; retain independent review, replay and external-change proof in the canonical evidence format. See [Standalone verification](verify.md). |
| `autocode conversation start\|attach\|read\|send\|refresh\|handoff` | Core Requirements conversation journal tied to one task run: queue human messages, explicitly refresh its draft and inspect the handoff. See [Core Requirements conversations](task-run.md#core-requirements-conversations). |
| `autocode ui` | Figma design (and optional `--build` handoff to implementation). |
| `autocode tasks` | Run a multi-lane task flow file. |
| `autocode components` | Build the components of an architecture record in parallel and combine them (see [Task lanes](task-lanes.md#building-components-of-an-architecture-in-parallel)); with `--integrate TARGET --run-local`, also start the combined system with Docker Compose and run its smoke check (see [Running the combined system locally](task-lanes.md#running-the-combined-system-locally)). |
| `autocode program plan\|derive\|show\|approve\|run\|status\|request-change\|resolve-change` | Plan a large requirement, derive a workstream manifest from the approved plan, read and approve that manifest as the program agreement by exact token, run workstreams in parallel worktrees merged onto an integration branch, and raise or reject interface change requests (see [Programs](program.md)). |
| `autocode dashboard` | Local browser dashboard. |
| `autocode unattended` | Agent wrapper that refuses operator decisions and reports a stop (see [Unattended callers](#unattended-callers-agents)). |
| `autocode issue brief\|start\|status\|continue\|pr` | Read a GitHub issue, work on it through the task-run interface, and prepare a pull request (see [GitHub issues](issues.md)). |
| `autocode arena init\|ingest\|cases\|run\|report\|propose\|annotate\|compare` | Run independent benchmark cases, append evidence-backed failure investigations and compare candidate cohorts (see [Arena](arena.md)). |
| `autocode checkpoint --workspace PATH --run-dir RUN --compare CHECKPOINT` | Compare retained code or restore it by the exact inspected token (see [Saved code checkpoints](task-run.md#saved-code-checkpoints)). |
| `autocode merge [WORKTREE] [--workspace PATH] [--into BRANCH]` | Merge a delivered task worktree into its base branch (see [Task lanes](task-lanes.md#when-a-task-finishes)). |
| `autocode capture --output FILE -- COMMAND` | Run a command with exact output retained for retrieval (see [Exact output](exact-output.md)). |
| `autocode output read\|retrieve` | Read or retrieve exact retained file bytes (see [Exact output](exact-output.md)). |
| `autocode visual-capture --config FILE` / `autocode visual-check --policy FILE` | Capture visual references or run a source-owned visual check (see [Visual checks](visual-captures.md)). |
| `autocode compare-baseline` | Compare Vitest failure evidence (see [Execution](execution.md#baseline-comparison)). |
| `autocode review-pr URL [--post] [--out NEW_DIRECTORY] [--json] -- PROVIDER_OPTIONS` / `autocode-issue review-pr` | Fetch the exact PR head into an owned checkout, run Code Reviewer only, and print authenticated findings/evidence. `--post` submits COMMENT only after every new-side anchor and the head are rechecked (see [Pull request reviews](issues.md#reviewing-a-pull-request)). |
| `autocode review --workspace PATH [--run-dir RUN]` | Inspect pending human-review criteria and their recorded evidence, then approve, reject with a note, or skip. Reads decisions from terminal or piped input. `--show` and `--json` display without prompting; decisions never start a model. See [Human review](execution.md#human-review-in-the-terminal). |
| `autocode clean-worktrees [--yes]` | List, then with `--yes` remove, task worktrees whose runs are complete and whose branch holds their work; records are archived and branches kept (see [Task lanes](task-lanes.md#when-a-task-finishes)). |
| `autocode prune-run RUN [--yes]` | List, then with `--yes` delete a run's settled evidence bytes: finished verification-copy trees (`tool-containment-*/scratch`) and settled job-source stores (`<attempt>.source/`). Manifests, receipts and hashes are retained, so what was there stays provable; `state.json` is never modified. Anything still named at file level by state, owned by a live stage or a pending failure recovery, or the newest of its kind is kept with the reason shown. |
| `autocode bundle RUN [--out FILE] [--with-diff] [--json]` | Write one redacted archive of a run for a bug report (default `WORKSPACE/.autocode/bundles/RUN.tar.gz`): the replay recording `replay RUN --export` writes, so a maintainer can [replay](replay.md) it; `state.json`, `activity.jsonl`, the stage reports, the check-replay receipts and the last 200 lines of each provider event stream; the resolved settings, `doctor --json` output and the version. Every file it writes passes the credential redaction exports use, plus the values of secret-named environment variables; a recording that redaction would still change is left out, and the manifest says why. No workspace source unless `--with-diff` adds the run's diff (against its base commit, else the uncommitted work). `manifest.json` lists each file's sha256; cite the printed manifest hash in a bug report or a `docs/bugs/` note instead of pasting logs. The run directory is only read. |
| `autocode --version` | Print the executing package version and its source commit when known. An installed package without a source checkout reports `commit unknown`; it does not borrow the workspace commit. |
| `autocode completion bash\|zsh\|fish` | Print a shell completion script read from the CLI's parser: the commands, `program`'s commands, and every public option of the main command with its choices, and file names after an option that takes a path. A subcommand's own options are not completed yet, only `--help`. Load it with `source <(autocode completion bash)` (zsh the same, after `compinit`; fish `autocode completion fish \| source`), or save it; see [Shell completion](install.md#shell-completion). |
| `autocode models [--provider NAME] [--workspace PATH] [--json] [--prices]` | List the models your plans offer, grouped by plan (subscription or pay per token) and tier (cheap worker, strong judge, Resolver only), and check every role's default route. Suggests a replacement for any default you cannot use; exits 1 when one is missing (see [Models](models.md#when-a-model-is-not-in-your-plans)). `--prices` prints the dated price table instead, and the rate each default route is priced at, or "no rate" (never a zero price); it runs nothing and works offline. |
| `autocode doctor [--workspace PATH] [--engine opencode\|codex\|claude] [--json]` | Check Python, psutil, Git, each engine (OpenCode must be 1.x; Codex must be logged in), that the workspace is a Git repository with a commit, and the projected run storage (workspace size x its measured ~3x copy growth: worktrees, verification copies, attempt snapshots) against free disk space — a doomed filesystem is a ✖, a tight one a warning with the numbers. Prints the fix for anything missing; exits 1 when not ready. Passes only when the engine a new run uses is ready, resolved as a run resolves it: `--engine codex` is Codex; without `--engine`, the `engine` the project's files set ([Project settings](#project-settings)) counts as that flag; otherwise (no engine, or `--engine opencode`) the provider `AUTOCODE_PROVIDER` or `default_provider` in `~/.config/autocode/config.toml` names, else OpenCode. That provider must answer and list every role's default model (`opencode models`, as `autocode models` checks). `AUTOCODE_PROVIDER=codex` names a provider config, not the Codex engine. A ready Codex alone does not pass; doctor names `--engine codex` as the single-login alternative. `--engine claude`, a project's `engine = "claude"`, or a `claude` default provider, checks Claude Code instead: `claude` on PATH and `claude auth status --json`, run in the agents' environment, reporting a Claude subscription login (`engine:claude`; an API key, Bedrock or Vertex is refused, by the same check a run makes: the agents' environment and the project's `.claude/settings.json`), its conformance row, and its routes; a user `claude.toml` it ignores is a warning. Doctor shows no Claude rows for other runs and never prints the account's email or organization. Lists the settings a new run takes from the project's files ([Project settings](#project-settings)) and fails on a file a run would refuse. Runs without psutil and reports it missing. Never reads credentials. |
| `autocode registry location\|list\|import` | Registry API (see [Registry API](registry-api.md)). |
| `autocode runs [--all] [--needs] [--json]` | Every run of this project (or, with `--all`, of every registered project) in one read-only table: what each run's own status view says it needs, and the command that answers it. Needs-first order, then working, then settled, then runs whose checkpoint cannot be read; the row shows how long since the checkpoint was written. Nothing here starts, resumes or changes a run. Where a saved run's status alone cannot tell an uncertain attempt from one whose retained report only needs repair, the row points at `autocode --run-dir RUN --status` instead of offering a command that could set accepted work aside. It reads saved checkpoints, so unlike `autocode --run-dir RUN --status` it inspects no supervised process: a row says what the run last saved, not whether its provider is still alive. `registry list` is the pointer index with each pointer's availability; `runs` is what each run now wants from you. |
| `autocode events [--run-dir RUN] [--follow]` | One saved run's recorded events as a JSON-lines stream: how it got to where `--status` says it stands, one event per line, printed exactly as the run wrote it. These are the run's own records — its approvals, answers, contract and configuration changes, routing decisions and self-taken recoveries — not a provider's transcript. `--follow` keeps printing new events until the run ends the stream, and the status view contract is what ends it: a completed run exits 0, a run that needs a person exits 2 after naming that person's command on stderr, and `--timeout SECONDS` bounds the wait. Stdout is always JSON lines, so `--json` does not apply; the envelope is [run-event.schema.json](schemas/run-event.schema.json), whose `kind` is an open vocabulary a consumer must tolerate. Read-only, and like `runs` it inspects no supervised process: a stage hung mid-flight keeps the stream open, so ask `autocode --run-dir RUN --status` whether it is alive. |
| `autocode wait [--run-dir RUN] [--timeout S]` | The blocking form of `autocode runs`: wait on one saved run until it reaches you, ends, or stops advancing on its own. Each second it rereads the checkpoint and, read-only, inspects whether a controller still owns the run — the one question a saved status cannot answer. The outcome names which ended the wait: `complete` (the run reported TASK_COMPLETE/COMPLETE; exit 0), `needs_you` (the status view names what you must do, and the command that answers it; exit 2), `controller_lost` (the saved status is still RUNNING but nothing owns the run, so it will not advance on its own; exit 2, pointing at `--status`), `stopped` (anything else; exit 2), or `timeout` when `--timeout SECONDS` elapses first (exit 2, reporting the last observed status and controller). `--json` prints the outcome as exactly one JSON object. Nothing here starts, resumes or changes a run. |
| `autocode intervention submit\|inspect` | Queued interventions (see [Interventions](interventions.md)). |

When working on AutoCode itself, the launcher on `PATH` may use an older installed
package. Run `autocode doctor --workspace /path/to/autocode --json` to compare its
application payload with a verified AutoCode checkout. The `install-drift` warning
names the executing path, the checkout commit and changed, missing or extra
package files. Matching payloads do not warn even when an installed build's commit
is unknown. Customer repositories are never treated as AutoCode provenance.

To run the checkout directly from its root, use `python tools/autocode.py doctor`
(or `python tools/autocode.py` with your ordinary task options). Doctor also prints
a deliberately invoked reinstall command for the current interpreter. It never
updates an installation or rewrites run state itself.

The old console-script aliases remain for compatibility. The unit entry points
`autopilot`, `autoplanner`, `autocode-build`, `autoreview`, `autoresolver`,
`autocode-orchestrator` and `autocode-unattended` are internal entry points; use
`autocode` and its public subcommands in new instructions. Internal stage and unit
names keep their existing meaning.

## Offline demo

Run `autocode demo` after installation; Git and Python are enough. You do not need
OpenCode, Codex or an account. The demo answers its sample requirements question
and approves its displayed sample plan, then runs real CLI checks. It first shows
a claimed passing check that AutoCode refuses when its independent rerun fails.
The successful sample also stops at the completion gate until demo acceptance of
the artifact is recorded. These automatic approvals apply only to the new demo
project.

The command prints the run directory, canonical evidence record and `demo.json`,
and keeps every output inside its new directory. To choose where it goes, use
`autocode demo --directory /path/to/new-demo`; the directory must not exist.
Remove that directory after inspection. Fake-provider evidence demonstrates the
workflow and is separate from the [real-model reliability results](../RELIABILITY.md).

## Common flags

### Supplying a brief

| Flag | Meaning |
| --- | --- |
| `--brief TEXT` | Start a new run with literal task text; `--brief -` reads UTF-8 bytes from stdin. Mutually exclusive with a positional task, `--brief-file`, saved-run options and `resume`. |
| `--brief-file PATH` | Start a new run with the file's exact UTF-8 task text, retaining line breaks and trailing whitespace. Mutually exclusive with a positional task, `--brief`, saved-run options and `resume`. |

Use `autocode --brief-file brief.txt` or pipe UTF-8 text into `autocode --brief -`
to preserve backticks, `$()`, quotes and line breaks exactly. These inputs start
a new run and cannot accompany a positional task, `resume` or `--run-dir`.
An empty or invalid UTF-8 input is refused before creating a run.

`--brief TEXT` and the positional task remain available. The shell processes
arguments before AutoCode receives them: use single quotes around literal shell
syntax, for example ``autocode 'Fix the `run()` function'``, or use a file or stdin
when the brief contains embedded single quotes. AutoCode cannot recover text
already changed by the caller's shell. The saved task and its UTF-8 SHA-256 appear
as `view.task` and `view.task_sha256` in status JSON; compare them with the source
brief before continuing.

### Targeting a run

| Flag | Meaning |
| --- | --- |
| `--workspace /path` | Committed Git workspace to work in. Defaults to the current directory. Without a task or `--run-dir`, where AutoCode looks for the run to act on. |
| `--run-dir /path` | Act on this saved run, from any directory. Without `--workspace`, the run's own checkout is used. |
| `--in-place` | Start a new task in the selected checkout instead of a fresh worktree. Uncommitted and untracked files there count as the code the task starts from (see [Workflow](workflow.md)). Only one run's agents work in a checkout at a time; a second run exits with status 2 and changes nothing (see [Task lanes](task-lanes.md#multiple-tasks-in-one-project)). |
| `--workflow build\|bugfix\|review\|design\|discuss` | Name the kind of job instead of having the recognizer read it from the request. Also accepted by a saved run whose recognizer has not run yet. A run whose job is already decided keeps it: start a new run to change it (see [Workflow](workflow.md)). |
| `--status` | Read-only status, including `milestone_checkpoint`, `interventions`, `active_stage.activity`. |
| `--json` | Stdout carries exactly one JSON object; everything else the invocation prints goes to stderr. A finished or paused run prints the same payload `--status` prints. A user action (an approval, answer, feedback, resolver response and the like) prints that payload plus `result`: `exit_code`, `saved`, `message` (the text a person would have read) and `error`, which is `{"code": "input_rejected", "message": …}` when the input was refused and null otherwise. A refusal before the run loads prints `{"result": …}` alone. A refusal also ends stderr with `{"error": …}` on its own line. Exit codes are unchanged ([Exit codes](#exit-codes)); a usage error still prints argparse's text. The read-only commands (`--status`, `--explain`, `--dry-run`, `doctor --json`, `registry`, `usage --json`) print their own output and are unaffected. |
| `--explain` | Read-only explanation of a saved run's stop and what the next command does. With an explicit task in a Git workspace, previews the initial explanation without creating a run. An explicit `--run-dir` cannot accompany a new task or new-run inputs. |
| `--dry-run` | Read-only preview; never emits an accepted handoff. |
| `--inspect-evidence` | With `--status`, inspect the current source and saved verification evidence without running checks. |

### Understanding a stop

The status view adds `pause_category`, its readable `pause_category_label`, and
`next_command`. The category says whose action is needed; the exact `status` and
`stop_reason` remain available as detail, together with the existing `needs` and
recovery tokens.

| Category | Meaning |
| --- | --- |
| `your_decision` — Your decision | Inspect and answer a question, approve a plan, or authorize a recovery or limit change. |
| `your_environment` — Your environment | Repair the workspace, configuration, prerequisites or other local setup. |
| `model_or_provider` — Model or provider | Inspect a model refusal, quota stop, provider failure or incomplete model output. |
| `autocode` — AutoCode | Inspect an internal consistency, supervision or completion-proof stop. |

Rejected human actions and invalid configuration changes also print the current
recovery summary and command. Answer a clarification with `--answer Q=TEXT` and
the displayed private resolver token; `--resolver-response` is for an operational
request. A rejected response does not turn a clarification into an operational
pause or grant a retry.

Use the offered `next_command` under the category. It is derived from the same
current `needs` and actions as the existing recovery instructions; the category
never approves, retries or changes a limit. `next_command` is `null` when the run has no executable next action.
Resolve any stated prerequisite before running a command that resumes work.
Replace `ANSWER`, `MODEL`, `N` or `GOAL_FILE` with your intended input; a displayed
token uses its paired environment variable with the `-` selector. When recovery
has two steps, `next_command` gives the first one; inspect status again after it.
A missing issued token offers a noninteractive display, and a missing source
identity offers inspection rather than an unbound retry. `autocode explain`
describes the stop and the offered action. See the [status view contract](task-run.md#status-view).

### Which run a command acts on

A command with no task and no `--run-dir` acts on a saved run found from the
`--workspace` directory: the run directory you are in; otherwise the runs of the task
worktree you are in; otherwise the project's in-place runs and the runs of all its task
worktrees. It names the run on stderr (`Using the saved run RUN (STATUS, ...)`) and goes on
as if you had passed `--run-dir RUN`.

| Command | Run it takes |
| --- | --- |
| `autocode`, `autocode resume`, `--resume-paused` and its `--retry-*` companions, `--unit` | The only unfinished run. A finished run (`TASK_COMPLETE`, or stopped) is never relaunched this way, and a run that `autocode program` or `autocode tasks` drives is left to that command. |
| `--status`, `--explain`, `--dry-run`, `autocode status`, `autocode explain` | The only unfinished run; with none, the latest finished one. |
| `--show-goal`, `--answer`, `--approve-goal`, `--feedback` and the other user actions | The only unfinished run, preferring one no program or task flow drives. These save the run, so they never pick a finished one. |
| `--follow-up` | The most recently completed run, preferring one no program or task flow drives. Refused when a run started after it finished has not finished: name the run with `--run-dir`. |

### Keeping authorization tokens out of argv

These options accept `-` to read their paired environment variable:

| Token option | Environment variable |
| --- | --- |
| `--resolver-token` | `AUTOCODE_RESOLVER_TOKEN` |
| `--job-retry-token` | `AUTOCODE_JOB_RETRY_TOKEN` |
| `--recover-job-report` | `AUTOCODE_RECOVER_JOB_REPORT` |
| `--approve-goal` | `AUTOCODE_APPROVE_GOAL_TOKEN` |
| `--review-token` | `AUTOCODE_REVIEW_TOKEN` |
| `--expected-goal-token` | `AUTOCODE_EXPECTED_GOAL_TOKEN` |
| `--expected-recovery-token` | `AUTOCODE_EXPECTED_RECOVERY_TOKEN` |
| `checkpoint --expected-token` | `AUTOCODE_CHECKPOINT_EXPECTED_TOKEN` |
| `program approve --token` | `AUTOCODE_PROGRAM_APPROVAL_TOKEN` |

Prefer this on shared machines: Linux exposes `/proc/<pid>/cmdline` to every local user, while
`/proc/<pid>/environ` is readable only by the owner, and a typed token also lands in shell
history. The environment variable alone never authorizes anything — the flag must still be
present. `TaskRun`, dashboard actions and the trial driver pass these tokens through
each CLI child's environment. Their process arguments and saved command records contain
`-`. Literal CLI tokens remain supported. The controller always removes all nine
variables before launching a provider or test command, even if named in `AUTOCODE_PASS_ENV`.
Programmatic command builders require full token option names; abbreviated token
options are refused before recording or launch.

With several unfinished runs the command changes nothing, exits 2 and lists them with the
`--run-dir` command for each. With no run it says where it looked; with only finished
runs, a bare `autocode` names the latest one instead of relaunching it. Inside a run
directory whose `state.json` is missing or cannot be used, or one `autocode clean-worktrees`
archived, it refuses rather than pick another run.
A task, or a new-run option (`--in-place`, `--figma-file`, `--figma-manifest`,
`--figma-review`, `--ui-run`, `--builder-strong-model`, `--conversation-handoff`, `--test-root`), starts
a new run instead; `autocode resume` never does.

### Conversation and approval

`--status` keeps its JSON document on stdout and prints a short pending-question
list and the current Resolver token on stderr. After a successful `--answer`,
stderr shows the questions still open and their new token, or says there are no
questions left to answer. Use that new token for the next answer; answering does
not approve a plan or start a model.

| Flag | Meaning |
| --- | --- |
| `--chat` | Interactive chat mode (default in a terminal). |
| `--no-chat` | One command per turn (default for non-interactive). |
| `--answer 'Q1=…'` | Answer a question the run is waiting on (repeatable; the status view's `needs.kind` is `answer`). Requires the current `--resolver-token` shown by Resolver. A finished run waits on none: the questions in its report take `--follow-up` (see [Waiting or finished](#waiting-or-finished)). |
| `--answer route-sol=MODEL` | At a quota stop (`PAUSED_BUDGET`), a content-filter refusal (`PAUSED_CONTENT_FILTER`), or an authenticated output-limit stop (`PAUSED_OUTPUT_CAP`), name the model the stopped role continues on; the only operational question `--answer` takes. Requires the current `--resolver-token`. The model must suit the role's engine, be listed by OpenCode on OpenCode runs and keep the cross-model rule; otherwise the run stays paused with the question open. Sets the stopped attempt aside as `--abandon-stage` does and records a `route_assignment`; continue with `--resume-paused`. The resume-flag form is `--abandon-stage ATTEMPT`, then `--resume-paused --sol-model MODEL`. A stopped workflow job (`needs.kind` `retry_job` with `needs.route`) takes `--answer route-ROLE=MODEL --job-retry-token TOKEN` instead: no resolver token, no `--abandon-stage`, no `--ROLE-model` flag; it issues a new job retry token, so continue with `--resume-paused --retry-failed-stage --job-retry-token NEW_TOKEN` ([Waiting or finished](#waiting-or-finished)). See [Models](models.md#when-a-roles-quota-runs-out). |
| `--feedback '…'` | Send a correction; returns to discovery and requires fresh approval. With `--adaptive-planning`, feedback on a plan shown for approval goes to the Planner, which revises it. Refused at an operational pause unless it offers feedback (an exhausted plan-review budget, a validation-only stop). |
| `--follow-up '…'` | Say the next thing to a finished run ("Fix them." after a review): the run recognizes the new job and continues in the same run directory. Finished runs only (`TASK_COMPLETE`); any other run exits 2 unchanged. After a design review, the reply revises that review: the Architect keeps every concern under its id, keeps a settled one as resolved with what settled it, and adds the revision to `revisions` in `review/design-review.json` ([Replying to a design review](#replying-to-a-design-review)). After a design turn, "Build it." builds that design as approved: it is checked against the code first (`check_design`). The message is saved as feedback that planning can cite for requested contract changes; the new plan still needs approval. |
| `--delegate Q1` | Accept a question's proposed default. Requires the current `--resolver-token` shown by Resolver. |
| `--delegate-all --review-token 'r3:<hash>'` | Delegate every pending question marked `delegable` with a proposed default, on the exact displayed revision. Refuses the whole call if any question lacks a default, is not delegable, has a protected or missing category (cost, quota, permission, external side effect, requested outcome), or asks about a rejected assumption. Never approves; invalidates any existing approval. |
| `--reject-assumption A1 --review-token 'r3:<hash>'` | Reject a structured assumption from the displayed requirements handoff (repeatable). A stale token, or a handoff refreshed since display, is refused. Never approves; invalidates any existing approval. |
| `--program-guidance '…' --review-token 'r3:<hash>'` | A program's correction of the exact displayed plan, such as "this plan drops inherited requirement C2" ([Program](program.md)). Saved as the program's guidance, never as your feedback: it is no requirement the next plan must quote, no decision basis, and starts no clarification episode. Returns the run to requirements and requires fresh approval. Refused for a stale token or a plan not awaiting approval. |
| `--show-goal` | Display the current contract/revision. At the approval stop it also says what approving authorizes and what the token locks, and ends with a summary of the decision (outcome, permissions, criteria and how each is checked, what passing proves), the limits in effect and the exact approve command ([sample](workflow.md#conversation-and-approval)). |
| `--approve-goal 'r3:<hash>'` | Approve the exact displayed revision. |
| `--edit-goal body.json` | Load a full contract body as a new draft revision. Refused at an operational pause unless it offers feedback (an exhausted plan-review budget, a validation-only stop). |
| `--approve-review C1 --review-token '…'` | Record a human-review decision for criterion `C1`. |
| `--reject-review C1 --review-token '…' --review-note TEXT` | Reject the current reviewed artifact for criterion `C1`, retaining the nonempty note as feedback for a fresh plan and approval. Uses the same artifact freshness checks as approval. |
| `--investigator-model MODEL`, `--investigator-reasoning-effort LEVEL` | Pin the stuck-stage Investigator's model for this run (default, at high: Claude Opus 5.5 in `kilocode` runs, otherwise GPT-6 Sol, or GLM 5.3 when the stuck stage runs on Sol). A `provider/model` id runs it through OpenCode. See [Workflow](workflow.md#when-a-stage-stops-making-progress). |
| `--resolver-response provide_information --resolver-request ID --resolver-token '…'` | Answer a Resolver request with `--resolver-message TEXT`. A displayed criterion recommendation accepts the exact message `Accept recommended revision` to install an unapproved draft for normal plan review and fresh approval. Other blocker information remains held; operational-exhaustion information is re-evaluated once on resume. Request ID and current token are required. The response launches no provider and grants no approval or budget change ([Execution](execution.md)). |
| `--close-finding ID --close-reason '…'` | Close an open reviewer finding as your own decision (repeatable), for example a duplicate of a problem you already settled. Records who closed it and why, and launches no agent. Closing every finding a validation-only stop asked about answers that stop, so the next `--resume-paused` continues. |

#### Waiting or finished

`--follow-up` continues only a finished run (`TASK_COMPLETE`). A waiting, paused or blocked run
refuses it (exit 2, state unchanged): answer it (`--answer`/`--delegate` with `--resolver-token`),
approve or correct it (`--approve-goal`/`--feedback`), or resume it. Jobs that report without
waiting for a reply (design review, discussion, code review) complete with their questions or
findings in their report; you reply to them with `--follow-up`. After a design turn, "Build it."
builds that design as approved (checked against the code first, no requirements gathering or
questions, plan approval still required). The build gets a plan of its own: the design turn's
plan (which wrote no code), with whatever earlier turns agreed that it carried, and its open
non-blocking findings are archived in the run, not revised; the design is now the requirements.
After a design review instead, building the design it approved revises the plan in force. A follow-up builds as approved only the design its previous turn wrote, or the design a
design review approved; any other document is planned from requirements as usual.

| The run is | Status view | Say the next thing with | Through `TaskRun` |
| --- | --- | --- | --- |
| finished | `done` is true, `needs` is null | `--follow-up TEXT` | `follow_up(text)` |
| waiting for you | `needs.kind` is `answer`, `approve_plan`, `review` or `planning_budget` | `--answer`/`--delegate` with `--resolver-token`, `--approve-goal`, `--approve-review`, `--feedback` | `answer`, `approve_plan`, `approve_review`, `feedback` |
| stopped | `needs.kind` is `resume` | `autocode resume`, once the cause in `stop_reason` is resolved (`--resume-paused` after editing the design at `PAUSED_DESIGN_CONFLICT`) | `resume_paused()` |
| stopped in a workflow job (Reviewer, Architect, Analyst, Investigator) | `needs.kind` is `retry_job`, or `recover_source` for an attempt with no saved source identity | `--resume-paused --retry-failed-stage --job-retry-token TOKEN` after inspecting `needs.archive`; with `needs.route` (quota, a content-filter refusal or an authenticated output limit), first `--answer route-ROLE=MODEL --job-retry-token TOKEN` to run it on another model, then the retry with the new token; for `recover_source`, a new run ([Task-run interface](task-run.md#failed-workflow-jobs)) | `retry_job(token)`; with `needs.route`, `assign_model(role, model)` first |
| waiting on another run | `needs.kind` is `dependency` | `--receive-dependency MANIFEST` once that run delivers | `receive_dependency(manifest)` |

The wrong one is refused with exit 2 and the run left as it was. `--follow-up` on an unfinished
run lists the alternatives (answer, approve or correct, resume). On a finished run, `--answer`,
`--delegate` or `--delegate-all` says: "This run is finished and waits for no answer; reply to
the questions in its report with --follow-up TEXT", and `--feedback`, `--edit-goal` or
`--reject-assumption` says it would reopen the run without a new turn. Without `--run-dir`,
`--follow-up` is also refused when a run started after the latest finished one has not finished:
the message lists that run, and the command that continues the finished one anyway. Through
`TaskRun` these raise `TaskRunError`.

#### Replying to a design review

A design review never waits for its questions: the run completes with them in
`review/design-review.json`, and you answer with `--follow-up` ("Ordering is per-domain."). The
Architect is asked to keep a concern that is a problem only under one answer to its question
advisory until you answer. A reply recognized as design makes the Architect revise the same
review instead of writing a new one. It is asked to:

- keep every earlier concern under its id, open or resolved, and to drop or renumber none;
- keep a concern the reply settles with `status` `resolved` and a `resolution` saying what
  settled it (an answer can also make a concern blocking), and to add a concern only for a
  problem the reply exposes;
- drop the questions the reply answered and keep the others under their ids.

The runner refuses a revision that leaves out an earlier concern id, uses an id twice, resolves
a concern without a resolution or resolves one raised in that revision, or whose verdict is not
`request_changes` exactly when an open concern is blocking. A reply that names a different
design gets a fresh review (revision 1); a reply asking for a new design hands it to the build
pipeline, as a first request would.

`revision` is the review's number, and `revisions` keeps one entry per review: the message that
prompted it (`said`, null for the run's first review), its feedback receipt (`event_id`), the
verdict, and the ids of its open blocking, open advisory and resolved concerns and of its
questions. The report you reply to must be the one the Architect wrote: if
`review/design-review.json` was edited since, `--follow-up` exits 2 with "changed since the
Architect's review; restore it or start a new run".

### Execution and recovery

`autocode resume` can replace `--resume-paused` alongside a recovery companion such
as `--grant-recovery N`, `--retry-failed-stage`, or an explicit budget change. This
also works when AutoResolver has published the operational pause as
`WAITING_FOR_USER`, provided its saved request is still valid. Bare `resume`
does not acknowledge that published request; ordinary questions and approvals
still require their own actions. Recovery eligibility and token checks are unchanged.

| Flag | Meaning |
| --- | --- |
| `--resume-paused` | Acknowledge an operational pause and continue. Does not approve a draft, and does not restore a spent recovery allowance. `autocode resume` implies it at `PAUSED_*` (not `PAUSED_DESIGN_CONFLICT`), `BLOCKED_*`, `*_REWORK_REQUIRED` and `RESOLVER_PENDING`, never with a user action or `--resolver-response`; with a recovery companion, also at a verified operational pause published as `WAITING_FOR_USER`. |
| `--retry-failed-stage` | With `--resume-paused`, authorize one fresh attempt at the recorded repeated failure after inspecting its retained work; no approval or budget bypass. |
| `--diagnose-failed-stage` | With `--resume-paused`, request bounded read-only diagnosis of a recorded repeated Builder report failure. Alternative to `--retry-failed-stage`; not a permission or budget override. |
| `--grant-recovery N` | With `--resume-paused`, authorize N more automatic timeout recoveries for a run paused at `PAUSED_TIMEOUT_RECOVERY` after its cause was fixed. Audited as a `recovery_grant` user event; recovery history is retained. |
| `--planning-review-call-limit N` | At a reconciled planning-budget pause, save a total allowance for the current cycle. `0` disables the cap for this and future cycles while preserving usage history; it can also be saved at a requested pause or after abandoning a stopped stage. No model launch or approval; resume separately. |
| `--pause-after-stage` | Stop at the next saved boundary. |
| `--verbose` / `--no-verbose` | Stream each stage's live model activity (tools started and finished, new model text, one bounded line each) to stderr as `Role/model: …`. On by default; `--no-verbose` or `AUTOCODE_VERBOSE=0` turns it off. Not saved with the run: each command that runs stages reads it afresh. The full stream is always kept in the stage's `iterations/NNN/<stage>-NN.jsonl`. |
| `--retry-builder M2` | With `--resume-paused`, authorize one retry of the exhausted current serial milestone or stopped parallel members (not a member stopped by its provider's content filter or response output limit: answer its `route-terra` question; once that request was answered with information or left paused, `--retry-builder` asks the question again and launches nothing). Keeps failure history, model routes and verification gates; all workers must be stopped. A refused or output-limited member retry with settings saves nothing. A parallel member reruns on its saved Builder route, so a Builder model, provider or reasoning effort change with its retry is refused. A difference saved separately in the run's Builder settings also refuses the retry, naming both routes. Answer that member's open `route-terra` question to choose its model, or restore the run's Builder settings to the member's route separately before retrying. |
| `--abandon-stage '001/terra-01'` | Archive a stopped attempt, keep partial edits and logs. |
| `--retry-report ATTEMPT_ID` | With `--resume-paused`, request fresh Tester evidence after report repair or repeated-failure limits stop a rejected report, using the exact attempt ID status names; saved source and evidence pins must still match. After a source edit, `--resume-paused` validates the current source instead. |
| `--accept-transport-change` | Resume a transport-change pause after route checks. |
| `--accept-source-edit` | With `--resume-paused`, hand a paused repair the source edited while it was stopped. The resolution's source revision becomes the current snapshot, and a recovery packet bound to the replaced source stays on disk but is detached from the request. The approved contract, task, budget, proof and evidence pins stay. It refuses any other change, and source the Resolver wrote. |
| `--export-repair` | Write the dispatched bounded repair (a run paused with `resolution_request` + `current_task`) as `handoff/repair-spec.{json,md}` beside the run: affected paths, the repair instruction, the binding contract hash, the acceptance criteria and the runnable verification commands. Run state is untouched; the export is also the binding `--submit-repair` re-checks. |
| `--submit-repair DIR` | Adopt a completed external implementation delivered as a checkout of this repository at the dispatched base with only this repair applied. Re-runs the static gates and the regression proof (including the fail-on-base property), imports the changed files inside the repair's affected paths (rolling the workspace back on rejection), and on success advances the milestone and records the external provenance in `user_events`; rejection names the failing criterion. |
| `--max-parallel-builders N` | Concurrency limit for independent milestone Builders. |
| `--milestone-checkpoints` / `--request-milestone-checkpoints` | Enable milestone checkpoints (idle boundary / queued). |
| `--max-milestone-seconds N` | Milestone active-time budget (default 5400; `0` disables). |
| `--max-milestone-replans N` | Changed-approach replan limit (default 1). |
| `--max-milestone-stalled-reviews N` | Reviews without progress before replanning (default 3; `0` disables). |
| `--max-findings-per-task N` | Cap open findings bundled into one REWORK task. |
| `--max-idle-seconds` / `--max-tool-seconds` / `--max-stage-seconds` | Watchdog limits (new-run defaults `300` / `1800` / `3600`; `0` disables). |
| `--max-seconds N` | Total active provider time for the run (new-run default `43200`, 12 hours; `0` disables). Checked at stage boundaries. |
| `--max-cost-usd N` | A money cap in USD: the run pauses at the next stage boundary once its spend reaches `N` (no cap by default; `0` removes it). Spend is the provider's reported cost, else the stage's tokens at the dated price table (`autocode models --prices`); a stage on a model without a rate, or one whose provider recorded no token counts, is not counted and the cost line says so. The pause is yours to decide (`PAUSED_COST_LIMIT`): `--resume-paused --max-cost-usd M` with `M` above the spend continues, and AutoCode never raises the cap itself. See [Money cap](execution.md#money-cap). |
| `--no-progress-limit N` | Unchanged-batch limit (new-run default `3`; `0` disables the cap, never the 3-recovery ceiling). With `--resume-paused`, an N above the retained count, or `0`, acknowledges a `PAUSED_NO_PROGRESS` request, also when N is already saved; it never acknowledges another cause's pause. When the limit caused the pause, its advice names this flag and the retained count, not `--resolver-response`: information alone never acknowledges it. |
| `--max-iterations N` | Optional total iteration ceiling; new runs default to unlimited, and resumes retain their saved limit. |
| `--unlimited-iterations` | Remove only the iteration ceiling; other safety and usage limits remain. |
| `--legacy-iteration-ceiling N` | Compatibility form of the iteration ceiling, used only when `--max-iterations` is absent. |
| `--autoresolver-managed-limits` | Delegate finite CLI safety limits to bounded Resolver recovery; never changes billing or model routes. |
| `--test-command CMD` | The project's test suite command for runner-owned regression proof (default: detected). Correct a saved command with `--resume-paused` at a reconciled pause before the Tester or combined checkpoint; see [Bug fixes](workflow.md#bug-fixes). |
| `--test-report junit:PATH` | Use one fresh JUnit XML file at a workspace-relative path as named test evidence. Requires an explicit `--test-command`; when `--regression-command` is omitted, that suite also runs in the regression proof trees. Every invocation must write the same relative report path; old output is cleared and captured XML is hash-pinned. Saved corrections use the same reconciled pre-validation pause as `--test-command`. No directory/glob or TRX input. See [JUnit proof setup](named-test-proof.md#junit-xml-from-other-test-runners). |
| `--test-root components/api/` | New runs only: save a plain workspace-relative directory for automatic Python suite detection (pytest or unittest). Fixed at launch, not changeable on resume. Changes outside it remain `UNVERIFIED`, including with an explicit test command. This selects proof tests, not a general write boundary; identified generated component tasks also bind their declared plan ownership to this caller root. See [Scoped Python proof](workflow.md#scoped-python-proof). |
| `--base-patch PATH` | Bug fixes whose only regression test needs a hook or variable the fix adds: a patch that adds only that instrumentation to the original code, so the test can run and fail there. Pinned by hash, may not change test files, and its edits must occur at the corresponding original source locations in the final change; every proof that uses it asks the Tester and Completion Reviewer to check it changes no behavior. Set it when the run starts, or with `--resume-paused` at a stop before the Tester or a completion check. |
| `--regression-command CMD` | A command that runs the new or changed tests (default: derived). Multi-milestone features accumulate changed Python test files in supported literal pytest/unittest lists while retaining selectors and options; see [Bug fixes](workflow.md#bug-fixes). Saved corrections require the same pre-validation `--resume-paused` boundary as `--test-command`. |

An execution permission question can offer explicit suite/tool/stage timeout
changes. Select the exact displayed choice with the normal `--answer` action;
the runner applies that choice's settings and records their old/new values.
Declining changes no timeout. These decisions preserve the approved goal and
elapsed run budget. Legacy prose-only consent changes no runner setting; see
[timeout permission payloads](bugs/permission-timeout-consent.md).

An accepted parallel `--retry-builder` copies the run's saved operator tool,
stage and milestone bounds (`--max-tool-seconds`, `--max-stage-seconds`,
`--max-milestone-seconds`) to only those members. Save these limits separately
or beside the retry. Zero keeps its disabled meaning; old attempt deadlines,
spent time and recovery allowances remain intact.

### Engine, provider, and models

| Flag | Meaning |
| --- | --- |
| `--engine opencode\|codex\|claude` | Engine for the run. OpenCode is the default; `claude` runs Claude Code, the [built-in claude provider](providers.md#claude-code-provider) (the OpenCode engine's facade with `--provider claude`; another `--provider` is refused). Other tools join as providers (see [Providers](providers.md)). |
| `--provider <name>` | External tool registered via TOML (see [Providers](providers.md#add-a-tool)). |
| `--role-transport ROLE=PROVIDER` | New run with `--engine opencode --provider opencode`: select an external configured tool for one role; repeat for more roles and provide each selected role's explicit model flag. Other roles keep their saved routes. The tool binding is immutable after launch and cannot combine with `--single-model` or recorded replay. See [Per-role tools](providers.md#a-different-tool-for-one-role). |
| `--allow-uncontained-tools` | Built-in OpenCode runs only (including a Codex run whose `--investigator-model` is an OpenCode `provider/model`). Without it, a run is refused before any stage when the kernel tool boundary cannot exist here (it needs macOS `sandbox-exec` and OpenCode 1.18.33). With it, the Builder, Validator and other non-planning stages run with OpenCode's own permission checks only, no kernel containment. Accepted on a new run or a resume (including a `PAUSED_TOOL_CONTAINMENT` run); saved with the run and recorded as a user event, so later resumes need not repeat it. The status view shows `tool_containment`. See [Execution](execution.md#native-tool-containment). |
| `--joint-planning` | Add joint Requirements / Plan Reviewer work. |
| `--adaptive-planning` / `--no-adaptive-planning` | New runs plan as deep as the job needs by default (joint planning on the default flow): a clear build request skips the Requirements stage, and a Plan Reviewer with no blocking concern approves the draft. `--no-adaptive-planning` keeps the fixed sequence; `--adaptive-planning` insists. See [Adaptive planning](adaptive-planning.md). |
| `--spec-first` / `--no-spec-first` | Off by default. A new run with joint planning on the default flow stops after Requirements every time it produces a handoff: the question (`needs.kind` `answer`, id `requirements-review-…`) shows the outcome, behaviors, acceptance examples, constraints, what was left out and the assumptions. Answer `plan` to start the Planner, or say what to change and Requirements runs again with your answer. Adaptive planning never skips Requirements on such a run. After plan approval the Tester writes the acceptance tests before the Builder, which may not change them. A saved run keeps its choice. See [Spec-first runs](spec-first.md). |
| `--builder-strong-model MODEL` | New run: the model for the Builder's stronger attempt after its ordinary retry (default `openai/gpt-6-sol`, or `strong_model` under `[builder_retry]` in the provider config; see [Models](models.md#builder-retry-policy)). Refused when it is the checker model. |
| `--single-model MODEL` | New run: use one model for every role, including reviewers; explicitly relaxes cross-model verification for single-subscription accounts and implies joint planning. |
| `--requirements-model`, `--glm-model`, `--plan-reviewer-model` | Planning-role model overrides (bare GPT names). |
| `--astra-model`, `--terra-model`, `--sol-model`, `--completion-model` | Execution-role model overrides (`provider/model` IDs). |
| `--astra-provider`, `--terra-provider`, `--sol-provider`, `--completion-provider` | Per-role Codex provider override (Responses API); the saved engine stays the same. |
| `--reasoning-effort`, `--astra-reasoning-effort`, `--terra-reasoning-effort`, `--sol-reasoning-effort`, `--completion-reasoning-effort` | Shared or execution-role reasoning effort (`low`, `medium`, `high`, `xhigh`, `max`). |
| `--glm-reasoning-effort`, `--requirements-reasoning-effort`, `--plan-reviewer-reasoning-effort` | Reasoning effort for the Planner, Requirements or Plan Reviewer. |
| `--resolver-model`, `--resolver-reasoning-effort` | Override the saved Resolver model or reasoning effort without changing its engine. |
| `--pin-model-role astra\|terra\|sol\|completion` | Keep the selected role model and reasoning effort instead of escalating automatically; repeat for multiple roles. |
| `--migrate-only` | Run the opt-in legacy migration and stop (see [Testing](testing.md#legacy-migration--opt-in-only)). |

Saved model changes (role model flags or answers to a stopped role's model question)
print an advisory to stderr when a previously independent Requirements, Planner, or
Builder producer/consumer route becomes the same model or GLM/MiMo family. Existing
shared links and reasoning-only changes do not repeat it. The advisory describes
selected routes for future work; saved artifacts retain their original authorship.
It does not block the change or relax the mandatory verifier checks. Different GPT
tiers remain distinct, as in those checks.

### Project settings

A team keeps the settings it would otherwise type on every new run in
`.autocode/config.toml` in the workspace, or, in a Python project, under
`[tool.autocode]` in `pyproject.toml` (`config.toml` wins a key both set). Commit
the file like any other: the `.autocode/.gitignore` AutoCode creates leaves
`config.toml` trackable. In a checkout whose `.autocode/.gitignore` AutoCode
created before this, add the line `!/config.toml` to it, or `git add -f
.autocode/config.toml`.

```toml
engine = "opencode"
test-command = "python -m pytest -q"
sol-model = "openai/gpt-6-sol"
terra-reasoning-effort = "high"
max-seconds = 7200
```

Each key is a flag's name without its leading dashes, and takes the values that
flag takes; a switch takes `true` or `false`.

| Kind | Keys |
| --- | --- |
| Engine | `engine` |
| Models | `single-model`, `astra-model`, `terra-model`, `sol-model`, `completion-model`, `glm-model`, `requirements-model`, `plan-reviewer-model`, `resolver-model`, `investigator-model`, `builder-strong-model` |
| Reasoning efforts | `reasoning-effort`, `astra-reasoning-effort`, `terra-reasoning-effort`, `sol-reasoning-effort`, `completion-reasoning-effort`, `glm-reasoning-effort`, `requirements-reasoning-effort`, `plan-reviewer-reasoning-effort`, `resolver-reasoning-effort`, `investigator-reasoning-effort` |
| Test command/report | `test-command`, `test-report` |
| Budgets | `max-iterations`, `unlimited-iterations`, `max-seconds`, `max-cost-usd`, `max-stage-seconds`, `max-idle-seconds`, `max-tool-seconds`, `no-progress-limit`, `max-milestone-seconds`, `max-milestone-replans`, `max-milestone-stalled-reviews`, `max-findings-per-task`, `max-parallel-builders` |

A flag on the command line wins over the file, and over any file key it cannot be
combined with: a typed `--single-model` drops the file's role models, a typed role
model drops its `single-model`, and a typed iteration ceiling drops its
`max-iterations` and `unlimited-iterations`. The file wins over the built-in
default; no environment variable sets these keys. A setting from the file counts
as typed, so a budget the team set is never extended as a runner default. A new
run prints which settings it took from which file. Only a new run reads the file:
a saved run keeps the settings it started with, so changing the file never alters
a run in progress.

`engine = "claude"` is allowed: it selects the built-in Claude Code provider, which runs AutoCode's
fixed command line, as `engine = "codex"` does. A project cannot choose the provider (`provider`, or a role's `astra-provider`,
`terra-provider`, `sol-provider`, `completion-provider`): it runs a command you
installed, so it comes from `--provider`, `AUTOCODE_PROVIDER` or
`default_provider` in `~/.config/autocode/config.toml`. Nor `task-preflight`, the
operator's prerequisite manifest. A file that names either, an unknown key, or a
value its flag would refuse stops a new run before it starts, naming the file and
the key. `autocode doctor` lists each setting a new run would take and the file
it came from, and fails on such a file.

### Advanced run inputs

| Flag | Meaning |
| --- | --- |
| `--help-all` | Full run-option help, including advanced controls omitted from the short overview. |
| `--unit autoplanner\|autocode\|autoreview\|autoresolver` | Run one unit and stop before the next; omitting it runs the full controller. |
| `--conversation-handoff PATH` | Attach a validated conversation receipt to a new task; grants no approval. |
| `--conversation-session` | With a creation-only `--conversation-handoff`, initialize the core conversation before ordinary planning; cannot target a saved run and grants no approval. |
| `--conversation-request PATH` | On a saved run, apply the owned journal-sealed request for its exact `--feedback` or `--answer`. Retains the saved acknowledgement for idempotent delivery; grants no approval and starts no provider. |
| `--task-preflight PATH` | Prerequisite manifest for planning, building and validation; saved corrections require its reconciled pause and `--resume-paused`. |
| `--figma-manifest PATH`, `--figma-additional-file URL` | Complete immutable multi-file/frame/state references for a new Figma run; repeat additional file URLs as needed. |
| `--figma-approved-scope PATH` | External reviewed product-scope artifact bound to a complete version 2 inventory and its approved cases. Requires `--figma-approved-scope-sha256` with new-run `--figma-manifest` or stopped `--revise-figma-manifest`. Grants no plan approval; full original-source validation remains required ([scope admission](figma.md#externally-reviewed-product-scope-version-2-inventories)). |
| `--figma-approved-scope-sha256 SHA256` | Trusted operator SHA256 of the exact `--figma-approved-scope` artifact; required with that option, never inferred from the design bundle. |
| `--revise-figma-manifest PATH`, `--expected-design-hash HASH`, `--design-change-reason TEXT` | At a stopped run, propose changed Figma references against the inspected hash, retaining history and requiring plan review. |
| `--revise-protected-tests PATH` | Explicit user revision of the original test inventory and command; requires a reconciled validation pause. |
| `--bind-dependency PATH`, `--receive-dependency PATH` | Register an authorized prerequisite or receive its verified delivery manifest; neither approves the plan. |
| `--resolver-message TEXT` | Corrective information accompanying an operational Resolver response. |
| `--reconcile-review CRITERION=ANSWER` | Bind an authenticated legacy acceptance to current validated evidence without granting a new approval. |
| `--accept-completion` | Accept completion only after the runner verifies every gate, when the model completion report cannot be produced. |
| `--evidence-provenance fake\|live\|unknown` | Disclose the run's model-evidence origin; saved with a new run. |
| `--context-soft-tokens N`, `--rotate-after-input-tokens N` | Context-compaction and checkpointed session-rotation thresholds; `0` disables rotation. |
| `--tool-output-mode raw\|conservative` | Display mode for AutoCode capture/file-read tools; saved across resume. |
| `--headroom off\|on` | Off by default; `on` refuses to run until compatibility has been verified. |
| `--planning-v2` | Opt in to transactional planning artifacts; keeps the existing role models and default planning flow. |

### Programs

| Command or flag | Meaning |
| --- | --- |
| `program plan BRIEF --workspace DIR` | Plan the request with the ordinary planning unit (`--unit autoplanner`, in place) and a program preamble. `--engine` and unrecognized flags go to that run. |
| `program derive --run-dir RUN --output program.json` | Write the manifest from the run's `approved_contract`; refuses unapproved plans. `--workspace` names the plan run's project, `--name` the program; without `--output` it prints the manifest. |
| `program show MANIFEST --workspace DIR` | Print the program agreement and the exact token (`a<revision>:<digest>`) that approves its pending revision, or the approved revision. Saves nothing. |
| `program approve MANIFEST --workspace DIR --token TOKEN` | Approve that agreement revision; refuses any other token. No workstream starts before the first approval, and every later revision is approved the same way. |
| `program run MANIFEST --max-parallel N` | Concurrent workstreams (default 2). |
| `program run MANIFEST --authorize-deployment` | Allow `deployment` workstreams to start or resume; their runs still need plan approval. Descriptor generation is ordinary `code`. |
| `program run MANIFEST --retry-workstream ID` | Explicitly retry a failed workstream in its existing worktree/checkpoint, without bypassing child gates. Repeat for multiple failed workstreams. |
| `program run MANIFEST --check-timeout S` | Seconds each cumulative check may take after a merge (default 900). |
| `program run MANIFEST --engine codex\|opencode` | Engine for the workstream runs it starts; a workstream's own `engine` wins. |
| `program run MANIFEST --dry-run` | Validate and preview without creating branches or worktrees. |
| `program status MANIFEST --workspace DIR` | The same summary as `run`, read from the saved state and each unfinished child's status view, without launching or saving anything. |
| `program request-change MANIFEST --workspace DIR --interface ID --by WORKSTREAM --reason TEXT [--proposal TEXT]` | Open a change request (`CR-N`) on a shared interface; its producer, its consumers and the final check (for an interface with no producer, every workstream) neither start, resume nor merge while it is open, and the program is not `COMPLETE` while any request is open. |
| `program resolve-change MANIFEST --workspace DIR --request CR-N --reject --reason TEXT` | Reject an open change request. Accepting one is an approved agreement revision that publishes the interface's next version. |

Every program command except `derive` takes `--workspace` (default: the current
directory), the root of the project's Git checkout. Unrecognized `program run` flags
(for example model overrides) are passed through, with `--workflow build`, to every
workstream run that pass starts; a resumed run keeps its saved settings. `program run`
exits 0 only when the program is `COMPLETE`.

Program manifests support `code`, `integration`, and `deployment`. UI workstreams are
deferred until the UI runner supports checkpoint recovery; use `autocode ui` separately.

### Figma / UI

| Flag | Meaning |
| --- | --- |
| `--build` | Hand an accepted Figma result to the implementation runner. |
| `--figma-file <url>` | Refine or implement from a specific Figma file. |
| `--ui-run /path` | Import a completed design run. |
| `--figma-review human` | Require a human visual approval during implementation. |
| `--max-plan-reworks`, `--max-reworks` | Review-loop limits (`none`, `0`, or a number). |

## Subcommand options

These options belong to the command in the first column. A shared spelling can
have a different scope: for example, `ui --run-dir` creates a new artifact
directory, while the main run's `--run-dir` selects a saved run. Every command
also accepts `-h` / `--help`.

| Command | Flags | Meaning |
| --- | --- | --- |
| `autocode conversation` | `--workspace`, `--run-dir`, `--text`, `--title`, `--request-id`, `--expected-revision`, `--supersedes-request-id`, `--question-id`, `--resolver-token`, `--expected-plan-token`, `--timeout` | Select a conversation/run, queue a human message, explicitly refresh a revision or hand off its reviewed draft; creation provider/model/effort/budget options follow `--`. On `send`, `--supersedes-request-id` queues a correction only for a definitively rejected, unapplied human request, retaining its history; it cannot replace acknowledged input or active delivery. Sending does not advance the run. All commands print JSON. |
| `autocode verify` | `--workspace`, `--base`, `--branch`, `--patch`, `--criteria`, `--criteria-file`, `--out`, `--record`, `--engine`, `--provider`, `--model`, `--reasoning-effort`, `--evidence-provenance`, `--suite-command`, `--check-timeout`, `--max-seconds`, `--max-stage-seconds`, `--json` | Verify an existing change against caller criteria, or read a retained verification and recheck freshness. See [Standalone verification](verify.md). |
| `autocode doctor` | `--workspace`, `--engine`, `--json` | Project and engine to inspect; optionally print checks as JSON. |
| `autocode models` | `--workspace`, `--provider`, `--json`, `--prices` | Project/provider model availability and default routes; optionally JSON; `--prices` shows the dated price table and each default route's rate. |
| `autocode review-pr` | `--out`, `--post`, `--json` | New owned review directory, optional COMMENT-only posting after full finding/head admission, and JSON findings/evidence; creation/provider options follow `--`. |
| `autocode review` | `--workspace`, `--run-dir`, `--show`, `--json` | Saved task workspace and run (otherwise the workspace's only run); text or JSON evidence without decisions. With neither display flag, ask approve/reject/skip for each pending criterion. |
| `autocode clean-worktrees` | `--workspace`, `--yes` | Select the project; remove eligible completed worktrees only with `--yes`. |
| `autocode bundle` | `--out`, `--with-diff`, `--json` | Where to write the archive; add the run's diff of the workspace source; print the archive path and manifest as JSON. |
| `autocode prune-run` | `--yes` | Delete the listed settled evidence bytes (manifests, receipts and hashes retained); without it the command only lists. |
| `autocode merge` | `--workspace`, `--into` | Select the project and target branch for a delivered task worktree. |
| `autocode checkpoint` | `--workspace`, `--run-dir`, `--compare`, `--restore`, `--expected-token`, `--request-id` | Compare or restore retained code. Restore requires its exact inspected token; the request ID identifies the action. |
| `autocode replay` | `--into`, `--export` | Create a replay at an exact fresh run directory in an isolated workspace, or export a bounded recording bundle. Exactly one target is required. |
| `autocode replay` | `--recorded-checks`, `--json` | Simulate archived checks with `--into` without current completion or delivery proof; optionally print the result as JSON. Fresh checks are the default. |
| `autocode tasks` | `--workspace`, `--max-parallel`, `--dry-run` | Project, concurrent lanes and a read-only manifest preview. |
| `autocode runs` | `--workspace`, `--all`, `--needs`, `--json` | Project whose runs to list; every registered project instead of one; only the runs waiting on a person; rows as JSON. Read-only: each row shows that run's own status and the command it accepts next. |
| `autocode events` | `--workspace`, `--run-dir`, `--follow`, `--timeout` | Project whose saved run to stream, and the run itself (otherwise the workspace's only run); keep printing new events until the run ends the stream; bound a follow to this many seconds. |
| `autocode wait` | `--workspace`, `--run-dir`, `--timeout`, `--json` | Project whose saved run to wait on, and the run itself (otherwise the workspace's only run); stop waiting after this many seconds; the outcome as one JSON object. |
| `autocode components` | `--workspace`, `--engine`, `--provider`, `--joint-planning`, `--reasoning-effort` | Project and model routing for each component. |
| `autocode components` | `--auto-approve` | Apply decisions the operator has already delegated; the flag itself supplies no delegation. |
| `autocode components` | `--options`, `--max-advances`, `--timeout` | Shell-quoted child flags, maximum CLI advances per component (20) and per-component wall-clock budget. |
| `autocode components` | `--integrate`, `--run-local`, `--health-timeout`, `--keep-running`, `--runtime-evidence-provenance` | Integration target, Compose smoke checks, readiness deadline, optional retained services and smoke-evidence origin (`fake`, `live`, `unknown`); see [local integration](task-lanes.md#running-the-combined-system-locally). |
| `autocode demo` | `--directory` | Create a new directory for retained offline demo outputs; defaults to a fresh temporary directory. Existing directories are refused. |
| `autocode ui` | `--workspace`, `--run-dir`, `--figma-file`, `--from-plan-run` | Project, new artifact directory, design file and saved accepted UI plan. |
| `autocode ui` | `--planner-model`, `--astra-model`, `--terra-model`, `--sol-model`, `--astra-reasoning-effort` | Requirements/Planner, Plan Reviewer, Builder and Tester model choices; review reasoning effort. |
| `autocode ui` | `--max-plan-reworks`, `--max-reworks`, `--dry-run`, `--build`, `--no-chat` | Plan/design rework limits, preview, implementation handoff and noninteractive presentation. |
| `autocode program` | `--workspace` | Project for program commands; `derive` defaults to its plan run's project. |
| `autocode program plan` | `--engine` | Engine for the ordinary planning run; additional run flags pass through. |
| `autocode program derive` | `--run-dir`, `--output`, `--name` | Approved planning run, optional manifest output and program name. |
| `autocode program approve` | `--token` | Exact displayed agreement token; `-` reads its paired environment variable. |
| `autocode program run` | `--max-parallel`, `--authorize-deployment`, `--engine`, `--retry-workstream`, `--check-timeout`, `--dry-run` | Parallelism, deployment authorization, child engine, explicit retries, cumulative check deadline and preview; additional run flags pass through. |
| `autocode program status` | `--max-parallel`, `--authorize-deployment`, `--engine`, `--retry-workstream`, `--check-timeout`, `--dry-run` | Accepted compatibility options; status remains read-only and starts no workstream. |
| `autocode program request-change` | `--interface`, `--by`, `--reason`, `--proposal` | Interface, requesting workstream, reason and optional proposed change. |
| `autocode program resolve-change` | `--request`, `--reject`, `--reason` | Reject the named change request with the recorded reason. |
| `autocode compare-baseline` | `--baseline-root`, `--candidate-root`, `--normalize-dependency-prefixes`, `--output` | Source roots, explicit dependency-prefix normalization and comparison-result output; see [baseline comparison](execution.md#baseline-comparison). |
| `autocode visual-capture` | `--config`, `--timeout` | Capture configuration and command deadline (120 seconds). |
| `autocode visual-check` | `--workspace`, `--policy`, `--policy-sha256`, `--output` | Project, source-owned policy and expected hash, and fresh check-output directory. |
| `autocode capture` | `--output`, `--no-compress`, `--mode`, `--known-output-sha256` | Retained output, full display, `raw`/`conservative` display and verified unchanged-output reference; a command follows `--`. |
| `autocode output` | `--store`, `--mode`, `--known-sha256`, `--raw`, `--start-line`, `--end-line` | Retained store, display mode, verified copy reference, raw retrieval and requested line range. |
| `autocode registry` | `--max-depth`, `--directory-budget`, `--workspace`, `--run-dir`, `--json` | Bound imports or select a run; see [Registry API](registry-api.md) for the operations using each option. |
| `autocode intervention` | `--workspace`, `--run-dir`, `--request-id`, `--kind`, `--text`, `--json` | Select a run, identify a feedback/pause/stop request and supply text; optionally JSON. |
| `autocode issue` | `--project`, `--remote`, `--issue-file`, `--note`, `--base`, `--pr-base`, `--open`, `--ready` | Project and remote; optional offline issue and extra guidance; start/PR bases; explicit push/open and ready-for-review controls. See [GitHub issues](issues.md). |
| `autocode arena` | `--arena` | Arena store (default `.autocode/arena`). |
| `autocode arena` | `--repository`, `--base`, `--issue`, `--issue-file`, `--note`, `--oracle`, `--reference`, `--check`, `--split` | Ingest a case with its pinned repository, request, oracle/reference, checks and development/regression/holdout split. |
| `autocode arena` | `--cohort`, `--runner`, `--option`, `--fixture`, `--i-authorize-live-model-spend`, `--approve-benchmark-plans`, `--timeout`, `--oracle-timeout` | Run/report/propose controls: cohort, runner checkout, repeated child options, fake or explicitly authorized live run, plan approval and deadlines. |
| `autocode arena` | `--baseline`, `--candidate` | Cohorts to compare. |
| `autocode arena` | `--report`, `--reviewer` | Annotate a completed attempt from an attribution JSON report; an explicit independent reviewer is required to confirm a diagnosis. |
| `autocode unattended --analyze` | `--analyze`, `--run-dir`, `--workspace`, `--out` | Read-only run analysis; optionally save its evidence report in `--out`. |
| `autocode dashboard` | `--workspace`, `--watch-root`, `--watch-depth`, `--runner`, `--port`, `--provider` | Repeated projects and discovery roots, discovery depth (3), CLI runner, local port (8765) and provider for new runs. |

## Unattended callers (agents)

`autocode unattended` (or the internal compatibility wrapper
`autocode-unattended` / `scripts/autocode-unattended` from a checkout) runs AutoCode
for another agent without letting that agent make the operator's decisions. It takes
AutoCode's own arguments but refuses every decision or recovery flag (`--answer`,
`--delegate*`, `--approve-*`, `--resume-paused`, `--retry-*`, `--feedback`, `--follow-up`,
`--accept-completion`, `--close-finding`, `--close-reason`, `--resolver-response`, …, including
abbreviations), the command word `resume` (on a paused or blocked run it stands for
`--resume-paused`; a bare relaunch still continues a run that is not paused)
and the `intervention`, `tasks`, `ui`, `program`, `registry`, `capture` and `compare-baseline`
subcommands. It forces `--no-chat` with no stdin, and when AutoCode stops it prints
`--status` and tells the caller to report and stop. Exit codes are AutoCode's.

When a run completes, the wrapper prints the command to analyze it:
`autocode unattended --analyze --run-dir RUN [--out DIR]`. That launches no stage; it
reads the saved run and reports the outcome, each acceptance criterion with its
recorded status and evidence, findings, stages (role, time, exit, tokens, report
path), cost by role (model calls, seconds and tokens per role, with report-format
repair calls counted separately), and the code changes against the task's base commit,
including new untracked files, plus a summary of the run's always-on [activity log](execution.md#activity-log).
`--out` saves `analysis.md`, the full `changes.diff` and a copy of `activity.jsonl`.

To lock a Claude Code agent to it, launch the agent from a copy of
[`examples/agent-operator`](../examples/agent-operator): its `.claude/settings.json`
allows only `autocode-unattended` and read-only tools, denies edits and other
AutoCode or provider commands, and uses `dontAsk` so anything else is refused
without a prompt. Its `CLAUDE.md` has the agent report stops verbatim and, after completion,
analyze the work read-only. Keep that directory outside the target workspace.

## Exit codes

- **0** — an action was saved, or the task completed (`status == "TASK_COMPLETE"`).
- **2** — user input, pause, or error. Inspect `--status`; do not rely on the exit code alone.

Scripts should prefer `--json`: the payload's `status`, `done` and `needs` fields say
which of the three it was, where an exit code cannot, and after an action `result.saved`
and `result.error.code` say whether it was saved or refused. The exit codes stay 0 and 2
for compatibility — argparse also uses 2 for usage errors.

See also: [Install](install.md) · [Workflow](workflow.md) · [Execution](execution.md)

## Exact tool output

Use `--tool-output-mode raw|conservative` for AutoCode capture/file-read display.
`autocode output read FILE` returns exact sections with retained originals;
`autocode output retrieve SHA256 --raw` recovers exact bytes. See
[exact output transport](exact-output.md) for options, recovery and measurement
limits. Native provider tools keep their existing behavior.

A failed read-only workflow job exposes an exact `retry_job` action in status.
After inspecting the archived attempt, retry with `--resume-paused
--retry-failed-stage --job-retry-token TOKEN` using its current
`needs.job_retry_token`. Plain resume does not repeat the job. Unrestored source
blocks retry until the exact original source is restored. The CLI verifies file
bytes, modes and Git HEAD even when a capture artifact is missing; changed
model/limits still invalidate the retry. An older attempt with no saved original
identity exposes `recover_source` and an explanation instead of a retry action.
Inspect its archive and current changes before starting a new run.

A failed Builder classification can also offer `retry_job` for the Investigator
at `PAUSED_BUILDER_CLASSIFICATION`. Inspect `needs.retained_evidence` and use the
current `needs.job_retry_token` through `AUTOCODE_JOB_RETRY_TOKEN` with
`--resume-paused --retry-failed-stage --job-retry-token -`. Run this retry
separately from feedback, approval, source acceptance or abandonment. It starts
one fresh read-only investigation of the same task, contract, source and failure;
any queued Resolver proposal stays unassigned until the normal classification
policy accepts the new diagnosis. Previous failures, repair counts, approvals,
Builder allowances and time limits remain in force. Status offers `--edit-goal
FILE` and requires fresh approval when a remaining failure or classification
limit prevents retry.
Older checkpoints that discarded the failed request also require a plan
correction or a new run; plain resume cannot reconstruct the missing proposal.

A job its provider's content filter refused (`PAUSED_CONTENT_FILTER`) or that ran
out of quota (`PAUSED_BUDGET`) pauses the same way, and its stop names the cause
and the model. The same model is likely to refuse again, so `needs.route` carries
the job's model question. `--job-retry-token` is accepted without `--resume-paused
--retry-failed-stage` only together with that answer:
`--answer route-ROLE=MODEL --job-retry-token TOKEN` checks the model the way a launch
would, records a `route_assignment` and issues a new token for the new model. The
old token stops working, and the exact retry with the new token is the only way on:
`--resume-paused --retry-failed-stage --job-retry-token NEW_TOKEN`. After a refusal,
until a model is named, the status view's `needs.action` is that answer and the
recovery card offers no retry, since the exact retry would replay the refused model. The Architect,
Analyst and Investigator have no `--ROLE-model` flag, so this answer is how they
move. The answer changes only that model: given with a limit or another role's
model, with or without its token, it is refused and nothing is saved. It issues a
new token, so it is given on its own: next to `--resume-paused --retry-failed-stage`
it is refused before anything is read or saved. At that stop `--abandon-stage` is
refused, and a `--ROLE-model` flag for the stopped role is refused with this advice
instead of being saved (saving it would make the retry stale); only a flag that
puts back the model the retry is bound to is saved. The same flag is refused while
the job's attempt is still uncertain, including with `--abandon-stage` in the same
command: run `--abandon-stage ATTEMPT` alone, then answer with the token it shows.
After a quota stop, the unchanged retry with the shown token also works once the
quota resets. A stopped stuck-stage Investigator (`investigate_stuck`) accepts
`route-stuck_investigator`: its answer saves both the preparation pin and the current
route, then issues a new exact-retry token. Use that token for the retry. Changing
`--investigator-model` alone at this stop is refused before saving settings.
An older Investigator stop can expose the same question only when its retained
attempt, launch configuration and original source establish that model. Other
older job stops without a saved model question keep their exact retry.
## Standalone verification

`autocode verify` accepts `--workspace`, `--base`, either `--branch` or `--patch`,
and either `--criteria` or `--criteria-file`. Criteria are explicit JSON; they
are never inferred. `--out` names a fresh output directory. `--record` reads an
existing verification and checks freshness instead of running models or checks.
Review controls are `--engine`, `--provider`, `--model`, `--reasoning-effort`,
`--evidence-provenance`, `--max-seconds` and `--max-stage-seconds`.
`--suite-command` selects the proof suite and `--check-timeout` bounds each runner
command. `--json` emits the verdict and evidence metadata. See
[Standalone verification](verify.md) for the criteria format, proof modes and
exit codes.
