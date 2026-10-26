# Working on AutoCode

Rules for anyone changing this repository, human or agent. They exist because
the code grew faster than its structure: much of `tools/` sat in import
cycles, and three naming schemes describe the same roles. The goal right
now is to make the existing workflow dependable and the code easier to change,
not to add surface area. See `RELIABILITY.md` for product priorities.

## Layout

| Path | What it is |
| --- | --- |
| `tools/` | The application, installed as the `autocode_cli` package. Flat for now. |
| `tests/test_*.py` | Unit and integration tests (`unittest`). `tests/__init__.py` puts `tools/` on `sys.path`, so tests import runtime modules by their top-level names (`import autopilot`, `from units import autoreview`). |
| `scenarios/` | End-to-end scenario harness and catalog. Black box: it drives the CLI and never imports `tools/`. |
| `test-scenarios/` | Older fault-injection suite (crash/resume, budgets, dirty workspaces) against a fake Codex. |
| `tools/dashboard/`, `macos-app/` | Browser dashboard and native macOS host: clients of the core. |
| `vscode-extension/` | VS Code thin client. The `autocode` CLI and the status view, nothing else. |
| `docs/` | User documentation. |

**The browser dashboard (`tools/dashboard/`) and the macOS app (`macos-app/`)
are open for features again** (owner decision, 2026-10-16; they were frozen from
2026-09-26). New client work follows #730: the core exposes what a client needs
through the task-run interface (`docs/task-run.md`) first, and the client renders
it and runs the actions the status view names. Their tests stay in the suite
gate. The installed macOS app runs the dashboard straight from this checkout, so
a change that breaks the dashboard breaks the app. The core must not import the
dashboard.

**VS Code client, approved 2026-10-15 for #884, in `vscode-extension/`.**
The owner expanded PR #1425 on 2026-10-16 to include sidebar chat, plan
feedback and follow-ups, reusing Kilo UI components where practical.
The owner also approved Connections setup: backend selection and delegated
sign-in through the core connection interface, with credentials owned by the backend.
Issue #1493 extends that approval to AutoCode-owned direct MiMo, Z.ai, OpenAI and Claude API setup and
a first-party core runtime when no external coding-agent backend is installed.
The editor still launches only the core CLI and renders its named actions.
It is a client of the
[task-run interface](docs/task-run.md) and nothing else:

