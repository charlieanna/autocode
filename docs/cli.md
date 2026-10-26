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
| `autocode resume` | Continue the unfinished run of this project or task worktree (see [Which run a command acts on](#which-run-a-command-acts-on)). Never starts a new task. On a paused or blocked run (`PAUSED_*`, `BLOCKED_*`, `*_REWORK_REQUIRED`, `RESOLVER_PENDING`) it also acknowledges the stop, as `--resume-paused` does, so the run goes on: no new budget or recovery allowance, though the per-cycle report-repair and resolver attempt counts restart. A design conflict (`PAUSED_DESIGN_CONFLICT`) is only shown until you edit the design and pass `--resume-paused`. Its companions (`--retry-failed-stage`, `--grant-recovery N`, ...) need no `--resume-paused` after it. Plain `autocode` with no task, run from a script or with output redirected, relaunches a running run the same way but only shows a stop; in an interactive terminal it opens `autocode chat`. |
| `autocode status` | The same as `autocode --status`: read-only status of that run. Command words are recognized wherever they stand among the options; a task whose whole text is `resume`, `status` or `explain` goes after `--` (`autocode -- status`). |
| `autocode explain` | The same as `autocode --explain`: explain the saved run's stop and the offered next command. Reads only; no model call, run lock or workspace changes. |
| `autocode demo [--directory PATH]` | Watch an offline sample plan, demo approval, build, rejected evidence and completion acceptance with the bundled fake provider. No account or model spend. Creates a new temporary directory by default, keeps its run and evidence records for inspection, and refuses an existing `--directory`. See [Offline demo](#offline-demo). |
| `autocode verify` | Check an existing working-tree change, branch or patch against caller-supplied criteria; retain independent review, replay and external-change proof in the canonical evidence format. See [Standalone verification](verify.md). |
| `autocode chat [BRIEF] [--mode ID] [-- RUN_OPTIONS]` | Talk to a run in the terminal, the way the VS Code extension does: it prints the run's conversation, asks you only for what the [status view](task-run.md#status-view) leaves to you (an answer, a plan approval, a reply) and runs exactly the command the view names. Plain `autocode` in an interactive terminal opens it too. With a brief it starts a new run in the project (`--in-place`, with the start flags of the `--mode` from `autocode modes list`, else its default). Without one it picks up the project's open run (or `--run-dir RUN`), lets you choose when several are open, and asks what you want done when none is. Leaving at a prompt changes nothing. While a stage works, Ctrl-C asks for the view's pause after the current step, a second Ctrl-C interrupts the step and a third stops AutoCode; the chat never exits while AutoCode still runs. A brief that starts with `-` goes in `--brief=TEXT`. Where the view names no action it can take, it prints the command to run yourself. Exits 0 when the run completes, 2 when it waits for you. See [Terminal chat](#terminal-chat). |
| `autocode conversation start\|attach\|read\|send\|refresh\|handoff` | Core Requirements conversation journal tied to one task run: queue human messages, explicitly refresh its draft and inspect the handoff. See [Core Requirements conversations](task-run.md#core-requirements-conversations). |
| `autocode ui` | Figma design (and optional `--build` handoff to implementation). |
| `autocode tasks` | Run a multi-lane task flow file; accepts `--json`. |
| `autocode components` | Build the components of an architecture record in parallel and combine them (see [Task lanes](task-lanes.md#building-components-of-an-architecture-in-parallel)); with `--integrate TARGET --run-local`, also start the combined system with Docker Compose and run its smoke check (see [Running the combined system locally](task-lanes.md#running-the-combined-system-locally)). |
| `autocode dashboard` | Local browser dashboard. |
| `autocode unattended` | Agent wrapper that refuses operator decisions and reports a stop (see [Unattended callers](#unattended-callers-agents)). |
| `autocode issue brief\|start\|status\|continue\|plan\|pr` | Read a GitHub issue, work on it through the task-run interface, put the plan waiting for approval in a pull request as a plan file for review (`plan`), and prepare a pull request (see [GitHub issues](issues.md)). |
| `autocode bridge run\|check\|status` | The channel bridge daemon: take labeled GitHub issues, start durable runs, relay questions, plan approvals and pauses to the issue, and take an allowlisted actor's reply as the exact action the status view names (see [Channel bridge](channels.md)). |
| `autocode changeset link\|status\|approve\|check-command\|verify\|waive\|reopen\|order\|pr-body\|list` | Start a run in another registered project for work a run's request places there, show the change set the linked runs form, approve all their plans at once, check them together with an integration command, and write the section each pull request carries (see [Linked runs](task-run.md#linked-runs)). |
| `autocode arena init\|ingest\|cases\|run\|report\|propose\|annotate\|compare` | Run independent benchmark cases, append evidence-backed failure investigations and compare candidate cohorts (see [Arena](arena.md)). |
| `autocode checkpoint --workspace PATH --run-dir RUN --compare CHECKPOINT` | Compare retained code or restore it by the exact inspected token (`--working` shows what the Builder has changed so far, display only); accepts `--json` (see [Saved code checkpoints](task-run.md#saved-code-checkpoints)). |
| `autocode merge [WORKTREE] [--workspace PATH] [--into BRANCH] [--json]` | Merge a delivered task worktree into its base branch (see [Task lanes](task-lanes.md#when-a-task-finishes)). |
| `autocode capture --output FILE -- COMMAND` | Run a command with exact output retained for retrieval (see [Exact output](exact-output.md)). |
| `autocode output read\|retrieve` | Read or retrieve exact retained file bytes (see [Exact output](exact-output.md)). |
| `autocode visual-capture --config FILE` / `autocode visual-check --policy FILE` | Capture visual references or run a source-owned visual check (see [Visual checks](visual-captures.md)). |
| `autocode compare-baseline` | Compare Vitest failure evidence (see [Execution](execution.md#baseline-comparison)). |
| `autocode review-pr URL [--post] [--out NEW_DIRECTORY] [--json] -- PROVIDER_OPTIONS` / `autocode-issue review-pr` | Fetch the exact PR head into an owned checkout, run Code Reviewer only, and print authenticated findings/evidence. `--post` submits COMMENT only after every new-side anchor and the head are rechecked (see [Pull request reviews](issues.md#reviewing-a-pull-request)). |
| `autocode review --workspace PATH [--run-dir RUN]` | Inspect pending human-review criteria and their recorded evidence, then approve, reject with a note, or skip. Reads decisions from terminal or piped input. `--show` and `--json` display without prompting; decisions never start a model. See [Human review](execution.md#human-review-in-the-terminal). |
| `autocode serve start\|stop\|status [--workspace PATH] [--run-dir RUN]` | Run the run's development server in the foreground from the project's saved `serve` command ([Project settings](#project-settings)) on a free loopback port until Ctrl-C or `autocode serve stop`; `stop` ends it and waits (`--timeout`); `status` shows whether it is configured, running and its address (`--json`). See [Development server](#development-server). |
| `autocode forget [--workspace PATH] [--json]` | Clear only the learned project record; keep explicit configuration, saved runs and evidence. See [Project memory](project-memory.md). |
| `autocode clean-worktrees [--yes] [--json]` | List, then with `--yes` remove, task worktrees whose runs are complete and whose branch holds their work; records are archived and branches kept (see [Task lanes](task-lanes.md#when-a-task-finishes)). |
| `autocode prune-run RUN [--yes] [--json]` | List, then with `--yes` delete a run's settled evidence bytes: finished verification-copy trees (`tool-containment-*/scratch`) and settled job-source stores (`<attempt>.source/`). Manifests, receipts and hashes are retained, so what was there stays provable; `state.json` is never modified. Anything still named at file level by state, owned by a live stage or a pending failure recovery, or the newest of its kind is kept with the reason shown. |
| `autocode bundle RUN [--out FILE] [--with-diff] [--json]` | Write one redacted archive of a run for a bug report (default `WORKSPACE/.autocode/bundles/RUN.tar.gz`): the replay recording `replay RUN --export` writes, so a maintainer can [replay](replay.md) it; `state.json`, `activity.jsonl`, the stage reports, the check-replay receipts and the last 200 lines of each provider event stream; the resolved settings, `doctor --json` output and the version. Every file it writes passes the credential redaction exports use, plus the values of secret-named environment variables; a recording that redaction would still change is left out, and the manifest says why. No workspace source unless `--with-diff` adds the run's diff (against its base commit, else the uncommitted work). `manifest.json` lists each file's sha256; cite the printed manifest hash in a bug report or a `docs/bugs/` note instead of pasting logs. The run directory is only read. |
| `autocode --version` | Print the executing package version and its source commit when known. An installed package without a source checkout reports `commit unknown`; it does not borrow the workspace commit. |
| `autocode completion bash\|zsh\|fish` | Print a shell completion script read from the CLI's parsers: the commands, every public option of the main command with its choices, and file names after an option that takes a path. A subcommand completes from the parser it reads: its options, its commands (`output read`, `arena run`) and their options, or its argument's choices (`completion bash`). `capture`, `registry`, `intervention` and `dashboard` complete only `--help`. Load it with `source <(autocode completion bash)` (zsh the same, after `compinit`; fish `autocode completion fish \| source`), or save it; see [Shell completion](install.md#shell-completion). |
| `autocode models [--provider NAME] [--workspace PATH] [--json] [--prices] [--selection]` | List the models your plans offer, grouped by plan (subscription or pay per token) and tier (cheap worker, strong judge, Resolver only), and check the route every role of a new run takes. Suggests a replacement for any route you cannot use; exits 1 when one is missing (see [Models](models.md#when-a-model-is-not-in-your-plans)). A profile default the provider no longer offers is not a gap when automatic selection replaces it with a route it does offer: the verdict then weighs the selected routes and names each replacement, because the profile defaults are only the fallback a run uses when selection cannot field a set (#1995). `--prices` prints the dated price table instead, and the rate each default route is priced at, or "no rate" (never a zero price); it runs nothing and works offline. `--selection` reads the tool's own model and login listings (still no model calls) and prints what automatic selection would choose - the route per role with its price and reasoning level, why every other qualified model lost, and which chosen routes the access check would still probe; exits 1 with the account-terms stop when the connected accounts cannot field an independent checker ([Choosing models automatically](models.md#choosing-models-automatically)). |
| `autocode doctor [--workspace PATH] [--engine opencode\|codex\|claude] [--json]` | Check Python, psutil, Git, each engine (OpenCode must be 1.x; Codex must be logged in), that the workspace is a Git repository with a commit, and the projected run storage (workspace size x its measured ~3x copy growth: worktrees, verification copies, attempt snapshots) against free disk space — a doomed filesystem is a ✖, a tight one a warning with the numbers. Prints the fix for anything missing; exits 1 when not ready. Passes only when the engine a new run uses is ready, resolved as a run resolves it: `--engine codex` is Codex; without `--engine`, the `engine` the project's files set ([Project settings](#project-settings)) counts as that flag; otherwise (no engine, or `--engine opencode`) the provider `AUTOCODE_PROVIDER` or `default_provider` in `~/.config/autocode/config.toml` names, else OpenCode. That provider must answer and list the route each role of a new run takes - its default model, or the route automatic selection replaces that default with (`opencode models`, as `autocode models` checks; #1995). `AUTOCODE_PROVIDER=codex` names a provider config, not the Codex engine. A ready Codex alone does not pass; doctor names `--engine codex` as the single-login alternative. `--engine claude`, a project's `engine = "claude"`, or a `claude` default provider, checks Claude Code instead: `claude` on PATH and `claude auth status --json`, run in the agents' environment, reporting a Claude subscription login (`engine:claude`; an API key, Bedrock or Vertex is refused, by the same check a run makes: the agents' environment and the project's `.claude/settings.json`), its conformance row, and its routes; a user `claude.toml` it ignores is a warning. Doctor shows no Claude rows for other runs and never prints the account's email or organization. Lists the settings a new run takes from the project's files ([Project settings](#project-settings)) and fails on a file a run would refuse. When a new run launches built-in OpenCode stages, names the kernel tool boundary they get on this machine (`containment`: macOS Seatbelt or Linux bubblewrap and its version, with OpenCode 1.18.33, by the check run setup makes; `--json` adds it as `containment` with `backend`, `backend_version` and `problem`, or `null` for a Codex, Claude Code or configured-provider run), as a warning that does not fail doctor when such a run would be refused without `--allow-uncontained-tools` ([Execution](execution.md#native-tool-containment)). Runs without psutil and reports it missing. Never reads credentials. |
| `autocode registry location\|list\|import` | Registry API (see [Registry API](registry-api.md)). |
| `autocode runs [--all] [--needs] [--json]` | Every run of this project (or, with `--all`, of every registered project) in one read-only table, each JSON row titled with the first line of its task (`title`): what each run's own status view says it needs, and the command that answers it. Needs-first order, then working, then settled, then runs whose checkpoint cannot be read; the row shows how long since the checkpoint was written. Nothing here starts, resumes or changes a run. Where a saved run's status alone cannot tell an uncertain attempt from one whose retained report only needs repair, the row points at `autocode --run-dir RUN --status` instead of offering a command that could set accepted work aside. It reads saved checkpoints, so unlike `autocode --run-dir RUN --status` it inspects no supervised process: a row says what the run last saved, not whether its provider is still alive. `registry list` is the pointer index with each pointer's availability; `runs` is what each run now wants from you. |
| `autocode events [--run-dir RUN] [--follow]` | One saved run's recorded events as a JSON-lines stream: how it got to where `--status` says it stands, one event per line, printed exactly as the run wrote it. These are the run's own records — its approvals, answers, contract and configuration changes, routing decisions and self-taken recoveries — not a provider's transcript. `--follow` keeps printing new events until the run ends the stream, and the status view contract is what ends it: a completed run exits 0, a run that needs a person exits 2 after naming that person's command on stderr, and `--timeout SECONDS` bounds the wait. Stdout is always JSON lines, so `--json` does not apply; the envelope is [run-event.schema.json](schemas/run-event.schema.json), whose `kind` is an open vocabulary a consumer must tolerate. Read-only, and like `runs` it inspects no supervised process: a stage hung mid-flight keeps the stream open, so ask `autocode --run-dir RUN --status` whether it is alive. |
| `autocode plan --export PATH [--run-dir RUN] [--json]` / `autocode plan --check PATH [--json]` / `autocode plan --render PATH [--json]` | Write a saved run's current plan (shown for approval or approved) as a Markdown file to review in a pull request: a front matter holding the contract body exactly, its goal hash (the SHA-256 of the body) and the revision it came from, then the plan as text generated from it. Never the approval token; credential-shaped text is masked as in `bundle`, and the export then says the file's goal hash differs from the run's. `--check` reads such a file and prints its goal hash, and exits 2 when the file cannot be read or its text no longer matches its front matter. `--render` regenerates that text after the front matter was edited. Reads the checkpoint like `events`: no run lock, nothing recorded, nothing launched; only the named file is written. `--json` formats results or error endings as structured JSON. See [Reviewing a plan in a pull request](workflow.md#reviewing-a-plan-in-a-pull-request). |
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
| `--workflow build\|bugfix\|review\|design\|discuss` | Name the kind of job instead of having Requirements (or, on a flow without it, the recognizer) name it from the request. A pinned build still runs Requirements first; any other kind starts its own first stage. Also accepted by a saved run whose job is not named yet. A run whose job is already decided keeps it: start a new run to change it (see [Workflow](workflow.md)). |
| `--status` | Read-only status, including `milestone_checkpoint`, `interventions`, `active_stage.activity`. |
| `--json` | Stdout carries exactly one JSON object unless the invocation ends with an error message other than a refused setting (below); everything else the invocation prints goes to stderr. A finished or paused run prints the same payload `--status` prints. A user action (an approval, answer, feedback, resolver response and the like) prints that payload plus `result`: `exit_code`, `saved`, `message` (the text a person would have read) and `error`, which is `{"code": "input_rejected", "message": …}` when the input was refused and null otherwise. A refusal before the run loads prints `{"result": …}` alone. Any other invocation that loaded the run and returned without printing the payload, such as one stopped by `--pause-after-stage` or a time or iteration limit, prints the saved run's payload plus `result`. A setting the run's configuration refuses as the run loads still ends with `autocode: …` on stderr, and also prints the saved run's payload plus `result` (`{"result": …}` alone for a new run refused before its first checkpoint), whose `error` is `input_rejected` with the refusal text. Such settings include `--single-model`, `--spec-first`, `--blind-tests`, `--utility-model`, `--builder-strong-model` or a different `--engine` on a saved run, `--single-model` with a per-role model flag, `--max-milestone-seconds` on a saved run without milestone checkpoints, and the tool-containment refusals: `--allow-uncontained-tools` on a run with no built-in OpenCode stage, or a built-in OpenCode run without it where the kernel tool boundary is unavailable. The refused setting is not saved, though a new run without `--in-place` may already have created its task worktree and branch, which `result.message` names. In a native visual run (a task with a `VISUAL_REVIEW_PROFILE=` directive) such a refusal is instead saved as a `PAUSED_INVALID_OUTPUT` pause and printed like any other pause, with `error` null. Any other invocation that ends with an error message (`autocode: …` or argparse's `usage:` text on stderr) prints nothing on stdout. A refusal reported in `result` also prints `{"error": …}` on its own line of stderr: its last line, except after a refused setting, where `autocode: …` follows. [status-view.schema.json](schemas/status-view.schema.json) describes the status payload. The lone `{"result": …}` does not validate against the schema's root; its `result` validates against the schema's `properties.result`. Exit codes are unchanged ([Exit codes](#exit-codes)); a usage error still prints argparse's text. The read-only commands (`--status`, `--explain`, `--dry-run`, `doctor --json`, `registry`, `usage --json`) print their own output and are unaffected. |
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
| `autocode`, `autocode resume`, `--resume-paused` and its `--retry-*` companions, `--unit` | The only unfinished run. A finished run (`TASK_COMPLETE`, or stopped) is never relaunched this way, and a run that `autocode tasks` drives is left to that command. |
| `--status`, `--explain`, `--dry-run`, `autocode status`, `autocode explain` | The only unfinished run; with none, the latest finished one. |
| `--show-goal`, `--answer`, `--approve-goal`, `--feedback` and the other user actions | The only unfinished run, preferring one no task flow drives. These save the run, so they never pick a finished one. |
| `--follow-up` | The most recently completed run, preferring one no task flow drives. Refused when a run started after it finished has not finished: name the run with `--run-dir`. |

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

Prefer this on shared machines: Linux exposes `/proc/<pid>/cmdline` to every local user, while
`/proc/<pid>/environ` is readable only by the owner, and a typed token also lands in shell
history. The environment variable alone never authorizes anything — the flag must still be
present. `TaskRun`, dashboard actions and the trial driver pass these tokens through
each CLI child's environment. Their process arguments and saved command records contain
`-`. Literal CLI tokens remain supported. The controller always removes all eight
variables before launching a provider or test command, even if named in `AUTOCODE_PASS_ENV`.
Programmatic command builders require full token option names; abbreviated token
options are refused before recording or launch.

With several unfinished runs the command changes nothing, exits 2 and lists them with the
`--run-dir` command for each. With no run it says where it looked; with only finished
runs, a bare `autocode` names the latest one instead of relaunching it. Inside a run
directory whose `state.json` is missing or cannot be used, or one `autocode clean-worktrees`
archived, it refuses rather than pick another run.
A task, or a new-run option (`--in-place`, `--figma-file`, `--figma-manifest`,
`--figma-review`, `--ui-run`, `--builder-strong-model`, `--conversation-handoff`, `--test-root`, `--package`), starts
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
| `--answer route-sol=MODEL` | At a quota stop (`PAUSED_BUDGET`), a content-filter refusal (`PAUSED_CONTENT_FILTER`), an authenticated output-limit stop (`PAUSED_OUTPUT_CAP`) or a context-window stop (`PAUSED_CONTEXT_WINDOW`: the request no longer fit, or the provider compacted the conversation), name the model the stopped role continues on. Requires the current `--resolver-token`. The model must suit the role's engine, be listed by OpenCode on OpenCode runs and keep the cross-model rule; otherwise the run stays paused with the question open. Sets the stopped attempt aside as `--abandon-stage` does and records a `route_assignment`; continue with `--resume-paused`. The resume-flag form is `--abandon-stage ATTEMPT`, then `--resume-paused --sol-model MODEL`. A stopped workflow job (`needs.kind` `retry_job` with `needs.route`) takes `--answer route-ROLE=MODEL --job-retry-token TOKEN` instead: no resolver token, no `--abandon-stage`, no `--ROLE-model` flag; it issues a new job retry token, so continue with `--resume-paused --retry-failed-stage --job-retry-token NEW_TOKEN` ([Waiting or finished](#waiting-or-finished)). See [Models](models.md#when-a-roles-quota-runs-out). |
| `--answer effort-ROLE=EFFORT` | At `PAUSED_EFFORT_UNSUPPORTED`, a reasoning effort you named that the role's model does not declare: name one of the variants the stop lists (the status view's `needs.effort.declared`). No attempt or issued token is involved, as for a cross-model stop. Saves the level as your own choice (origin `user_explicit`) and records an `effort_assignment`; the run waits at the same pause, and `--resume-paused` then continues. A variant the model does not declare is refused with nothing saved. See [Reasoning effort](models.md#reasoning-effort). |
| `--feedback '…'` | Send a correction; returns to discovery and requires fresh approval. With `--adaptive-planning`, feedback on a plan shown for approval goes to the Planner, which revises it. Refused at an operational pause unless it offers feedback (an exhausted plan-review budget, a validation-only stop). |
| `--follow-up '…'` | Say the next thing to a finished run ("Fix them." after a review): the run starts at Requirements again, which names the new job, and continues in the same run directory. Finished runs only (`TASK_COMPLETE`); any other run exits 2 unchanged. After a design review, the reply revises that review: the Architect keeps every concern under its id, keeps a settled one as resolved with what settled it, and adds the revision to `revisions` in `review/design-review.json` ([Replying to a design review](#replying-to-a-design-review)). After a design turn, "Build it." builds that design as approved: it is checked against the code first (`check_design`). The message is saved as feedback that planning can cite for requested contract changes; the new plan still needs approval. |
| `--delegate Q1` | Accept a question's proposed default. Requires the current `--resolver-token` shown by Resolver. |
| `--delegate-all --review-token 'r3:<hash>'` | Delegate every pending question marked `delegable` with a proposed default, on the exact displayed revision. Refuses the whole call if any question lacks a default, is not delegable, has a protected or missing category (cost, quota, permission, external side effect, requested outcome), or asks about a rejected assumption. Never approves; invalidates any existing approval. |
| `--reject-assumption A1 --review-token 'r3:<hash>'` | Reject a structured assumption from the displayed requirements handoff (repeatable). A stale token, or a handoff refreshed since display, is refused. Never approves; invalidates any existing approval. |
| `--show-goal` | Display the current contract/revision. At the approval stop it also says what approving authorizes and what the token locks, and ends with a summary of the decision (outcome, permissions, criteria and how each is checked, what passing proves), the limits in effect and the exact approve command ([sample](workflow.md#conversation-and-approval)). |
| `--approve-goal 'r3:<hash>'` | Approve the exact displayed revision. |
| `--edit-goal body.json` | Load a full contract body as a new draft revision. Refused at an operational pause unless it offers feedback (an exhausted plan-review budget, a validation-only stop). |
| `--goal-file PATH` | Load a plan file (`autocode plan --export`, reviewed and edited in a pull request) into a saved run as its next draft revision, as `--edit-goal` loads a body; the plan then goes through ordinary approval (in joint planning the Plan Reviewer reviews it first). Refused with the run left as it was when the file cannot be read, its text no longer matches its front matter, it still holds text the export masked as credential-shaped, its runner records are not the run's, or a planner's draft of it would be sent back: a literal the request states that the plan drops (with the message the planner gets), a Go test the request names that no criterion proves by that name, `git status` in a check, or a command the shell cannot parse. The approval stop then names the file and its goal hash and says whether the plan to approve is still the plan loaded from it, and the completed run's evidence report (and the PR body built from it) cites the file, its goal hash and the approved plan's. Refused at an operational pause unless it offers feedback (an exhausted plan-review budget, a validation-only stop). It does not start a new run. See [Reviewing a plan in a pull request](workflow.md#reviewing-a-plan-in-a-pull-request). |
| `--approve-review C1 --review-token '…'` | Record a human-review decision for criterion `C1`. |
| `--as NAME` | With `--approve-goal`, `--approve-review`, `--accept-completion`, `--edit-goal`, `--goal-file` or a resume (`--resume-paused`, or `autocode resume` at a pause) on a shared machine: the name of the person approving, accepting completion, editing or resuming, saved beside the Git identity as their claim, never verified ([Who approved](execution.md#who-approved)). With a resume it is saved only when the resume releases a pause; a resume that finds none, or that the pause keeps, ignores it. `--status`, the completion summary and, for a build, bug-fix or design run, the completed run's evidence report (and so the `autocode-issue pr` body) list it with the rest of the record. A project's `require-approver` checks the Git identity, never this name ([Required approver](execution.md#required-approver)). |
| `--reject-review C1 --review-token '…' --review-note TEXT` | Reject the current reviewed artifact for criterion `C1`, retaining the nonempty note as feedback for a fresh plan and approval. Uses the same artifact freshness checks as approval. |
| `--investigator-model MODEL`, `--investigator-reasoning-effort LEVEL` | Pin the stuck-stage Investigator's model for this run (default, at high: in `kilocode` runs the run's own direct route outside the stuck stage's model family, never a `kilo/` gateway route; otherwise GPT-6 Sol, or GLM 5.3 when the stuck stage runs on Sol). A `provider/model` id runs it through OpenCode. See [Workflow](workflow.md#when-a-stage-stops-making-progress). |
| `--utility-model MODEL`, `--utility-reasoning-effort LEVEL` | New runs only: run the stages that only classify or reformat (today the Job recognizer, `recognize_workflow`, which runs only on a flow without Requirements) on a cheaper model. The route keeps the Plan Reviewer's engine, provider and reasoning effort and replaces only the model. Never used for a stage that writes code or judges evidence. Without it every stage keeps its own route. Refused on a saved run. See [Models](models.md#the-utility-route). |
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
the questions in its report with --follow-up TEXT", and `--feedback`, `--edit-goal`, `--goal-file` or
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
| `--skip-requirements` | New run, for an unattended worker: wherever Requirements would wait for your answers, each question that `--delegate-all` could delegate is answered with its proposed default and recorded as delegated (`source: skip_requirements`). The Planner and Plan Reviewer still run in full; a question with no safe default (cost, quota, permission, external side effect, the requested outcome) still waits for a person, and so does plan approval. A saved run keeps the choice it started with. |
| `--skip-to-build` | New run: `--skip-requirements`, and once the Plan Reviewer has finalized the plan, approve it and go on to the Orchestrator and building in the same invocation. The approval is recorded as yours, with this checkout's provenance, marked `source: skip_to_build` in `approvals` and `approved_contract.approval_source`; a run's `--require-approver` still applies. Needs joint planning. Without this flag AutoCode never approves its own plan. |
| `--plan-review-full` | Keep full-depth plan review for this run: the review allowance stays per planning cycle even for a small clear brief (see [Milestone checkpoints](execution.md)). |
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
| `--milestone-checkpoints` / `--request-milestone-checkpoints` | Enable milestone checkpoints (idle boundary / queued). A `--max-milestone-seconds N` given with either on a saved run is saved without being recorded as yours: AutoResolver may extend it once and a Builder climb is not projected against it, until a later resume sets `--max-milestone-seconds` again. See [Budget ownership](execution.md#budget-ownership-and-human-escalation). |
| `--max-milestone-seconds N` | Milestone active-time budget (default 5400; `0` disables). Checked before each writer launch, and, when you set it, before a climbed Builder attempt (its stronger attempt or a climb on its effort ladder) whose projected time, the last Builder attempt's duration, would take the milestone past `N`: that pause (`PAUSED_MILESTONE_BUDGET`) names the total, and `--resume-paused --max-milestone-seconds M` with `M` at least that total launches the attempt. With `--resume-paused` it also restarts the milestone's counted time, except under a progressive run's slice budget, which it also sets but whose count it does not restart. See [Milestone checkpoints](execution.md#milestone-checkpoints-and-acceptance). |
| `--max-milestone-replans N` | Changed-approach replan limit (default 1). |
| `--max-milestone-stalled-reviews N` | Reviews without progress before replanning (default 3; `0` disables). |
| `--max-findings-per-task N` | Cap open findings bundled into one REWORK task. |
| `--max-idle-seconds` / `--max-tool-seconds` / `--max-stage-seconds` | Watchdog limits (new-run defaults `300` / `1800` / `3600`; `0` disables). On the default idle limit, Requirements, Planner and Plan Reviewer launches run under a 600-second floor and MiMo routes under 900 (see [Timeouts and watchdog](execution.md#timeouts-and-watchdog)); a limit you set is used as given. |
| `--max-seconds N` | Total active provider time for the run (new-run default `43200`, 12 hours; `0` disables). Checked at stage boundaries, and, when you set it, before an attempt on a stronger route (the Builder's stronger attempt or a role's climb on its effort ladder) whose projected time, the role's last attempt's duration, would take the run past `N`: that pause (`PAUSED_TIME_LIMIT`) names the total, and `--resume-paused --max-seconds M` with `M` at least that total launches the attempt. See [Timeouts and watchdog](execution.md#timeouts-and-watchdog). |
| `--max-cost-usd N` | A money cap in USD: the run pauses at the next stage boundary once its spend reaches `N` (no cap by default; `0` removes it), and before any attempt it can price (a job's next attempt, projected from that job's last attempt's tokens at the saved route's rate; the Builder's stronger attempt or a role's climb on its effort ladder, at the stronger route's rate) whose projected cost would take the spend past `N`, so a stage the run can price never crosses the cap. Spend is the provider's reported cost, else the stage's tokens at the dated price table (`autocode models --prices`); a stage on a model without a rate, or one whose provider recorded no token counts, is not counted and the cost line says so. The pause is yours to decide (`PAUSED_COST_LIMIT`): `--resume-paused --max-cost-usd M` with `M` above the spend (and at least the cap a stop before a priced attempt names) continues, and AutoCode never raises the cap itself. See [Money cap](execution.md#money-cap). |
| `--no-progress-limit N` | Unchanged-batch limit (new-run default `3`; `0` disables the cap, never the 3-recovery ceiling). With `--resume-paused`, an N above the retained count, or `0`, acknowledges a `PAUSED_NO_PROGRESS` request, also when N is already saved; it never acknowledges another cause's pause. When the limit caused the pause, its advice names this flag and the retained count, not `--resolver-response`: information alone never acknowledges it. |
| `--max-iterations N` | Optional total iteration ceiling; new runs default to unlimited, and resumes retain their saved limit. |
| `--unlimited-iterations` | Remove only the iteration ceiling; other safety and usage limits remain. |
| `--legacy-iteration-ceiling N` | Compatibility form of the iteration ceiling, used only when `--max-iterations` is absent. |
| `--autoresolver-managed-limits` | Delegate finite CLI safety limits to bounded Resolver recovery; never changes billing or model routes. |
| `--test-command CMD` | The project's test suite command for runner-owned regression proof (default: detected). Correct a saved command with `--resume-paused` at a reconciled pause before the Tester or combined checkpoint; see [Bug fixes](workflow.md#bug-fixes). |
| `--test-report junit:PATH` | Use one fresh JUnit XML file at a workspace-relative path as named test evidence. Requires an explicit `--test-command`; when `--regression-command` is omitted, that suite also runs in the regression proof trees. Every invocation must write the same relative report path; old output is cleared and captured XML is hash-pinned. Saved corrections use the same reconciled pre-validation pause as `--test-command`. No directory/glob or TRX input. See [JUnit proof setup](named-test-proof.md#junit-xml-from-other-test-runners). |
| `--test-root components/api/` | New runs only: save a plain workspace-relative directory for automatic Python suite detection (pytest or unittest). Fixed at launch, not changeable on resume. Changes outside it remain `UNVERIFIED`, including with an explicit test command. This selects proof tests, not a general write boundary; identified generated component tasks also bind their declared plan ownership to this caller root. See [Scoped Python proof](workflow.md#scoped-python-proof). |
| `--package packages/api/` | New runs in a monorepo: the repository directory the task works in. Suite detection reads only that directory, and the detected suite, the derived regression command and the original-test replay run from it (`cd packages/api && SUITE`); an explicit `--test-command` still runs from the repository root as given (write `cd packages/api && …` to run it in the package), and the learned project-memory test command is not applied. Only the package's existing tests are retained as [original test gates](protected-tests.md). In a bug fix the Investigator is told the package and its suite, and a diagnosis that names files outside it is refused as invalid output. The package is the Builder's write scope: a change outside it, in any package run, is a blocking runner finding naming each path, checked before each Tester and Completion Reviewer dispatch and by `--accept-completion` after its own checks (a runner regression proof also leaves it `UNVERIFIED`); what it leaves out, such as an ignored file the plan does not name or the checkout's own nested repositories, is in the linked section. The directory must hold committed files (an `--in-place` run: files Git does not ignore). Fixed at launch; not with `--test-root`. Nothing limits the Builder's writes while it works. See [Monorepo packages](workflow.md#monorepo-packages). |
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
| `--allow-uncontained-tools` | Built-in OpenCode runs only (including a Codex run whose `--investigator-model` is an OpenCode `provider/model`). Without it, a run is refused before any stage when the kernel tool boundary cannot exist here (it needs macOS `sandbox-exec` or Linux bubblewrap, and OpenCode 1.18.33). With it, every stage, the Planner's read-only shell included, runs with OpenCode's own permission checks only, no kernel containment. Accepted on a new run or a resume (including a `PAUSED_TOOL_CONTAINMENT` run); saved with the run and recorded as a user event, so later resumes need not repeat it. The status view shows `tool_containment`. See [Execution](execution.md#native-tool-containment). |
| `--joint-planning` | Add joint Requirements / Plan Reviewer work. |
| `--adaptive-planning` / `--no-adaptive-planning` | New runs plan as deep as the job needs by default (joint planning on the default flow): a Plan Reviewer with no blocking concern approves the draft, a large plan gets a third review call, and feedback on a shown plan goes to the Planner. Requirements always runs first. `--no-adaptive-planning` keeps the fixed sequence; `--adaptive-planning` insists. See [Adaptive planning](adaptive-planning.md). |
| `--compact-bugfix-planning` / `--no-compact-bugfix-planning` | New run, bug fixes only: when the Investigator reproduces the bug with a probe the runner ran, sizes the fix small, names its root cause, invariant and regression cases, touches at most four files and none the project's `sensitive-paths` name, the runner writes the plan draft itself from the diagnosis and the Requirements record instead of the Planner's paid draft call; the Plan Reviewer reviews that draft and you approve the displayed plan as usual, and a blocking concern returns the run to the Planner. Off by default; needs joint planning and adaptive planning. A project with no `sensitive-paths` declaration keeps the full path. The status view's `stage_policy` records the decision and its reasons ([Compact planning for bug fixes](adaptive-planning.md#compact-planning-for-bug-fixes)). |
| `--spec-first` / `--no-spec-first` | Off by default. A new run with joint planning on the default flow stops after Requirements every time it produces a handoff: the question (`needs.kind` `answer`, id `requirements-review-…`) shows the outcome, behaviors, acceptance examples, constraints, what was left out and the assumptions. Answer `plan` to start the Planner, or say what to change and Requirements runs again with your answer. Adaptive planning never skips Requirements on such a run. After plan approval the Tester writes the acceptance tests before the Builder, which may not change them. A saved run keeps its choice. See [Spec-first runs](spec-first.md). |
| `--blind-tests` / `--no-blind-tests` | Off by default. Implies `--spec-first`. The Tester writes acceptance tests before the Builder starts, and the runner archives them in evidence and removes them from the workspace. The Builder gets no prompt note naming them and never sees the test code. Before review and completion, the runner executes them in a candidate scratch tree; any failure becomes a blocking runner finding. See [Blind tests](spec-first.md#blind-tests-blind-tests). |
| `--builder-strong-model MODEL` | New run: the model for the Builder's stronger attempt after its ordinary retry (default `openai/gpt-6-sol`, or `strong_model` under `[builder_retry]` in the provider config; see [Models](models.md#builder-retry-policy)). Refused when it is the checker model or shares its GLM or MiMo family, and when the run's provider lists its models without it (or without the checker model a Tester or Completion Reviewer on its family would move to). |
| `--single-model MODEL` | New run: use one model for every role, including reviewers; explicitly relaxes cross-model verification for single-subscription accounts and implies joint planning.  With a tool that lists its models' reasoning variants (Kilo, OpenCode), a role effort the model does not declare moves to its nearest declared variant before anything launches (said once, recorded as `effort_fits`); an effort you named with `--reasoning-effort` or a role's flag is never moved: the run pauses as `PAUSED_EFFORT_UNSUPPORTED` and names the variants the model declares, which `--answer effort-ROLE=EFFORT` takes (#2057). See [Reasoning effort](models.md#reasoning-effort)|
| `--auto-select` / `--no-auto-select` | New run: choose every role's model from the tool's connected accounts (#1426) - cheapest qualified route per role, checkers independent, the routes still unproven probed at their own reasoning level before the first stage. Per-role model flags, `--single-model` and role-provider flags still win; a saved run keeps the routes it started with; a selection that cannot field an independent checker refuses to start, in account terms. `--no-auto-select` is the explicit opt-out. Preview with `autocode models --selection`. Not yet the default: that flip waits on #1426 step 12's qualification. See [Choosing models automatically](models.md#choosing-models-automatically). |
| `--requirements-model`, `--glm-model`, `--plan-reviewer-model` | Planning-role model overrides (bare GPT names). |
| `--astra-model`, `--terra-model`, `--sol-model`, `--completion-model` | Execution-role model overrides (`provider/model` IDs). |
| `--astra-provider`, `--terra-provider`, `--sol-provider`, `--completion-provider` | Per-role Codex provider override (Responses API); the saved engine stays the same. |
| `--reasoning-effort`, `--astra-reasoning-effort`, `--terra-reasoning-effort`, `--sol-reasoning-effort`, `--completion-reasoning-effort` | Shared or execution-role reasoning effort (`low`, `medium`, `high`, `xhigh`, `max`). |
| `--glm-reasoning-effort`, `--requirements-reasoning-effort`, `--plan-reviewer-reasoning-effort` | Reasoning effort for the Planner, Requirements or Plan Reviewer. |
| `--resolver-model`, `--resolver-reasoning-effort` | Override the saved Resolver model or reasoning effort without changing its engine. OpenCode-engine runs only: a Codex-engine run, or a saved run without joint planning, ignores them, and its Resolver starts from the `astra` route (`--astra-model`). See [Models](models.md#default-models). |
| `--pin-model-role astra\|terra\|sol\|completion` | Keep the selected role model and reasoning effort instead of escalating automatically; repeat for multiple roles. Of these routes only `terra`, `sol` and `completion` have an effort ladder; the Resolver's own route climbs GPT-6 Astra and has no choice here. `astra` pins the route the Resolver's route is copied from, so on a new run it pins the Resolver too; once a run's Resolver route exists (a Resolver stage ran, or `--resolver-model` created it), no flag pins it. |
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
| Approvals (no flag) | `require-approver` |
| Development server (no flag) | `serve` |
| Sensitive paths (no flag) | `sensitive-paths` |

`require-approver` has no flag: one plain Git email address, or a list of
them, allowed to approve the run's plan and reviews and to accept its completion, such as
`require-approver = ["lead@example.com", "deputy@example.com"]`. A run started with it refuses
a plan approval, an artifact acceptance or `--accept-completion` when
`git -C WORKSPACE config --get user.email`, for the run's saved workspace in AutoCode's own
environment, is not listed (compared without regard to case), before anything is approved, and
stays at the same stop; an `--as` name never counts. Without it anyone may approve. No flag
sets or overrides it. It guards against an approval under the wrong configured Git identity,
not against someone who deliberately sets a listed address (Git's environment variables or a
repository-local `user.email` change what is checked): it is not authentication. A replay
never takes it, and `autocode components` child runs read it from their
own worktree, so only a committed file reaches them
([Required approver](execution.md#required-approver)).

`serve` has no flag either: the command that runs the project's development server, with
`{port}` where the port goes. It is a string, or in `.autocode/config.toml` a table that also
names the path that answers once the server is ready (`ready-path`, any answer below 400) and
how long to wait for it (`ready-seconds`, 1 to 600, default 60); `pyproject.toml` takes only
the string. A new run saves it with its settings, so an edit to the file, including one the
Builder makes, never changes the command a run serves with. Every `{port}` is replaced with a
free loopback port; any other text goes to the shell unchanged.

`sensitive-paths` has no flag either: a list of shell-style path patterns over repository-relative paths (`src/billing/**`, `*.lock`, `migrations/`) naming the files a shorter planning path may never touch, saved with a new run. The compact bug-fix planning path (`--compact-bugfix-planning`) takes the full path for a fix whose files match any pattern, and for a project that declares no list at all; an empty list (`sensitive-paths = []`) declares that nothing is sensitive. Matching is by path only, so it cannot see sensitive behaviour in ordinary files; that is why the compact path also stays off unless a person opts the run into it ([Compact planning for bug fixes](adaptive-planning.md#compact-planning-for-bug-fixes)).

```toml
[serve]
command = "npm run dev -- --host 127.0.0.1 --port {port}"
ready-path = "/"
ready-seconds = 90
```

A flag on the command line wins over the file, and over any file key it cannot be
combined with: a typed `--single-model` drops the file's role models, a typed role
model drops its `single-model`, and a typed iteration ceiling drops its
`max-iterations` and `unlimited-iterations`. The file wins over the built-in
default; no environment variable sets these keys. A setting from the file counts
as typed, so a budget the team set is never extended as a runner default. A new
run prints which settings it took from which file. Only a new run reads the file,
including one started from a design handoff with `--ui-run` and no typed task:
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

### Project memory

Completed tasks save shareable learned project facts in `.autocode/project.json`.
Its test command is a default below flags and both explicit project files
(not in a `--package` run, which neither takes nor saves it);
historical human conventions are Requirements context, never new acceptance
criteria or approval. Saved runs retain their original snapshot.
`autocode doctor` shows the record and setting sources. `autocode forget` clears
only the learned file. See [Project memory](project-memory.md) for provenance,
sharing, the versioned schema and the two-run behavior.

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
| `--accept-completion` | Accept completion only after the runner verifies every gate, when the model completion report cannot be produced. Records who accepted it, as an approval does ([Who approved](execution.md#who-approved)), and a project's `require-approver` checks it ([Required approver](execution.md#required-approver)). |
| `--evidence-provenance fake\|live\|unknown` | Disclose the run's model-evidence origin; saved with a new run. |
| `--context-soft-tokens N`, `--rotate-after-input-tokens N` | Context-compaction and checkpointed session-rotation thresholds; `0` disables rotation. |
| `--tool-output-mode raw\|conservative` | Display mode for AutoCode capture/file-read tools; saved across resume. |
| `--headroom off\|on` | Off by default; `on` refuses to run until compatibility has been verified. |
| `--planning-v2` | Opt in to transactional planning artifacts; keeps the existing role models and default planning flow. |

### Figma / UI

| Flag | Meaning |
| --- | --- |
| `--build` | Hand an accepted Figma result to the implementation runner. |
| `--figma-file <url>` | Refine or implement from a specific Figma file. |
| `--ui-run /path` | Import a completed design run. |
| `--figma-review human` | Require a human visual approval during implementation. |
| `--max-plan-reworks`, `--max-reworks` | Review-loop limits (`none`, `0`, or a number). |

## Development server

`autocode serve start` runs the project's `serve` command for a saved run and
keeps it in the foreground. AutoCode picks a free port on 127.0.0.1, puts it
wherever the command says `{port}` and in `PORT`, sets `HOST=127.0.0.1`, and runs
the command in the run's workspace with credentials removed from its
environment, under the same process supervision as a test command. Once the
server answers (any connection, or a status below 400 at `ready-path`) it prints
`Serving http://127.0.0.1:PORT/`. Ctrl-C, closing the terminal or
`autocode serve stop` from another terminal stops its whole process group; if
the `autocode serve start` process dies, its supervisor ends the server. A run
has at most one server; a second `start` is refused while one runs.

`start` follows the run's code. After each code-changing step the run applies
(each new code checkpoint), it stops the server and starts it again, on the same
port when that port can be bound again, so the address stays the same, and
prints `Serving http://127.0.0.1:PORT/ again after a code change`. It reads the
run's `state.json` for this and never writes it. A pause or the run's completion
leaves the server running, so you can try the app while you answer a question,
approve a plan or review the result; it stops only when you stop it.

The server never starts, pauses or decides a stage. Its record is
`dev-server.json` in the run directory, and each start keeps its output and
supervision receipt under `dev-server/`. `autocode serve status` and
`autocode --status` (`view.dev_server`) show `configured`, `status` (`off`,
`starting`, `running`, `stopped`, `exited`, `failed`, or `lost` when the process
that served it is gone), the `url` while it runs, the `command`, its `port`,
times, `exit_code`, `reason`, `log`, `restarts` (how many code changes restarted
it), `code_checkpoint` (the code checkpoint it serves) and `waiting`. A server
that exits, or does not answer within `ready-seconds`, is recorded as `exited` or
`failed` with its last output, which `start` prints; `start` then keeps waiting
(`waiting` is true) and starts it again after the run's next code change, so a
step that fixes a broken app brings the preview back. `autocode serve stop` or
Ctrl-C ends the wait. Once the run has completed no step will change its code,
so such a server ends `start` with exit status 1 instead.
A run started before its project named `serve` has none: name it in
`.autocode/config.toml` and start a new run.

## Subcommand options

These options belong to the command in the first column. A shared spelling can
have a different scope: for example, `ui --run-dir` creates a new artifact
directory, while the main run's `--run-dir` selects a saved run. Every command
also accepts `-h` / `--help`.

| Command | Flags | Meaning |
| --- | --- | --- |
| `autocode connections` | `--json`, `--provider`, `--routes` | List installed/registered backends, check local credentials without inference, delegate sign-in, or configure AutoCode’s direct connection with `setup`. `list --routes` adds the route each role would use, from the connected tool's own model listing (`routes[]` and `routes_lines`, or `routes_stop`), and launches no model; the plain listing names it as `routes_next_command`. See [Connections for clients](task-run.md#connections-for-clients). |
| `autocode modes` | `--json` | List the run modes a client may offer for a new run: each mode's id, label, description and the start flags to pass. Launches nothing. See [Run modes for clients](task-run.md#run-modes-for-clients). |
| `autocode worker` | `--workspace`, `--run-dir`, `--checkpoint`, `--request-id` | `capabilities` reads host admission. `launch` takes literal core arguments after `--`, using the selected workspace and exact run destination, a current checkpoint token (or `-` for a fresh run), and an optional stable request ID. It returns a launch receipt, not completion. `inspect ID` reads that receipt; `launches --workspace PATH [--run-dir PATH]` finds retained launches after a lost reply. These reads never replay a dispatch. See [Durable local workers](task-run.md#durable-local-workers). |
| `autocode chat` | `--workspace`, `--run-dir`, `--mode`, `--brief`, `--line`, `--execution` | Project (the current directory by default); pick up this saved run instead of the project's only run; the run mode a new run starts with; the brief, for one that starts with `-` (`--brief=-TEXT`); the line-by-line prompts instead of the full-screen interface. New-run options follow `--`. `--execution durable_local` opts new chats in this invocation into persistent Linux user services; the default is `owned`. Reattached runs use their saved status mode. |
| `autocode attention` | `--workspace`, `--run-dir`, `--item-id` | Its `ack` command acknowledges the current saved alert using the status/inventory's named argument array. Records only this local OS user's read state; never answers, approves, resumes or launches a model. Repeating the same acknowledged ID reconciles its original receipt. See [attention semantics](task-run.md#observation-decision-and-attention-requirements). |
| `autocode conversation` | `--workspace`, `--run-dir`, `--text`, `--title`, `--request-id`, `--expected-revision`, `--supersedes-request-id`, `--question-id`, `--resolver-token`, `--expected-plan-token`, `--timeout` | Select a conversation/run, queue a human message, explicitly refresh a revision or hand off its reviewed draft; creation provider/model/effort/budget options follow `--`. On `send`, `--supersedes-request-id` queues a correction only for a definitively rejected, unapplied human request, retaining its history; it cannot replace acknowledged input or active delivery. Sending does not advance the run. All commands print JSON. |
| `autocode verify` | `--workspace`, `--base`, `--branch`, `--patch`, `--criteria`, `--criteria-file`, `--out`, `--record`, `--engine`, `--provider`, `--model`, `--reasoning-effort`, `--evidence-provenance`, `--suite-command`, `--check-timeout`, `--max-seconds`, `--max-stage-seconds`, `--json` | Verify an existing change against caller criteria, or read a retained verification and recheck freshness. See [Standalone verification](verify.md). |
| `autocode doctor` | `--workspace`, `--engine`, `--json` | Project and engine to inspect; optionally print checks as JSON. |
| `autocode forget` | `--workspace`, `--json` | Clear the selected project's learned record only; configuration and saved runs remain. |
| `autocode models` | `--workspace`, `--provider`, `--json`, `--prices`, `--selection` | Project/provider model availability and default routes; optionally JSON; `--prices` shows the dated price table and each default route's rate; `--selection` shows what automatic selection would choose and why (#1426). |
| `autocode review-pr` | `--out`, `--post`, `--json` | New owned review directory, optional COMMENT-only posting after full finding/head admission, and JSON findings/evidence; creation/provider options follow `--`. |
| `autocode review` | `--workspace`, `--run-dir`, `--show`, `--json` | Saved task workspace and run (otherwise the workspace's only run); text or JSON evidence without decisions. With neither display flag, ask approve/reject/skip for each pending criterion. |
| `autocode serve` | `--workspace`, `--run-dir`, `--timeout`, `--json` | Saved task workspace and run (otherwise the workspace's only run); with `stop`, seconds to wait for the server to end (default 15); with `status`, one JSON object. |
| `autocode clean-worktrees` | `--workspace`, `--yes`, `--json` | Select the project; remove eligible completed worktrees only with `--yes`; optionally format output as JSON. |
| `autocode bundle` | `--out`, `--with-diff`, `--json` | Where to write the archive; add the run's diff of the workspace source; print the archive path and manifest as JSON. |
| `autocode prune-run` | `--yes`, `--json` | Delete the listed settled evidence bytes (manifests, receipts and hashes retained); without it the command only lists; optionally format output as JSON. |
| `autocode merge` | `--workspace`, `--into`, `--json` | Select the project and target branch for a delivered task worktree; optionally format result as JSON. |
| `autocode checkpoint` | `--workspace`, `--run-dir`, `--compare`, `--restore`, `--working`, `--expected-token`, `--request-id`, `--json` | Compare or restore retained code; `--working` shows what the running Builder has changed so far (display only). Restore requires its exact inspected token; the request ID identifies the action. |
| `autocode replay` | `--into`, `--export` | Create a replay at an exact fresh run directory in an isolated workspace, or export a bounded recording bundle. Exactly one target is required. |
| `autocode replay` | `--recorded-checks`, `--json` | Simulate archived checks with `--into` without current completion or delivery proof; optionally print the result as JSON. Fresh checks are the default. |
| `autocode tasks` | `--workspace`, `--max-parallel`, `--dry-run`, `--json` | Project, concurrent lanes and a read-only manifest preview. |
| `autocode runs` | `--workspace`, `--all`, `--needs`, `--json` | Project whose runs to list; every registered project instead of one; only the runs waiting on a person; rows as JSON. Read-only: each row shows that run's own status and the command it accepts next. |
| `autocode events` | `--workspace`, `--run-dir`, `--follow`, `--timeout` | Project whose saved run to stream, and the run itself (otherwise the workspace's only run); keep printing new events until the run ends the stream; bound a follow to this many seconds. |
| `autocode plan` | `--export`, `--check`, `--render`, `--workspace`, `--run-dir`, `--json` | Exactly one of: write the run's plan to this file, read a plan file and print its goal hash, regenerate a plan file's text from its front matter. With `--export`, the project whose saved run to export and the run itself (otherwise the workspace's only run). `--json` outputs structured JSON payloads. |
| `autocode wait` | `--workspace`, `--run-dir`, `--timeout`, `--json` | Project whose saved run to wait on, and the run itself (otherwise the workspace's only run); stop waiting after this many seconds; the outcome as one JSON object. |
| `autocode components` | `--workspace`, `--engine`, `--provider`, `--joint-planning`, `--reasoning-effort` | Project and model routing for each component. |
| `autocode components` | `--auto-approve` | Apply decisions the operator has already delegated; the flag itself supplies no delegation. |
| `autocode components` | `--options`, `--max-advances`, `--timeout` | Shell-quoted child flags, maximum CLI advances per component (20) and per-component wall-clock budget. |
| `autocode components` | `--integrate`, `--run-local`, `--health-timeout`, `--keep-running`, `--runtime-evidence-provenance` | Integration target, Compose smoke checks, readiness deadline, optional retained services and smoke-evidence origin (`fake`, `live`, `unknown`); see [local integration](task-lanes.md#running-the-combined-system-locally). |
| `autocode demo` | `--directory` | Create a new directory for retained offline demo outputs; defaults to a fresh temporary directory. Existing directories are refused. |
| `autocode ui` | `--workspace`, `--run-dir`, `--figma-file`, `--from-plan-run` | Project, new artifact directory, design file and saved accepted UI plan. |
| `autocode ui` | `--planner-model`, `--astra-model`, `--terra-model`, `--sol-model`, `--astra-reasoning-effort` | Requirements/Planner, Plan Reviewer, Builder and Tester model choices; review reasoning effort. |
| `autocode ui` | `--max-plan-reworks`, `--max-reworks`, `--dry-run`, `--build`, `--no-chat` | Plan/design rework limits, preview, implementation handoff and noninteractive presentation. |
| `autocode compare-baseline` | `--baseline-root`, `--candidate-root`, `--normalize-dependency-prefixes`, `--output` | Source roots, explicit dependency-prefix normalization and comparison-result output; see [baseline comparison](execution.md#baseline-comparison). |
| `autocode visual-capture` | `--config`, `--timeout` | Capture configuration and command deadline (120 seconds). |
| `autocode visual-check` | `--workspace`, `--policy`, `--policy-sha256`, `--output` | Project, source-owned policy and expected hash, and fresh check-output directory. |
| `autocode capture` | `--output`, `--no-compress`, `--mode`, `--known-output-sha256` | Retained output, full display, `raw`/`conservative` display and verified unchanged-output reference; a command follows `--`. |
| `autocode output` | `--store`, `--mode`, `--known-sha256`, `--raw`, `--start-line`, `--end-line` | Retained store, display mode, verified copy reference, raw retrieval and requested line range. |
| `autocode registry` | `--max-depth`, `--directory-budget`, `--workspace`, `--run-dir`, `--json` | Bound imports or select a run; see [Registry API](registry-api.md) for the operations using each option. |
| `autocode intervention` | `--workspace`, `--run-dir`, `--request-id`, `--kind`, `--text`, `--json` | Select a run, identify a feedback/pause/stop request and supply text; optionally JSON. |
| `autocode issue` | `--project`, `--remote`, `--issue-file`, `--note`, `--base`, `--pr-base`, `--open`, `--ready`, `--path`, `--chat`, `--no-chat` | Project and remote; optional offline issue and extra guidance; start/PR bases; explicit push/open and ready-for-review controls; with `plan`, where the plan file goes in the repository (default `plans/issue-N.md`). For `start` and `continue`, `--chat` enters terminal chat when input is needed (default with terminal stdin/stdout); `--no-chat` returns the status and next action instead. See [GitHub issues](issues.md). |
| `autocode arena` | `--arena` | Arena store (default `.autocode/arena`). |
| `autocode arena` | `--repository`, `--base`, `--issue`, `--issue-file`, `--note`, `--oracle`, `--reference`, `--check`, `--split` | Ingest a case with its pinned repository, request, oracle/reference, checks and development/regression/holdout split. |
| `autocode arena` | `--cohort`, `--runner`, `--option`, `--fixture`, `--i-authorize-live-model-spend`, `--approve-benchmark-plans`, `--timeout`, `--oracle-timeout` | Run/report/propose controls: cohort, runner checkout, repeated child options, fake or explicitly authorized live run, plan approval and deadlines. |
| `autocode arena` | `--baseline`, `--candidate` | Cohorts to compare. |
| `autocode arena` | `--report`, `--reviewer` | Annotate a completed attempt from an attribution JSON report; an explicit independent reviewer is required to confirm a diagnosis. |
| `autocode unattended --analyze` | `--analyze`, `--run-dir`, `--workspace`, `--out` | Read-only run analysis; optionally save its evidence report in `--out`. |
| `autocode dashboard` | `--workspace`, `--watch-root`, `--watch-depth`, `--runner`, `--port`, `--provider` | Repeated projects and discovery roots, discovery depth (3), CLI runner, local port (8765) and provider for new runs. |
| `autocode changeset` | `--run-dir`, `--target`, `--workspace`, `--set-token`, `--in`, `--reason`, `--message`, `--json` | The source run (with every other action: any run of the change set), the repository as its `scope.external_parts` names it (with `reopen` and `pr-body`: the run to act on), the registered project to start the linked run in, the `approval.set_token` that `status` showed (`-` reads `AUTOCODE_CHANGESET_TOKEN`), the run whose workspace the integration command runs in, a waiver's reason, a follow-up's text (default: the failed check's output), and JSON output. Arguments after `--` are the linked run's run options with `link`, and the integration command with `check-command`. `order` takes every run's repository name, first to merge first. |
| `autocode bridge` | `--once`, `--project`, `--poll-seconds`, `--json` | With `run`: one poll then exit (for tests and cron-style operation), the persistent checkout to build in (default: `AUTOCODE_BRIDGE_PROJECT` or the daemon's directory) and seconds between polls (default 10, or `AUTOCODE_BRIDGE_POLL_SECONDS`); with `status`, JSON output. See [Channel bridge](channels.md). |

## Terminal chat

`autocode chat --execution durable_local "YOUR TASK"` opts a new run into the
[durable local worker](task-run.md#durable-local-workers) mode on a supported
persistent Linux host. The core checks an existing systemd user manager (254+)
and existing linger policy; it never configures them. Normal quit, Ctrl-D,
terminal closure and SSH disconnect leave that worker running. Reopen with
`autocode chat --run-dir RUN`. Repeated Ctrl-C requests only the current boundary
pause, without escalating to worker termination. This does not change ordinary
owned-worker behavior or migrate saved runs. While the current view advertises
durable detach support, `/open`, `/new`, `/tab` and the tab keys are available
during work. The worker being left continues independently. Returning to an
idle boundary reads status and waits for explicit Continue.

Within this terminal session, each of the most recent 32 run identities keeps
its transcript, unsent draft and cursor, scroll, log filter and panel choices.
Titles are display text; workspace and run paths select the context. Switching
always reads fresh core status before a decision. A changed question or plan
keeps its earlier draft in input history for explicit recovery with Up, rather
than filling the new question automatically. Pending restore confirmations and
queued keys never transfer between runs. If a target disappears or cannot be
read, Enter retries its status and `/runs`, `/open` or `/tab` selects another;
typed text cannot submit a task decision while status is unavailable.

`/new` opens a local request composer; no core run or tab is created until a
brief is submitted. An empty request returns to the original context. Unsent
drafts and view choices live only in this TUI process. After restart, saved
core conversations, registered runs and existing project input history remain;
session drafts, scroll and filters do not.

`autocode chat` is the terminal client of the [task-run interface](task-run.md),
beside the VS Code extension and the browser dashboard. It reads only
`autocode --status`, `autocode runs --json` and `runs --all --json` (your chats, in this project and every registered one) and
`autocode modes list --json` (a new run's mode), shows the conversation the view
carries (`view.chat`), and acts
only through the view's named actions: `next_command`, the `chat.input` message
offer and the `pause` offer. The rules it reads them by are in
`tools/autocode_client_actions.py`, the same ones the extension
(`vscode-extension/src/core/next-command.ts`) and the headless conformance client
follow, so a person gets the same choices in each:

- **Requirements** asks in its own words; you reply in yours, and confirm the
  understanding it summarizes with `y`. Before the plan is prepared the chat shows the
  core's spend confirmation (`review.spend_confirmation`: what planning spends) and
  asks `Prepare plan? [y/N]`; anything but yes sends nothing. A mode that could plan at
  once (`spend_confirmation` `start`; the core lists none since `quick` was removed on
  2026-10-22) would ask `Start it? [y/N]` before the run starts instead, as the VS Code
  extension does.
- **A question** with options takes the option's number or your own answer; Enter
  takes the proposed default when there is one.
- **The plan** is shown from `displayed_plan` and approved with `y` only while its
  token is still the one shown; anything else you type is sent as plan feedback.
- **A stop only you decide** (a pause, a stopped job) says why and continues only
  on your `y`; a Resolver request takes your reply.
- **When the run completes** you can type a follow-up, or press Enter to finish.

```sh
autocode                            # in a terminal: pick up the open run, or ask what to do
autocode chat "Add a --verbose flag to greet.py"
autocode chat                       # the same as plain autocode in a terminal
autocode chat --mode full "Fix the README typo" -- --engine claude
autocode chat --line                # the line-by-line prompts, even in a terminal
```

In a terminal the chat takes the whole screen: a wordmark splash, the conversation
in a scrollable transcript with a colored label for each speaker
(`PgUp`/`PgDn`), the current question or activity above a boxed composer, and a
hint bar. The composer is labeled **New chat** when typing there starts a run and
**Reply** when it answers the run (`Up`/`Down` for history, `Ctrl-W`/`Ctrl-U` to
edit, `Enter` to send, `Ctrl-D` to leave). A paste stays in the composer as one
reply, line breaks included, until you press `Enter`. A plan, a question or a stop is shown
where it is asked, and `Ctrl-C` keeps its meaning: the first asks the run to pause
after the current step, the second interrupts it. When the chat ends, its last words
stay on the terminal: an error, where to pick the run up, or the command a stop
leaves for you to run. `--line` keeps the plain
prompts, and scripts get them automatically when there is no terminal.

In a terminal at least 100 columns wide, a side panel shows the run beside the
conversation once it has a plan: where it is and what it needs from you, the
current step and its last few attempts (the view's `chat.progress`), what the
running step is doing (the commands it runs, the files it edits and the tools it
calls, from the view's `chat.activity`), each requirement and task with its state,
open problems, and a log of how the run moved (a stage starting or finishing, an
attempt finishing, a command run, a file edited, a task done, a requirement
checked). The status row under the conversation names the newest command or edit,
in any width. A command that exited 0 is shown as run, not as passing: only the
requirement checks say what passed. Everything in it comes from the status views
the chat already reads.

While AutoCode works, a live log under the conversation shows what each running
agent is thinking and doing as it happens: its reasoning (`∴`), its messages
(`»`), the commands it runs (`$`), the files it edits (`±`) and the other tools it
calls, each line led by the agent's name (`Builder · task M2`, `Plan Reviewer`).
Parallel Builders each appear under their own name. It comes from the view's
`chat.log`, is refreshed with each status read (every two seconds) and keeps the
last 500 entries; the composer stays below it, so you can keep typing commands.
It shows on its own in a terminal at least 24 rows tall once an agent is working.
Reasoning is shown on one line with its newest words, and tool output is never
shown. Like the activity rows it is display only: a thought is not a report and
a command shown is not a passed check.

Type `/` in the composer for commands, at a question or while AutoCode works. The
menu narrows as you type; `Up`/`Down` choose, `Tab` completes and `Enter` runs the
highlighted one:

| Command | What it does |
| --- | --- |
| `/plan`, `/tasks`, `/log` | Show the requirements, the task list or the run's log in the conversation |
| `/details` | Why the run is where it is, verification and recovery evidence, recorded timings, cost coverage, token usage, actual attempt routes and saved routes for future attempts |
| `/diff` | List the saved code steps with the files each changed; `/diff N` shows the files and the diff changed since step N |
| `/undo N` | At a question: go back to saved step N. It shows what differs and asks first; yes makes a new run on a new branch, your branch and its later work stay as they are, and the chat carries on with the new run, which checks every requirement again |
| `/changeset` | For a run linked with runs in other repositories (`autocode changeset link`): every run of the change set and its state. Once every run waits on a complete plan it shows each plan and asks before approving them together; once every run is complete and an integration check is due it asks before running it. Nothing else is offered: linking, the check command, waivers, reopening and the merge order stay `autocode changeset` commands |
| `/runs` | Your chats in every project, open and finished, grouped by project: this project first, then the others by their latest activity, newest chat first in each. Each shows its request, its state and when it last moved; a chat whose run was removed is left out and counted. Chats of one change set are marked and listed together at the end. Also at the first "What do you want done?" |
| `/open N` | At a question, or during detachable durable work: open chat N from `/runs`, restore its session context and read current status before carrying on. The chat left keeps its worker lifetime |
| `/new` | At a question, or during detachable durable work: compose a new request (also `Ctrl-T`); an empty request returns to the original chat |
| `/tab N` | At a question, or during detachable durable work: switch to tab N (also `Ctrl-←`/`Ctrl-→`); a tab Ctrl-T opened can be left the same way before its request is sent, since nothing runs there. Once a session has two chats, a tab bar under the header lists them with a mark for each: `●` working, `!` needs you, `✓` done |
| `/mode [ID]` | The modes the core lists (`autocode modes list`; only `full` since 2026-10-22), and which one new chats start in; `/mode ID` changes it for new chats only, and a saved mode the core no longer lists gives way to its default |
| `/engine [NAME]` | The engine new chats run on: `opencode`, `codex` or `claude`, set as the VS Code extension's engine picker sets it |
| `/connect [NAME check\|login\|setup\|use]` | The backends `autocode connections list` reports, with their state. `check` runs the connection's own check; `login` and `setup` hand the terminal to the backend's sign-in or AutoCode's setup and bring the chat back afterwards; `use` makes new chats use it. Only an action the connection offers is run |
| `/panel` | Show or hide the side panel |
| `/live [NAME\|thinking\|messages\|tools\|all\|off]` | Show or hide the live agent log. With a word it keeps only one agent (any part of its name, such as `M2` or `reviewer`) or one kind of entry; `all` shows everything again and `off` hides it. Where the terminal is too short for it, the newest entries are printed in the conversation |
| `/attention` or F8 | Inspect current saved questions, decisions and completed turns across registered projects, even before selecting a chat. `a` acknowledges the selected alert, Enter opens its chat, `r` requests a refreshed snapshot and Esc returns to the original draft and cursor. Acknowledgement leaves the task's answer or plan approval pending. Unavailable inventory and unknown read state remain visible. |
| `/pause` | While AutoCode works: ask it to pause after the current step, as the first `Ctrl-C` does |
| `/help` | List the commands |
| `/quit` | At a question: leave the chat; the run stays as it is |

The side panel's Usage section shows the accounted subtotal, missing prices or usage,
active/pending attempts and any configured accounting cap. `/details` separates
provider-reported figures, dated-table estimates and historical comparison estimates;
subscription/API-equivalent estimates are not the subscription invoice. Cache and
reasoning counts are shown as subsets of input and output, never added twice.
Historical attempt models and efforts come from their launch records, while saved
routes describe future attempts. Missing fields in older cores remain unknown.
Displaying this information calls no model and changes no route, cap or workflow.

The header keeps a last-read status visible while you read history; new background
messages do not move the history viewport. If the current tab's read fails, the
header says the previous read is being shown. It separates the viewer's successful
read age (local monotonic time) from the core snapshot's sample time. `/details`
shows the full sample timestamp and opaque checkpoint/workspace/run identity.
Missing or mismatched optional metadata stays unknown. A new poll can refresh
the local receipt while the core checkpoint stays identical; neither timestamp
is a worker-liveness check, evidence acceptance or meaningful work progress.
Failed reads retain the last snapshot under a last-known label; a fresh read
clears that read error. No ETA or completion percentage is inferred.

A Plan path section appears for a bug-fix run that recorded which planning path it took (the
status view's `stage_policy`, [compact planning](adaptive-planning.md#compact-planning-for-bug-fixes)):
`compact: Planner draft omitted` with what covers the omission (the runner's draft, the Plan Reviewer,
your approval, the regression proof), or `full: every planning stage runs` with the first reason the
full path was kept, and where a review fell back to it. `/details` lists every reason, the decision
rows and the hashes the decision rests on. It renders the core's record and offers no action.

The Verification section and `/details` distinguish the core's current delivery
gate from report inspection. A recorded PASS can be unchecked or stale, and a
verified delivery can still have a report not explicitly inspected by that status
request. Criteria show their recorded results, supplied coverage state, pending
human acceptance and evidence references. Retained edits and successful commands
alone do not establish accepted evidence. Recovery details show the core's reason,
retained work and offered actions; `/diff` remains the way to inspect saved changes.
Recorded execution, observed interval wall time, current-attempt duration and
answered-request waiting are shown only where the core supplies those measurements;
missing durations stay unknown, and overlapping categories are not added together.

A command never answers the question that is waiting. To send a reply that starts
with `/`, type `//`. `/diff` and `/undo` take only the `compare` and `restore`
actions the status view names for a saved step ([Saved code
checkpoints](task-run.md#saved-code-checkpoints)); the view offers going back only
at a pause it can restore from, and `/undo` says why when it cannot.

The first screen says what a new chat starts with (`New chats: Full · OpenCode`). A
choice made with `/mode`, `/engine` or `/connect … use` is kept for the project in
`.autocode/chat-settings.json`; options after `--` on the command line take its place
for that chat. While Requirements writes its reply, the reply grows on screen as it is
written (the view's streaming message). The terminal rings its bell when AutoCode has
worked and now needs you, and a chat you opened earlier in the session is checked while
AutoCode works: when it starts to wait for you or finishes, the chat says so.

`autocode` in a project with earlier chats says how many there are. `Up`/`Down` recall
your earlier requests and messages in this project, across chats: they are kept, the
last 100, in the project's `.autocode/chat-history.jsonl` once a run has made that
directory (a bare `y`, `n` or option number is not kept).

The older inline chat (`--chat`, the default when `autocode "TASK"` runs in a
terminal) still works and is being replaced by this client. Scripts that call plain
`autocode` without a terminal are unchanged.

## Unattended callers (agents)

`autocode unattended` (or the internal compatibility wrapper
`autocode-unattended` / `scripts/autocode-unattended` from a checkout) runs AutoCode
for another agent without letting that agent make the operator's decisions. It takes
AutoCode's own arguments but refuses every decision or recovery flag (`--answer`,
`--delegate*`, `--approve-*`, `--resume-paused`, `--retry-*`, `--feedback`, `--follow-up`,
`--accept-completion`, `--close-finding`, `--close-reason`, `--resolver-response`, …, including
abbreviations) and every flag that sets operator policy instead of a person: `--skip-requirements`
(answers Requirements questions by default), `--skip-to-build` (approves the plan),
`--allow-uncontained-tools` (lifts the tool boundary) and `--accept-source-edit`. Setup, routing
and start-time budgets (`--max-seconds`, `--unlimited-iterations`, model flags) stay allowed. The two
classes are `DECISION_FLAGS` and `START_FLAGS` in `tools/autocode_decision_flags.py`; a test fails
when a parser flag is in neither, the command word `resume` (on a paused or blocked run it stands for
`--resume-paused`; a bare relaunch still continues a run that is not paused)
and the `intervention`, `tasks`, `ui`, `registry`, `capture` and `compare-baseline`
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

| Code | Outcome | When | What `--json` prints |
| --- | --- | --- | --- |
| `0` | `completed` | The task completed: `view.done` is true. | The status payload; with `result` when the invocation also acted. |
| `0` | `saved` | A user action (an approval, answer, review decision, feedback and the like) was saved. | The status payload plus `result`, whose `saved` is true. |
| `0` | `read` | A read printed what it read: `--status`, `runs`, or `events` without `--follow`. | That output; `--status` prints the status payload. |
| `2` | `stopped` | The run stopped short of completing: it waits on a person (a question, an approval, a review, a requested pause) or on its environment, a provider or AutoCode. `view.pause_category` says whose action it needs and `view.next_command` what answers it. `wait` also exits 2 for a stopped run, and for endings that are not stops (below). | The status payload; with `result` unless the run printed the payload itself as it stopped. `wait --json` prints its own outcome object instead (below). |
| `2` | `refused` | The input was refused and not saved. A new run refused for a setting may already have created its task worktree and branch (without `--in-place`). | For a refused action on the loaded run, or a setting the run's configuration refuses (such as `--single-model` or `--spec-first` on a saved run, or `--allow-uncontained-tools` where it does not apply; the `--json` option above lists more), the status payload plus `result`, whose `error.code` is `input_rejected`; for a refusal before the run loads, or a setting refused before a new run's first checkpoint, that `result` alone. A refused setting still ends stderr with `autocode: …`. A native visual run saves a refused setting as a `PAUSED_INVALID_OUTPUT` pause instead (`stopped`, `error` null). |
| `2` | `usage` | The arguments did not parse, or a later check of them ended with argparse's error. | Nothing on stdout: argparse's usage text on stderr. |

`autocode wait` only watches a run, and its `outcome` says why the wait ended. It exits 0
for `complete`. It exits 2 for `needs_you` (the run waits on you) and `stopped` (the run
stopped otherwise), and also for three endings that are not stops of the run:
`controller_lost` (the saved status says RUNNING but nothing owns the run), `timeout`
(`--timeout` elapsed first) and `unreadable` (the checkpoint could not be read). It also
exits 2 when it cannot tell which run to wait on (none is saved, or several are and no
`--run-dir` names one), with `autocode wait: …` on stderr and nothing on stdout. Otherwise
`wait --json` prints that outcome object (`outcome`, `status`, `reason` and, when it has
one, `next_command`), never the status payload.

`tests/test_json_contract.py` holds the CLI to this table on a fake run that stops only to
wait on you (`view.pause_category` is `your_decision`): at a requested pause
(`--pause-after-stage`), a question, the plan approval and a human review, then it
completes. At each stop the test runs `--status`, a plain relaunch, the command
`view.next_command` offers (resume, answer, approve the plan, approve the review), that
command with stale tokens, `wait --json`, `runs --json`, `events` and a usage error. It
validates each status payload the main command prints under `--json` or `--status` against
[status-view.schema.json](schemas/status-view.schema.json), and the lone `{"result": …}`
against its `properties.result`. It validates each `events` line against
[run-event.schema.json](schemas/run-event.schema.json), and checks that `wait --json` and
`runs --json`, which print their own shapes, report the same status and `next_command` as
the status view. The fake run never stops for its environment, a provider or AutoCode.
`tests/test_json_output.py` covers those: the iteration and time limits (AutoCode), and a saved
auth or rate-limit pause (`your_environment` / `model_or_provider`) under `--status` and `--json`.

Because 2 has several meanings, scripts should prefer `--json`: `view.done`, `view.needs`
and `view.pause_category` say which outcome it was, and after an action `result.saved` and
`result.error.code` say whether it was saved or refused. The exit codes stay 0 and 2 for
compatibility.

### Subcommand exit codes

Subcommands exit 0 on success, 1 on a domain check or operational failure, and 2 when a run stopped short, input was rejected, or command arguments failed parsing. Under `--json`, results or error codes are reported in structured JSON.

| Subcommand | Exit code 0 | Exit code 1 | Exit code 2 |
| --- | --- | --- | --- |
| `autocode plan` | Plan exported, verified matching front matter (`--check`), or rendered (`--render`). | — | Stale plan text (`stale_plan_text`), missing plan error (`plan_error`), or usage error. |
| `autocode prune-run` | Settled evidence bytes pruned (`--yes`) or listed in dry run. | — | Active stage refusal (`stage_active`), or usage error. |
| `autocode merge` | Worktree branch fast-forward merged into base. | Merge refused (branch not delivered or diverged). | Ambiguous worktree argument, or usage error. |
| `autocode clean-worktrees` | Completed worktrees removed (`--yes`) or listed in dry run. | Worktree removal failed with error. | Usage error. |
| `autocode checkpoint` | Checkpoint inspected (`--compare`) or restored (`--restore`). | — | Checkpoint refused (`checkpoint_refused`), or usage error. |
| `autocode tasks` | Flow completed (`COMPLETE`) or dry-run validated. | — | Flow stopped (`PAUSED`, `WAITING`, `BLOCKED`, `FAILED`), or usage error. |
| `autocode wait` | Watched run completed (`view.done` is true). | — | Watched run stopped (`needs_you`, `stopped`), timeout, lost controller, or usage error. |
| `autocode doctor` | Environment, engines, tools and dependencies are ready. | Check missing or not ready. | Usage error. |
| `autocode models` | Models available and default routes verified. | Missing required default model route. | Usage error. |
| `autocode verify` | Verified criteria passed and evidence produced. | Verification checks failed. | Criteria invalid, run error, or usage error. |

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

`autocode modes list` lists the run modes a client may offer for a new run (`--json` for the version-1 list). See [Run modes for clients](task-run.md#run-modes-for-clients).

`autocode connections list|check|login|setup` manages backend setup; list and check return status without inference, and login delegates to the backend in a terminal. `connections setup` ends by naming the route each role will use, and `connections list --routes --json` returns the same choice as `routes[]` — both read the connected tool's own model listing and launch no model. See [Connections for clients](task-run.md#connections-for-clients).
