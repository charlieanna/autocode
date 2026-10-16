# Changelog

All notable changes to AutoCode are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each release lists the pull requests that changed user-visible behaviour.
PR template asks for an entry here; a change without one is incomplete.

## [Unreleased]

### Added

- `tests/test_json_output.py` holds `--status` and `--json` to the status-view schema and exit-code table on a
  saved auth pause (`your_environment`) and rate-limit pause (`model_or_provider`), matching the AutoCode
  iteration/time-limit coverage already there. The fake person-pause contract test still does not reach those
  stops; `--quiet` remains open (#714).

- The capacity-recovery scenario can inject its first failure before a live report-file provider retry; its oracle
  binds recovery and later clarification calls to public accounting, completing #1071 qualification.

- Supervision receipts and `autocode --status --json` now report wall-clock and monotonic elapsed time, the
  admitted stage limit, and excess wall time, identifying a likely host or VM suspension only when the clock
  gap exceeds a conservative tolerance. Stage, idle and tool limits still count monotonic awake time only (#1161).
- macOS contributors can run one focused fake provider-backed unittest selector or one named fake scenario in a pinned
  Ubuntu Docker image. The wrapper preserves Linux session and bubblewrap admission, mounts no home or credentials,
  refuses live-provider flags and never defaults to a suite or catalog run (#1274).
- `autocode plan --export PATH` writes a saved run's current plan as a Markdown file to review in a pull request:
  a front matter holding the contract body exactly, its goal hash (the SHA-256 of the body, the same in any run)
  and the revision it came from, then the plan as text generated from it; never the approval token, and
  credential-shaped text is masked as in `autocode bundle`.
  `autocode plan --check PATH` prints a plan file's goal hash and exits 2 when its text no longer matches its
  front matter; `autocode plan --render PATH` regenerates the text after the front matter was edited. Starting a
  run from such a file is not part of this change (#720).

- Approvals record who gave them (#722, first part): each plan approval (`--approve-goal`, or yes in the
  terminal conversation) and each artifact acceptance (`--approve-review`, `autocode review`, or y at the review
  prompt) saves the workspace's Git `user.name` and `user.email`, the host name and the command's option names
  (never their values, so no token). `--as NAME` adds the approver's own name on a shared machine, recorded as a
  claim. `--status` lists them under the additive `approvals` field and the completion summary lists them before
  its cost line. Nothing is verified and recording grants no approval authority. The evidence report and pull
  request body (#692) and a required approver followed as the third and fourth parts below.
- Goal edits and resumes record who made them (#722, second part): `--edit-goal` saves the workspace's Git
  `user.name` and `user.email`, the host name and the command's option names on the draft revision it
  installs, and an explicit resume (`--resume-paused` or `autocode resume`) that takes the run out of its
  pause saves the same, with the pause it released, once the run passes the checks that could keep it there
  and its next stage launches. A resume the pause's own rules refuse records nothing, such as one made while a
  `pause-requested` file is still in place. `--as NAME` now also goes with `--edit-goal` and a resume (with a
  resume it is saved only when the resume releases a pause). `--status` lists them in `approvals` (kinds `edit`
  and `resume`; each row gains `pause_status`) and the completion summary names them. Nothing is verified and
  no authority changes. The evidence report and pull request body (#692) and a required approver followed as the
  third and fourth parts below.
- The evidence report and the pull request body list who approved (#722, third part): a completed build,
  bug-fix or design run's `evidence.json` gains an additive `approvals` list (the status view's rows: kind,
  time, actor, plan token, criterion or released pause, and the recorded Git identity, host, command and `--as`
  claim), and `evidence.md` shows it as an Approvals table after the intended outcome, so the
  `autocode-issue pr` body, which wraps that file exactly, carries it too. A record saved without provenance
  says it was not recorded, a workflow-policy approval says no person made it, and "Not verified" adds that
  approver identity is recorded as given. Such a run with no record says so, in the report and in the
  completion summary (`Approvals: none recorded`). Review and discuss runs never ask for an approval, so their
  reports have no approvals field or section, not even a resume, and their completion summary has no such
  line; a review posted with `autocode review-pr --post` therefore carries no approval record or resumer's Git
  identity. Reports written before this change keep no field and render unchanged, so they still authenticate;
  such a report gains the section only when AutoCode saves the completed run again. The evidence report schema
  stays version 1. The required approver is the fourth part below.
- A project can name who may approve (#722, fourth part): `require-approver` in `.autocode/config.toml` (or
  `[tool.autocode]`) lists one or more plain Git email addresses (one `@`, no spaces, angle brackets, quotes,
  parentheses, brackets, commas, semicolons, colons or backslashes; anything else stops a new run). A run started
  with it refuses a plan approval (`--approve-goal`, or yes in the terminal conversation) or an artifact acceptance
  (`--approve-review`, `autocode review`, TaskRun, or y at the review prompt) when `git -C WORKSPACE config --get
  user.email`, for the run's saved workspace in AutoCode's own environment, is not listed, compared without regard
  to case, before anything is approved: exit 2 (the conversation stops at the same question) with a message naming
  the required and the actual identity, and the run stays at the same stop. An `--as` name never counts. Goal
  edits, resumes, workflow-policy approvals and `--accept-completion` are not checked. The run saves the list when
  it starts (`settings.require_approver`), so a changed file never alters a run in progress; the key has no flag,
  and `autocode doctor` shows it with its source. Without the setting nothing changes. It guards against an
  approval under the wrong configured Git identity, not against someone who deliberately sets a listed address
  (Git's environment variables or a repository-local `user.email` change what is checked): it is not
  authentication. A replay (`autocode replay`, `--replay-recording`) never takes it, `autocode program` and
  `autocode components` child runs read it from their own worktree (so only a committed file reaches them), and
  `autocode program approve` is not checked. `autocode --ui-run HANDOFF` without a typed task now reads the project
  settings, all of them, as every other new run does (it read none before), so a project `engine` other than
  `codex` refuses it, as it already did with a typed task.
- AutoCode runs as a step inside the coding agent you already use: a Claude Code skill
  (`examples/agent-step`) and an MCP server (`python -m autocode_cli.autocode_mcp`) start a run, report
  each stop with its pause category and next command, pass on only your own plan approval or answer, and
  return the evidence report. Both go through the task-run interface, use the standard library only, and
  can start a review run to verify the agent's own change (`docs/agent-step.md`) (#693).
- `--utility-model MODEL` (and `--utility-reasoning-effort`) sends the stages that only classify or reformat to a
  cheaper model, on the Plan Reviewer's engine and provider. The stage list is `UTILITY_STAGES` in
  `autocode_roles.py`, today only the Job recognizer; a stage that writes code or judges evidence is never on it.
  New runs only; without the flag nothing changes (#710).
- Native Windows groundwork (#698): `autocode_util` no longer imports `fcntl`; its workspace and run locks use a
  portable `lock_file` (`msvcrt` on Windows) and `atomic_json` no longer fails there syncing a directory. A
  Windows CI job reports `autocode doctor` and the fast suite without gating, and [Native Windows](docs/windows.md)
  records the audit and what remains. Use WSL until it is green.
- Completed runs can remember shareable project test defaults and genuine human conventions for future Requirements, with explicit-setting precedence, doctor display and `autocode forget`; every task keeps its own criteria and approvals (#719).
- `tools/reliability_import.py` fills a reliability campaign ledger's rows from the records the harnesses
  already write: a scenario run's `result.json` or an Arena attempt in its SQLite ledger, each named for its
  scheduled attempt. Native verdicts and oracle checks are copied, never rescored; a commit, cost or spend the
  record does not establish stays null, and a recorded attempt is never replaced. An Arena attempt whose
  forwarded runner options differ from the declared profile and iteration cap is reported as a deviation, and
  its roles are read with AutoCode's own flag precedence. An Arena case must still match its recorded digest
  and the attempt's, and its oracle must be the declared task's `oracle.py` at the declared revision, read
  from local Git objects. A skipped hybrid scenario run is fixture evidence, and an ungraded scenario attempt
  is refused with how to record it by hand. `tools/reliability_ledger.py`
  now compares each attempt's spent budget with its task's declared limits: an attempt over a limit blocks a
  comparison and its `PASS` is not a verified delivery, and a declaration whose task lacks a `limits` object of
  finite nonnegative numbers is refused. A ledger row may record a null `source_revision` when
  the commit that ran is unknown; it is reported as a deviation (#634).
- Review runs report mutations missed by passing tests in an explicit Python patch, separately from the reviewer's failing finding tests, without changing review verdicts or completion policy (#699).
- `tools/reliability_ledger.py` checks a reliability campaign's attempt ledger against its pinned declaration
  (`arena/campaign.json`) and reports the portfolio: every scheduled attempt exactly once, live and fixture rows
  apart, verified delivery, false completion, stops, human decisions and repairs, cost with failed attempts
  included and time, with unknown cost or time never shown as zero. `template` starts a ledger with every
  attempt `DEFERRED`. It reads files only and runs nothing (#634).
- Claude Code is a built-in provider: `--engine claude` (or `--provider claude`, or `engine = "claude"` in the
  project settings) runs every role through `claude -p` with the Claude Code login. Sonnet drafts requirements
  and plans and tests, Opus reviews plans, resolves and completes, Haiku builds; every stage starts a fresh
  session with a restricted tool set and no MCP servers or skills; reports arrive through Claude's structured
  output; Bash results keep their real exit codes, and a command whose exit code is unknown is never evidence.
  A length stop pauses `PAUSED_OUTPUT_CAP` (a truncated Tester or Completion report is repaired automatically),
  a subscription usage limit pauses `PAUSED_BUDGET`, and the reported cost is per call (#717).
- `autocode doctor --engine claude` checks that Claude Code is installed and logged in to a Claude subscription in
  the environment AutoCode gives its agents, and shows its conformance row; API-key, Bedrock and Vertex billing
  are refused there and before every stage, whether they come from the agents' environment or the project's
  `.claude/settings.json` (#717).
- `autocode doctor` and the status view name the containment backend (#709). For a run that launches built-in
  OpenCode stages, doctor's new `containment` line (and `containment` in `--json`) names the kernel boundary they
  get on this machine, macOS Seatbelt or Linux bubblewrap with its version, by the check run setup makes; it
  warns, naming `--allow-uncontained-tools`, when that run would be refused. The `engine:opencode` line no longer
  repeats the 1.18.33 qualification for OpenCode 2.x. The status view gains `tool_containment_backend`: the
  backend and version the latest contained launch ran in, read from that launch's own stage record.
- Strict tool containment on Linux (#709): built-in OpenCode stages other than planning now run their shell
  inside bubblewrap (`/usr/bin/bwrap`) with the authority the macOS Seatbelt profile gives: no home, `/tmp` or
  network, the workspace read-only (writable for the Builder) with runner state and configuration read-only over
  it, and only the stage's scratch writable. A Linux run with a working bubblewrap and OpenCode 1.18.33 is no
  longer refused at setup; without them it still is, unless `--allow-uncontained-tools`. Each launch records the
  `backend` and `backend_version`. Toolchain discovery reads ELF dependencies on Linux, and a
  `linux-tool-containment` CI job runs the kernel controls. Differences from Seatbelt are listed in
  [Linux bubblewrap boundary](docs/execution.md#linux-bubblewrap-boundary).
- The provider conformance suite covers Claude Code (#717, #706).
- `--package PATH` starts a run on one package of a monorepo (#711, first part): the suite is detected inside
  that directory and runs from it (`cd PATH && SUITE`), only its tests are protected, and a change outside it
  leaves the regression proof UNVERIFIED, which keeps a run that needs that proof from completing. The status
  view gains `package`. An explicit `--test-command` still runs from the repository root. The package is fixed
  at launch. Builder write scope and the two-package scenario are still to come.
- The `claude-tiers` scenario profile is in `scenarios/harness/profiles.py` (#717, #694).
- The status view gains `provider`: the provider the run's OpenCode-engine roles launch through (#717).
- `autocode completion` completes the options of `autocode program`'s commands (`autocode program approve --`), and in fish `autocode program plan` no longer offers the options of the top-level `plan` command. `docs/schemas/status-view.schema.json` describes the status payload `--status` and `--json` print, and the exit codes in `docs/cli.md` are a table. `tests/test_json_contract.py` enforces the table on a fake run that stops only to wait on you: at a requested pause, a question, the plan approval and a human review, then completion. At each stop it runs the stop's offered command, with current and with stale tokens, and the other commands a script reads the run with. It validates each status payload the main command prints under `--json` or `--status` against the schema (a lone `{"result": …}` against its `properties.result`) and each `events` line against `run-event.schema.json`, and checks that `wait --json` and `runs --json` agree with the status view. The test does not reach stops for the environment, a provider or AutoCode (#714).
- Trusted `--test-report junit:relative/file.xml` settings supply named proof from fresh, hash-pinned JUnit XML, with retained base-suite checks and a real Maven/JUnit catalog fixture (#696).

- Searchable documentation site built from the existing guides, with five task-oriented entry points, canonical stage names, strict link checks, and latest/release-tag versions (#728).
- `autocode --goal-file PATH` loads a plan file that `autocode plan --export` wrote, reviewed and edited in a
  pull request, into a saved run as its next draft revision (#720). The file is refused, with the run left as
  it was, when its text no longer matches its front matter, its runner records are not the run's, or a
  planner's draft of the same plan would be sent back: a literal of the request it drops (with the planner's
  message), a Go test the request names that no criterion proves, `git status` in a check, or a command the
  shell cannot parse. The plan then goes through ordinary approval; the approval stop names the file and its
  goal hash and says whether the plan to approve is still the plan loaded from it. Starting a new run from a
  plan file is not part of this change.
- A focused fake-provider regression kills the controller after a runner finding is durably saved, then verifies restart retains the finding and prior attempts until corrected source passes fresh lifecycle proof (#451).
- `--spec-first` (opt-in, new runs): the run stops after Requirements on every handoff and shows what it
  understood; `plan` starts the Planner, anything else runs Requirements again with that answer. The scenario
  harness records the confirmation as an approval, not a question (#1200).
- On a `--spec-first` run the Tester writes the approved acceptance tests before the Builder; the runner
  requires each `test:` case to fail and each `guard:` case to pass on today's code, then keeps the files fixed
  for the Builder. A case that already works stops the run (`PAUSED_ACCEPTANCE_TESTS`) (#1200).
- Spec-first runs: a `test:` case the runner sees already pass goes back to plan review as a `guard:`;
  bug fixes get their reproduction tests from the Tester; milestones and parallel batches get their own
  Tester passes and a batch that edits the Tester's files is not integrated; a Builder that edits them is
  sent straight back with the files restored (#1200).
- A money cap: `--max-cost-usd N` (or `max-cost-usd` in the project settings) pauses a run at the
  next stage boundary once its spend reaches `N` USD, with `PAUSED_COST_LIMIT` in the "your
  decision" category. Only you continue it, with `--resume-paused --max-cost-usd M` above the
  spend (or `0`); AutoCode never raises it, and a restored checkpoint keeps counting. Spend is the
  provider's reported cost, else the stage's tokens at the new dated price table
  `tools/autocode_prices.json`, which `autocode models --prices` prints with "no rate" for a model
  without a row. The status view gains a `cost` field (`estimated_usd`, `reported_usd`,
  `spent_usd`, `cap_usd`, `unpriced_stages`, ...), and `--status` and every completion summary print
  one `Cost: ...` line (#721).

- Add a deterministic capacity-recovery scenario through the real CLI: one Requirements overload, a fresh retry on the same model, retained failed-attempt evidence, and independent completion checks with a broken control (#1071).
- Core Requirements conversations can be started, reattached, queued, refreshed and handed off through the CLI and TaskRun on one durable unapproved run. Accepted drafts and independent review are visible without the dashboard (#1011).
- Named proof repeats its proving tests three times (and a `test:` test three times
  on the original code); a test whose runs disagree is `unstable`, does not count
  as proof, and stops completion with the next step (#700). The same proof reports
  which changed product hunks no proving test executed, using the project's own
  coverage tool when it has one (`evidence.diff_coverage`, report only) (#701).
- Once a Python named proof passes (its verdict final), the runner mutates the
  change's product lines (negate a condition, swap a `<`/`>` bound or shift its
  integer literal by one, drop a branch, return a constant; at most 6 per file, one
  900-second budget for the unmutated run and every mutant) and runs the proving
  tests against each. A mutant they still pass is reported beside its test case
  (keyed like `case_tests`) in `evidence.mutation_check`, the evidence report and
  the Tester's and Completion Reviewer's handoff. Report only: it never changes the
  verdict or completion, and a proof that fails runs no mutant. The review
  workflow runs no named proof and is not covered yet (#699).
- `autocode completion bash|zsh|fish` prints a shell completion script read from the
  CLI's parser: commands, `program`'s commands, and the main command's options with
  their choices and file names (#714).
- `autocode completion` also completes each subcommand from the parser it reads: its
  options and their choices (`autocode doctor --engine <TAB>`), its own commands and their
  options (`autocode output read --<TAB>`), and its argument's choices
  (`autocode completion <TAB>`). Every subcommand module except the dashboard,
  `unattended` and `program` exposes that parser as `parser()` (#714).
- Replay saved task runs offline through the public task-run interface, export
  bounded recordings, and refuse missing source or dispatch divergence. Fresh
  checks run normally; explicit recorded-check simulation cannot establish
  current completion (#708).
- Declare a planned, pinned 26-attempt reliability campaign with original task limits and complete outcome accounting; no campaign results are claimed (Refs #634).

- `autocode demo` shows planning, demo approvals, building, refused evidence and
  completion with the bundled offline provider; no account or model spend (#689).

- Add `autocode review-pr URL` (also `autocode-issue review-pr`) to review an exact fetched PR head through public TaskRun, with authenticated findings/evidence and optional fully anchored GitHub COMMENT reviews.
- Add a public TaskRun regression that executes the stale-source repair action advertised by the status view, preserving the approved contract and refusing a plain resume until the edit is explicitly accepted (#1010).

- Verify an externally produced working-tree change, branch or patch with
  `autocode verify`, using caller criteria, independent review and fresh runner
  evidence without planning or building (#690).

- Record first question, approval-ready plan and Builder dispatch times in the
  task status and canonical evidence report; generated reliability tables retain
  the dated samples and recorded latency medians (#727).

- Task, program and component runs now publish a shared evidence report with
  explicit model provenance; TaskRun and issue pull requests use the same
  canonical report (#692).
- Preserve evidence-backed failure investigations beside completed Arena and scenario attempts, keeping suspicion separate from explicitly reviewed diagnoses and pinned regression contrast. Original outcomes and holdout filtering remain intact (#635).
- **KiloCode Alibaba Token Plan route**: the bundled `kilocode` provider now declares an
  `[[auth.routes]]` entry for `alibaba-token-plan/`, so a run using those models verifies the login
  is present in `api` mode before it launches, as it already does for `openai/`. That route serves
  Qwen (`qwen3.8-max`, `qwen3.7-plus`, `qwen3.6-flash`) on the operator's own plan, and DeepSeek,
  GLM, Kimi and MiniMax on the same login, so a verifier can come from a different vendor than the
  producer it checks.
- Version stage prompts and shared instruction fragments as packaged Markdown resources.
  New runs and model attempts record their prompt-set hash in status and evidence;
  prompt edits require scoped live-run evidence or an exemption in the pull request (#713).

- Compact `autocode --help` with complete `--help-all`, unified issue/arena/
  dashboard/unattended commands, and a guard requiring public-flag documentation.
  Compatibility console scripts remain internal aliases. Paused runs show one
  of four cause categories and the current scoped recovery command, preserving
  saved state names and all approval gates (#691).

- `autocode runs [--all] [--needs] [--json]` lists every run of one project, or of
  every registered project, in one read-only table: needs-first, then working,
  settled, then runs whose checkpoint cannot be read (shown with the reason). Each
  row carries what that run's own status view says it needs and the command that
  answers it; where only the run itself can tell an uncertain attempt from one whose
  retained report only needs repair, the row points at its status instead of
  offering a command that could set accepted work aside. Nothing it prints starts,
  resumes or changes a run.

### Changed

- Under a money cap (`--max-cost-usd`), a milestone's stronger Builder attempt is priced before it launches: the
  last Builder attempt's tokens at the stronger model's rate. When the spend so far plus that projection would pass
  the cap, the run pauses `PAUSED_COST_LIMIT` before the attempt starts, naming the model and effort, the tokens,
  the projected total and the cap to resume with, instead of climbing and stopping at the cap afterwards. Without
  a cap, a price or token counts nothing changes (#611).
- Source snapshots no longer reread every file. Within one AutoCode process, a file whose device, inode, size and
  both file times are unchanged since the previous snapshot of that workspace keeps its hash, provided those times
  were already 10 seconds old when it was read; a wall-clock jump forgets every kept hash. Windows keeps none.
  Hashes and source revisions are unchanged. On a 100,000-file repository a repeated snapshot takes about 0.8 s
  of CPU instead of 3.5 s, and the first one in a process about 1.8 s (#711).
- In every run, with or without `--package`, a check, probe or protected-test replay whose command is exactly
  `cd DIR && RUNNER` (a plain relative directory; unittest, pytest, node:test or `go test`) runs with that
  runner's per-test results read inside `DIR`, so a run of zero tests, only skipped tests or incomplete results
  is refused as for the same runner at the root; it used to run as a plain shell command judged by its exit
  code. A direct node:test command in that form always runs fresh, never from the baseline cache. Native visual
  checks cannot run such a command: an approved check in that form now pauses with `PAUSED_VISUAL_EVIDENCE`
  (#711).
- Under a money cap (`--max-cost-usd`), a role's climb on its effort ladder (such as the Tester's GPT-6 Sol High →
  XHigh → Max after a failed validation) is priced before the climbed attempt launches, as the Builder's stronger
  attempt already was: the role's last attempt's tokens at the climbed route's rate. When that would take the spend
  past the cap, the run pauses `PAUSED_COST_LIMIT` before the attempt, naming the job and the rung ("The Tester's
  climb to GPT-6 Sol XHigh"), the projection, the spend, the total and the cap to resume with, instead of launching
  it and stopping at the cap afterwards. Without a cap, a price or token counts nothing changes (#611).
- Under an active-time budget you set (`--max-seconds N`), the same climbs (the Builder's stronger attempt and a
  role's climb on its effort ladder) are timed before the climbed attempt launches: the role's last attempt's
  recorded duration, never more than the stage time limit. When the active time so far plus that projection would
  pass the budget, the run pauses `PAUSED_TIME_LIMIT` before the attempt, naming the climb, the projection, the
  active time so far, the total and the budget to resume with; `--resume-paused --max-seconds M` with `M` at least
  that total launches exactly that attempt. When both the cost cap and the budget would be crossed, the cost stop
  comes first. The new-run default budget and one delegated with `--autoresolver-managed-limits` are not
  projected, since AutoResolver may extend them once at the stage boundary; without a budget you set, or a
  recorded duration, nothing changes (#611).
- The name `claude` always selects the built-in provider; a user `~/.config/autocode/providers/claude.toml` is
  ignored and doctor names it. A run started with the old example pauses `PAUSED_TRANSPORT_CHANGED` at its next
  stage; resume it with `--accept-transport-change` (#717).
- `examples/claude-provider` is now the cloud-session guide only (#717).
- The Resolver's diagnosis names each implicated test by the exact identifier its failure evidence shows (a
  regression proof's `case_tests`, `fail_to_pass` or `pass_to_pass`, a failed test run, a finding), beside its
  case or criterion ID and correction, and says a test is unidentified rather than guessing a name; a bare
  criterion ID no longer stands in for a test. Report schema, scoring and repair checks are unchanged (#775).
- The findings ledger (`autocode_findings`) takes the typed `RunState`, and `tests/test_run_state.py` ratchets the
  remaining unannotated `state` parameters (888) so new code cannot add more. A `goal_contract` saved as `null`
  now reads like an absent contract when findings are recorded or scoped, instead of raising (#695).
- When the scenario harness's time budget ends a drive, the error (and a scenario's `harness_error`) now says
  where the budget went: each earlier CLI call's seconds, the status reads together, the harness's own time
  between calls, and how much time the cut-off call had left when it started. A CLI test that removes its
  fixture keeps no `steps.jsonl`, so its failure message was the only record left (#967).
- When a supervised provider or command launch stops with "Keeper did not arm within the launch limit", the error
  now says where the limit went: the limit, when the parent spawned the keeper and the provider and sent the
  ownership frame, whether the keeper is still alive and in what process state, whether it read that frame (Linux),
  and whether it wrote a receipt. The CLI's own lifeline guard keeps the bare message. The 15-second limit,
  cleanup and refusal to release the provider are unchanged; this is a diagnostic only, the cause is still open
  (part of #989).
- Six more modules take the typed `RunState`: the findings ledger's companions (`autocode_finding_cause`,
  `autocode_finding_close`, `autocode_finding_rescope`, `autocode_risk_findings`, `autocode_validation_rounds`) and
  `autocode_milestone_scope`, so the untyped-`state` ratchet drops from 888 to 871. The run-state key check now
  follows a key held in a module constant, which brought `validation_only_rounds` into the contract. A
  `goal_contract`, `current_task` or `validation` saved as `null` reads as absent when a milestone's scope or a
  validation's freshness is worked out, instead of raising (#695).
- The AutoResolver's modules (`autocode_resolver_recovery`, `autocode_resolver_human`, `autocode_resolver_runtime`
  and `units/autoresolver`) take the typed `RunState`, so the untyped-`state` ratchet drops from 863 to 780 and mypy
  checks those 81 function bodies. Type-only: no behaviour, output or `state.json` change (#695).
- The progressive-planning record's writer (`autocode_progressive_state`) takes the typed `RunState`, so the
  untyped-`state` ratchet drops from 780 to 742 and mypy checks those 38 function bodies. The 34 `state` parameters
  in `autocode_program` stay untyped: they hold a program's own record, not a task run's. Type-only: no behaviour,
  output or `state.json` change (#695).
- The milestone gates (`autocode_milestones`), the planning unit (`units/autoplanner`) and the goal lifecycle
  (`autocode_goal_lifecycle`) take the typed `RunState`, so the untyped-`state` ratchet drops from 742 to 680 and mypy
  checks those 62 function bodies. Type-only: no behaviour, output or `state.json` change (#695).
- The goal contract (`autocode_goals`) and the runner's regression proof (`autocode_regression`) take the typed
  `RunState`, so the untyped-`state` ratchet drops from 680 to 628 and mypy checks those 52 function bodies.
  Type-only: no behaviour, output or `state.json` change (#695).
- The Builder retry policy (`autocode_builder_policy`), the parallel Builder dispatch (`autocode_dispatch`), stage
  recovery (`autocode_stage_recovery`), the planning handoffs (`autocode_planning_artifacts`), the brief obligations
  (`autocode_brief_obligations`) and the reviewer fallback (`autocode_reviewer_fallback`) take the typed `RunState`,
  so the untyped-`state` ratchet drops from 628 to 545 and mypy checks those 83 function bodies. Type-only: no
  behaviour, output or `state.json` change (#695).
- The operational-information re-evaluation (`autocode_operational_information`), workflow job failures
  (`autocode_job_failure`), reviewer routing (`autocode_workflow`), the classification retry
  (`autocode_classification_recovery`), pause authority (`autocode_pause_authority`), the recovery view
  (`autocode_recovery_view`), the external repair handoff (`autocode_repair_handoff`), recorded check replay
  (`autocode_replay_checks`), the risk obligations (`autocode_risk_obligations`) and the parallel Builder quota
  handoff (`autocode_worker_quota`) take the typed `RunState`, so the untyped-`state` ratchet drops from 545 to 451
  and mypy checks those 94 function bodies. Type-only: no behaviour, output or `state.json` change (#695).
- The recovery allowance limits (`autocode_recovery_limits`), a parallel Builder member's model stop
  (`autocode_member_stop`), Builder failure classification (`autocode_builder_failure`), the CLI's saved-run actions
  (`autocode_run_actions`), the progress view (`autocode_progress_view`), design coverage (`autocode_design_coverage`),
  the controller owner (`autocode_controller_owner`), bug-investigation questions (`autocode_bug_questions`), the
  AutoReview unit (`units/autoreview`), the completion gate (`autocode_completion`) and checkpoint restore
  (`autocode_checkpoint_cli`) take the typed `RunState`, so the untyped-`state` ratchet drops from 451 to 374 and mypy
  checks those 77 function bodies. Type-only: no behaviour, output or `state.json` change (#695).
- Visual capture verification (`autocode_visual_evidence`), the human source log (`autocode_source_log`), the replay
  view (`autocode_replay_view`), recovery-note retirement (`autocode_recovery_context`), progressive completion
  (`autocode_progressive_completion`), planning clarification (`autocode_planning_clarification`), launch inputs
  (`autocode_launch_inputs`), job model routing (`autocode_job_route`), design intake (`autocode_design_intake`), code
  checkpoints (`autocode_code_checkpoints`), the pause authority steps (`autocode_action_authority`), the verification
  plan (`autocode_verification_plan`), source tracing (`autocode_source_trace`), risk evidence
  (`autocode_risk_evidence`), retained work (`autocode_retained_work`), requirement cues (`autocode_requirement_cues`),
  `prune-run` (`autocode_prune_run`), dependency waits (`autocode_dependency`) and milestone carry-forward
  (`autocode_carryforward`) take the typed `RunState`, so the untyped-`state` ratchet drops from 374 to 287 and mypy
  checks those 87 function bodies. Type-only: no behaviour, output or `state.json` change (#695).
- In a `--package` run, a change outside the package is a blocking finding (#711): before each Tester and Completion
  Reviewer dispatch (not the Code Reviewer, Designer or Design Reviewer), the runner compares the candidate with the
  run's base, and any file added, modified (content or mode) or deleted outside the package (a rename counts on both
  sides) opens one finding with source `runner` naming each path. What the regression proof does not count (AutoCode's
  `.autocode/` files, bytecode, the runner's dependency links, an ignored file the approved plan does not name) does
  not count here either. The completion gate refuses while the finding is open, and only a later check that finds no
  such change closes it. `--accept-completion` runs the same check once its own checks pass, so closing the finding
  with `--close-finding` does not accept the change. Before, a run that owed no regression proof (a build whose
  criteria name no test) completed with the change. Neither this check nor the regression proof counts an untracked
  Git repository or a submodule's uncommitted work the checkout held at launch, which every new package run now
  records (`package_outside_at_launch`), while it is still a Git repository; once deleted or no longer one, the
  finding names it and tells the Builder to leave it to the person. A package run saved without that record leaves
  every nested repository and submodule's work out of the check. Nor do they count the files the runner wrote for the
  run while unchanged: the bug fix's diagnosis note and a review's or design review's report are compared with what
  the runner wrote, and a changed one is reported; a design check's blockers and what an earlier turn left are
  recognized by path only, so an edit to them is not seen. Before, the proof left a package bug fix with its own
  diagnosis note, or an in-place run beside a nested repository, `UNVERIFIED`. Only the Builder's handoff states the
  write scope (`write_scope`); nothing limits its writes while it works.

### Removed

- `examples/claude-provider/claude_stage.py`, `claude.toml` and `trial.py`: superseded by the built-in provider
  and the `claude-tiers` profile (#717).
- The unreleased `--engine qwen` transport. It could not complete a run: provider
  selection resolved its built-in OpenCode default and the engine's own conflict
  check then refused every run; a run that did start compared Codex's settings with
  its saved Qwen identity and paused at the first stage boundary; the transport
  asked for an output format whose single JSON array the event log cannot read; no
  adapter was wired into the event reader; and `doctor` and `autopilot` had no case
  for it at all. Qwen models remain reachable through the bundled KiloCode
  provider's Alibaba Token Plan route. A saved run that used the engine now pauses
  with `PAUSED_TRANSPORT_CHANGED`, the same retirement path `gocode` uses.

### Fixed

- Ineligible plan approvals are rejected before stage reconciliation, preserving the stopped checkpoint and retained report. Verified conversation-action replays remain idempotent.
- `--package` follow-ups (#711): a feature that adds the first Go module in the package, on a base with no Go
  project, is no longer left UNVERIFIED, as at the root (#685). A `--test-command` written for the run's
  package as `cd PACKAGE && RUNNER` (unittest, pytest, node:test or `go test`) is read with that runner inside
  the package by the proof and the named-proof preflight, as the protected-test replay already read it. Before,
  the proof read it with the runner detected in the package: unittest when pytest is configured only at the
  repository root, and none in a package of a single root Go module, whose bug fix then passed on the exit code
  without naming a test. A package run no longer takes the learned test command from project memory, and no
  longer saves its own test command and framework there, where they became the default of every later run in
  the repository.
- Truncated command output counts a final write even when it races with the watcher cutting the file, preserving accurate dropped-byte totals (#1376).
- Keyboard focus reveals the full focused details tab on narrow screens and keeps the focus ring separated from the selected tab boundary in both themes (#1375).

- The native saved-profile refusal fixture supplies the JSON invocation adapter so it reaches its original pre-provider refusal assertion.
- Accepting a replacement model for a parallel Builder consumes its routing question and leaves the parent paused for explicit resume, instead of stranding it in an unauthorized clarification (#1370).

- The dashboard keeps a project in its project filter, Settings and the top New conversation scope when
  that project's only task ran in a task worktree that has since been deleted; only the missing worktree
  itself is hidden. Removing the project from an open worktree task's project details keeps the task open
  with its Restore banner instead of returning to All work. Merging #809 into the Figma integration (#1325)
  had broken both and failed the dashboard suite on master.
- Keeper admission rejects missing timing declarations without crashing; the nested-cleanup clock fixture supplies the current timing contract (#1366).
- Native Figma verification now confines read-only checks, captures and scoped writes on Linux with
  bubblewrap and seccomp, preserving original-owner admission and fresh proof requirements. The
  installed wheel includes the required capture/image resources; platform fixtures no longer
  change unrelated process discovery while simulating macOS.
- Native launch authentication retains kernel process identity when the wall clock shifts, and
  original owner loss stays explicit even when provider exit beats the lifeline watcher.
- Mobile artifact tabs reveal the whole focused tab and keep its focus indicator clear of
  the selected border in both light and dark themes. Native modules remain importable on Windows.

- macOS runs providers again under portable supervision with one stderr notice, as the owner chose
  in #1195; #1265 had made macOS refuse every provider launch (`PAUSED_PROCESS_CLEANUP`), fake runs included.
  Linux keeps the kernel boundary, and other unsupported hosts still refuse.
- Model-access preflight passes are now bound to the saved provider configuration: accepting a transport change
  re-probes the same models, while legacy model-only markers re-probe once instead of authorizing a new route
  (#1333).

- A native visual run's planning shell can no longer read AutoCode's run store. Before, the contained shell
  blocked only writes there, so a Planner could read its own run's `state.json`, events and receipts and every
  other run's files. On Seatbelt and bubblewrap the shell now reads only its own run's `planning/` artifacts in
  `.autocode/runs`. Each launch proves this through the actual native shell, records it as `private_reads`, and
  pauses as `PAUSED_TOOL_CONTAINMENT` if the boundary stops enforcing it (#1250).

- Completed runs now print every unresolved non-blocking finding and its evidence; the public status view exposes
  each finding's `blocking` and `evidence` fields so clients can show what completion accepted. Completion
  eligibility is unchanged (#1167).

- A scoped native Figma run (`VISUAL_SLICE_GATES=complete-cases-v1`) now takes the CONTINUE readiness shortcut:
  when the Completion Reviewer says CONTINUE although every completion gate passes, the visual gate is checked
  with the run's own scope authority, as terminal completion already was, and the reviewer is sent back once
  instead of the gate always failing closed. Public `--status` stays lock-free and reports scoped visual
  acceptance as `UNKNOWN` (with `completion_current` null) instead of a false `NOT_READY` denial when the
  runner's CODE scope authority is not available (#1255).
- Pull request CI selects affected fake tests and relevant heavy jobs against pinned event commits, with explicit prompt/CLI consumers and a stable required-check verdict. Master coverage and strict provider refusal remain unchanged.
- Milestone review prompts make a final validation task conditional on missing current independent criterion or whole-flow evidence, retaining the full completion gate (#1292). Repair stale rendered-prompt snapshots introduced by a master merge without changing their production instructions.
- Model-access preflight now probes each role through the tool that runs it. A role bound with `--role-transport`
  to a tool with `[auth]` billing routes, such as Kilo, is probed through that tool and its login is checked; before,
  it was never probed when the built-in OpenCode ran the other roles, and its prepaid route could fail after paid
  work. A tool that still has a model to probe has its login checked before the first request, and passes are kept
  per tool, so a pass on one tool never counts for the same model on another. Runs that use one tool are probed
  exactly as before, and their saved passes are still reused (#1287).
- The Resolver now climbs its effort ladder: a Resolver report still rejected once report repair is spent, or
  an uncertain Resolver attempt the operator abandons, moves the Resolver from GPT-6 Astra High to XHigh and
  then Max with a fresh session, and records a `reasoning_escalations` event. The ladder was keyed to the
  `astra` route, so the Resolver (which runs on its own `resolver` route) stayed on High, while the
  per-task Planner on the `astra` route climbed GPT-6 Astra against the 2026-09-28 decision; it no longer
  does. Saved runs read a ladder persisted under `astra` as the Resolver's, with no state migration. A
  recovery packet bound before a climb still holds after it, so the climbed attempt meets the packet's usual
  causal-progress check (`--retry-failed-stage` authorizes one attempt) instead of a stale handoff. Under
  `--max-cost-usd`, a Resolver climb is priced before its attempt launches, as every climb is (#611, #1345).
  `autocode models` and doctor now name the `astra` route "Resolver and per-task Planner", and the docs say
  which runs take `--resolver-model` (OpenCode-engine runs) and which seed the Resolver from `--astra-model`.
- Credential masking in exports (bundles, evidence reports, pull request bodies, plan files) no longer
  masks AutoCode's own `risk-<sha256>` declaration ids as `sk-` keys; every other `sk-` key is still
  masked (#720).

- A repair of the Plan Reviewer's final plan report no longer includes the superseded first draft's whole
  contract next to the revised draft under review. The final review itself never receives that contract, and the
  extra copy helped a program workstream's third final-review repair pass the 256 KiB repair limit and pause at
  `PAUSED_REPORT_REPAIR_INPUT`. The first draft's report keeps every other field in the repair, and its full text
  stays at its saved path. The revised draft's full contract appears once; the repair's `protected_contract`
  excerpt, taken from that same draft, still repeats its required behaviors, scope exclusions, constraints,
  failure cases, acceptance criteria and permission boundaries. The limit and every report check are unchanged
  (Refs #1337).

- `--planning-v2`: feedback, an answer, a follow-up or a program's plan guidance that changes the Requirements
  handoff now begins a new planning cycle with the configured Plan Reviewer allowance, as on the default flow.
  The corrected plan's first review was refused at `PAUSED_PLANNING_BUDGET` for the previous cycle's charges.
  The previous cycle's reports and charges move to `planning_history`; the same handoff admitted again, or a
  retried Requirements report with nothing new from you, keeps the cycle and its spent reviews. A planning record
  saved before this change keeps its charges until your next input (#1252).

- Under `--json`, a run stopped inside the build loop (`--pause-after-stage`, a time or iteration limit, a stop an intervention asked for) printed only `{"result": …}`, with no `run_dir`, `status` or `view`. It now prints the saved run's status payload with `result`. `tests/test_json_output.py` checks a requested pause and the iteration and time limits (#714).
- Dashboard: the Plan pane shows the attached conversation draft that the task's draft selection picks, read-only
  with its draft label, Planner attribution, freshness and earlier drafts, instead of "No saved plan yet", "Plan
  not ready yet" or an older unreviewed revision (which stays under a disclosure). History's "Read draft in Plan"
  link now opens that pane at every width rather than focusing the hidden plan rail. Reviewed and approved plans
  keep their presentation and chat-only approval controls (#1056).
- A CLI test that hits the subprocess fixture's 240-second deadline now reports what the killed CLI had printed:
  the last 4,000 characters of its stdout and stderr appear in the test error, and a retained fixture
  (`BUILD_AUDIT_ARTIFACTS`) keeps both streams whole as `command-timeout.stdout.log` and `.stderr.log`. The
  deadline and the exception are unchanged (Refs #1005).
- A new run checks the Builder's stronger attempt against the model list its provider gives for the run's
  routes, before any model call: a `--builder-strong-model` that list lacks (or whose moved Tester or
  Completion Reviewer would need a checker model it lacks) refuses the run, and a default stronger attempt it
  lacks is dropped, so the Builder retries once more and pauses naming the missing model instead of failing
  that launch partway through the run. Effort rungs are filtered by the same list, and one function,
  `autocode_route_ladder.next_route`, now decides every effort climb (#612).
- A `TaskRun` call that outlives its timeout no longer drops the output that reached the caller: the error
  message keeps the end of its stdout and stderr (or says none arrived), and `TaskRunError.partial_output` holds
  both whole. Output the CLI had not yet flushed is still lost. The limit and process cleanup are unchanged. A
  timed-out status read in a loaded suite gate now keeps whatever output it had already sent (#992).
- Automatic billing-route access probes now count toward run money/time limits, retain interrupted usage, and honor Stop between requests after verified cleanup (#1291).

- Provider supervision preserves a watchdog handoff failure when its lifeline has already closed, and
  never writes completion metadata into an unrelated file that reused the descriptor (#1278).

- Keep repeated-failure resume admission in the cycle-free recovery policy module, preserving
  inspected retries and their one-use authorization while shrinking the CLI (#899).

- Run automatic model-access probes inside mandatory provider supervision, refusing
  unsupported hosts and unverified cleanup. Preserve failed probe receipts on retry
  and keep timed-out probes uncertain rather than caching an access pass (#1283).
- Clarify test-host requirements: macOS supports editing and checks that avoid
  supervised provider launches; fake CLI and scenario stages use supported Linux
  or existing Linux CI. Strict host refusal and optional live verification remain
  unchanged (#1274).
- `tools/run_suite.py` compiles `tools/` bytecode once before any test module starts. With bytecode writes
  off for the run (#935), every CLI process a test launched compiled the ~330 runtime modules from source,
  about three quarters of a `--status` query's CPU, which on a loaded parallel run can push the concurrent
  task-workspace test past its 10-second status limit. It compiles the interpreter's stdlib and site-packages
  outside the checkout too: a process started with `-E` or `-I` rewrote a stale stdlib cache during a check replay,
  which read it as a changed dependency. Nothing writes bytecode during the run (#999).
- The lazy-validator scenario now requires the Validator attempt and its recorded
  citation refusal; empty run evidence and unrelated pauses cannot pass (#1142).

- Keep valid Node 22.14 ordinary test results usable as named proof when Node's
  event omits the test type; suites and malformed retained evidence keep their
  existing checks (#1266).
- A harness-guarded CLI whose keeper does not discharge within the unchanged eight-second bound now exits 2 with
  `Harness keeper discharge did not finish within its bound`, naming the receipt phase it saw and the seconds since
  its finished message, instead of an uncaught `TimeoutExpired` traceback. Every keeper's terminal supervision
  receipt gains `stop_timing`: the stop request, sampling-loop exit, the loop's latest and a discharge's final
  process sample, and the time spent in whole-host session and process-group scans, as seconds before
  publication. The diagnostics change no wait, ownership check or cleanup decision (part of #1143).
- Restore strict task-stage provider refusal on unsupported hosts, including macOS, before any
  child or supervision receipt is created. This replaces the
  macOS-only fallback from #1195; ordinary verification commands retain portable supervision (#454).
- A stage handoff lists at most 200 protected tests (32 KB) of its affected paths, then says how many more
  there are and points at the retained full inventory. Copying every match once made a 7.8 MB Builder prompt
  that overflowed the model's context; the protected binding and the original-test replay still cover every
  file (#1140).
- Planning stages now check each acceptance criterion against itself before returning the plan: its examples
  must agree with each other, with its own rule and with an approved design, and a boundary the request and
  design leave open (a value exactly at a limit) stays untested instead of being stated both ways. A live
  `discuss-then-design-then-build` build turn otherwise had to ask the user to reword such a criterion. The
  approved-design rule no longer claims only an approved criterion needs the user to change; approval rules
  are unchanged (#732).
- Report repair reads a configured tool's saved report, or the Claude provider's event log, as the original, so
  finding dispositions survive the repair (#717).
- Supervision qualification docs now match the accepted macOS portable policy and distinguish completed provider/harness fault coverage from unqualified standalone helpers (#454).
- The dashboard no longer treats a task's Git worktree as a project. The task and its linked conversation
  stay under the parent project in the sidebar and project filter. Opening the task keeps that project
  selected, and New conversation (including a stopped task's "Start a new conversation") starts in it.
  Task status and task actions still target the worktree. A project the dashboard knows only through a
  task worktree can be removed from Settings (#809).
- Keep the main CLI within its existing architecture size limit by moving iteration-limit policy to the build loop, preserving the compatibility name and limit behavior (#1256, #881).
- Keep the newly integrated `autocode wait` checkpoint boundary compatible with the shared `RunState` contract and signature guard (#1253, #1256, #881).
- Dashboard links between the conversation and the artifact pane now work on phones. At 759 px and below,
  chat links such as Open preview, View changes, View plan, Review checks and Inspect open the Details
  drawer on the pane they select. Work and Plan links (Open in conversation, Review in conversation) close
  that drawer, open any collapsed section around their chat target and focus it. Previously the selected
  pane stayed hidden, or the target could not take focus because the conversation was still inert. The
  links still only navigate, and polling never reopens the drawer or moves focus (#1054).
- Explicit run-state parameters now use the shared `RunState` contract, so type checks reject unknown keys and invalid recovery-counter values instead of silently accepting plain dictionaries. Guarded relative imports preserve installed CLI startup; a signature ratchet prevents regression (#881, #695).
- Document contributor layers, canonical stage names and workflow-job wiring, and align verification guidance with focused fake tests and current CI platforms (Refs #695).
- The Builder's stronger attempt and the cross-model guard share one verifier-independence rule: a Tester or
  Completion Reviewer in the stronger model's GLM or MiMo family now moves to the checker model instead of
  staying and pausing the escalated run with `PAUSED_CROSS_MODEL` (on a configured tool too), a new run
  refuses a `--builder-strong-model` that shares its checker model's family at creation rather than mid-run,
  and a tool's `[builder_retry]` table naming such a pair is refused when it loads (#612).
- Every documented task-run need and Resolver scope now has a fast execution-contract test through
  the real CLI and public TaskRun methods. Scoped permission answers reject unoffered choices before
  saving a decision, while free-text permissions and exact timeout choices retain their existing
  behavior. Progress guidance names planning feedback/limits, operational responses and scoped
  resume controls. A failed job without its original source identity now asks for archive/source
  inspection before a new run instead of saying nothing is needed from the operator. Progressive
  material permission prompts accept authored conditions; revised plans still require fresh approval
  (#1010).
- Include the existing progressive-learning-journey CLI authority and restart checks in
  the scenario CI gate, one case per process. Unrelated PRs retain the existing slow-test
  selection; master and changes to scenarios or the suite run the checks (#455).
- Scope editable-install verification to AutoCode's package before hashing, so concurrent test fixtures elsewhere in the checkout cannot change a proof's dependency context. Package changes and read failures still invalidate reuse (Refs #1215).
- Declare the protected acceptance-test file mapping type so the spec-first workflow passes the repository type-check gate (#1215).
- Refresh the fake Plan Reviewer's source coverage after it cites an accepted Resolver correction, so joint review reaches fresh plan approval (#1215).
- Reuse the freshly verified Figma inventory catalog and resource blockers within a
  status projection, avoiding duplicate topology parsing while each later read still
  checks reference identity, hashes and dimensions. Compute reusable saved captures
  once per projection for both coverage and retained results; later reads still
  reject source or evidence changes (Refs #1227).
- Keep fake Resolver reports aligned with the requested nullable recommendation schema, preserving strict retained-proposal comparisons during classification recovery (#1215).
- Live runs work on macOS again. macOS has no seccomp, so providers there run
  under the portable lifeline and sampled ownership that runner commands use,
  with a one-line notice and no `session_boundary` in their receipt. The fallback
  is macOS only again: a Linux host whose boundary fails (wrong ABI, CAP_SYS_ADMIN,
  namespace mismatch) refuses provider exec, as #1187 intended (#1195).
- Restore the Resolver prompt regression fixture to the shipped role schema, preserving recommendation isolation and expected-failure command guidance (#1215).
- Use the shipped review schema in both plan-rule prompt fixtures so Resolver recommendation fields no longer break their checks (Refs #1215).
- Bind recorded-check replay's source-trace review to its authenticated original occurrence and fresh evidence, while retaining exact semantic scope and simulated-only proof (Refs #1215).
- Accept older Resolver reports that omit the nullable recommendation field,
  preserving the raw report and treating omission as no new authority (#1211).
- Apply explicitly accepted Resolver criterion corrections as unapproved draft
  revisions through the configured plan review and fresh approval boundary;
  preserve verification requirements and reject stale recommendations (#679).
- `--answer`, `TaskRun.answer()` and `--chat` refuse an answer to a question with
  `options` unless it is one of those labels or the question's `proposed_default`.
  The refusal lists the valid labels, saves nothing and leaves the Resolver token
  current; `--chat` asks again. Questions without options still take free text (#1013).
- Keep an answered Resolver request consumed when a retained private proposal
  is replayed after elapsed-time accounting advances; duplicate reviews still
  defer without reissuing the question or granting another attempt (#1108).
- Accept a completed review when the current source already has a Resolver
  diagnosis, retaining its unanswered request instead of rejecting the report
  and rearming a retry loop. Consumed responses still grant no new attempt;
  meaningful source or verification-setting changes can reopen diagnosis (#1108).
- Refuse a parallel Builder retry when its saved route differs from the run's
  current Builder route, including after a separate settings save. The refusal
  names both routes and the existing model-question or settings-restoration
  recovery; completed siblings and the approved goal are retained (#1097).
- Preserve nested verification preparation and replay commands alongside the
  mandatory provider session boundary; runner commands retain their separate
  portable lifelines and sampled SID guards (#454).
- Keep v2 prerequisite probes and worker sandbox helpers owned after caller
  loss, with durable pre-exec admission and verified cleanup before readiness (#454).
- Retain provider children that create a new session and reparent before any
  sample. On supported Linux hosts, record kernel-mediated session transitions
  before allowing them; refuse provider exec when this boundary is unavailable
  (including macOS), rather than publishing unverified cleanup (#454).
- Restore refusal before provider launch when the required kernel containment
  is unavailable, and acknowledge cancelled test requests only after their
  exact caller exits (#1202).
- Keep scripted providers compatible with source-trace generation schemas in
  fresh plans and report repairs, retaining supplied citations and failed
  verdicts instead of stalling planning or completion (#1202).
- Keep an unseen reparented provider child owned when it changes process groups
  but stays in the admitted session. Verify fresh native membership and refuse
  uncertain empty-session scans (Refs #454).
- Keep the parent checkout locked through parallel Builder cleanup when the
  Orchestrator disappears; worker keepers retain the kernel lease, and uncertain
  cleanup retains birth-identified ownership for the next writer's admission (#454).
- Stop a provider's child that reparents before the first keeper sample but
  remains in its recorded session group, including when the keeper itself dies.
  Preserve the provider's collected exit code and verify each process birth
  before cleanup signals (Refs #454).
- Scenario harness owner loss and CLI death let parallel worker lifeline keepers
  finish their declared cleanup before the outer keeper or harness force-stops
  the tree. Cleanup remains bounded; task deadlines and provider budgets stay
  unchanged (#454).
- Preserve session-aware verification preparation custody and settle transient
  unrelated PID churn before declaring keeper cleanup uncertain. Unknown process
  ownership and denied inspection still block cleanup; fresh leader births
  prevent a retired group from capturing foreign processes (Refs #454).

- Recheck a retained Resolver diagnosis after effective settings change instead
  of replaying its stale blocker. Unchanged inputs and budget-only changes keep
  the existing diagnosis; legacy diagnoses with unknown settings remain held (#1106).
- Job recovery records the saved suite and regression commands, so changing a
  command invalidates an exact retry instead of reusing stale configuration (#1107).
- Make the Resolver identity purity test guard canonical source readers as well
  as compatibility aliases, with injected-read controls and observed snapshot
  use in the adjacent source-change and truncated-review fixtures (Refs #1127).
- Builder failure tests now exercise the canonical source reader, verify it is
  consumed, and reject a changed source revision; remove unused Builder snapshot
  mocks that hid the tested seam (Refs #1127).
- Final completion and an adopted external repair require a passing regression proof
  for every milestone's `test:` and `guard:` criteria, even when the current milestone
  has none; a milestone's own review still proves only the cases due at that point (#1112).
- Include each feature milestone's cumulative changed Python test files in a
  saved literal pytest/unittest regression selection. Keep existing selectors,
  filters and bug-fix command authority; missing or failing acceptance cases
  still block the runner proof (#1105).
- Recovery no longer depends on which dispatch path hit the pause: the live loop,
  serial unit dispatch and explicit resume share one ordered recovery chain, so a
  truncated review recovers automatically on resume and a provider-capacity pause
  recovers during a serial (unorchestrated) stage (#1071).
- Route the remaining report-repair and permission-recovery running transitions
  through the shared status owner, with a ratchet preventing direct writes (#1071).
- Restore master lint after the model-route advisory change by sorting the CLI's
  package and standalone import blocks (#1027).
- A quota-stopped Investigator accepts an explicit model answer, saves its preparation pin, and issues a fresh exact-retry token (#970).
- Keep repeated split and equals-form TaskRun model options current after an
  accepted route answer, so resuming does not retry the exhausted model (Refs #1010).
- Apply explicitly selected suite, tool and stage timeout allowances when a
  structured permission question is answered, and preserve them across TaskRun
  resume instead of retrying under the old settings (#987).
- Apply saved operator tool, stage and milestone limits to explicitly retried
  parallel Builders. Successful siblings, frozen model routes, recorded attempt
  deadlines and accumulated spend are retained; automatic recovery does not
  adopt the parent's limits (#1096).

- Warn after a saved model-route change creates a shared Requirements, Planner, or
  Builder producer/consumer route; preserve mandatory verifier independence (#979).

- Accept literal UTF-8 briefs from `--brief-file PATH` or `--brief -` (stdin),
  retaining shell syntax and line breaks; save and expose the task's SHA-256
  so callers can verify the received request (#1015).
- Align configured-provider preflights with stage credential filtering and show names-only opt-in guidance on authentication failures (#975).
- Make rework source fixtures observe the actual snapshot reader: human decisions
  stop before reads, and direct repairs require fresh source after queue admission (#1127).
- Keep unittest output parsing in the existing Python test module with unchanged
  named outcomes and setup classification, and tighten verification to 3,352 lines (#1125).
- Keep Go JSON test-result parsing in the existing Go test module, preserve
  verification's compatibility entry point, and tighten its size limit (#1125).
- Keep verification's JS and shell literal scanner in a pure module with
  compatibility exports, and prevent the remaining verification controller
  from growing beyond its reduced size (#1125).
- Preserve valid Node changes when unrelated package scripts have unresolved
  inputs or product code adds package `imports`/`exports`. Keep base test policy
  while loading candidate modules, reject maps that may redirect a runner,
  and avoid redundant definition suites and per-file Git processes (#652).
- Refuse AutoCode self-builds that can edit their executing controller or run
  outside a linked worktree. Public status records the saved controller identity
  alongside the Builder's base revision; safe caller-owned worktrees remain
  supported (#1017).
- Reject false PASS in suite preservation caused by inherited package-manager
  configuration, edited symlink targets, missing per-test evidence, or reversions
  of reviewed code. Keep proof copies outside the candidate workspace and compare
  the retained suite against the candidate's actual reviewed-source changes (Refs #652).
- Name the configured model prefixes when a billing-route check refuses an
  unrouted model, so the pause identifies the available routing choices (#973).
- Explain disk-full failures during parallel Git dispatch and verification-copy
  preparation, preserving the paused run and original diagnostic so work can
  resume after freeing storage (Refs #971).
- Rejected answers and incorrect recovery flags now show the current scoped
  recovery command and private token; wrong clarification response forms retain
  the waiting request instead of becoming an invalid-output pause (#909).
- Show pending question IDs and the current Resolver token on stderr after a
  saved answer and in status, making consecutive answers readable while keeping
  status stdout as JSON (#1014).
- Offer an exact read-only Investigator retry after a failed Builder classification,
  retaining pending Resolver proposals and existing failure and spending limits (#961).
- Retry an explicitly retryable native network failure once when the owned process
  stopped normally before any observed turn/tool or source effects; retain both
  attempts and usage, and preserve manual holds for uncertain or repeated failures (#908).

- Fast PR tests now check the provider reporting contract in every planning prompt
  variant, catching the regression that full-suite tests found after prompt extraction.

- Offer an explicit same-engine model choice after an authenticated response output-limit stop, retaining incomplete work and usage without automatically retrying Builder or Requirements.
- Requirements coverage now requires the whole ignored statement, so ignoring
  a short heading cannot hide a longer requirement containing the same words.
- OpenCode request intervals now pair starts and finishes within the same
  message; ambiguous timing stays unknown while known usage remains available.
- Reuse a successful new-run model inventory for its immediate route validation,
  removing a duplicate provider catalogue subprocess before the first interaction.
  Resumes and later validation still fetch a fresh inventory (#727).
- Clarify the v1 Requirements report's ignored-statement string-array shape
  and nonoperative source-label guidance while preserving actual examples and
  requested literal output.

- Pin Ruff and mypy in a shared local/CI lint dependency file so upstream tool
  releases cannot change the required gate without a reviewed version bump (#905).

- KiloCode stages now use `kilo run --auto`, so ordinary permission requests
  during noninteractive runs no longer terminate the provider. Effective native
  deny rules remain in force; custom provider files need the same command update
  (#891).

- New provider-defined Requirements routes inherit an explicit `--glm-model`
  before defaults, including KiloCode and native Codex; explicit Requirements
  choices, conversation-handoff profiles, separate reasoning efforts and saved
  role pins remain authoritative (#889).
- Present artifact review when automated criteria and the technical flow pass
  but the contract still requires human acceptance, avoiding repeated validation
  while retaining current evidence checks and separate artifact approval (#988).

- Scope diagnosed bug regression cases to approved milestone test bindings, retain accepted cases, and require every case at final completion.
- Refuse unsupported dotted Python named-proof selectors, including paired
  Markdown code spans, before draft installation and goal approval, preserving
  supported names and criterion-ID fallback (#1000).
- Report repair permits the provider-required JSON artifact write while continuing to forbid repository source edits.
- Keep report-file provider payloads and validation scripts in each report’s run artifact directory so read-only stages preserve repository source.
- Preserve the original human task and named preservation guards when planning from a bug diagnosis.
- Supply the complete approved contract to discovery report repairs so independent protected constraints remain intact.

- Run all lint diagnostics after a failed lint/format check, and report the real
  Compose gate on every pull request so master can require both suite and Compose (#843).
- Clarify provider-derived Requirements defaults and the separate Planner and
  Requirements override flags in `docs/providers.md`.
- Match the parallel quota fixture recognizer by syntax so formatting cannot
  silently omit its required clarity field before worker recovery tests (#854).
- Guard the OpenCode, planning and orchestrator compatibility exports in flat
  and installed-package modes against removal by import cleanup (#845).
- Run direct Seatbelt enforcement tests in a targeted macOS CI job that rejects
  unavailable containment, empty selections and skipped tests (Refs #709).
- Strict macOS tool containment permits accented and CJK workspace paths while
  retaining protected-path denials and quote/backslash escaping (#812).
- Verify through the public CLI that an exhausted report-repair allowance buys
  no third provider call, even when successive errors differ (#846).

- Doctor checks psutil in the process keeper’s isolated interpreter and explains how to fix PYTHONPATH-only installations (#815).
- Recovery browser fixtures can import core modules when launched directly
  with no checkout-specific PYTHONPATH, as in CI (#856).

- Preserve review scenario seed context so the supplied PR patch applies and
  both regression mutation checks execute after repository formatting (#853).

- Keep native inventory fixture injection at its requested output indentation
  when formatted report-repair code contains the same output statement (#851).

- Registered Codex artifact providers refuse workspace relocation and malformed
  or conflicting config overrides before launch, preserving runner-bound paths (#811).
- Refuse malformed legacy command receipts, including timed-out, interrupted,
  errored or uncollected entries, from contributing completion evidence (#813).

- Verification copies prepare large source inventories using packed source
  blobs while retaining Git staging rules, source checks and clean replay.
- Preserve fresh-task `--explain` previews when combining CLI corrections, while
  keeping explicit saved-run inputs exclusive and explanation commands read-only.

- Preserve a separate command-output capture for each verification attempt, including successive Analyst probes and report repair (#806).

- Text checkouts use LF across Git line-ending settings, preserving binary
  assets; WSL installation notes separate Linux and Windows environments (#802).
- V2 contract-writing stages and report repairs receive the progressive-plan
  rules required by their output schema, including the empty form for small
  tasks and disjoint planned and outstanding criteria (#801).
- Source gates exclude test fixtures and cover nested application modules;
  missing v2 planning routes have a specific pause explanation (#799).
- Fresh dry runs work without an existing run directory. Explanation commands
  remain read-only in fresh and completed workspaces, including the installed CLI.
- Bug investigations receive the configured Python test interpreter, including
  external virtualenvs, for scratch checks and clean replay (#778).
- Codex and configured-provider judging captures run in a source-bound copy
  inside the owning run, keeping generated test output out of the project;
  original-source and clean-replay checks still apply. Qwen launches also
  discard inherited verification authority from another stage (#782).
- Literal absolute Go commands collect native test results before proving a
  reproduction. Empty and skipped-only test probes no longer qualify (#783).
- Operational pauses retain their recovery authority through queued feedback
  and pause requests; unrelated budget flags and historical job-failure
  records cannot authorize an unscoped retry (#661).

- Fresh `--dry-run` commands work after the addition of `--explain`; actions
  that need an existing run still require `--run-dir` (#796).
- `autocode explain` and `--explain` read saved stops, including finished runs,
  without starting a provider, taking a run lock or creating a worktree (#796).
- Installed `doctor` commands can read the bundled provider matrix and show
  untested versions and containment/visual refusals correctly (#794).
- Verify optional Z.AI Coding Plan and Kilo Gateway logins before Kilo model
  calls, alongside the default Zhipu login check; refuse missing or wrong-mode
  logins and billing-route environment overrides (#888).


- Doctor warns when its executing installed AutoCode payload differs from the verified AutoCode checkout being inspected, with source-run and deliberate reinstall guidance; installed runner commit provenance remains unknown when unavailable (#1016).
- Report a lost ordinary CLI controller between stages as `PAUSED_CONTROLLER_EXIT`,
  retaining its saved checkpoint and showing `--resume-paused`. Birth-bound owner
  receipts distinguish active execution from deliberate ready handoffs; status
  stays read-only and recovery persists under the existing run lock (#1021).

## [0.7.1] — 2026-10-09

The first tagged release. AutoCode runs a coding agent through planning,
approval, implementation and independent validation, and does not report
completion without evidence.

### Added

- **Original-brief acceptance** (#452, #617, #669, #672, #742): a `one per line`
  CLI output format is checked line by line, with optional exact/absent items,
  against a runner replay — not only the Planner's criteria.
- **Verdict taxonomy** (#455, #738): `stop_class` on every scenario result
  (false completion, safety stop, provider failure, harness failure, budget).
- **Reliability table** (#694, #750): a generated dated table in
  `docs/reliability-table.md` and a short copy on the README; live and fake
  rows never mixed.
- **`autocode explain`** (#715, #788): plain-English explanation of why a run
  stopped, what it means, and what each offered command does.
- **`autocode merge`** (#724, #789): deliver a task worktree's branch into the
  base; a moved base is refused with the re-validate step.
- **Provider conformance matrix** (#706, #790): `docs/providers.md` names the
  engine versions the offline suite has run against; `doctor` reports the
  installed one, or honest `untested`.
- **Property tests** (#705, #745): no generated state may complete without
  evidence (completion gate, brief literals, regression judge, tokens).
- **Secret redaction** (#712, #786): credentials never leave the run directory
  in a PR body or export; the raw capture stays intact.

### Fixed

- A parallel Builder's model stop has one way forward after corrective
  information (#541, #683).
- A `{"cmd": …}` final message is a known provider defect: never accepted as a
  report, never executed (#512, #735).
- Probe containment is unavailable off macOS: the classification becomes an
  untestable advisory instead of parking the run (#667, #743).
- A packed-ref `branch -D` no longer trips the tip guard on older Git (#668, #743).
- The completion gate refuses a COMPLETE whose runner observation is missing,
  including under `glm_first_v1` routing (#451, #740).

### Changed

- A new task runs in its own Git worktree by default; `--in-place` opts out
  (#724). The person's checkout stays usable while the Builder works.

[Unreleased]: https://github.com/charlieanna/autocode/compare/v0.7.1...HEAD
[0.7.1]: https://github.com/charlieanna/autocode/releases/tag/v0.7.1