- it starts `autocode` as an argument array with no shell, and reads only
  `--status`, `registry list --json`, `connections list --json` and
  `modes list --json` (a new chat's run mode, passed unchanged);
- it never imports a module from `tools/`, never reads `state.json`, and never
  parses the CLI's prose to decide what to do;
- it takes only the action the status view names (`view.next_command`, the
  optional `view.chat.input.next_command` message offer, or the optional
  `view.pause.next_command` boundary pause the owner approved on 2026-10-19
  for #1598, and a saved step's `compare`/`restore` or the running Builder's
  display-only `code_checkpoints.working` diff, owner decision 2026-10-19), so it can
  do nothing the headless conformance client (#1012,
  `scenarios/harness/conformance.py`) cannot. Where the view names no action the
  client could take, that is a gap in the CLI or the view: report it, never work
  around it here;
- connection checks and sign-in take only the connection view's named
  `actions[].next_command` argument array (`docs/task-run.md#connections-for-clients`);
- it never launches a model on its own, and binds an approval to the plan token
  it displayed, so it cannot approve a plan nobody read.

**Terminal chat, approved 2026-10-19 (#1622), in `tools/autocode_chat_client.py`.**
The same contract as the VS Code client, with the shared rules in
`tools/autocode_client_actions.py`: it starts `autocode` as an argument array, reads
only `--status`, `runs --json` and `modes list --json`, and takes only the actions
the status view names. Its full-screen form (`autocode_chat_tui.py`) adds a side
panel, a run log and a `/` command menu (owner decision, 2026-10-19), drawn only
from those status views; `/pause` is the view's own pause offer. Its `/diff` and
`/undo` take a saved code step's named `compare` and `restore` actions
(`view.code_checkpoints`, owner decision 2026-10-19), with `restore` confirmed by the
person against the comparison it shows. Its `/runs`, `/open` and `/new` (owner decision
2026-10-19, #1655) list the chats of this project and of every registered project
(`runs --json` and `runs --all --json`, grouped by project), open one to carry on
there, and start a new one, as the VS Code client's history and new chat do; the
composer's input history is kept in the project's `.autocode`. Its `/mode`, `/engine`
and `/connect` (owner decision 2026-10-19) read `modes list --json` and
`connections list --json` and take only a connection's named `actions[].next_command`;
an interactive `login` or `setup` runs on the terminal while the full-screen chat
steps aside. A choice for new chats is kept in the project's
`.autocode/chat-settings.json`. Its live agent log (`/live`, owner decision 2026-10-21) draws
only from the status view's `chat.log`, which, unlike `chat.activity`, carries the running agents'
reasoning and messages as bounded, redacted display text.

**Asynchronous TUI lifecycle direction approved 2026-10-23 (#1879, PR #1985).**
The owner approved opt-in durable execution on supported persistent Linux under
the [task-run lifecycle direction](docs/task-run.md#asynchronous-viewer-direction-1879-implementation-pending).
The core owns launch, ownership and reconciliation; viewers consume its public
capabilities, observations, attention and named actions. In the opt-in mode,
viewer/SSH exit leaves the worker running and Ctrl-C requests only a boundary
pause. This authorizes the dependent implementation within that recorded scope,
including active switching and durable attention, without repeating the same
policy approval. The terminal's F8 or `/attention` consumes only public
status/run-inventory attention and its exact `acknowledge.next_command` array;
acknowledgement records local read state and grants no task authority.
Existing defaults and saved routes, efforts, usage, limits and
approvals remain unchanged. This records direction, not completed runtime/SSH
qualification or authority to administer a host or replay an uncertain call.

**Channel bridge (GitHub first) approved 2026-10-24 (#2035, under roadmap
#2022).** The owner approved a channel-bridge client — flat modules
`tools/autocode_bridge_*.py`, the `autocode bridge run|check|status` subcommand
— that runs on the same persistent Linux host as the core, takes labeled
GitHub issues as requirements, starts `durable_local` runs, relays the human
gates (questions, plan approval, pauses, attention) to the issue, and takes an
allowlisted actor's reply as the exact action the fresh status view names
(`docs/channels.md`). Its rules: control tokens never leave the host; free text
never approves, and an approval binds to the plan token the issue was shown;
person-only stops act only on an explicit allowlisted reply; refusals are
relayed and never auto-retried; it may do nothing the headless conformance
client cannot, and any gap is reported against the CLI or the view. Slack and
Jira adapters follow on the same core, each on the owner's recorded decision.

**Change sets in clients approved 2026-10-25 (#2066, step 7).** The terminal chat, the VS Code
client and the channel bridge may read `autocode changeset status --run-dir RUN --json` for a run
whose status view lists `scope.linked`, and take only the two argument arrays it names: the set
approval (`approval.next_command`), bound to the `set_token` of the plans the person was shown,
and the joint check (`verification.next_command`)
([change sets in clients](docs/task-run.md#change-sets-in-clients)). They show the linked runs, and
group a change set's chats by the `changeset` of each `runs --json` row. Linking, the check
command, waivers, reopening, the merge order and pull-request text stay the person's commands.

A further new client, or growth beyond that contract, needs the owner's decision again.

Historical audit records (`audits/`), `learn/` and two top-level result reports
were archived at tag `archive/pre-restructure-2026-09-26`
(`git show archive/pre-restructure-2026-09-26:audits/...`).

## Architecture rules

1. **Keep modules focused.** `autocode.py`, `autocode_goals.py`,
   `autocode_support.py` and `autopilot.py` already carry broad responsibilities.
   New behavior goes in a module with one purpose at the appropriate layer.
   Review responsibilities and dependency direction. A new CLI flag's action
   goes in the `autocode_action_*` step for its family (`autocode_run_actions.STEPS`),
   not in `handle()`.
2. **Do not create an import cycle.** No module is in one now (`TANGLED` in
   `tests/test_architecture.py` is empty since #695), and that test fails on a
   new one. `autocode_goals` is the contract; the steps that act on it are in
   `autocode_goal_lifecycle`. `autocode_support` no longer holds the completion
   gate (`autocode_completion`) or the stage prompt (`autocode_stage_context`).
   A new module must depend only on lower-level modules, never on `autocode`,
   `autopilot` or the planning unit. Pass what you need as arguments instead.
   The shared helpers (clock, hashing, JSON files, locks, snapshots, schemas)
   are in `autocode_util`, which imports nothing from AutoCode.
3. **Target layering**, from the bottom: utilities (files, hashing, locking,
   schemas) → domain (contract, findings, milestones, completion gate; pure
   functions over state) → runtime (processes, providers) → controller
   (`autopilot`, owns the loop) → interfaces (CLI). Lower layers never import
   higher ones.
4. **Run state is a dict of about 230 named keys.** `tools/autocode_run_state.py`
   is the typed contract that names every key with who writes and who reads it;
   `tests/test_run_state.py` fails when `tools/` touches a key it does not name.
   Prefer an existing key: read the contract before adding one. If you must add
   one, write it in one place and document what reads it. Replacing the dict one
   module at a time is next (#695).
5. **Anything that works across tasks** (architecture, multi-component builds,
   integration, deployment) goes in a new layer that drives task runs through
   the task-run interface (`docs/task-run.md`): `autocode_taskrun.TaskRun` and
   the status view in `autocode_run_view`. It must not import `autocode.py`
   internals or read `state.json`. Coordination between independent task runs
   is that separate layer. Progressive planning inside one existing run is
   not: it uses that run's existing controller and cycle-free policy helpers,
   with `autocode_progressive_state` owning the progressive record, never a second controller,
   cross-run private-state access, broader controller responsibilities or new import
   cycles.
6. **The status view is a contract.** Add fields to `autocode_run_view.view`;
   never rename or remove one.

## Names

The code still uses internal stage names. **Screen and doc names come from one
list:** `tools/autocode_roles.py` (`SCREEN` / `STAGE_JOB` / `role_name`). Do not
add a second table. Name the job being done, not the AI that does it: under
reviewer routing (`glm_first_v1`), `astra_checkpoint` is **Tester**, because that
stage runs the testing job on the Plan Reviewer. Unit names (AutoPlanner,
AutoCode, AutoReview, AutoResolver) stay behind the scenes.

| In code | Job on screen (`autocode_roles`) | Unit |
| --- | --- | --- |
| `recognize_workflow` | Job recognizer: which of the five workflows (`autocode_workflows.py`) | AutoPlanner |
| `requirements_gather`, `astra_discovery`, `glm_revise` | Requirements | AutoPlanner |
| `astra_plan` | Planner | AutoCode build unit |
| `astra_challenge`, `astra_finalize` | Plan Reviewer | AutoPlanner |
| `orchestrator` | Orchestrator: parallel milestone scheduling (no model) | AutoCode build unit |
| `terra` | Builder | AutoCode build unit |
| `sol` | Tester | AutoReview |
| `review_change` | Code Reviewer: the review workflow's only stage (`autocode_review_job.py`) | AutoReview |
| `review_design` | Designer: the design workflow's first stage (`autocode_design_job.py`) | AutoReview |
| `check_design` | Design Reviewer: checks an approved design against the repository (`autocode_design_check_job.py`) | AutoReview |
| `investigate_stuck` | Investigator: why a stage stopped converging (`autocode_stuck_job.py`) | AutoResolver |
| `astra_review`, `astra_checkpoint` | Completion Reviewer (`astra_checkpoint` is Tester under reviewer routing) | AutoReview |
| `astra_resolve` | Resolver | AutoResolver |
| `investigate_bug` | Investigator: the bug-fix workflow's first stage (`autocode_bug_job.py`) | AutoResolver |
| `answer_question` | Analyst: the discuss workflow's only stage (`autocode_discuss_job.py`) | AutoResolver |

Stages that belong to one workflow rather than to the build pipeline (`review_change`,
`investigate_bug`, `review_design`, `answer_question`, `check_design`, `investigate_stuck`) are listed in `tools/autocode_jobs.py`; the runner and Autopilot
consult that table, so a new one is added there, not in `autocode.py` or `autopilot.py`.

CLI model flags follow the code names: `--astra-model`, `--glm-model`,
`--terra-model`, `--sol-model`. Do not introduce a fourth naming scheme.
Per-stage artifact file stems under `iterations/` use readable slugs from
`autocode_artifacts.FILE_SLUGS` (`terra-01.jsonl` is written as
`builder-01.jsonl`); a launch still refuses when a legacy code-name stem holds
artifacts.

## Testing

Run checks from the repository root, with the venv interpreter. **Default to
task-focused verification so issue fixes move quickly.** For a localized fix,
run a regression for the reported behavior and the tests for affected public
interfaces or direct consumers. Add the architecture check when changing
imports or module boundaries. A focused CLI regression may also supply the
fake-provider evidence for that path.

Choose a host that supports the check. macOS can edit source and run static,
pure or mocked checks that avoid supervised provider launches. CLI and scenario
tests that launch a supervised fake provider require the same supported Linux
boundary as real task-stage providers; `--fake` removes model traffic, not host
admission. Run those focused checks in a supported Linux checkout or use the
existing Ubuntu CI evidence for the exact revision. A refusal or an unrun Linux
check stays unverified. See [test host requirements](docs/testing.md#host-requirements-for-tests)
for setup and the distinction between local and Linux evidence.

Do not make the complete changed-test gate, full unit suite or entire fake
catalog prerequisites for a localized fix. Broaden only when a failure or a
demonstrated affected consumer needs it, the change alters a shared contract
across the repository, or the user explicitly requests a repository-wide check.
Explain the concrete reason before starting a broad run. Use existing green CI
from the same runtime revision as evidence rather than duplicating its broad
checks locally.

Run each necessary check once against the final runtime change. Repeat it only
after a relevant behavior change, a failure or a change to its test. Documentation,
formatting and evidence updates do not require rerunning already passing
behavior checks. Once focused checks pass, proceed to the pull-request update;
do not add unrelated verification work or wait for unrelated CI jobs. Report
any pending CI checks accurately.

The commands below are available checks, not a checklist to run for every fix.

```sh
PY=.venv/bin/python   # has psutil; the system python3 does not
$PY -m unittest tests.test_architecture                        # seconds
$PY tools/run_suite.py --changed                               # the tests for what you changed, in parallel
$PY scenarios/run.py run --fake                                # supported Linux; full catalog only when justified
$PY tools/run_suite.py --scenario-harness                      # provider-backed cases require supported Linux
$PY tools/run_suite.py                                         # every test, in parallel; what master's CI runs
```

The fake catalog now includes real Go, `node:test` and Vitest execution and
pinned dependency setup. Allow three hours for the full catalog on a shared
host instead of the old 5.5-minute estimate for 63 scenarios. On a heavily
loaded host, use `--timeout-minutes 45` to expand the harness's per-scenario
wall bound; AutoCode's own budgets and every semantic oracle still apply.
Keep any failed attempts and report the exact commands and measured duration
with the qualification results.

`tools/run_suite.py` discovers `tests/test_*.py` minus the modules listed, with
reasons, in `tests/suite_exclusions.json`. Most of
the full suite's time is spent waiting on subprocesses and timeouts, not
computing, so it runs one test module per CPU at a time (`--jobs 1` for one
process). `--changed` picks the tests for the files changed since
`origin/master`: a changed test, the tests named after a changed `tools/`
module, and the tests that import it directly (the script's docstring has the
rules). It leaves out the slow end-to-end modules in `tests/suite_slow.json`
(over 10 s each in CI, about three quarters of the suite's time) unless the
module itself changed; `--include-slow` runs them too. Before committing a
change to `tools/`, pass the task-focused tests and fake-provider regression
that exercise the changed behavior. Use `--changed` when its broader selection
is justified by the actual impact; do not run every fake scenario by default.

A pull request's CI runs `--changed` against the event's pinned base commit.
Explicit resource and CLI-consumer mappings in `tools/ci_test_selection.py`
cover dependencies the import selector cannot see; update those mappings when
adding a dynamically loaded resource or a public CLI consumer. Required mapped
consumers run even when listed as slow. `tools/ci_scope.py` selects relevant
browser, container, platform and packaging jobs, with a stable `ci-required`
check that rejects a failed or unexpectedly skipped selected job. PR checks use
fake providers; strict host admission and cleanup still apply. A push to master
runs every test and every existing master job, including the slow ones.
A break only a slow module catches can
therefore first show up on master: fix it forward straight away. Making a slow test fast (in-process instead of a CLI
subprocess, a copied Git fixture instead of `git init`, a fake clock instead
of a wait) and taking it off the slow list is progress. Run the full suite yourself only when you change something many
modules share.

Where a new test belongs:

- **Behavior a user would notice** (a run plans, builds, pauses, completes,
  refuses to complete): a scenario in `scenarios/`, or a CLI-level test that
  drives `autocode` through the task-run interface. These survive refactors.
- **Pure logic** (a function from inputs to outputs, such as the status view or
  requirement tracing): a small unit test beside that module's other tests.
- **Not** a test that asserts internal state-dict keys after a sequence of
  private calls. It breaks on every refactor without catching more bugs. When
  touching such a test, prefer rewriting it against the CLI or a public function.
- **Never** a test that re-runs another test, waits in real time, or asserts
  something that cannot fail. Fake the clock or the provider instead of waiting.

Tests live in `tests/`; do not start a second test directory. For new
end-to-end coverage add a catalog entry with an oracle, a reference solution
and a broken variant (see `scenarios/README.md`). Live-model runs need `--i-authorize-live-model-spend` and are never part of a
routine test run. To run scenarios live on Claude models from a Claude Code cloud session
(setup, batches, restarts, reading results), follow `examples/claude-provider/CLOUD-SESSION.md`.

## Fake-first verification and optional live runs

**Focused deterministic tests are the default PR evidence. A live-model run is
not a blanket requirement for opening a pull request or marking it ready.**
For localized fixes to startup, state transitions, routing, retries, receipts,
crash recovery or budgets, use a fake provider or fault injection through the
public interface. The regression must expose the reported defect and verify
the corrected behavior; a fake returning PASS alone proves nothing.

Use representative saved provider responses when parsing or model-answer
handling matters. Turn real-provider incidents into small deterministic
fixtures so future fixes do not need to spend tokens to reproduce them. Do not
require creating new replay infrastructure for an unrelated fix.

Live runs check real-provider integration and model behavior that fixtures
cannot establish. Run one only when the user explicitly requests it or a
concrete risk needs real-provider evidence, such as changed transport,
authentication, tool invocation or output protocol. Explain that risk and why
focused fakes or existing conformance evidence cannot cover it before starting.
Documentation, CI and test-only changes do not require live runs.

- **Keep it bounded and relevant.** Choose one scenario or task that reaches
  the changed behavior, with explicit time and spend limits. A full catalog,
  repeated model variants or reliability sweep needs its own demonstrated
  reason or explicit user request. Reuse valid evidence from the same runtime
  change; do not repeat a passing run for documentation or formatting updates.
- **Keep the requested routing.** Preserve the user's provider and model
  choices for each role. A substitution for one role does not change others.
  For a provider-specific risk, exercise that provider rather than switching
  unrelated roles. See `docs/providers.md` and
  `scenarios/harness/profiles.py` for supported configurations.
- **Authorization.** A justified live check within this policy is authorized
  by this file; the agent passes `--i-authorize-live-model-spend` without asking
  for approval on every run. This does not authorize unrelated paid sweeps.
- **Classify failures before retrying.** Separate introduced defects from
  quota, setup and model variance. Do not automatically launch a paid baseline
  run or try another provider. Use a baseline only when a concrete comparison
  is needed to decide whether the change caused the failure. Preserve failed
  attempts and report what was reached and what remains unverified.
- **Report the evidence.** The PR body names the focused checks and their
  results. If live models were used, include the command, provider, model for
  each reached role, verdict, duration, run-directory name and a stage, event
  or log line showing the changed code ran. Never paste credentials or whole
  logs. If live testing was unnecessary, state what deterministic evidence
  covers the changed behavior.

An unavailable live provider does not by itself force an otherwise verified PR
into draft. If the user specifically requires a live result and it remains
incomplete, report that limitation and keep any required readiness condition
explicit; do not claim fake tests establish real-provider compatibility.

## Taking an issue

Several agents (Claude Code and Codex sessions) work on this repository at once,
all as the same GitHub account, so an assignee cannot show who is working on
what. Two sessions fixing the same issue in parallel has happened repeatedly.
Mark the work instead:

- **Before starting**, check the issue. If it has the `in progress` label, or
  `gh pr list --state all --search <N>` shows a pull request for it, someone
  else has it: pick another issue or ask the user.
- **When you take it**, before your first change, add the `in progress` label
  and post one comment: who is working on it (the agent or session), the branch,
  and the next step.
- **Keep that comment current** when the plan changes: handed off, blocked,
  waiting on a live run, branch renamed.
- **When you stop**, remove the label and say why in a comment. When you open
  the pull request, the pull request tracks the work from then on. When you
  drop the work, it is free for someone else.

## Hygiene

- Do not commit run output, logs, `.patch` files or evidence bundles. Scenario
  results go to `.scenario-runs/` (ignored); AutoCode's own state goes to
  `.autocode/` (ignored).
- Write findings worth keeping as a short Markdown note in `docs/bugs/` or in
  the relevant doc, not as a new top-level report file. Cite a run by its
  `autocode bundle` manifest hash rather than pasting its logs.
- Other AutoCode runs, including self-builds, may be running from this
  checkout. Never edit or delete `.autocode/` contents you did not create.
