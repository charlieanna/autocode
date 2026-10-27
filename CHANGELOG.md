# Changelog

All notable changes to AutoCode are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each release lists the pull requests that changed user-visible behaviour.
PR template asks for an entry here; a change without one is incomplete.

## [Unreleased]

### Added

- `autocode status --summary` prints a run in one sentence: needs you, working, done or stopped, then the
  exact command the status view names (#2049). The same object is `view.summary` in the `--status` JSON.
  Only a completion verified against the current checkout reads done; a finished run whose evidence no
  longer covers the checkout, or a running checkpoint nothing supervises, reads stopped, never done or
  working. `--status` without `--summary` is unchanged.
- A bug-fix plan's regression criteria carry a `current_behavior` box: what happens today, copied from the
  Investigator's diagnosis bound to the run, whose probe the runner saw show the bug (#2133). The runner
  fills an empty box, and the approval
  screen, the terminal chat and the channel bridge show it as "Today:" under the criterion. A planning
  revision that sets the box to exactly the diagnosis text is recorded as a change receipt
  (`current_behavior: C1 -> …` on the approval screen) and asks no question; any other change to it on a
  protected criterion still needs a saved user answer. A planning report that leaves the box out has an
  empty one, with no report repair. Before, that note lived inside the criterion text, and correcting it
  cost a report repair, a question and a second planning round.
- Qualification evidence is structured (#2059). `tools/qualify_routes.py` now appends a `runs` record
  to each table entry it confirms: date, run, scenario, profile, the role's accepted attempts on that route,
  and their minutes and cost (null when an attempt did not record one). The selector validates the records,
  and `autocode models --selection` shows each listed entry's evidence, for example
  `[provisional: no AutoCode run yet]`. Selection itself is unchanged: provisional entries stay selectable.
- A plan whose criteria can prove only a stand-in for what you see, because code outside the
  checkout renders it, now says so (#2068): the requirement is traced `proxied` with a
  `proxy_reason`, and the plan asks you a decision question until a saved answer accepts the
  stand-in. The status view lists these rows as `proxied_requirements`, and the approval screen
  shows them under "Proven only on a stand-in".
- The VS Code run details and sidebar chat now show the separate limit raise a bound pause names once
  AutoResolver holds it or `next_command` is a plain resume: a number field and a **Raise limit and
  continue** button that takes only the row's own command, confirmed with the limit it changes. Typed
  raises at the cost, iteration, milestone and unchanged-batch pauses are confirmed by name instead of
  as "total active time". The browser dashboard's recovery card gains a number entry for the one
  number a pause leaves to a person (`needs.command_inputs`), so a run paused at a limit can be
  restarted with a typed number from the dashboard too (#2044 follow-up).

- `autocode connections setup` now ends by naming the route each role will use (model, reasoning
  level, the account and that it is a qualified choice), and a new run with no model flag prints the
  same lines before it spends anything (#2057). Both read the connected tool's own model listing and
  launch no model; `connections list --routes --json` returns the same rows as `routes[]` for clients,
  with `routes_stop` when no connected account can field a qualified route for a role. The plain
  listing stays subprocess-free and names `routes_next_command` instead. `docs/providers.md` now starts
  with "You do not need to choose a model", with the model and effort flags as overrides.

- A reasoning effort you named that the role's model does not declare is now a pause you can answer
  (#2057). The run stops as `PAUSED_EFFORT_UNSUPPORTED` before any stage launches, and the status view
  names `--answer effort-ROLE=EFFORT` with the model's declared levels (`needs.effort.declared`) as the
  only options. The terminal chat, the VS Code client and the headless conformance client offer those
  levels and refuse any other; the answer saves the level as your choice and the run resumes. Before, the
  refusal was prose, and the chat could offer only `Continue? [y/N]`, which stopped at the same launch
  again (#2020, #2039). `TaskRun.assign_effort(role, effort)` gives the same answer.

- A job that stopped on its time limit or the Investigator's exploration budget now offers the same
  model at its next lower declared reasoning variant first (#1902). The status view names
  `--answer route-ROLE=MODEL@LEVEL --job-retry-token -` as `next_command` (`needs.route.lower_effort`),
  the answer binds the exact retry to that level, and the retry launches at it. Only a level the model's
  listing declares (or a probe passed) is offered; a named level it does not declare is refused. The
  retry's handoff now also says which limit stopped the attempt (`stop_kind`), which it left empty before.
- The bug Investigator now has a reproduction-first budget (#1902). After 30 completed source-reading or
  searching calls with no attempt to reproduce (`limits.investigation_exploration_budget`; `0` disables;
  no flag yet), the runner stops the stage early instead of letting it read until the stage deadline. It
  pauses as a stage timeout with the usual retry question, and the exact retry's handoff lists what the
  stopped attempt already read. See `docs/execution.md`.
- Every number a bound pause leaves to a person is now described in `needs.command_inputs`, not only the
  active-time total, the plan-review allowance and the recovery grant. The cost cap, iteration ceiling,
  milestone time, milestone replans and unchanged-batch limit each get a row with its current value, what
  was used and the minimum that releases the run. Once AutoResolver holds such a pause, `next_command` is a
  plain resume, and the row now carries the raise as its own `command`. The terminal chat, the VS Code
  client and the headless conformance client validate any such row the same way and take only that
  command, so a number typed at the pause raises the bound. Before, those pauses named an `N` no client
  could fill, and #1950's fix could only say the typed number set nothing (#2044).

- The terminal chat shows which planning path a bug-fix run took: a Plan path section in the side
  panel (`compact: Planner draft omitted` with what covers it, or `full: every planning stage runs` with
  the first reason, and any fallback) and a Planning path block in `/details` with every reason, the
  decision rows and the hashes behind them, from the status view's `stage_policy` alone (#1884 F2).
- `--compact-bugfix-planning` (new runs, off by default): for a bug the Investigator reproduces with a
  runner-run probe, sizes small and localizes to at most four files none of which the project's
  `sensitive-paths` name, the runner writes the plan draft from the diagnosis and the Requirements record
  instead of launching the Planner's draft call; the Plan Reviewer reviews that draft, a clean review goes
  to the person's exact-token approval and a blocking concern returns the run to the Planner. The new
  project setting `sensitive-paths` (no flag) lists the path patterns such a path may never touch; a
  project without it keeps the full path. The status view's new `stage_policy` field records the decision
  and its reasons (#2021, the first #1867 checkpoint).

### Changed

- `autocode doctor` no longer sends a user of the released package to maintainer-only steps: an engine that is
  not installed gets no conformance row (its own row already says how to install it); an untested engine
  version points at the tested list in the provider docs; an installed release is identified by its version
  instead of a warning to run from a Git checkout; the PATH fix suggests `pipx install autocode-supervisor`;
  and a single installed copy is no longer called "linked/editable". `autocode --help` links the full CLI
  reference on GitHub, which a package install does not ship.
- `autocode doctor` now fails with a `session` row when this Linux shell holds `CAP_SYS_ADMIN` or
  `CAP_SYS_PTRACE` (root in most containers, VMs and CI images), the same check every stage launch makes,
  and names the fix: run as an ordinary user or prefix `setpriv --bounding-set=-sys_admin,-sys_ptrace`.
  Before, doctor passed and the first run, even `autocode demo`, stopped at `PAUSED_PROCESS_CLEANUP`.
  `autocode demo` now prints the stop's reason when it cannot reach the sample plan.
- On an automatically selected run, the one-use planning-review fallback after repeated provider-stream
  silence now takes the next qualified Plan Reviewer from the run's saved permitted set, the same pool and
  independence check a quota stop uses (#2061). Before, it borrowed whichever other role's model was
  configured, so a GLM/MiMo run could hand plan review to the Builder's MiMo, which the qualification
  table excludes for that role (#1723). When no other qualified reviewer remains, or the person named the
  reviewer, the pause stands as before. Runs without a saved permitted set keep the old rule.
- A Tester or Completion Reviewer report that mistypes the echoed contract hash, contract revision or
  task id no longer costs a paid report repair (#2135). On the current, admitted attempt whose launch
  recorded today's contract and task, the runner adopts that launch identity, keeps the provider's
  bytes as `reported_output` and records each change in the stage's `identity_corrections`. A
  contract or task that changed during the stage, an echo of an earlier contract or task, another
  attempt, or a record without a launch identity is still refused.
- The qualification table records the 2026-10-25 `bugfix-trivial` live run on `glm-mimo-kilo`:
  Requirements, Planner, Builder, Tester and Completion Reviewer reached `TASK_COMPLETE` on their
  routes, while the per-task Planner route `astra` stayed unqualified because the run never launched
  it and the Plan Reviewer's MiMo route stays excluded by the owner decision (#2057 E).

- The terminal's `/runs` and an uncached `/open` now read project and global
  listings asynchronously in the existing bounded read pool. Keyboard input
  stays available during slow discovery, and numbered `/open` choices stay
  fixed until the person inspects `/runs` again (#1882).
- Switching between questions preserves the restored chat's prompt and draft;
  cleanup from the question being left no longer erases that saved context
  (#1882).

- Attention rows keep read state and event type visible before long task titles
  at narrow terminal widths. Integrated qualification also covers three projects
  and two viewers sharing acknowledgement and refusing stale approval (#2070,
  #1886).

- Independent reviewers receive complete, snapshot-matched source text for small approved file
  scopes, avoiding repeated source-discovery calls. Larger or uncertain scopes keep ordinary
  filesystem inspection; checks, approvals and independent completion review remain required (#2113).

- A quota switch that moves a role onto another way of paying now says so (#2061). When an automatically
  selected run's stopped model is replaced by one the permitted set says bills differently (a subscription
  route replaced by a pay-per-token one), its `route_assignment` record carries `billing: {from, to}`,
  shown in the status view's `route_assignments`, and the printed switch line ends
  `; it bills pay per token where MODEL was subscription`. Before, the line named only the new account.
- Automatic model selection now runs only on an OpenCode version the provider conformance matrix
  qualifies (`tools/provider-matrix.json`: `known-good` or `accepted-for-ordinary-runs`, #2060). On a
  version with no such row, a new run prints why and keeps the fixed default routes, and an explicit
  `--auto-select` refuses before any model call. Before, selection chose routes on any installed version,
  including one the conformance suite had never run against. `autocode doctor` already showed the row.
- Requirements, Planner and Plan Reviewer launches on the default idle limit (300 s) run under a
  600-second floor, as MiMo routes already run under 900: those stages return one report and emit
  nothing before it, and on the 2026-10-20 vague-brief trial every Planner call for a large plan
  took 300 to 340 s, three were killed at 308 to 310 s and replayed under the same limit, spending
  every automatic recovery. An explicit `--max-idle-seconds` is used as given, the saved setting is
  never rewritten, the stage timeout still bounds the call and the stop text names the floor.
- Planning on a vague brief no longer stalls on the Planner's own text or restarts after its own
  answer (the 2026-10-20 trial needed a person five times and reached no plan). A revision that
  answers the Plan Reviewer's blocking concern may reword or drop a constraint, or replace a
  permission boundary, that the Planner itself wrote (it repeats none of the person's sentences)
  while nobody has approved or edited the plan, declaring the change as its own; the approval
  screen shows the change with the concern that asked for it. A technical question AutoCode settles
  with the Planner's default continues the reviewed planning cycle instead of opening a new one:
  the spent review calls, the first review and the revision are carried, the re-review is told
  which answers were AutoCode's, and with one call left the final review decides on the redraft.
  The allowance grows by one call, once per run, only when the final review's own question spent
  it, and never past a limit the person set. The Planner is told to state which files a milestone
  owns in `affected_paths`, never as a constraint or a permission boundary.
- Under a money cap (`--max-cost-usd`), every attempt of a job that already ran in the run is priced before
  it launches, from that job's last attempt's tokens at the saved route's rate, and the run stops as
  `PAUSED_COST_LIMIT` before an attempt projected past the cap, naming the job, the route, the projection and
  the cap to resume with, as a climbed attempt already was. Before, only climbs were projected, so a run could
  cross its cap by a whole stage ($1.81 to $2.96 under a $2.00 cap) and pause only afterwards (#2019).
- A small clear brief's reduced Plan Reviewer effort (one rung below the configured one, #1564) is fitted to
  the variants the reviewer's model declares, and the move is recorded in `effort_fits`; a `--single-model`
  run keeps the model's listing for that. Before, `--single-model zai-coding-plan/glm-5.3` stopped once at
  the Plan Reviewer on a derived `medium` the model does not declare (#2020).
- The regression-proof gate treats a plain text or data file in a greenfield base (`.txt`, `.csv`,
  `.json`, `.yaml` and the like, read by the deliverable and never run) as it treats a manifest: when no
  nonempty file in the base is code or a test path, the base holds no behaviour to preserve and the gate
  does not stay closed on it. Before, a chat task that shipped its own input beside the README could pass
  every criterion and never complete (#2036).
- In the terminal chat, `/connect NAME use`, `/mode` and `/engine` typed at the first
  "What do you want done?" question now apply to the chat started there, as they already did
  for `/new`; before, that first chat ran on the default connection and mode while the header
  named the chosen ones (#2009).
- A configured Kilo connection (a TOML whose command runs `kilo`, such as a copy of
  `kilocode.toml` with other roles) now launches as the bundled `kilocode` does: with the
  activity plugin, the output cap, the native variant check and the read-only checker policy.
  Before, it ran as a plain command provider and got none of them (#2010).
- In the terminal chat, the tab Ctrl-T opens can be left with Ctrl-←/→ or `/tab` before its
  request is sent; before, it answered "Tabs switch once a chat is open" and only `ctrl-d`
  led out of it. Tabs with an open chat keep the rule that a working step is never left
  unwatched (#1997).
- The terminal chat no longer ends with `[Errno 1] Operation not permitted` when it stops a status
  read whose process group the kernel will not signal (macOS, once the group's leader has exited):
  the read's own child is killed instead and the chat carries on (#2011).
- The terminal chat no longer ends when the core refuses an action it took (another autocode
  runner holds the run lock, a stale token): it says what was refused, reads the run again and
  asks again, keeping the plan on screen; before, the pane, the plan and the draft were gone with
  no retry (#2000).
- A stage whose request no longer fits its model's context window, or whose conversation the provider
  compacted, stops as `PAUSED_CONTEXT_WINDOW` and says which: it names the provider's error or how the
  compaction was seen, reports the size of the stage's last completed request, refuses the report written
  from the summary, and asks `route-ROLE` for a model with a larger window — instead of pausing as an
  uncertain provider failure. An ordinary Kilo launch sets `compaction.auto` to `false`, so a stage is
  never silently summarized (#1942).
- `--single-model` and a route answer at a model stop fit each role's effort to the variants the
  named model declares: a runner-chosen effort the model cannot serve moves to the nearest declared
  one (said and recorded), an effort a person named refuses with the flag to change, and Kilo's
  variant refusal names that flag; before, the run stopped at the first launch and roles such as the
  Analyst had no flag at all (#2006).
- A workflow job stopped on quota, a refusal, an output limit or a timeout names its model question as
  a command on the status view (`needs.route.command`, beside the retry in `next_command`), and the
  terminal chat takes a model typed at its `Continue? [y/N]` through it; before, the pause asked for a
  model in prose that the chat read as a decline (#2005).
- Terminal attention survives viewer restarts with shared local read receipts.
  F8 or `/attention` inspects current decisions and distinct same-chat completions
  without advancing tasks or losing drafts; empty JSON run inventories remain
  readable and receipt failures stay unknown (#1993).
- `autocode doctor` and `autocode models` no longer refuse a profile route a new run never
  takes. Automatic selection replaces a provider's default route when the plan serves it
  under another prefix, and the checks now weigh the routes the run would actually use,
  naming each replacement; a real gap still fails, with the reason selection could not
  cover it (#1995, #1996).

- Lifecycle status metadata is covered by the public JSON schema and the
  completed-worktree consumer. Read samples may differ while every task and
  checkpoint field remains identical; focused CI keeps both regressions (#1994).

- The final Plan Reviewer can correct acceptance criteria inside its retained-plan
  reference, with the same hash-bound criteria delta a Planner revision uses (changed
  or new rows, removed IDs, corrected proofs), instead of turning a wrong criterion or
  proof into a blocking question and another planning cycle. The correction takes every
  ordinary change receipt and guard; milestones, the technical approach and permissions
  stay with the Planner (#1991).

- The terminal distinguishes viewer read age from the core snapshot sample time.
  Failed reads retain a last-known snapshot, and details show source/checkpoint
  identity without treating polling as work progress (#1990).

- Durable TUI chats can switch tabs during work and keep separate session
  drafts, cursors, history position and log/panel choices. Reopening a tab reads
  fresh status before a decision; unavailable targets remain navigable (#1989).

- Opt-in durable local TUI runs use an existing persistent Linux user service.
  Viewers can detach and reconnect while the ordinary core worker continues;
  repeated Ctrl-C requests only a boundary pause. Launch receipts reconcile lost
  replies without replay, and status exposes additive execution/observation
  metadata. Default owned runs and saved legacy runs retain their behavior (#1988).

- When the Plan Reviewer still blocks a revised plan at its second challenge, the Planner's
  next revision runs once on a stronger model (Claude Opus 5.5 High, or GPT-6 Sol High under a
  Claude reviewer), when the provider serves it, the saved accounts permit it and it stays
  outside the reviewer's family. It shows as a `plan_sent_back` escalation. Under the default
  two-call review allowance it never fires.

- A reconciled OpenCode/Kilo connection-change pause offers its existing explicit
  acceptance action through the public view, with confirmation guidance, so thin
  clients can recover without inventing CLI flags. Uncertain attempts and immutable
  per-role tools retain their existing refusals (#1978).

- Completed runs' core explanation describes their saved outcome and evidence
  inspection instead of calling completion an environment stop or suggesting
  recovery. Legacy `COMPLETE` runs keep the same guidance (#1976).

- The clean-review scenario's source patch applies to its current seed again.
  Its oracle checks trusted input validity and candidate tests before judging a
  report, classifying broken fixtures as harness errors (#1980).

- Terminal chat keeps its last-read status visible while browsing history and
  preserves the viewport when background messages arrive. Verification and
  `/details` separate recorded checks, current delivery, retained work, recovery
  and supplied timings; missing freshness and duration measurements stay unknown
  (#1883 E1).

- The stock `glm-mimo-kilo` scenario profile selects GLM-5.3 High for Requirements
  and Completion Reviewer, matching the bundled Kilo default. Its former explicit
  Medium pins were unsupported and refused before inference (#1983).

- Terminal chat shows existing cost coverage and accounting caps in its side panel;
  `/details` adds token subsets, billing/estimate bases, attempt routes, saved future
  routes and route-change receipts (#1884 F1). Missing usage stays unknown; displaying
  these fields adds no model calls or stage-policy changes.

- Installed pytest console commands qualify their paired Python runtime inside
  the tool boundary instead of blocking Tester before launch (#1971). Modified
  or unknown wrappers still refuse; filesystem and network limits are unchanged.

- Spec-first bug fixes reuse an unambiguous approved test name for its diagnosis
  case instead of requesting a duplicate function (#1969). Both case IDs still
  need proof; guards, legacy jobs and protected test files retain their checks.

- Draft proof corrections can update only the verification method through a bound
  criterion delta, retaining the saved wording and sources exactly (#1966). A
  literal Python suite mistakenly labeled as a test name can lose only that prefix
  before approval; real test/guard obligations and user-protected proofs stay protected.

- A conversation start the launch refuses no longer leaves a new project in the selected
  folder per attempt (#1957). The refusal names the project it created, a retry of the same
  task reuses it while it holds only AutoCode's baseline, and the same request ID launches again.

- Collector regressions run on the interpreter owning the declared test dependency;
  multi-component fake repairs reconstruct historical JSON overlays while preserving
  original failures and evidence, and wrong-collector proof refusals stay covered (#1960).

- Questions at an approved acceptance-test pause use the bounded plan conversation
  and return to the same pause without invalidating the plan or repeating review (#1961).
  Actual or mixed change requests still require replanning and fresh approval.

- Projects with declared pytest-style test functions or classes use pytest for
  automatic acceptance and regression checks even without a pytest config (#1960).
  Explicit commands and unittest collection hooks keep their authority; scoped
  checks retain pinned-base framework evidence instead of collecting zero tests.

- An authorized Builder classification retry on a run whose time, cost or iteration limit is
  already spent, or whose provider transport changed, now pauses on that hold instead of
  recording a failed classification and offering the same retry again (#1949). Raising the
  limit, or `--accept-transport-change`, and resuming launches the same Investigator; no
  classification is spent by the stop. A run saved before this fix
  gets back the classifications those stops recorded, so its retry is offered again.

- Planning revisions can retain all unchanged acceptance criteria with one exact
  contract reference and return only changed/new rows and explicit removals (#1958).
  This avoids retranscribing every criterion hash and the resulting report repairs.
  Stale identities, unauthorized edits, independent review and plan approval remain checked.

- Terminal status polling preserves complete valid long-run histories instead of
  rejecting responses above 2 MiB (#1951). Before the first view arrives, the
  terminal shows that it is reading status rather than claiming a worker is running.

- Every Kilo stage, the Builder's included, starts in a fresh session, as Claude Code and
  direct-API stages already do (#1939). A resumed Builder session grew stage after stage until
  Kilo compacted it on a context overflow, which failed the stage. The stage record names the
  `fresh_session_reason`; a same-stage correction still resumes its own session.

- Discovery and revision report repairs receive the current exact criterion-reference
  inventory, so unchanged criteria can be retained without copying whole rows again (#1946).
  A superseded author draft keeps every value inline as exact changes from the complete
  latest draft, avoiding repeated full plans without requiring repair tools or file access.
  Stale-reference checks and final-review approval requirements stay enforced.

- Attached terminal chats consume keyboard commands while an external worker runs,
  and request only its named boundary pause (#1880). Current and previously opened
  chats use bounded asynchronous status reads, including at questions (#1882 D1).
  Status failures remain visible; unchanged idle screens no longer redraw on a timer.

- Kilo launches verify explicitly selected reasoning variants against the selected
  model's native catalog before inference (#1940). Unsupported saved variants now
  require an explicit correction instead of silently using the provider default.
  New bundled Kilo GLM routes use its supported High level; saved routes stay unchanged.

- Restricted Kilo stages retain the selected agent's custom prompt while keeping
  AutoCode's tool restrictions and fresh-session boundary (#1935).

- A callback socket closing after a verified clean stage deadline retains the timeout result
  instead of reporting unknown process cleanup (#1920). Missing or uncertain cleanup, armed
  ownership failures and unsuccessful keeper exits remain blocked; this grants no retry.

- Exact Investigator retries receive links to the matching failed attempt's prompt and evidence
  instead of losing that recovery context and repeating the investigation (#1902). Links are
  unverified leads only; source/configuration admission and fresh reproduction checks stay required.

- Investigator handoffs retain the configured validation command for later Builder/Tester work
  instead of offering it as an investigation command (#1902). Reproduction and diagnosis remain
  required; broader baseline suites no longer compete with the investigation report. At a stopped
  job timeout, naming the current model explicitly can keep that route and expose its exact retry
  through the TUI without forcing a model substitution; all saved limits and usage remain intact.

- Fully stopped workflow-job timeouts can offer the existing token-bound model answer for that
  failed role, preserving other roles, effort, limits, usage and history (#1907). A named model
  launches nothing and rotates the exact retry token; an unchanged retry remains available.
  Rejected configuration changes at a stopped job leave its saved settings unchanged.

- Idle run boundaries without a retained stage or runner check now name the existing advance command even though they have no pause category,
  so terminal and editor clients can continue after Requirements approval (#1901). The Investigator
  receives its saved stage window and prioritizes a minimal reproduction before expanding source
  exploration, with the existing evidence and deadline checks retained (#1902).

- The Tester's pause (`PAUSED_ACCEPTANCE_TESTS`) now offers the `--feedback` action it already told the person to
  use (#1869). The core refused it there; it now restarts planning from the pause, as at a planning-budget pause.
  The status view's `chat.input` offers the feedback message, `next_command` is `--feedback 'WHAT TO CHANGE'`
  instead of a `--resume-paused` that reruns the same Tester on the same plan, and `needs.guidance` says what
  feedback and a resume each do. `needs.kind` stays `resume`.


- `autocode unattended` now refuses `--skip-requirements`, `--skip-to-build`, `--allow-uncontained-tools`,
  `--accept-source-edit` and the companions of a recovery (`--grant-recovery`, `--job-retry-token`, `--resolver-token`,
  `--revise-protected-tests`, `--submit-repair`, …), which it previously let an agent pass although they decide for
  the operator. `tools/autocode_decision_flags.py` classifies every parser flag as a decision or a start flag, and
  `tests/test_decision_flags.py` fails on an unclassified one (#1870).

- Requirements is the first stage of every run and every follow-up turn, and its report names the job, so the
  separate Job recognizer call no longer runs on a flow that has Requirements (owner decision 2026-10-22, #1860).
  A plain CLI bug fix goes from Requirements to the Investigator, a review to the Code Reviewer, a question to the
  Analyst; "Fix them." after a review is named a build from the earlier turn's context. `--workflow build` still
  runs Requirements; another pinned kind starts its own first stage. Adaptive planning no longer skips
  Requirements for a request judged clear (`planning_depth.requirements_skipped` is `null` on new runs); every
  adaptive run's Requirements makes that clarity judgment, as a chat's has since #1846. The v2
  planning flow and runs without a Requirements role keep the recognizer.

- A chat's Requirements names the job it understood, and the chat starts that job without a separate recognizer
  call (#1846). A question goes straight to the Analyst: its handoff no longer needs invented acceptance tests, so
  there is no report repair, and the chat no longer asks twice to "prepare a plan" that never comes. A review, a
  design or a bug fix is confirmed with wording that says what approving starts. In an adaptive chat
  Requirements also judges the request's clarity, so a small clear brief keeps its reduced plan review.
- Every chat starts with the Requirements conversation (owner decision, 2026-10-22): `autocode modes list` now
  offers only `full` (`--joint-planning --spec-first`), and the `quick` mode, which sent a clear request straight to
  the Planner, is gone from the terminal chat's `/mode` and the VS Code Run mode chip. A new chat whose saved choice
  is `quick` starts in Full instead of failing; `autocode chat --mode quick` names the modes left. The
  `--adaptive-planning` flag itself is unchanged.
- Automatic model selection runs a GLM 5.3 Tester at medium effort, not high (owner decision, 2026-10-21). On
  ladder-07 on Kilo the Tester at medium took 242 s with 14.9k reasoning tokens, against 269-468 s and 23.1k at high
  in the day's earlier runs, and the task passed 8/8 with no send-back. A GPT-6 Sol Tester and the fixed default
  routes keep high; `--sol-reasoning-effort` still sets either.

### Added

- A manual-only disposable Linux guest qualification harness for literal reboot and public
  no-replay evidence. Preparation and pure oracle checks do not claim that #1881 C4 or #1886 S05
  has passed; actual guest execution remains unverified.

### Fixed

- The terminal chat no longer prints the Requirements question a second time after the person has already
  seen and answered it. The direct prompt's echo was dropped while the public copy of the question was still
  being written, so the finished message looked new on the next poll. The echo now waits for the finished
  message.
- A Builder on Claude Code can cite the command it ran as a bare `event:` in `evidence_refs`, and the
  runner binds it to the stage's latest completed command with a known exit code, as a Validator check's
  bare `event:` already was (#2253). Claude Code stages never see the `item_N` IDs the runner assigns,
  so the old prompt ("cited as `event:item_N`", "never invent event IDs") left the Builder no way to
  cite a command without a report repair; a guessed ID is still rejected. On the 2026-10-27 live
  `bugfix-trivial` run that cost a Resolver pass and a 16 s repair stage.

- A new task that stops before its run is first saved (for example, when the coding-agent CLI is not
  on PATH) now removes the task worktree and branch it had just created, and says so. Before, every
  retry left another `.autocode/worktrees/` checkout and `autocode/*` branch that `autocode runs` could
  not list and `autocode clean-worktrees` would not remove. A worktree holding a saved run, a change or a
  moved branch is kept.

- An exact runner-supplied command event can now fill omitted check command and exit fields during
  report repair (#2113). Conflicting native event identities, unknown references, bare placeholders
  without a command and mismatched exits still refuse; repeated updates of one native operation remain
  usable. Tester and repair guidance now describe the same evidence rules.

- Acceptance writers now hand provider-local blocked integration tests to the existing core scratch runner instead of calling the requirement untestable; per-test red/guard qualification remains mandatory (#2161).
- Kilo write stages now use a single-session tool policy, preventing subagent delegation that would invalidate the stage's evidence while retaining operator restrictions on coding tools (#2163).

- A stopped acceptance-test writer now exposes its own token-bound model choice, including safely bound retained timeouts. Selecting a model preserves the other roles, usage and plan and still requires a separate retry (#2158).

- A person can now change a failed job's total time/accounting caps using its exact retry token
  (#2156). The job stays paused, usage stays recorded and a fresh token is required for a separate
  retry. Stale source, mixed settings and changes to other limits remain refused.

- Acceptance-test prompts reference exact duplicate case text inside their complete inline approved
  contract (#2153). Case IDs, test names, proof kinds, source quotes and all permissions are retained;
  repair inputs remain self-contained. This reduces prompt size without skipping tests or stages.

- Planning no longer treats documentary `file:line` labels or prose references to known source
  records as product values that acceptance criteria must repeat (#2150). Source quotes stay
  intact; actual numeric limits, output literals, file-scope constraints and negations remain checked.

- Malformed planning reports now admit and name corrective chat input. Terminal and full-screen
  chat accept that text at the Resume prompt instead of treating it as a decline (#2147).
  Corrections still require a fresh plan approval and cannot release operational or execution holds.

- A probe that passes whatever the code says no longer counts as evidence (#1769). A probed claim
  from the Analyst, a probed concern from the Designer and a probed conflict from the Design Reviewer
  now carry a `counterexample`: one edit (`file`, `find`, `replace`) that makes the claim false. The
  runner makes it in a second scratch copy and the probe must fail there. A probe such as
  `python3 -c 'assert 2 + 2 == 4'`, which exits 0 either way, is refused and goes to the usual bounded
  report repair instead of being shown as a check that ran. The Analyst's answer now lists claims
  without a probe as read from the source, not run.
  The bug Investigator's reproduction probe and the stuck Investigator's probe now follow the same
  rule: a reproduction names the edit that removes the bug, and a diagnosed cause names the edit to a
  cited file after which the file no longer shows it.

- A report rejected for its format on a configured Kilo connection (a provider TOML whose command
  runs `kilo`, such as `kilo_glm_mimo`) now goes straight to the full report repair, as on the bundled
  `kilocode` provider. Since #2010 these connections get the Kilo launch, whose read-only boundary
  drops the session, so the one-turn same-session correction was spent with no context.

- The qualification table can now record that a role must never auto-select a model, with the
  decision as its evidence, and both selection and `tools/qualify_routes.py` honor it: a stock
  profile that pins the model still reaches it, but the run qualifies the other roles and names
  the exclusion instead of adding an entry (#2121, #2057). The Plan Reviewer's MiMo v2.6 Pro
  exclusion restores the owner's 2026-10-20 "never choose MiMo to review a plan" decision, which
  the qualifier's documented command silently reversed by adding it back at rank 0.

- `tools/qualify_routes.py` keeps the qualification table's top-level fields when it writes, so a
  merge no longer deletes the table's `basis` policy text: it rebuilt the file from the parsed
  `as_of` and `roles` alone, and only a real write (not `--dry-run`) showed the loss (#2117, #2057).

- `tools/qualify_routes.py` counts only attempts that finished with accepted output: a rejected,
  timed-out, interrupted, still-active or report-repair attempt no longer qualifies its route, so a
  reviewer route that went silent and was replaced by a fallback is not written as qualified (#2114).

- `tools/qualify_routes.py` now credits each attempt to the route slot its stage launched, so a
  finished live run can qualify its Plan Reviewer and Completion Reviewer routes (#2114, #2057).
  Those stages run under the `astra` unit; the attempt rows named the unit, so the two slots were
  reported as never attempted while the Planner route `astra` never launched was added instead.

- The model access preflight now proves each route under the role that owns it, and a tool refusing
  the level there pauses as `PAUSED_TRANSPORT_UNVERIFIED` with that role's own flag instead of an
  unclassified error reported as `PAUSED_INVALID_OUTPUT` with the Tester's flag (#2108, #2057). This
  is the case the pre-fit cannot decide: a manually given level on a model with no declared listing,
  where only the tool's own variant check can answer.

- A feature follow-up no longer inherits an instruction to re-investigate the previous turn's
  reproduced bug. Current-turn bug invariants still require independent checks. Kilo/OpenCode
  receipt guidance now agrees with the Tester's supported command/exit-code hydration, avoiding
  redundant command transcription without relaxing receipt authentication (#2113).

- The model access preflight now fits every role's effort before its first probe, as the launch
  boundary does (#2104, #2057). A person-named level its model does not declare pauses as
  `PAUSED_EFFORT_UNSUPPORTED` with the role's own flag before any probe spends, instead of probing at
  the unsupported level until Kilo's own refusal surfaced as `PAUSED_INVALID_OUTPUT` with the Tester's
  flag. A runner-chosen level is probed at the variant its model declares, the one a stage will launch.

- Follow-up test generation keeps unchanged tests from a completed turn under their approved
  fail-first meaning instead of reopening planning when they pass current code. Fresh proof checks
  inherited regressions against the original reference and new guards against the completed source.
  Exact runner-only corrections can recover through ordinary Resume; review charges, saved allowances,
  approvals and history remain intact, including renewed default limits on same-turn restarts (#2096).

- A direct connection's OpenRouter provider defaults to `https://openrouter.ai/api/v1`, its real API
  base, and VS Code's Connections view runs a multi-provider connection's `add` action in a terminal
  as it does `setup` (#2055).

- Inherited Plan Reviewer charges count once when a small plan's review policy activates. Saved false
  budget pauses offer ordinary Resume when the existing allowance still has room, preserving review
  limits, reported usage, history and plan approval (#2087). New unapproved model interpretations of
  feedback also remain correctable without treating them as the person's own words (#2085).

- Manual C4 qualification composes preparation and reboot request deadlines within the existing
  global cap, retains bounded fixture progress, and collects read-only diagnostics before cleanup.
  Both failed qualification attempts remain failed; literal reboot proof is still pending (#1881).

- The manual C4 qualification reports named admission errors before setup and explicitly uses TCG
  for its disposable Linux guest. It retains accelerator/phase timing and never publishes PASS
  before guest cleanup succeeds; literal reboot proof remains pending (#1881, #1886).

- At a single option question, the terminal chat sends a typed correction through the core's offered
  feedback action with its existing fresh-context checks. Exact/default/numeric choices remain answers;
  no feedback action is invented when the view offers none. Corrections no longer require leaving the
  TUI for `--feedback` (#2078).

- Every role's stage launch fits its reasoning effort to the variants its model declares through one rule
  (`autocode_route_fit`), so a derived tier — the Plan Reviewer's reduced depth, the Builder's light first
  attempt, an escalation rung, a route answer or `--single-model` — no longer reaches a variant the model
  does not serve (GLM 5.3 declares low, high and max, not medium). A runner-chosen level moves to the
  nearest declared variant and the move is recorded in `effort_fits`; a level a person named is refused at
  the launch with the flag that changes it; a model with no listing is recorded as `unverified` instead of
  guessed. The access preflight now probes every billing-routed saved role at its own level, not only
  automatic routes, and a passing probe certifies that level for later fits (#2057).

- An approval from an earlier turn no longer makes new criterion IDs in the current draft count as
  approved. The Planner can correct its new examples while retaining their source quotes; criteria
  actually approved or edited by the person, proof selectors and incomplete legacy history stay
  protected. The final revised plan still requires approval (#2076).

- Planning, independent plan review and report repair now name the saved repository regression reference.
  A same-chat follow-up does not reset that commit: an existing regression may pass in today's fixed
  workspace and still fail on the original reference. The guidance retains fresh runner proof and
  exact-plan approval instead of requesting duplicate tests or historical-proof reuse from that fact alone (#2074).

- Requirements scans an identical follow-up source once when it is both the current task and saved
  feedback. Planning and repair checklists and missing-coverage diagnostics no longer repeat those
  obligations; the source journal and all distinct requirement checks remain intact (#2072).

- The channel bridge daemon takes its single-instance lock through `autocode_util.lock_file`, so the bridge
  loads and runs on native Windows. Before, a module-level `fcntl` import failed at load there (#2035).
- A base whose prose is a non-Markdown document in the design workflow's directory
  (`docs/design/feature.rst` or `.adoc`) is no longer admitted as a document-only base, so it needs the
  ordinary passing-test evidence again. The wider data-file list added for #2036 admitted `.rst` and `.adoc`
  anywhere; the design workflow writes Markdown, so a document in another format there is not its output.

- A Requirements report that copies a coverage-checklist sentence exactly, as its prompt says, is no longer
  refused as citing text the brief does not hold. The checklist adds a period to a Markdown heading and
  shows a hard-wrapped sentence's line break, which models write as a space; the quote check now maps such a
  quote back to the brief's own characters and saves that text. A quote no source holds is still refused,
  but the refusal now names every such requirement at once, shows the closest sentence in the brief, and
  says to copy it exactly or remove the requirement. Before, each repair repeated the same quote and the
  run paused as invalid output again on every resume.
- The Completion Reviewer no longer runs again and again on an unchanged source after the AutoResolver's
  diagnosis was answered. A repeated block on an already-diagnosed source now holds the run (`RESOLVER_PENDING`,
  with a stop reason naming the existing diagnosis) instead of scheduling the same review without limit, and a
  regression proof of the current source that failed once is replayed once before the Completion Owner reviews,
  so a single flaky test does not drive rework. See `docs/bugs/2026-10-25-repeated-completion-review-after-diagnosis.md`.

- Every place that judges whether one model may check another's work now uses the one family rule in
  `autocode_model_identity`. The Plan Reviewer stream fallback, the run scorer's independence check, the
  model catalogue's suggestions and the stuck-job Investigator's route each kept an older copy that grouped
  only GLM and MiMo, so the fallback could let GPT-6 Sol take over reviewing a plan GPT-6 Luna drafted, or
  one Claude route check another's build, while `score_autocode_run` still reported the run as independent.
  All four now agree with dispatch: GPT tiers are one family (2026-10-20), Claude is one family, and a gateway
  spelling of a model is the same model.
- Stages on `anthropic/claude-sonnet-5-5`, `anthropic/claude-haiku-4-5` and `xiaomi-token-plan-sgp/mimo-v2.6-flash`
  are now priced (`tools/autocode_prices.json`, as of 2026-10-25). Live runs recorded their tokens with no rate, so
  the run's spend left them out and a `--max-cost-usd` cap could not count them. The two Claude ids take the rates
  of the rows they alias; MiMo 2.6 Flash takes Xiaomi's pay-as-you-go price.

- A permission question now says so before the person answers when none of its choices can do anything.
  A greenfield run's completion gate offered "complete base-side collection evidence runner-side" as an
  equivalent outcome, and nothing in the runner implements it: choosing it recorded the decision, popped the
  request, resumed execution and spent two more validation rounds returning the same findings. A choice
  bound to no setting now states that it changes nothing and names the actions that do — `--feedback`,
  `--close-finding`, `--edit-goal` with `--approve-goal` (#2041).
- A new chat no longer reports its own start as a status outage. `--status` exits 2 only while the run
  being created has no state file yet, and the client called that "Status unavailable … Last view is
  stale", followed a second later by "Status available again". Only those two lines wait for a run that
  has answered a read before; the tab state and the observation still record every failed read (#1998).
  Its working line and `/details` also stopped claiming a snapshot was retained before any read showed
  one, which a cold start reaches in its first second.

- The live agent log shows one line per kind of opaque streaming receipt, with the parts it stands
  for, instead of a row per part. A backend that sends no reasoning text filled the pane with identical
  "text unavailable" lines and buried the tool calls `/live` exists to show (#1999, #2002).
- Ctrl-← and Ctrl-→ switch chat tabs on terminals whose terminfo names no extended key, such as tmux's
  `xterm-256color`. They were recognised only by terminfo name, so the raw Escape tail fell through and
  the composer filled with `[1;5C` instead of moving tabs (#1997, #2001).

- A number typed at a pause's yes/no that only *prose* tells the person to raise no longer vanishes. The
  time-limit pause reached through an AutoResolver hold names a plain `--resume-paused` and advertises no
  `--max-seconds` entry, so typing the total it asked for was consumed as a decline and the run stayed
  paused while the person believed they had raised the bound. The chat now says the number set nothing and
  names the command that would: it still takes no action the view did not name (#1950).
- Time a run spent waiting for the person is now measured. Each question, plan approval or other request a
  person answered counts from when it became answerable to their response, under `efficiency.time`'s `waiting`
  category, so it is kept apart from execution time. The category had no source before (#1867 S1d).
- `cost.breakdown.by_billing` groups a run's spend by how each stage's model bills (subscription, pay per token
  or unknown), so an API-equivalent estimate for a coding-plan stage is no longer indistinguishable from a
  metered one. The provider billing table moved from `model_catalogue` to `autocode_role_route` so the cost
  view can use it; it is still one table (#1867 S1b).
- Cost per accepted task is no longer always unknown on coding-plan routes. `efficiency.unit_metrics.spent_usd`
  divides the run's own spend (`cost.spent_usd`: reported where the provider reported, else the dated-table
  estimate) by the accepted deliveries, and stays null with a reason while any stage is unpriced, partial or
  running. `reported_usd` is unchanged (#1867 S1c).
- The status-view schema now requires the eight fields the view always returns (`workspace`, `run_dir`,
  `planning_depth`, `pause`, `package`, `tool_containment_backend`, `changes_by_criterion`, `dev_server`), so
  removing one fails the JSON contract test, and a new always-present field must be added to the schema
  (#1867 S1f).
- A price table update no longer rewrites what earlier stages cost. A row in `tools/autocode_prices.json`
  can keep the rates it replaced in `history`, each with the date it stopped applying, and every stage is priced at
  the rate in force on the day it finished, so a resumed run's spend and money-cap decision stay as they were.
  `cost.breakdown.price_bases` lists the rates used (#1867 S1a).
- `efficiency.repeats` in the status view now counts a check the runner executed again although the same
  obligation already had a completed receipt, with its reason (a changed environment, a failed earlier attempt, or
  an approved command that always runs). The `repeat` observation was defined but never recorded, so the count was
  always 0. The scheduler's execute record names the earlier attempt as `previous_attempt_id` (#1910).
- A Completion Reviewer can no longer mark a task complete while its own source verdict says FAIL or names an
  uncovered sentence of the original request. The verdict was required but never read, so the reviewer's own
  finding was dropped. Such a report now goes back through the bounded report repair for one consistent answer:
  a PASS verdict, or a REWORK that covers the sentences. A report for another contract or task still pauses as
  before (#1909).
- The documentation no longer says a quota stop never switches models: on a run that chose its models
  automatically, a quota, content-filter or output-limit stop moves the role to the next qualified model on
  its own and asks only when none remains, as it has since #1426. The `--adaptive-planning` help and CLI
  reference no longer promise the Requirements skip #1860 removed, `docs/cost-reporting.md` points to the
  comparison rates where they are, and the status-view schema now lists eight fields the view already
  returned, including `planning_depth` and `pause` (#1905).
- A run paused with a repeated failure whose report repair is spent no longer tells the person to authorize an
  inspected retry that its own gate then refuses. A resume now reconciles the repair first: an exhausted or
  unlaunchable one is archived before the unchanged-failure hold, so the retry that hold names is admissible on
  the next resume, and a repair with an attempt still queued dispatches its report-only repair instead of being
  held as though the resume restarted the failed work (#1899). Rejected reports stay in the archive, and neither
  path authorizes a fresh execution attempt.
- A bug fix no longer stops at `PAUSED_ACCEPTANCE_TESTS` when a `test:` case its own plan added already works. That
  case goes back to plan review relabelled `guard:`, as in a build; only the Investigator's reproduction passing
  today still pauses the run, with a message saying the diagnosis and the code disagree (#1868).
- A brief that names the report it fixes ("Fix issue #474 ...") no longer dead-ends plan finalize: the source-trace
  audit no longer treats an issue or pull-request reference as a value the criteria must repeat, while a value
  stated beside it ("process exactly 7 rows") still has to survive (#1874).
- A run paused with a repeated failure no longer names `--retry-failed-stage` while the attempt or report
  repair behind that pause is unreconciled, which its own CLI refuses ("Reconcile the active attempt or pending
  report repair before authorizing a retry"). The pause now says why the retry waits, in `needs.retry_unavailable`
  carrying that same reason, instead of dropping the retry and implying a resume answers it (#1897). One
  predicate (`autocode_recovery_limits.repeated_failure_retry_refusal`) now feeds the CLI's gate and both command
  projections, replacing the hand-written set behind the view's naming, which disagreed with the gate about
  `uncertain_artifacts`: refused by the CLI, still named by the view. The resolver's own advice keeps its separate
  and broader key set for now — a divergence that predates this fix, not one it removes.
- A Validator report that passes its own checks but leaves the declared human-review rows unverified without the
  proof completion requires is now rejected at that gate, and the bounded report repair names the gaps to fill
  without rerunning a command or inventing an approval. Previously it was accepted, completion then refused to
  offer the person's review, and the run deadlocked. A completion-gate pause raised while applying an accepted
  result now pauses under its own status with the gate's reason, with no failure record and no Investigator; the
  report is set aside so a resume starts a fresh attempt instead of re-applying it against the same gate (#1851).
- A new human follow-up gets its own default planning-review allowance while preserving cumulative usage
  and explicit user-selected bounds. Inherited criteria can be retained by hash instead of paraphrased,
  and default-flow final reviews return a hash-bound verdict rather than copying the full plan again. Ordinary
  contract, source, dependency, permission and user-approval checks still run after expansion (#1853).

- The CLI's usage line names the command that was run on Python 3.14 too, where argparse otherwise names how
  Python started (`python -m unittest`); `tests.test_cli_surface` failed on every Python 3.14 checkout.
- A PASS Validator report that cites a nonzero check is no longer sent to bounded report repair, which cannot
  rerun a command or change an exit code. The rejection names the failed check and the exit-0 rule, so the stage
  runs again with a successful execution instead of spending two guaranteed-futile repairs (#1852).
- The terminal chat no longer repeats a stage's progress line when it only flips between "working", "finished"
  and "Next:" with unchanged counts. A stage's first line is said once; the next is when its counts or the stage
  itself change (#1844).
- A malformed original-brief observation in an otherwise non-blocking first plan review now goes back to the Reviewer through bounded report repair, instead of sending the Planner through an unnecessary revision and another review. Duplicate, unknown, missing and invalid proposal bindings remain rejected; genuine draft errors still go to the Planner (#1839).
- A planning revision no longer loops between the reviewer's blocking concerns and a protected draft contract.
  The Planner may correct or drop its own `agent_proposed` assumption without declaring a permission change, and an
  unapproved criterion it may reword may correct its unmarked verification in the same revision; a runner-checked
  proof still cannot become a plain command. Text saved from the person's answer, feedback or delegation stays
  theirs: rewording or dropping it is refused and, even in an unapproved draft, becomes one question naming the
  proposed text (or that the assumption is dropped) instead of a generic pause and report repair. A resumed stage
  now carries the rejection it is resuming from in its handoff, and the planning prompts state the same allowances
  the guard enforces instead of telling the Planner to ask the person about its own draft wording.

- The terminal chat live log now shows an honest streaming activity indicator when Kilo supplies hashed progress without prose, instead of claiming no agent is working. Actual prose replaces the indicator when supplied; hashes and tool output stay out of the display, and supervision checks remain enforced (#1837).
- On a finished run, a saved step's `restore` is no longer explained as "Pause after the current step": nothing is running, so the status view's `code_checkpoints.blocked_reason` now says a finished run cannot go back to a saved step, that its steps can still be compared, and that a follow-up changes the code. The terminal chat's `/undo` shows that text (#1643).
- The chat's recent steps no longer show "Orchestrator attempt stopped" on a clean run. The Orchestrator's dispatch launches no process and records no exit code, and that absence was read as a failed exit; a model stage with no exit code still reads as stopped (#1643).
- A small-brief run no longer stops before the final review of a revision that answered the Plan Reviewer's blocking
  concerns (#1815). Under the two-call total (#1564) a live `--skip-to-build` run spent both calls on challenges, the
  Planner revised, and the run waited for a person with nothing left but that review. The runner now allows that one
  final review once per run; a limit you set (flag or config.toml) is never raised. The planning-budget pause now
  names `--resume-paused --planning-review-call-limit N` and `--feedback TEXT`, and its question says "a limit
  AutoCode set by default" instead of "internal_default".
- A complete Requirements handoff and its repair now agree on `source_refs` (#1822). A handoff that states
  behaviors or acceptance tests must cite at least one existing workspace file; an empty list is valid only while
  it asks clarifying questions, and runner-owned paths such as `.autocode/` are never citable. The Requirements
  stage prompt and the repair handoff share one rule, and a Requirements repair names the citable workspace files.
  A greenfield repair had repeated only the validator error, then cited an archived `.autocode/` log. The workspace
  inventory no longer lists tool caches (`.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`) the citation check refuses.

- The `greenfield-todo-cli` brief now says the listing's status word is printed inside literal square brackets (`1 buy milk [open]`), as its oracle and its `unbracketed-status` broken variant already required. Before, a model could read `[open|done]` as usage notation, ask, and take its own "bare word" default. The scenario harness also classes a `PAUSED_PROVIDER_UNCERTAIN` stop as a provider failure rather than a safety stop (#1794).
- A planner report that resolves a requirement conflict nobody recorded is now rejected with an error that lists the recorded conflicts, or says none is recorded and `conflict_resolutions` must be `[]`. The report repair sees only that error, so it no longer guesses new requirement-ID groupings until its repairs run out (#1823).
- The terminal chat's startup run picker now lists runs as `/runs` does (#1754): by their request's title instead
  of the run directory name, newest first, and under the number `/runs` gives each one, so a number means the same
  run in both. A finished run in between leaves a gap (`Pick one (1, 3), …`).

- Preparing a launch no longer starts `git` to find the global ignore file (#1630): it reads `core.excludesFile`
  from git's config files, so a launch that is refused before it starts runs no process at all.

- A read-only stage that compiles Python in the workspace no longer pauses the run as a source edit (#1630).
  The exclusions AutoCode wrote to the project's `.git/info/exclude` never reached a provider's snapshot
  repository, which has its own git dir; the runner now names its own excludes file through the provider's
  environment (`core.excludesFile`), for Kilo as well as OpenCode, and the read-only guard and the exclusions
  share one predicate. The user's global ignore rules are kept. `docs/bugs/native-snapshot-excludes.md`.
- Leaving the full-screen terminal chat at its first question (Ctrl-D or `/quit` at the run picker) no longer leaves
  the whole run list on the terminal (#1755). Before any conversation, only what the chat said after its last
  question, such as `Nothing started.`, stays behind.

- A criterion change the Plan Reviewer asked for, which the runner refuses without the person's say, is now the
  person's question at once instead of a report repair (#1630). In a plan nobody approved, when a blocking concern of
  the saved review names the criterion, the refusal becomes the existing rewording question (#1691), naming the
  concern and, when it changed, the proof; `Accept the new wording` lets the next revision cite the answer, `Keep the
  approved wording` sends the Planner back with the refusal's hint. Two live revisions had spent two repairs each on
  this before the Planner asked the same question itself.
- The terminal chat's banner, `/engine` and `/connect` now agree on what new chats use when the user config sets
  `default_provider` (#1756). The banner named the default connection by its id, `/engine` marked OpenCode current
  and `/connect` marked nothing. All three now read one rule (`autocode_chat_setup.backend`): the banner shows the
  connection's label, `/connect` marks it, and `/engine` says new chats use that connection.

- A Linux `kilocode` launch now refuses a Kilo other than the qualified 7.8.3 before any spend (#1739). Restricted
  read-only and planning stages already refused another release, but only when containment ran, after the run had
  started; the version is now checked at the same point a launch would reach the model. macOS keeps any Kilo release:
  it has no kernel shell, so its checkers use Kilo's own read tools and shell with edits denied (#1766), and a pin
  there only refused a newer install for no benefit.

- Switching tabs in the terminal chat (`/tab`, Ctrl-←/→) no longer draws the chat switched to as working when it is
  waiting for the person or done (#1757). The tab kept no state until its status was read again; it now keeps the
  state last read for it.

- A running chat no longer says `Working on your request…` twice in a row when the status view's notice
  briefly clears between stages (#1807). The terminal chat says an identical notice once per turn and still
  shows the progress lines underneath; a different notice, such as a pause, is still said, and the notice is
  said again once the run carries on after waiting for you.
- A multi-line bracketed paste into the full-screen terminal chat no longer doubles each line break (#1758). The
  terminal turned the pasted CR of each CRLF into NL before the chat read it; the chat now keeps CR as typed
  (`curses.nonl()`) while it is open and restores the terminal's setting when it closes.

- Before a chat is open, the full-screen terminal chat's `/new` and Ctrl-T now say `This is a new chat: type what
  you want done.` (#1759). `/new` answered `/new is not available right now` and Ctrl-T `Tabs switch once a chat is
  open`, though the client already had the right reply; `/new` is now offered in the `/` menu there too.

- Typing `autocode` in a new shell after the README's virtual-environment install answered `command not found`
  (#1761). The README and install guide now say the activation lasts for one shell and name the lasting fixes
  (`pipx install --editable .` or the venv's `bin` on `PATH`), and `autocode doctor` has a `path` row that warns,
  without failing, when `autocode` is not on `PATH` and names the directory to add.
- A provider connection that OpenCode marks retryable now gets its one no-effect retry even when it names the loss
  only generically (`Network connection failed`, with empty `code`/`syscall` and no HTTP status) instead of stopping
  as `PAUSED_PROVIDER_UNCERTAIN` (#1797). The provider's own retryable flag, a read-only stage, an unchanged
  workspace and no written files are still required; empty errno fields no longer hide the flag.
- A refused revision of a criterion now says what to change (#1630). Rewording a criterion in a plan nobody approved
  names the request quotes it dropped, and replacing a `test:`/`guard:` proof with an unmarked command says to keep a
  named test and put an aggregate run in its own criterion. Both refusals were right, but said only that a user answer
  was missing, so two live revisions spent two report repairs each and then paused. No refusal is loosened.
- The terminal chat shows every Requirements question, not only the first (#1792). Every question shared one chat
  message id, and the client shows an id once, so a second question stayed hidden behind a bare `You:` prompt and
  the person's reply answered a question they never saw. Each question now has its own message id; the chat prints
  the question its prompt waits on, waits while Requirements is still working on a reply, and repeats the question
  instead of a bare prompt when a reply has not been taken up.
- The terminal chat says a running run's notice (`Working on your request…`) once per state instead of repeating it
  every time the progress line changes, and says it again after it clears and returns (#1798).

- A Planner that declares a rewording while leaving the protected criterion or list entry unchanged no longer
  creates a human wording-choice question (#1791, #1793). Inconsistent change metadata now follows the existing
  report-repair path. Genuine protected wording changes still require the person's decision.

- A recovery novelty hold whose AutoResolver request is not yet published (`RESOLVER_PENDING`) now names the
  scoped retry, `--resume-paused --retry-failed-stage`, as its next command and recovery-card action (#1770). The
  status view offered a plain `--resume-paused`, which only held the run again, and the retry was named only in
  prose a thin client must not parse. The view names it only where the CLI would admit it: nothing unsettled, the
  hold's own status and no different pending decision. The view and the CLI now share that one admission rule, and
  when the retry cannot be given yet, `needs.retry_unavailable` says why instead of implying a resume will do. The
  CLI still rechecks the hold's source, settings and scope binding.

- A first project whose pinned base holds only a README, a manifest and an empty test package can now complete
  (#1783). The preservation gate accepted only the root README.md, Markdown, .gitignore and empty files, so a base
  holding `pyproject.toml` was reported as having no complete passing-test evidence and the run could never finish,
  even though the base had no executable source. A project, dependency or license manifest (such as
  `pyproject.toml`, a lockfile or `LICENSE`) is now accepted when no nonempty file in the base is code or a test
  path, and the proof records that the base has no source to preserve; a base that holds source still needs
  passing-test evidence, and other non-empty files still need preservation.

- Concern-only Plan Reviewers now receive contract-authoring policies as rules to audit,
  with instructions to report concerns rather than return or edit contract fields their
  report schema forbids (#1808). Contract-author prompts and the approval/validation gates are unchanged.

- Routine recovery now uses a bounded failure streak between valid saved provider stages (#1820),
  preserving attempt history and usage. Proven legacy run-wide exhaustion offers ordinary Resume
  without another slot grant; inconsistent history and all independent admission gates stay held.

## [0.8.2] — 2026-10-21

### Added

- A live agent log in the full-screen terminal chat (owner decision 2026-10-21). While AutoCode works, a pane under
  the conversation shows what each running agent is thinking and doing (reasoning, messages, commands, edits and tool
  calls), led by the agent's name, with each parallel Builder under its own; the composer stays usable below it.
  `/live` hides or shows it and `/live NAME|thinking|messages|tools|all|off` filters it. It reads the status view's
  new additive `chat.log`; prose is one bounded, redacted line and tool output is never copied.

- The status view's `cost.breakdown.unpriced` says why each finished unpriced stage has no cost (#1713):
  no token counts recorded, no model identified, or no rate for its model, naming the models without a rate.
  The counts are stages, never a guessed amount, and the known spend and cap are unchanged.

- Chat everywhere (#1731, step 2). In a terminal, `autocode-issue start` and `continue` open the run's chat when it
  needs you (`--chat`/`--no-chat` to choose), and every waiting step they print starts with that chat command. A
  `--goal-file` load says to review and approve it in chat. A project's `autocode runs` (and the terminal chat's
  `/runs`) lists its autocode-issue worktrees' runs too.
- `--skip-requirements` and `--skip-to-build` for an unattended worker (#1731, owner decisions 2026-10-21). The first
  answers each delegable Requirements question with its proposed default, recorded as delegated, and still runs the
  Planner and Plan Reviewer and waits for a person's plan approval. The second also approves the plan once the Plan
  Reviewer has finalized it and goes on to build; the approval is recorded as the person's choice, marked
  `source: skip_to_build` in the status view. A question with no safe default always waits for a person, and without
  `--skip-to-build` AutoCode never approves its own plan.

### Changed

- Every Planner has a shell (owner decision 2026-10-21). On OpenCode and Kilo, planning stages had read tools only, so a
  Planner worked out the exact values its examples state by hand; a replayed one derived IEEE-754 bits for a
  temperature conversion. They now get the shell the read-only checkers already get: the kernel-contained one
  (Seatbelt on macOS for OpenCode, bubblewrap on Linux), which also cannot read the run's private state, or on macOS
  Kilo's own shell with edits denied and any source write put back. `--allow-uncontained-tools` covers planning like
  every other stage. Codex and Claude Code Planners already had one. Planning report recovery still has no tools.
- A `kilocode` run's default stuck-stage Investigator is one of the run's own direct routes, the first outside the
  stuck stage's model family, never the Kilo Gateway's Claude it used to be (#1630). A gateway (`kilo/`) model now
  needs `--investigator-model`; a run on GLM-5.3 and GPT-5.6 Sol without the flag had sent it to the gateway.
- Planning handoffs for the stages that write criteria now split each source into numbered sentences
  (`source_sentences`, ids like `task:0#3`, with the requirement rows each sentence supports), and a criterion may cite a
  whole sentence by putting its id in the citation's `quote` (#1630). The runner replaces the id with the verbatim sentence
  before any check, so saved contracts, the seal and the source log are unchanged. A verbatim quote still works, and a
  span inside a sentence still needs one.
- Replies that only steer a question the runner wrote are no longer sources every plan must quote (#1630):
  `plan` to the Requirements review, and `Accept the new wording` or `Keep the approved wording` on a rewording
  question. They are registered once, by the runner's own question ids, in `autocode_control_replies`; a correction
  typed to the same question, or any answer to a model-written question, is still a source. The reply stays in the
  source log and ledger, and contracts that already quote it keep verifying.
- The runner now names the brief sentences a Planner draft's criteria leave uncited (#1630). The next stage's
  handoff lists them as `uncited_source_sentences`, the Plan Reviewer must raise each or place it out of scope, and
  a revision that leaves one uncited which the review did not excuse is refused before any reviewer call. A first
  draft is never refused. The status view's `planning_depth.uncited_sentences` shows the count.
- The model-access probe costs a few hundred input tokens instead of ~18k (#1744). On OpenCode and Kilo it runs as a
  bare agent with no tools and a one-line system prompt (Kilo 19,053 -> 627 input tokens, OpenCode 17,672 -> 218),
  and the Resolver's own model is probed only before the first stage that runs it. On a small task the probes cost
  $0.26 of $1.31, most of it on GPT-6 Astra.
- The stuck-stage Investigator on OpenCode is GLM 5.3 whenever the stuck stage runs a GPT model, not only GPT-6 Sol:
  GPT models are one family, so GPT-6 Sol no longer diagnoses a stuck GPT-6 Luna Builder.
- The automatic model switch after a quota, content-filter or output stop (#1426 step 9) leaves a model the
  person pinned with `--<role>-model` alone: that stop asks for the next model, as every stop did before the
  switch existed. Automatic selection's own choices still move within the run's permitted set.
- Automatic model selection prefers GPT-6 Luna at medium effort as Builder (owner decision, 2026-10-21; high cost
  twice the time and money on the measured task), then GPT-6 Sol, then MiMo v2.6 Flash
  (provisional), then Claude Opus 5.5 (provisional), then the cheapest of GLM 5.3, MiMo v2.6 Pro and DeepSeek (owner
  decision, 2026-10-20, for pay-per-use companies). On ladder-07 on Kilo the task passed 9/9 with each: Luna medium
  16.3 min for $1.05, Sol 16.4 min for $1.14, Luna high 34.6 min for $2.58 after one Tester send-back, and MiMo
  v2.6 Pro 34-46 min for $1.02-1.07. GLM checks an OpenAI build as Tester and Completion Reviewer.
- OpenAI's GPT models are one family for verifier independence, as Claude's are: GPT-6 Sol no longer tests or
  reviews a GPT-6 Luna build on Kilo, OpenCode or a custom provider (owner decision, 2026-10-20). The Codex engine,
  which reaches only GPT models, still lets different GPT models check each other.
- Claude models are one family for verifier independence. Before, a Claude Sonnet could check a Claude Opus's
  work, or the reverse, on Kilo, OpenCode or a custom provider, because only GLM and MiMo counted as families.
  The built-in Claude Code engine is unchanged: it reaches only Claude models, so its default routes (Sonnet
  producing, Opus checking) still run, and a model still never checks itself.
- Automatic model selection no longer chooses MiMo to review a plan. The Plan Reviewer is GPT-6 Sol when the
  ChatGPT login is connected, then Claude Opus 5.5 (provisional), and with neither the run stops and offers those
  logins (owner decision, 2026-10-20). On ladder-07, GPT-6 Sol reviewed in 43 s with three substantive concerns
  where MiMo v2.6 Pro took 526-890 s and 32-45k reasoning tokens, and twice ran out of output. Qualification
  entries may carry a `preference` that orders a role's models before billing and price. The Completion
  Reviewer, the final gate, likewise prefers GPT-6 Sol when connected, with GLM 5.3 as its fallback.

### Removed

- `autocode program` and `autocode --program-guidance` are gone from master, so every task follows one flow:
  Requirements, a plan you approve, Builders, Tester and review, in one run. A large task gets more milestones in
  that run, not separate workstream runs. The program code, its tests, its guide and its two scenarios are kept
  unchanged on the `archive/program` branch (master at 5ee3435) to bring back if a real project needs them.
  Clean-up still keeps worktrees an older program owns, and agents are still denied `.autocode/programs`.

### Fixed

- A permission answer now resolves the findings its published request names (#1783). The answer read finding ids
  only from the question's `payload`, which no runner question ever carries, so a question that named blocking
  findings closed none of them. It now reads the published request's `finding_ids` as well, which is where the
  validation-only stop puts them, and still accepts the legacy question payload. The completion gate's
  preservation requirement for a first project is unchanged (#1783).
- The Planner's draft-example rule no longer contradicts the unapproved-wording allowance (#1691). It said "other
  criterion changes require a saved user basis", so a live GLM revision that fixed a fixture value at the Plan
  Reviewer's request declared it as an example correction, which the guard refuses for an input change (#1750), and
  spent a 143 s report repair. The rule now says an example correction is only for a stdout result, and that a
  never-approved criterion the Planner wrote may be reworded as a plain agent-proposed change.
- A spec-first run's `plan` reply to the Requirements review is no longer a source every plan must quote
  (#1630). A Planner draft that cited every sentence of the brief failed the Plan Reviewer's source-trace seal
  for leaving the single word `plan` uncited. The reply stays in the run's source log, and contracts that
  already cite it still verify.
- A criterion quoting a hard-wrapped brief is no longer refused as a misquote (#1777). Every live GLM Requirements
  report on ladder-07 quoted a sentence with a space where the brief breaks the line, failed "without quoting it
  verbatim" and spent 100-151 s on a report repair. Quotes now match with runs of whitespace treated alike; any other
  difference is still refused.
- Kilo runs on macOS get past the Builder again (#1766). Since #1739 a read-only Kilo checker required Kilo 7.8.3's
  Linux kernel shell, so the Tester was refused on every Mac. On macOS it now keeps Kilo's read tools and shell with
  edits denied, a source change is put back after the stage, and the run says so; Linux keeps the kernel shell.
- A report repair of an Analyst, Investigator, Designer or Design Reviewer report is now told what a `probe` is
  (#1769). The repair prompt carries the report schema, where a probe is only a string, and a model that guessed
  wrote its own event ID there; the runner handed it to the shell, got `exit 127` and rejected the whole report,
  costing a paid call for an error the model could not read back as a rule. A probe that is a reference rather
  than a command is now refused with the rule itself, before anything is run.

- An adaptive plan the Plan Reviewer approved, but the runner could not install (for example a brief sentence no
  criterion cites), now goes back to the Planner to revise, as outside adaptive planning, with the reason in
  `planning.adaptive.approval_refused` (#1630). The review used to be rejected as invalid output; report repair
  cannot change the Planner's contract, so the run paused with no plan. Successful adaptive reviews retain
  their report in the current planning record so the runner can publish the plan for approval. `autocode capture`
  run from the Investigator's scratch copy also accepts the project's own `.autocode/evidence/...` path its instructions name,
  instead of refusing it and costing model turns.
- A Planner revision that rewords its own unapproved criterion and the way that criterion is verified, backed by
  a saved user answer, is accepted (#1764). The #1691 allowance for the Planner's own wording also swallowed the
  declared change, so the guard refused a correction you had already authorized and the next repair put the
  contradiction back and asked you about it again.

- A Planner's change that declares an example correction (a stdout count fixed by a receipt) is judged by the
  example rules alone again. #1691 let a Planner reword its own unapproved criterion, and that allowance also
  passed a receipt that changed the example's input as well as its output, so a plan could reach approval with
  a new input. The same change declared as a plain rewording is still the Planner's own to correct.

- VS Code chat: while you read older messages, the Latest button now names the step running now
  ("↓ Now: Tester working") and follows each status read, so a stage change is visible without being moved to the
  bottom (#1711). It returns to "↓ Latest messages" when no step is running.

- The Builder's working diff (`checkpoint --working`) offered during a same-chat follow-up no longer refuses with
  "Checkpoint identity or approved plan changed" once the follow-up's plan is approved (#1712). It still verifies the
  saved step's receipt, commit and run; restoring a step still requires the plan approved now.

- On a Codex run, where every model is a GPT model, the model-route advisory reports two roles that a saved
  change made share one model (`gpt-5.6-terra / gpt-5.6-terra`) instead of staying silent because the GPT
  family was already shared. Sharing is read as verifier independence reads it: one model on an engine that
  reaches one vendor, a family elsewhere.
- A cost cap, time limit or milestone budget changed at a novelty hold restated the stop reason with the old
  "Stage costs: ..." sentence (#1728) and the budgets after it kept alongside the new ones, so every change
  added another "Active time" sentence; the restated stop now carries one copy of each.
- Saved-run replay accepts the Builder's recorded effort origin. #1727 began recording `effort_origins.terra`, and
  replay export refused every new recording ("Recording reviewer effort origin is invalid"), which turned
  `tests.test_saved_run_replay_cli` red on master. Replay validation now allows `plan_reviewer` and `terra`, each
  with `user_explicit`, `runner_default` or `auto_selected`, and still refuses any other role or value.
- A small clear brief's plan-review total (#1564) dead-ended planning when AutoCode settled the reviewer's final
  technical question itself: planning restarted for the redraft, but the new cycle carried the spent 2/2, so the
  redrafted plan could never be reviewed and the run waited for a person (live, 2026-10-20). A restart caused by
  AutoCode's own answer now renews the allowance once per run; a second one still stops.
- The low-effort first Builder attempt (#1710) no longer overrides an effort the person chose. A run started with
  `--terra-reasoning-effort high`, `--reasoning-effort high` or a project effort setting ran its first attempt at
  `low` anyway, which is also what had turned master's `tests.test_planning` red. The Builder's effort provenance
  is now recorded like the Plan Reviewer's (`effort_origins.terra`): only the runner's or selector's own default
  is lowered, and a saved run with no recorded provenance keeps its effort. Also annotates the new
  `conversation_only(state)` as `RunState`, which had pushed `tests.test_run_state`'s ratchet one over.
- A vague request no longer stalls at its first step on the default flow. A Requirements handoff that has only
  questions so far (no behaviors or acceptance tests yet) was refused as invalid output unless the run was
  `--spec-first`, and a person had to resume it; it is now accepted on every flow, the run stops for the answers
  with the handoff's own questions, and Requirements runs again with them.
- 0.8.1's keep-awake (#1717) spawned `caffeinate -w PID` as a child. A supervised CLI's keeper (the scenario
  harness's owner lifeline) found it alive at discharge and SIGKILLed the CLI, so every macOS scenario run ended
  `INTERRUPTED_UNGRADED — start exited on signal 9`. The controller now takes the `PreventUserIdleSystemSleep`
  assertion in its own process through IOKit, which the kernel releases when the process exits. No child remains.

## [0.8.1] — 2026-10-20

### Changed

- Requirements conversation reports use a smaller output contract without unrelated test-verdict and command-receipt instructions; the model, session and structured handoff stay unchanged.
- A small approved plan's first Builder attempt runs at low reasoning effort (#1710, owner decision): one milestone
  and at most four files. A small `peek()` feature's Builder spent about 555 of 786 s reasoning at medium. Any
  earlier attempt at the same task (rejected report, failed check, Tester send-back) brings the configured effort
  back. Planning roles, the Tester and the Completion Reviewer are unchanged. `AUTOCODE_LIGHT_BUILDER=0` turns it off.
- A Tester check that cites a capture receipt may give `command` null, and the runner copies the command from the
  receipt and verifies it as before (#1710). The Tester no longer retypes every command, about 9 KB of a 20 KB report.
- Passed tool checks are now reused for an hour within a run (was 10 minutes; owner decision).
- A chat turn no longer re-runs the tool's version, model-list and login checks it already passed in this run:
  a successful check is reused for 10 minutes, keyed by the exact command, folder, environment and tool
  executable, so an upgrade or changed setting checks again. On Kilo this saves about 6 s per follow-up message.

### Fixed

- The terminal chat's Requirements line kept showing the first short reply after a later one arrived, and a
  reply being written showed at most one new piece every 2 s. The newest reply now replaces earlier ones, and
  while it is still changing the chat reads it every 0.25 s, going back to the ordinary pace once it has not
  changed for a few reads, so a model running tools between messages does not keep `--status` running nonstop.
- A Mac that idled to sleep mid-stage stopped a live run for a person: the controller cannot supervise while
  suspended, and the provider connection reset on wake (#1717). A controller driving a run now holds macOS's
  idle-sleep assertion (`caffeinate -i -w PID`, released when it exits; `AUTOCODE_KEEP_AWAKE=0` turns it off),
  and a read-only stage (Requirements, planning, plan or completion review, Investigator) that a retryable reset
  cuts short after read-only tools or partial text is retried once automatically when the workspace is
  unchanged. The Builder and Tester keep their hold.
- Requirements chat no longer scans workspace disk usage before each reply; the copy-space gate runs when planning starts. The estimate prunes run history, caches and sibling Git worktrees before traversing them.
- A question clarifying user intent no longer triggers report repair merely because it retains a stated requirement without citing code. Complete handoffs and explicitly cited source files still require source inspection.

- Answering Requirements' greeting with "Nothing yet, just saying hello." got the honest reply (no goal, no
  question) refused as a handoff without an outcome, repaired twice and sent to the Investigator. In a spec-first
  chat, such a reply now asks the standard "what would you like to build or change" question and waits for you.
- A run stopped with "Cannot snapshot source ... git rev-parse HEAD failed" when the project held an
  untracked nested Git repository with no commit yet (a scratch `git init`). The nested repository is now
  hashed with an unborn head, so edits inside it are still seen; the project itself still needs a commit.
  The controller's own editable-source identity keeps refusing such a repository, so it never binds a cache key to it.

- `autocode` in AutoCode's own main checkout refused to start even when the running `autocode` was
  installed elsewhere, asking for a separate Git worktree. AutoCode now works in place there like in any
  other project; it still refuses only when the running controller is inside the checkout it would edit.

- `autocode` in a project that keeps an old vendored copy of `tools/autocode.py` beside its own `AGENTS.md`
  (idlecampus) refused to start as an "Unsafe AutoCode self-build". A self-build is now recognized only in a
  checkout that also has AutoCode's self-build admission module.
- A planner correcting its own wording in a plan nobody has approved yet (an expected-output literal with
  one space too many) was refused as a protected rewording that "needs a saved user answer", and the run
  spent its repair attempts restoring the wrong text. Before any approval, a criterion may now be reworded
  when it keeps every request quote it cited; once a plan was approved, every rewording still needs the
  person (#1691).
- When a planner rewords a criterion or protected item of a plan the person already approved, AutoCode now
  asks the person, showing the approved and the proposed wording, instead of repairing the report back and
  pausing as invalid output. The answer is saved like any other, so the next plan can cite it (#1691).
- A Code Review on Kilo that delivered only its allowed `review/tests/` files paused as "Repository changed
  during read-only review": the check read OpenCode's snapshot cache, and Kilo keeps its own
  (`kilo/snapshot`, `.git/kilo`). The check now reads the cache of the CLI that ran the stage.
- Planning restarts that retain consumed review calls also preserve the user's
  explicit finite allowance, including a spec-first return for guard correction.
  Restarting does not refund calls or approve the corrected plan (#1699).
- Same-chat follow-ups validate historical source exclusions against the full
  source inventory while checking current-turn obligations, avoiding a source-trace
  crash. A controller exit while applying a completed, accounted report offers
  Resume after confirmed cleanup, preserving the report and used calls (#1697).
- Wait for controller-recorded sibling completion before killing the owner in the
  guarded-worker recovery CI fixture (#1695).

- VS Code suppresses a person-wait notice while its own action automatically continues a chat;
  a genuinely stopped Continue checkpoint still notifies after that action finishes (#1692).
- The source-trace check no longer rejects a correct plan for not repeating a standard's name: "ISO 8601",
  "RFC 3339", "UTF-8" or "SHA-256" in the request is a name, not a number the criteria must restate.
  Every other number the request states must still survive (#1689).
- VS Code completion notifications say the chat finished, rather than claiming a completed run
  is waiting for the user (#1687).
- A Tester report that cited a file it read (`hello.txt`) beside its passing checks was refused whole and
  needed a Resolver step and a repair call. AutoCode now drops the extra citation when the requirement
  already cites a verified check, records the drop on the stage (`dropped_evidence_refs`, the raw report
  kept as `.reported.json`), and accepts. A requirement with no verified citation is still refused, and
  the Tester's instructions now say a project file is not criterion evidence.
- Session ownership checks briefly retry a missing initial receipt open during atomic replacement
  on Mac Docker binds; persistent absence and invalid ownership evidence still stop the run (#1680).
- A stopped step's chat notice no longer tells terminal users to "Open Run details", a button only VS Code has;
  it now says the run's details say what needs attention, which every client shows its own way.
- VS Code saved-code compare and restore buttons explain when they are unavailable while
  this chat waits for a command, instead of accepting clicks that silently do nothing (#1676).
- The chat's "New chats:" line named OpenCode even when `default_provider` chose another
  connection. `connections list --json` now marks the connection new runs use (`default: true`),
  and the line names it, for example `New chats: Full · kilo_glm_mimo_gpt`.
- When AutoCode refuses to start a new chat's run (no engine on PATH, an unsupported host), `autocode`
  now shows AutoCode's reason instead of `--status did not return a status view: … state.json`.
- The status view remains usable when a saved active-stage record is malformed: it omits the
  optional running-Builder diff action rather than raising an exception (#1671).
- A run stopped by the provider's HTTP 401 now says so in the chat (VS Code and terminal): "The
  model provider rejected the saved credentials (HTTP 401) … sign in again", instead of the generic
  "The provider ended without a confirmed result". `connections check` now says plainly that it finds
  saved credentials without testing them, so an expired key is caught when a run starts.

### Added

- Opt-in warm source snapshots (`AUTOCODE_SOURCE_HOST=1`, #1707 stage 1). A long-lived helper keeps file
  fingerprints in memory and answers every AutoCode process's source snapshot. Each new process otherwise
  re-reads the whole workspace: 2.5-3 s on a 13.6k-file project. A chat status refresh drops from 1.5-3 s to
  0.45 s. The answers are identical, and nothing is written to disk.

- Tabs in the full-screen `autocode chat`, as in the VS Code extension: `Ctrl-T` (or `/new`)
  opens another chat in a new tab, `Ctrl-←`/`Ctrl-→` or `/tab N` switch, and a tab bar lists the
  session's chats with what each waits on (working, needs you, done). Tabs switch at a question,
  so a step is never left running unwatched; the other tabs are checked and announced.

## [0.8.0] — 2026-10-19

### Added

- `scripts/homebrew_formula.py` writes the Homebrew formula for a PyPI release, pinning its
  source archive and dependencies by sha256, so a release can be installed with
  `brew install charlieanna/tap/autocode` ([Releasing](docs/releasing.md#homebrew)).
- The terminal chat now does what the VS Code extension's pickers and Connections view do:
  `/mode` and `/engine` choose what new chats start with (kept per project), and `/connect`
  lists the backends, runs a connection's check, hands the terminal to its sign-in or setup,
  or uses it for new chats. Requirements' reply streams in as it is written, the terminal
  bell rings when AutoCode needs you, and a chat opened earlier in the session is announced
  when it starts to wait or finishes.

- The terminal chat asks before AutoCode spends, as the VS Code extension does: it shows
  the core's spend confirmation and asks `Prepare plan? [y/N]` before paid planning, and asks
  `Start it? [y/N]` before starting a run whose mode can plan at once (`--mode quick`).
  Typing the review's own answer (`plan`) is confirmed the same way.
- The VS Code chat shows what the running step is doing (`chat.activity`: its commands, edits and
  tool calls, a failed one marked) and, while the Builder works, a "Show what the Builder has changed
  so far" button that runs the view's named `checkpoint --working` diff (#1643).

- The full-screen `autocode chat` keeps a history of your chats, as the VS Code extension
  does: `/runs` lists them across every project, open and finished, grouped by project (this
  one first, then by latest activity) with each one's request and state;
  `/open N` shows that chat's whole conversation and carries on there (a follow-up on a
  finished one); `/new` starts another without leaving. `autocode` mentions earlier chats at
  start, `Up`/`Down` recall earlier requests across chats, and `autocode runs --json` rows carry
  a `title`, the first line of the task (#1655).

- `autocode checkpoint --working` and `code_checkpoints.working` in the status view: while a Builder step
  runs, a display-only diff of what it has changed so far, against the newest saved step or, before any
  is saved, the checked-out commit. The full-screen chat's `/diff now` shows it (#1643).

- The full-screen `autocode chat` shows a side panel in a wide terminal: where the run is and what
  it needs from you, each requirement and task with its state, open problems, and a log of how the
  run moved. Typing `/` opens a command menu: `/plan`, `/tasks`, `/log`, `/details`, `/panel`,
  `/help`, `/quit` at a question, and `/pause` while AutoCode works. All of it comes from the status
  views the chat already reads.

- `autocode --status` carries an optional `view.chat.activity` while a supervised step works: the
  last 20 commands, file edits and tool calls of the running attempt, from its own event file
  (Codex-style items, which the built-in Claude provider also writes, and OpenCode or Kilo tool
  parts). It is display only: no prose, reasoning or tool output is copied, an attempt that is not
  confirmed as supervised shows none, and a command that exited 0 is not a passed check. The
  full-screen chat shows it in the panel's new Activity section, in the log, and on the status row,
  so a build no longer looks like one line of "working".

- The full-screen chat's panel shows what the run is doing now and its last few attempts, and logs
  each one. `/diff` lists the saved code steps and shows what changed since one; `/undo N`, at a
  question, goes back to a saved step on a new branch after asking, and the chat carries on with
  the new run. Both take only the compare and restore actions the status view names.

- `autocode chat` talks to a run in the terminal the way the VS Code extension does: it shows the
  run's conversation, asks you only for the answer, approval or reply the status view leaves to you,
  and runs exactly the command the view names. Plain `autocode` in a terminal now opens it: it picks
  up the run left open, or asks what you want done.

- `autocode chat` takes the whole terminal in an interactive session: a wordmark splash, a
  scrollable transcript with a colored label for each speaker, the current question or activity
  above a boxed composer, labeled **New chat** or **Reply**, with editing and history. `--line` keeps the line-by-line prompts,
  and scripts still get them when there is no terminal.

- New chats in VS Code offer a **Run mode**: Full (the default, Requirements first) or Quick plan,
  where a clear request goes straight to the plan. The modes and their start flags come from the
  new `autocode modes list --json`; every mode keeps plan approval, the Tester and the Completion
  Reviewer.

### Changed

- Joint-planning handoffs use compact JSON while preserving exact task and source
  values, saved decisions and commands. The retained small-task incident input is
  3,215 bytes smaller (3.38%); latency and report-delivery improvement remain
  unverified (#1630).

- New CLI and TaskRun runs select qualified models from connected accounts automatically,
  preserving explicit overrides and saved routes. OpenCode/Kilo metadata governs supported
  reasoning levels; preflight receipts distinguish levels and actual tools. Automatic
  escalation and stuck-run investigation stay inside saved accounts. Discovery shares its roster
  with admission, and installed wheels include the qualification
  table. `--no-auto-select` retains fixed defaults (#1426).

### Fixed

- VS Code shows the core’s cost counted toward the configured cap, with explicit
  partial and unpriced coverage, before the separate historical comparison estimate.
  The client does not reprice usage or change the cap (#1665).

- The full-screen chat's `/` menu reached only its first six commands with the arrow keys; it now
  scrolls to keep the choice in view (up to ten rows, fewer on a short screen). Before a chat is
  open it offers only what works without one: `/runs`, `/open`, `/mode`, `/engine`, `/connect`,
  `/help` and `/quit`.
- Session ownership receipts are size-checked and read from one opened file descriptor,
  with a bounded read even if the file grows. A path replacement or removal after opening
  cannot mix the checked receipt with another file; absent or invalid receipts still block
  admission without retry or grace (#1662).

- Review findings on changed or unverified source stay blocking when their tests fail
  identically on base and change. The runner checks actual applied source locations,
  including offset patches, before treating an older defect as an observation (#1658).

- Session ownership failures identify the failed receipt-read check with fixed phase/reason
  labels, without exposing its path, content or exception payload. The same failed read still
  blocks admission and closes the lifeline for cleanup; no retry or grace is added.

- VS Code’s “Latest messages” shortcut occupies its own row, so it cannot cover
  the sticky recovery and approval actions in a long chat (#1652).

- Source-trace citations and reviewer exclusions apply only to the source ID they
  name, preventing false coverage conflicts and preserving each source's number,
  literal and negation checks (#1650).

- The offline recovery-context regression checks the saved timeout kind and event path in the
  structured handoff, so compact JSON formatting does not falsely fail the full-suite gate.

- In the full-screen chat, a run that continues on its own no longer lists "Needs you: AutoResolver
  must reconcile…" in the panel and log, the panel says "Starting the next step…" instead of
  "plan approval needed" in the seconds after you approve, `/undo` on a finished run says going back
  is offered only at a pause, and `/diff N` with no changes says the files match step N.

- VS Code shares simultaneous polling reads for the same run and CLI settings,
  without caching completed status. Action preflights stay fresh, and a poll
  overtaken by a command cannot restore the older decision state (#1642).

- Session ownership handoff failures retain a bounded callback phase and safe reason
  code, so different ownership failures can be diagnosed without exporting exception
  payloads. Failure classification and scoped cleanup stay unchanged (#1640).
- In the full-screen chat, Esc closes the `/` menu and the key typed after it is kept, and text that
  starts with an unknown `/word` is never sent, a sentence included. The panel catches up within half
  a second of a decision instead of the next two-second poll. A stop's "Next it runs:" names only
  the action, without the run's paths or an issued token. A run stopped to apply a pause or feedback
  someone asked for says so, instead of "I couldn't finish this step".

- The full-screen `autocode chat` no longer lets keys typed while AutoCode works answer the next
  question, so a plan cannot be approved before it is shown. A multi-line paste stays one reply
  instead of answering the questions that follow. The request is shown once, a stop's question keeps
  its `[y/N]` in view (the command is shown above it), and the chat's last words stay on the terminal
  when it closes: an error or interrupt, and the whole hand-off including the command to run.

- Planning prompts ask each stage only for report fields its schema accepts:
  the Planner proposes remediations, the Plan Reviewer judges them, and the
  first review checks progressive proposals without returning one. This fixes
  contradictory report instructions; the #1630 timeout cause remains unverified.

- Review evidence exports accept the optional, typed attribution metadata added
  for explicit-patch findings. Canonical review reports and external-change
  verification no longer fail export on that field (#1632 follow-up).

- Explicit-patch reviews compare identical finding tests on base and changed source.
  Proven pre-existing Python failures remain observations instead of blocking an
  unrelated patch or becoming automatic follow-up repairs. Inconclusive evidence
  retains blockers, and reports show the runner-derived verdict (#1602).

- `autocode chat` no longer exits while AutoCode keeps working: Ctrl-C while the pause is being asked
  for interrupts the step, and a third Ctrl-C stops AutoCode. It stops instead of relaunching a run
  that makes no progress, shows a review's criteria before asking you to accept them, shows what
  stopped and the models offered at a model question, hands you the command when it cannot continue,
  reports a failing run listing instead of treating the project as empty, and takes a brief that
  starts with `-` as `--brief=TEXT`.

- Small-task review keeps explicitly selected Plan Reviewer effort and legacy saved
  efforts. Fresh default/automatic routes still use the reduced tier, with review
  charges, model independence and replay matching unchanged (#1618).

- Offline replay preserves the recorded Plan Reviewer effort instead of applying
  the small-task reduction twice. Provider light-effort settings remain portable;
  review budgets and exact recorded-route validation still apply (#1616).

- A pause you asked for in the VS Code chat now says "Paused as you asked" with a plain **Resume**,
  instead of "I couldn't finish this step" and "Resume after correction". The chat also shows
  your button presses in words ("Looks right. Prepare the plan.", "Approved the plan.") instead
  of the raw answer `plan`, the Requirements summary keeps its sections apart, and chat tabs are
  named after the request rather than the run directory, with the open chat kept in view.

- Ordinary Kilo launches forward bounded native text and reasoning progress while response blocks
  are still streaming. Silent streams retain the configured idle deadline; progress remains
  separate from tool execution and completed-report evidence (#1599).

- The scripted conformance lifecycle declares a finite starting planning-review allowance for
  its deliberate revisions and feedback, preserving review accounting and every lifecycle check (#1604).

- Native and headless clients can bind explicit finite planning-review totals and
  additional recovery slots to exact named commands and the current pause. Save-only
  actions stay save-only; existing accounting, other limits and plan approval remain (#1605).

- Kilo's persistent native provider child no longer counts as an inferred running tool after
  completed tool calls, so a silent model wait honors the configured idle limit. Real or unknown
  child tools and explicit tool starts retain their separate execution grace and cleanup (#1595).

- VS Code keeps live controller/preflight continuation quiet instead of announcing that it is
  waiting for a person. Chat and Run details share the working classification; stopped or
  unverified continuation still asks for action, and real questions and completion still notify (#1590).

- When the runner cannot see an acceptance test the Tester wrote, its rejection now says the file
  defines the test, names the runner's command and how many tests it ran, and for unittest that only
  `TestCase` methods are collected, so the retry can fix the test instead of the run pausing for good (#1567).

- VS Code chat disables its actions and Send while the latest status read has failed, says so
  above the transcript, and re-enables them on the next good read; a background tab keeps its
  own read error (#1593).

- The named recovery action can set aside a stopped planning attempt after a Resolver reply,
  instead of publishing the same operational question before reaching abandonment. Exact attempt
  and stopped-process validation still apply; resuming remains a separate action (#1588).

- A stopped report-only repair can use its remaining bounded attempt after an output-truncated
  review, preserving the original evidence and charged attempts. Unchanged source, evidence pins,
  review ownership and stopped-process checks still gate recovery (#1584).

- Native chat can request an explicit finite total active-time limit from a typed,
  recovery-token-bound core offer. The selected total preserves other caps, model
  routes and plan approval; unsupported numeric recovery controls still report a gap (#1579).
- Dashboard status and intervention inspection can run alongside plan approval without being
  mistaken for duplicate workers. The observer grammar retains active-writer and ambiguous-command
  protections (#1581).

- Authentication preflight stops retain names-only guidance for credential variables actually
  withheld from the probe, without exposing secret values or enabling pass-through (#1577).

- `autocode --version` and `autocode doctor` again start when psutil is missing, including
  through the grader imports used by Git delivery. Process supervision still requires the
  dependency and reports its repair instructions (#1571).
- Fixed-model CLI qualification explicitly retains fixed routes, and liveness checks cover
  their intended working notice without weakening status, action or evidence assertions (#1573).
- Verification falls back to fresh checks when an editable AutoCode package cannot be
  snapshotted. Incomplete controller identities never authorize cached checks; unavailable
  task source and unrelated pause reasons still stop verification (#1572).

- Applied Pause requests survive deferred plan-approval normalization until explicit resume, retaining the pending proposal and saved evidence (#1561).

- Malformed saved activity records no longer crash chat status or its provisional reply view (#1522).

- Resolver blocker questions now offer a request-bound reply in task chat. Approved build plans can
  request a controller-owned atomic local commit, followed by independent verification on that commit.
  Plans without Git delivery keep the ordinary build flow and gain no commit authority. Resolver
  recovery accepts both forms while preserving their approved delivery authority (#1522).

- Isolated verification copies retain the delivered Git commit and its reachable history, so atomic
  delivery checks inspect the real commit instead of an unrelated synthetic root. Current source
  changes remain in the copy; original Git configuration, hooks and other refs stay excluded (#1522).

- Chat questions about a reviewed plan now receive an explanation without invalidating its approval
  token. Requirements distinguishes questions from requested changes; changes still need a refreshed
  review and explicit approval (#1522).

- Joint planning keeps technical questions with recommended defaults inside AutoCode, including
  test-fixture and command details that previously interrupted the VS Code chat with criterion IDs.
  Internal decisions remain separate from human answers; behavior, authorization and plan approval
  still require their existing checks (#1522).
- Automatic model selection preserves explicit reasoning-effort flags and models using their default
  level. Automatic quota recovery retains and accounts for the stopped attempt before retrying,
  and remembers exhausted alternatives across restarts instead of cycling between them (#1426).

- The VS Code chat showed plan-approval internals: "Plan the approval is bound to (revision 3)",
  criterion IDs (`AC1`, `AC2`), each verification command and a token notice with a raw CLI command.
  The chat now lists what the plan will deliver under "Plan to approve", marks the items you check
  yourself, and drops IDs from review criteria and open problems. Run details keeps the technical view
  under "Plan to approve · revision N". Approval is still refused for a plan that changed after it was
  shown (#1518).
- Live chat recovery names structured authentication failures safely, avoids duplicate operational questions, explains stopped runs and reconciliation actions, and applies polling changes without reload (#1563).

- The dashboard offered no plan approval when the Plan Reviewer accepted a draft during its challenge
  (adaptive review). The runner accepts that plan for approval, but the dashboard only recognized a plan
  finalized by a separate finalize step, so the task showed "Planning checkpoint · not currently ready for
  approval" and the chat had no Approve & build button. Plans from adaptive review and Builder-led
  finalization are now ready for approval too (#730).
- Requirements served by parallel Builders now show their saved changes. An integrated parallel batch recorded
  no code checkpoint, so `changes_by_criterion` attributed nothing to its milestones' requirements and their
  diffs were missing. The batch's integration is now one saved step whose `members` name each milestone with
  the files its Builder changed, and each requirement counts only its own member's files (#730).
- `autocode review` and the dashboard's **Checks** pane showed every file of a shared parallel step under each
  of its requirements. Each requirement now shows only the files its own milestone changed in that step, names
  the ones left out, and the step names the milestones it served (#730).
- A step held for no causal progress (a recovery novelty hold) now offers "Retry the held step once" on its
  recovery card, running the `--resume-paused --retry-failed-stage` that `needs.retry_action` names with the
  card's token. The dashboard could previously only inspect the AutoResolver request, so it could not grant
  the one retry the hold allows (#730, #1012).
- A repair paused because its source was edited (`PAUSED_STALE_HANDOFF`) now offers "Accept the edited source"
  on its recovery card, running `--resume-paused --accept-source-edit` with the card's token. The dashboard
  could previously only inspect or send feedback, so such a run could not be continued there (#730).
- A `--package` run no longer stays blocked when the person removes their own untracked nested repository
  entirely during the run: the launch record held it as added, so its absence names no change of the
  candidate. A deleted submodule or a path that is no longer a Git repository is still named (#711).
- A design check that passes now removes the blockers file an earlier, stopped check of the same run wrote,
  so no stale one is left outside the package; and while a blockers file is live its content is recorded
  (a saved hash), so an edit to it is reported as the runner's file instead of being left out by path (#711).
- Stamp open reasoning escalations and Builder retries as `passed` when completion is accepted via `--accept-completion` (#612).
- Filter each role's escalation ladder only against its own tool's model list under `--role-transport`, preserving effort ladders for roles running on other tools (#1426).
- Treat a model reached through a gateway (`kilo/openai/gpt-6-sol`, `kilo/anthropic/claude-opus-5.5`) as the model
  it is, so the cross-model rule no longer lets one route check the same weights reached another way (#1426).
- A resume is listed among the run's approvals only once a role's climb on its effort ladder is admitted against
  the cost cap, time limit and milestone budget; a resume that the climb's projection pauses again is no longer
  recorded as releasing the pause, matching the Builder's stronger attempt (#611).

- Lifecycle-only provider events no longer let a persistent helper process bypass an explicitly configured idle timeout; genuine tool execution keeps its tool deadline (#1562).

### Added
- VS Code chat can pause a working run: Pause takes Send's place, the current step finishes and
  saves, and Continue resumes where it paused. The status view names the request as `pause`, so
  the client runs only the core's own `intervention submit --kind pause` (#1598).
- The soft-default machinery for automatic selection (#1426 step 11's second half): an internal
  `auto_select=None` launch selects and, when the tool's listings cannot be read or its accounts cannot field
  a checker, prints why in account terms and uses the fixed default routes instead of refusing - a defaulted
  launch is never blocked. Shipped opt-in (`--auto-select`); flipping the visible default waits on step 12's
  qualification, because it would otherwise make the suite read a machine's real logins.
- Step 9 complete (#1426): a run that chose its routes with `--auto-select` now switches a stopped model's role
  on its own. A quota, content-filter or output stop enters the shared recovery chain first as a `route` row
  (`autocode_route_recovery`): the next qualified model from the run's saved permitted set, checkers kept
  independent, applied as the usual recorded `route_assignment` (actor `runner`) with the saved selection kept
  current, and the run continues without asking. When no permitted model remains, the long-standing question
  stands exactly as before (#184); runs without a permitted set, native runs and workflow jobs keep asking.
- Step 9's decision logic (#1426): an `--auto-select` run saves its permitted set - every qualified, connected
  candidate for each role, all on accounts the launch already used - under `route_selection.candidates`, and
  `autocode_route_selector.next_route` answers "where does this role go when its model stopped?" from it:
  the next cheapest qualified model with every other route pinned, so checker independence holds, and None
  when nothing remains so the person is asked as before. The recovery-chain wiring that applies the switch
  automatically on a quota stop is the remaining half of step 9.
- `--auto-select` (#1426, step 11's flag-gated form): a new run chooses every role's model from the tool's
  connected accounts - discovery, the selector, and the selection-driven access probe all run for real. Per-role
  model flags, `--single-model`, role-provider flags and conversation history still win; a saved run refuses the
  flag and keeps its routes; the chosen routes, levels and probe debt are saved under `route_selection`, origins
  record `auto_selected`, and a selection that cannot field an independent checker refuses to start in account
  terms. Selection is not yet the default for new runs.
- `autocode models --selection` (#1426 step 10): automatic selection explained. Local listings only - no model
  calls: the command shows the logins the tool reports, the route chosen for each role with its price and
  reasoning level, why every other qualified model lost (not connected, a cheaper qualified route won, or it
  would check its own family's work), and which chosen routes the access check still has to prove. A run whose
  accounts cannot field an independent checker prints the account-terms stop instead.
- The selector's access check (#1426 step 8): `check(selection=...)` probes exactly the routes automatic
  selection marks unknown, each at its role's own reasoning level (None sends no level), through the tool that
  runs it, inside the existing cost cap, receipts and crash-safe attempt accounting. Built-in OpenCode routes
  are probed too; saved-run probes keep their `low`-effort behavior and still skip non-billing tools.
- Route discovery (#1426 steps 4-5): reading a tool's own local listings - `kilo models` with
  `kilo auth list`, `opencode models` with `opencode auth list` - into the candidate list the
  automatic selector consumes, with no model calls and no credentials touched. A configured
  tool's `[auth]` routes now also carry the login's display `name` and how it `billing`s
  (subscription or pay per token); a model no connected login can bill is never offered. A
  command template's `{effort}` arguments (Kilo's `--variant`) are omitted when a route has no
  reasoning level, instead of passing an empty `--variant ""`. Verified in-process against the
  real kilo 7.8.3 and opencode logins on the build machine: discovery plus the Step-7 selector
  reproduce the live glm53-mimo family split from the account state alone.
- The automatic route selector (#1426 step 7): one deterministic function from the models a tool's
  connected accounts actually offer to one route per role, or a stop that names what is missing in
  account terms. A model is eligible only when the new dated qualification table
  (`tools/autocode_route_qualification.json`) lists it for the role with the evidence that admitted
  it — AutoCode's own runs qualify, a benchmark admits provisionally — and within a role the cheapest
  qualified route wins (subscription before pay per token, proven access before a probe, then the
  price table). Producer and checker families stay independent, no reasoning level is invented, and
  the choice is recorded as the `auto_selected` origin in the route record. New Kilo Gateway price
  rows (GLM 5.3, GPT-6 Sol, GPT-6 Astra, DeepSeek V4.1 Flash) price those routes. Not yet the
  default for new runs: discovery and wiring are the next steps of #1426.
- Requirements is a conversation in chat. In a spec-first run the Requirements agent answers the first message,
  whatever it says (a greeting or an off-topic question gets a short answer and a question about what to build),
  in its own words (`assistant_reply`). It keeps one provider session across your answers and streams its text to
  the status view. Until it finds work, your newest reply becomes the task. When it understands the job, it shows a
  summary to approve or correct; the Job recognizer then classifies the job from that understanding before
  planning starts. The VS Code chat shows the conversation without the empty task dashboard, and offers
  **Continue** when a Requirements step has stopped instead of showing "Thinking…" forever (#1516).
- An end-to-end acceptance test for the chat client: `test_scenario_client_browser_ui.js` types a catalog
  scenario's brief (parallel-diamond) in chat, attaches the project, approves and builds the plan, and checks in
  **Checks** that each requirement shows its reference solution's hunks and only its own files of the shared
  parallel step, at desktop, tablet and phone widths. The task runs the real runner against fake providers. It
  runs in CI's browser step (#730).
- Each action on the status view's recovery card that runs a control names its exact CLI flags in `args`, for
  example `["--resume-paused", "--retry-builder", "M3"]`. The dashboard's recovery buttons run those flags, bound to
  the inspected pause's token, instead of keeping their own table of action kinds; an action that names no flags
  runs nothing (#730).
- The dashboard's **Checks** pane shows, under each requirement's **Inspect**, the diff of each saved Builder step
  attributed to it, labelled as attribution rather than proof and marked when it overlaps another requirement. A
  requirement with no attributed step offers **View all run changes** instead (#730).
- The dashboard's **Preview** shows the run's development server while `autocode serve start` runs it, reloading
  after each restart, and otherwise falls back to the saved app address with the server's state and the command to
  start it (#730).
- `autocode serve start` restarts the run's development server after each code-changing step the run applies, on
  the same port when it can, and leaves it running through pauses and completion. A server that exits or never
  answers is shown with its last output and started again after the next code change, unless the run has
  completed. `dev_server` in the status view gains `restarts`, `code_checkpoint` and `waiting` (#730).
- `autocode serve start|stop|status` runs a saved run's development server from its `serve` command (#730): in the
  foreground on a free loopback port, under the same process supervision as a test command and without the
  environment's credentials, until Ctrl-C or `autocode serve stop`. `autocode --status` shows it as
  `view.dev_server` (whether configured, its status and its address while it runs); its record is
  `dev-server.json` in the run directory, never `state.json`.
- A project can name the command that runs its development server (#730): `serve` in `.autocode/config.toml`, a
  command string with `{port}` where the port goes, or a `[serve]` table that also names `ready-path` and
  `ready-seconds` (`pyproject.toml` takes only the string). A new run saves it with its settings, and
  `autocode doctor` lists it with the other project settings. A value without `{port}`, an unknown field or a
  ready path that is not a server path stops a new run, naming the file.
- `autocode --status --json` shows the run's planning-depth decisions under `planning_depth`: whether the request
  was judged clear and Requirements skipped, the plan's size and the review allowance it set, and whether the Plan
  Reviewer approved the draft at its first review. Before, these were only in `state.json` (#697).
- `autocode --status --json` shows `changes_by_criterion`: for each acceptance criterion of the approved plan, the
  saved Builder steps of the milestones that cover it and the files they changed, with files another milestone also
  changed marked as overlapping. It says where to look, not that the criterion is met (#730).
- `autocode review` shows, for each criterion, the diff of each saved Builder step attributed to it, against the
  saved step before it and read from Git objects rather than the working tree (`criterion_diffs` in `--json`).
  Labeled as attribution, not proof (#730).

- A novelty hold's stop also states the run's active time against its time limit and, with milestone checkpoints
  on, the current milestone's time against its budget, after the spend against the cost cap; a limit or budget
  changed at the hold is the one the stop then states (#611).
- The status view's `routes` names, for every role, the `account` its model bills — the model ID's billing segment,
  null when the engine's own login decides — and the `origin` of the route: `user_explicit`, `single_model`,
  `runner_default`, `catalogue_replacement`, `quota_assigned`, `stronger_attempt`, `escalated`, `derived`, or
  `unrecorded` for a run saved before the record existed. The whole record, those two beside the `model`, `engine`,
  `job`, `reasoning_effort` and `provider` it already carried, is now built in one place (`autocode_role_route`)
  instead of in the status view (#1426).

- Opt-in blind tests via `--blind-tests` execute acceptance tests written by the Tester in a separate candidate
  scratch tree without exposing test implementations or file paths to the Builder's workspace or prompt (#702).

- `docs/cli.md` now includes a dedicated subcommand exit-code reference table documenting exit codes 0, 1, and 2
  across all subcommands, and `tests/test_cli_surface.py` tests that documented inventory (#714).

- `autocode plan`, `autocode prune-run`, `autocode merge`, and `autocode clean-worktrees` now support `--json`
  output for structured machine-readable reporting of results and errors, and `autocode checkpoint` and
  `autocode tasks` accept `--json` for CLI caller consistency (#714).

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
  edits, resumes and workflow-policy approvals are not checked; `--accept-completion` is, since the fifth part
  below. The run saves the list when it starts (`settings.require_approver`), so a changed file never alters a run
  in progress; the key has no flag, and `autocode doctor` shows it with its source. Without the setting nothing
  changes. It guards against an approval under the wrong configured Git identity, not against someone who
  deliberately sets a listed address (Git's environment variables or a repository-local `user.email` change what is
  checked): it is not authentication. A replay (`autocode replay`, `--replay-recording`) never takes it,
  `autocode program` and `autocode components` child runs read it from their own worktree (so only a committed file
  reaches them), and `autocode program approve` is not checked. `autocode --ui-run HANDOFF` without a typed task
  now reads the project settings, all of them, as every other new run does (it read none before), so a project
  `engine` other than `codex` refuses it, as it already did with a typed task.
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

- A fake OpenCode run through the CLI can launch its Builder, Tester and Completion Reviewer inside the real
  Linux bubblewrap boundary (#709). With `AUTOCODE_FIXTURE_QUALIFIED_OPENCODE=1` the test fixture reports
  OpenCode 1.18.33 and answers the runner's model-free conformance checks through the boundary shell. The new
  `tests/test_contained_fake_cli.py` checks that such a run finishes and that `--status --json` names `bubblewrap`
  and its version in `tool_containment_backend`. It also checks that a Builder shell write planted outside the
  workspace is blocked, while an uncontained control shows the same write lands. The `linux-tool-containment` CI
  job runs it without skips, and changes to the fixture select that job. The fixture's own edits and checks still
  run outside the boundary, so OpenCode's tool dispatch stays with the opt-in `NativeToolTests`. Without the
  variable the fixture is unchanged.
- Accepting completion records who accepted it, and a required approver covers it (#722, fifth part):
  `--accept-completion` saves the workspace's Git `user.name` and `user.email`, the host name and the command's
  option names on the event it records, and takes `--as NAME`. `--status` lists it in `approvals` (kind
  `completion`), and the completion summary, the evidence report and so the `autocode-issue pr` body show it as
  "Completion accepted"; a report that lists one says so in its Approvals introduction, and every earlier report
  renders unchanged. A run started with `require-approver` refuses `--accept-completion` from a Git `user.email`
  it does not list, as it refuses a plan approval: exit 2, nothing saved, and the run stays at its pause. A
  completion accepted before this change is listed with no recorded acceptor. `--as NAME` now also goes with
  `--goal-file`, which already recorded who loaded the plan as a goal edit.
- A completed run's evidence report, and so the pull request body `autocode-issue pr` builds from `evidence.md`,
  gives the approved plan's goal hash (what `autocode plan --check FILE` prints for a file holding exactly that
  plan). When the approved plan was loaded with `--goal-file`, it also names the file, its goal hash and the plan
  revision it was loaded as, and says whether the approved plan is the file's plan unchanged, differs only in the
  runner's records of the plan's review, or differs in named parts. The status view's `approved_contract` gains
  `goal_hash` and `plan_file`, and the report's `plan` gains the same two optional fields; older reports still
  authenticate. Starting a new run from a plan file is not part of this change (#720).
- `autocode --status` shows each escalation a run made: a role's climb on its effort ladder and the Builder's
  stronger attempt, as rows of the status view's new `escalations` field (`--status --json`) and one line each on
  stderr after the cost line. A row names the job and stage it was for, the route it climbed from and to, its
  escalated attempts and what they cost at the dated price table, and how it ended: `passed`, `failed_again`,
  `paused` or `running`, with what decided it. A cost that cannot be known is unknown, never zero: an attempt
  with no usage record, an escalation a checkpoint continuation inherited (its attempts ran before the restore),
  and the role's stage records saved before launch routes were recorded (2026-10-01), which cannot be shown to
  be its attempts (`unrouted_attempts`). Only a validation of the escalation's own work for its own milestone counts
  as its pass, and not once the Builder's retry policy charged that work as a failure (a review sent it back, also
  after a validate task's re-check): the runner's blanket `passed` stamp does not override a failure of its own
  work, is not taken once the role ran on another route, and is never taken for the Builder, whose work validations
  judge. It is read from the records the run already keeps; no run-state key is added (#612).
- Fake scenarios for escalation outcomes, read black box through `autocode --status --json`: `escalation-converts`
  (the Builder's stronger attempt fixes what its ordinary attempts could not; one `escalations` row with both routes,
  one attempt and its cost, `passed`, and the run completes) and `escalation-ladder-end` (that attempt fails too; the
  row reads `paused` and the run pauses at `PAUSED_BUILDER_RETRY_LIMIT` with the stuck-stage Investigator's
  diagnosis). `scenarios/test_escalation.py` runs both and checks that a `--builder-strong-model` the tool does not
  list refuses the new run before any model call. The scripted provider gains the two faults
  (`harness/escalation_provider.py`) and the oracle helper `escalation_checks`; no runtime change (#612).
- A scenario catalog case for #611's rework loop, with an oracle, a reference and broken variants.
  `rework-same-failure` has a correct greeting repair whose required suite also fails a release test the task may
  not change: the run must stop after the second identical failure, naming the check, with no paid diagnosis or
  third Builder attempt, and its status view's `cost` must show the spend under the $5 cap; it passes today.
- The scenario harness runs a fake OpenCode build inside the Linux bubblewrap boundary (#709).
  `scenarios/test_contained_opencode.py` runs `greenfield-greeting-cli` on the fake OpenCode client, with the
  harness's scripted model behind it and `AUTOCODE_FIXTURE_QUALIFIED_OPENCODE=1`, so its Builder, Tester and
  Completion Reviewer launch contained. It checks that the scenario's oracle passes and the run completes, that
  every record of those three stages names `bubblewrap` and the version `autocode doctor` reports, that a Builder
  shell write planted outside the project is blocked, and that `--status --json` says `tool_containment:
  "contained"` and names the same backend. The test asks `autocode doctor --json` whether the boundary is
  available. Without a working bubblewrap, the test skips, except in CI on Linux, where it fails; the suite job
  installs bubblewrap before the scenario harness runs. Any other problem doctor reports, such as the fake no
  longer reporting the qualified OpenCode version, fails it everywhere. A pull request that changes only
  `tools/fake_opencode.py` now runs the scenario harness step and this case (`SCENARIO_FIXTURES` in
  `tools/run_suite.py`, read by `tools/ci_scope.py`). Only tests and CI selection change.
- The scenario harness now checks who approved each task run a scenario completes (#722, sixth part). It reads
  `approvals` from that run's final `--status --json`. Each row a person made must name the scenario's person
  (`Scenario Person <person@scenario.example.test>`, which every scenario project now sets in its own Git
  configuration), this host, and a command of `autocode` and option names only. A workflow-policy row must name no
  one. In a scenario of one run, each plan and review approval the harness gave must also be listed; the
  conformance client now records the approvals it takes in `result.json`'s `approvals`, as the ordinary driver
  does. A program's plan and workstream runs and a components run's architecture and component runs are checked for
  provenance only. The `approval provenance: RUN` check sits beside `canonical evidence: RUN` in `result.json`. A
  failure makes the verdict `FALSE_COMPLETE` when the scenario completed, and `HONEST_BLOCKER` when it stopped.
- The scenario catalog has a two-package scenario, `feature-monorepo-package` (#711). Its seed is a repository with
  `packages/greet/` and `packages/farewell/`, and the task adds an option to the greeting package only. The scripted
  run is started with `--package packages/greet/`: the reference completes, and `broken/edits-farewell-package`,
  which also edits the other package, must stop with both files named in an open blocking `runner` finding (the
  oracle reads it from the status view). Run against AutoCode from before #1415, that variant completes and is
  judged `FALSE_COMPLETE`. The harness no longer repeats `--package` on calls to the saved run, which the CLI
  refuses. A live profile gets no `[fake] flags`, so it runs the brief without `--package` and is judged on the
  delivered files alone. A pull request that changes `tools/autocode_regression.py` or
  `tools/autocode_build_loop.py` now also runs `tests.test_package_scope_cli`: the regression proof records a
  launch's files outside the package and runs the package check before each review, and the build loop calls it.
- `autocode-issue plan ISSUE` puts the plan an issue's run is waiting to have approved in a pull request, so a
  team reviews what will be built before anything is built. It exports the plan as `autocode plan --export` writes
  it and commits it as `plans/issue-N.md` (`--path` for another place) on branch `autocode/issue-N-<title>-plan`,
  made from the run's base commit, without touching any checkout or the run, and writes the pull request's body.
  `--open` pushes the branch and opens a draft pull request (`--base`, `--ready` as for `pr`). Run again after the
  plan changes while that pull request is open, it commits the new plan on top, so the same pull request shows the
  change; it never replaces commits it did not make (a plan branch deleted here is made again at its last plan
  commit). Merging approves nothing: once `plan --open` has opened the plan pull request, `autocode-issue status` at
  the approval stop prints its URL and the `autocode --goal-file` command that loads the merged file into the run,
  until the run's plan moves past the one it carried. A merged plan pull request is never reused, even when its
  branch was deleted with it: `plan` fetches the base branch first, and a plan that changed after the merge goes in
  a new pull request, on `-plan-2`, made from the base branch that holds the merged file.
  `TaskRun.export_plan(path)` is the task-run interface's way to write the plan file. `docs/workflow.md` describes
  the team workflow. Starting a new run from the merged file is not available yet (#720).
- A completed program's or components run's evidence report now lists its child runs' approvals (#722, seventh
  part). Each row is copied from that child's own report with the child's id added as `child`, child by child, and
  `evidence.md` shows them in an Approvals table with a Child column. It also says what is left out: a program
  agreement (`autocode program approve`) records no approver, a program does not list approvals of a plan run it was
  derived from (`autocode program derive`), and a components run does not list approvals of its architecture. A child whose report lists no approvals, written before AutoCode listed them, is named in the
  report's not-verified list. When no child's report lists any, the report is unchanged. The scenario harness's
  `approval provenance: RUN` check now also matches each budget pause a scenario's person acknowledged (`[fake]
  acknowledge`) to the `resume` row naming that pause, as it matches plan approvals by token and review approvals
  by criterion.
- The scenario catalog has a bug fix in one package of a two-package repository, `bugfix-monorepo-package` (#711).
  Both `packages/invoices/` and `packages/search/` keep a copy of the same off-by-one pager; the report and the fix
  belong to the invoice package. The scripted run is started with `--package packages/invoices/`, goes through the
  Investigator first, runs its proof in the package and completes; the runner's diagnosis note under `docs/bugs/` is
  not counted as a change outside the package. `broken/fixes-search-copy-too`, which also fixes the other team's copy,
  must stop with that file named in an open blocking `runner` finding. Against AutoCode from before #1415 the
  reference did not complete, because the proof counted the diagnosis note as a change outside the package. The
  status view's `findings` documentation (`autocode_run_view`, `docs/task-run.md`) now names the package check as a
  source of `runner` findings, and `docs/workflow.md` records that the Investigator is not told the package.
- The scenario runner (`scenarios/run.py`) now checks at every plan approval a catalog run reaches that the plan
  file round trip reproduces the plan's goal hash (#720, the first acceptance check). Before it approves a plan, it
  exports the plan with `autocode plan --export` to `plan-files/NN.md` in the evidence directory and reads the file
  back with `autocode plan --check`, the reader `--goal-file` uses. The `plan round trip` check in `result.json`
  requires the file to read back with the run plan's goal hash, the run directory to be unchanged by the two
  commands, and the approved plan's goal hash in the final `--status --json` to be the exported one. A masked export
  fails: its file does not reproduce the goal hash, and `--goal-file` refuses it. The two calls are no steps; they
  add about 4 s per plan approval on the fake provider. Each plan approval's row in `approvals` keeps their output
  as `plan_file`, and the record a program or components scenario is judged on now carries `approvals` too. A
  failure makes the verdict `FALSE_COMPLETE` when the scenario completed, and `HONEST_BLOCKER` when it stopped.
- The scenario harness's `approval provenance: RUN` check now matches the gates it gave a program's or components
  run's task runs to each run's own `--status --json` approvals (#722, eighth part). Before, one `approvals` list
  held the gates of all of them, so their runs were checked for provenance only and a plan approval listed in no
  run, or only in another workstream's, still passed. Now each gate the harness gives one of those runs (the
  program's plan run, each workstream run, a components run's architecture run) names that run's `run_dir` in
  `result.json`'s `approvals`, and must be listed among that run's own rows: otherwise the check fails, named for
  the run, as in `approval provenance: T` with `the harness approved plan r2:..., which no plan row lists`. A
  program's plan run is now judged once its plan is approved, since the program never relaunches it and it never
  completes. A components run's `auto_approve` rows name no run. A passing check now ends by naming the gates it
  matched, for a scenario of one run too; nothing else changes there.

### Changed

- Native Windows support is no longer planned; WSL stays the supported Windows path. [Install](docs/install.md) says
  so, and [Native Windows](docs/windows.md) keeps the audit and the open work for whoever takes it up again (#698).

- The Builder retry policy now weighs each failure's evidence against the milestone's earlier failures. A failure
  with the same failing checks, criteria, open findings and changed files as an earlier one skips the remaining
  ordinary retries for the stronger attempt, or pauses once that is spent, and the stop says it repeated. A failure
  whose earlier failing items are all resolved, and that fails only on new ones, does not count toward the limit.
  Each decision records which it was as `novelty` (#611).

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
- The money cap (`autocode_cost`), token usage (`autocode_usage`), efficiency measurements (`autocode_efficiency`),
  evidence report publication (`autocode_evidence_export`, except `record`), `--status` rendering
  (`autocode_status_command`), progress updates (`autocode_status`, except `persist`), runner checks
  (`autocode_runner_check`), review report schemas (`autocode_report_schema`), the recovery chain
  (`autocode_recovery_dispatch`), protected test gates (`autocode_protected_oracles`), planning metadata recovery
  (`autocode_planning_metadata`), the output display policy (`autocode_output_policy`), timeout changes
  (`autocode_operational_change`), network retries (`autocode_network_retry`), Investigator report recovery
  (`autocode_job_report_recovery`), the report format correction (`autocode_format_correction`), the repeated-failure
  ledger (`autocode_failures`), draft example corrections (`autocode_draft_examples`), the conversation projection,
  policy and ingress (`autocode_conversation_projection`, `autocode_conversation_policy`,
  `autocode_conversation_ingress`), original-brief evidence (`autocode_brief_evidence`), the CLI's resolver-answer and
  decision steps (`autocode_action_resolver`, `autocode_action_decisions`), accepted source edits
  (`autocode_accepted_source`) and the test contract fixtures (`goal_fixtures`) take the typed `RunState`, so the
  untyped-`state` ratchet drops from 287 to 201 and mypy checks those 86 function bodies. Type-only: no behaviour,
  output or `state.json` change (#695).
- Operator prerequisites (`autocode_task_preflight`, `autocode_preflight_contract`), the first serial repair
  assignment (`autocode_rework_policy`), saved-run replay (`autocode_replay_transport`, `autocode_replay_source_trace`),
  requested Go test names (`autocode_native_test_names`), the approved source scope and contract approval
  (`autocode_source_scope`, `autocode_contract_identity`), each stage's context packet and the units' model request
  (`autocode_stage_context`, `units/common`, `units/autocode`), run setup and configuration (`autocode_run_setup`,
  `autocode_configure`, `autocode_containment_policy`, `autocode_verification_config`, `autocode_base_patch`), the
  isolated Builder and its retries (`autocode_builder_worker`, `autocode_builder_recovery`, `autocode_worker_limits`),
  result and repaired-report application (`autocode_result_application`, `autocode_repaired_result`), recovery
  (`autocode_validation_recovery`, `autocode_report_retry`, `autocode_recovery_progress`, `autocode_recovery_grants`,
  `autocode_recovery_inputs`, `autocode_provider_recovery`, `autocode_budget_recovery`, `autocode_action_recovery`),
  human review (`autocode_human_review_artifact`, `autocode_review_rejection`), the plan and its revisions
  (`autocode_contract_revision`, `autocode_draft_assignment`, `autocode_program_guidance`, `autocode_planning_graph`,
  `autocode_repair_provenance`, `autocode_base_examples`, `autocode_toolchain_requirements`), verification and visual
  evidence (`autocode_verification_inspection`, `autocode_verification_view`, `autocode_visual_profile`,
  `autocode_visual_acceptance`), request usage (`autocode_request_usage`), risk disclosure (`autocode_risk_disclosure`),
  task worktrees (`autocode_worktrees`, `autocode_workspaces`), execution checkpoints (`autocode_checkpoints`),
  joint-planning transports (`autocode_joint_transport`), failed-job source captures (`autocode_job_source`), design
  revisions (`autocode_design_revision`), the CLI's edit step (`autocode_action_edits`) and a UI run's accepted plan
  (`autocode_ui`) take the typed `RunState`, so the untyped-`state` ratchet drops from 201 to 110 and mypy checks those
  85 function bodies. Type-only: no behaviour, output or `state.json` change (#695).
- No module in `tools/` is in an import cycle any more. Which role and engine run a stage (`autocode_stage_roles`), the
  planning cycle (`autocode_planning_cycle`) and the units' `ModelRequest` type (`units/model_request`) moved unchanged
  out of the planning unit (`units/autoplanner`) and `units/common`, which re-export them under their old names. That
  took the last six modules out of the cycle: the goal lifecycle, reviewer routing (`autocode_workflow`), the planning
  unit and its `autocode_planning` shim, `units/common` and the stage context. `tests/test_architecture.py` now fails
  when any module joins a cycle. Structure only: no behaviour, prompt, output or `state.json` change (#695).
- Under a milestone budget you set (`--max-milestone-seconds N`), a climbed Builder attempt (the milestone's
  stronger attempt or a climb on the Builder's effort ladder) is timed before it launches, from the last Builder
  attempt's duration held to the stage time limit. When the milestone's counted time plus that projection would
  pass the budget, the run pauses `PAUSED_MILESTONE_BUDGET` before the attempt, naming the climb, the projection,
  the milestone's time so far, the total and the budget to resume with; `--resume-paused --max-milestone-seconds M`
  with `M` at least that total launches exactly that attempt. Before, the milestone budget was checked only once
  its time was already spent, so the climbed attempt launched. The cost cap and the run's active-time budget are
  checked first. The new-run default, a delegated budget, a progressive run's slice budget and a budget given with
  `--max-milestone-seconds` while turning checkpoints on for a saved run (`--milestone-checkpoints` on a resume, or
  `--request-milestone-checkpoints`) are not projected, and Tester and Completion Reviewer climbs are not checked
  against it. On every `PAUSED_MILESTONE_BUDGET` stop, the advice AutoResolver's request appends to its decision
  and the stop reason now names `--max-milestone-seconds` instead of `--resolver-response provide_information`
  followed by a resume. For a progressive run's slice-budget stop, which shares the status, it asks for a slice
  budget above the slice's counted time, which a resume does not restart, and `--max-seconds` when the stop names
  `run_seconds`. The request's options, the status view's `needs.action` and the progress line still offer that
  response, which AutoResolver holds at this stop as before; what releases the pause is unchanged (#611).
- A rework loop that stops because a repeat brings no new evidence (the recovery novelty hold, "No causal
  progress for the same incident") now says what the run has spent of its cost cap. With `--max-cost-usd`, the
  hold's reason, in the line printed when the run stops and in the stop reason `--status` shows, is followed by
  the `Cost:` line `--status` prints: "... authorizes one attempt under existing limits. Cost: $0.53 so far; cap
  $5.00." You can see what is left of the cap before the one scoped retry the hold admits. Without a cap nothing
  is added, as the cost stops do. The pause status, exit code and request kind are unchanged. The hold's own
  record and AutoResolver's request keep its reason without the spend, which is read again whenever the stop is
  written: a cap changed at the hold, with or without `--resume-paused`, is the cap the stop then states, and
  removing the cap removes the `Cost:` line. The `rework-same-failure` scenario's oracle now checks that the stop
  states the status view's spend and cap (#611).
- In a `--package` bug fix, the Investigator is told the package (#711). Its handoff gains `package_scope`: the
  package, `suite` (the command the run tests the package with, the suite detected there or `--test-command`) when
  the run has one, and an instruction to name only files inside the package. A reproduced diagnosis whose
  `affected_paths` or `test_paths` name anything outside the package is refused as invalid output, so it goes
  through report repair with the reason and the run pauses rather than plan from it; before, it was accepted and the
  fix was planned from it. When the fix does need a file outside the package, the instruction and the reason tell
  the Investigator to keep naming it, so the run pauses: such a fix needs a run without `--package`. The diagnosis
  note stays under `docs/bugs/` at the repository root and is still left out of the check for changes outside the
  package. The scripted Investigator in `scenarios/` names only the package's files when its handoff names a
  package, so `bugfix-monorepo-package`'s broken variant still reaches the Builder.
- Native Windows (#698): the last thirteen runtime modules that imported `fcntl` at load (the checkout, registry,
  intervention, milestone-request, usage, worktree and conversation locks among them) now lock through
  `autocode_util.lock_file` / `unlock_file`, so the CLI loads without `fcntl`. On Linux and macOS each lock is the
  same exclusive `flock`, blocking or not, with the same busy error. The registry, intervention and conversation locks
  unlock only a lock they took, so on Windows a timed-out or interrupted wait keeps its own error. `tests/test_windows_portability.py` now
  allows no module-level POSIX-only import and loads the CLI with `fcntl` unimportable. On Windows the CLI still
  stops loading at `autocode_junit`'s POSIX-only open flags; [Native Windows](docs/windows.md) records that and
  the POSIX-only calls the thirteen modules still make.

### Removed

- The dashboard's separate "Recover saved work" control for an interrupted attempt (`recover_stage`). It ran
  `--abandon-stage` after checking only the attempt id, not the recovery card's token, so other changes to the
  saved pause after inspection went unchecked. The same control is on the recovery card, bound to the saved
  pause; a task without a card asks for a refresh instead of offering an unbound command (#730).
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

- When a role is copied mid-run from an existing role (such as Analyst or Investigator in the discuss
  and bug-fix workflows, Architect in the design workflow, Acceptance Tester in spec-first runs, Resolver,
  Stuck Investigator, or Utility route), it now inherits the source role's saved transport tool identity,
  preventing missing-identity failures when using external role transports such as `--role-transport` (#1426).
  A derived role whose own route stays on the run's tool (a Resolver with its own `--resolver-model` on
  OpenCode while the Completion Reviewer moves to another tool) inherits nothing and keeps that route. Before,
  it was handed the source role's identity, and every start then refused with "Saved role transport does not
  match its immutable route".
- When the OpenCode planning roles receive the complete Figma manifest and catalog as SHA-256 and
  byte-bound file references, their handoff now also carries the complete case roster inline (ID, route
  and state of every case, in manifest order), as Builder and Tester handoffs already did. Before, the
  case IDs were only in the referenced files (#1263).
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
- That planning shell also can no longer read these `.autocode` stores, which hold other runs, conversation
  records, investigation workspaces or what they keep: archived and bundled runs (`archive`, `bundles`), `autocode arena`
  attempts (`arena`), parallel Builder workspaces (`builders`), requirements conversation handoffs (`conversation-handoffs`),
  conversation start markers (`conversation-starts`), bug investigation workspaces (`investigation`), `autocode issue`
  worktrees (`issues`), `programs`, `autocode review-pr` checkouts (`pull-reviews`), the run `registry`, `replay`
  recordings, `task-flows`, `autocode verify` checkouts and reports (`verification`) and task worktrees (`worktrees`).
  A store is denied when it exists as the launch is prepared; none is created. Other `.autocode` directories, including
  other launches' `tool-containment-*` control directories, stay readable, as do its own scratch, project settings,
  retained design inputs and capture bundles. Each denial is in `private_reads`, the native-shell proof covers each store,
  and a profile missing any one of them pauses as `PAUSED_TOOL_CONTAINMENT` before the provider starts. So does a store
  first made after the profile was written, up to the launch's last check just before the provider is released: every
  check before then computes the denial again, including native preparation and the held launch's seal. That pause names
  the new store, and a plain `--resume-paused` prepares the boundary again with it denied. A store made after that last
  check, such as one made once the provider has started, causes no pause and stays readable to that stage until it ends;
  a later planning launch denies it. A store path that is a symlink, even a dangling one, or not a directory cannot be
  denied, so each native planning launch in that workspace pauses as `PAUSED_TOOL_CONTAINMENT`, naming the path: replace it
  with a real directory, or remove it, then `--resume-paused` (#1250).

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
- Native visual planning (`VISUAL_REVIEW_PROFILE=`): a Requirements, Planner or Plan Reviewer report that finished
  but failed validation now gets the ordinary bounded report-only repair of the same stage on the same saved route
  and model, through the run's native writer, instead of pausing at once with `PAUSED_INVALID_OUTPUT`. A resume does
  not renew a queued repair's attempts, `--retry-report` stays refused, and the planning review allowance is not
  charged for a repair. A resume that gives that stage's role a new model or reasoning effort archives the pending
  repair and runs the stage again as a fresh attempt on the new route, as before. Warning: that resume discards the
  repair even when none of its attempts has run, the fresh attempt is charged (with the two-review allowance spent,
  `autocode resume --plan-reviewer-reasoning-effort low` can stop the fresh Plan Reviewer at
  `PAUSED_PLANNING_BUDGET`), and restoring the old model later does not bring the repair back; to keep the repair,
  resume without changing that role's model or effort. A repair launch that stops
  (timeout, output limit, cleanup) is closed with `--abandon-stage`, as an original planning stop is. A native run
  no longer sends a stage pause to an Investigator it cannot run, so that pause keeps its reason and spends no
  investigation; a Builder failure classification still pauses as before. The final plan review (`plan_finalize`)
  now supports bounded report-only repair and publishes reviewed-plan records into the native visual protection
  journal before approval; and unparsed OpenCode planning launches that left the launch unclosed at
  `PAUSED_INVALID_OUTPUT` support operator closure via `--abandon-stage` without dispatching an agent (#1257).
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
- A native planning launch whose recorded private-read denial was weakened, such as one that no longer lists the
  run store, still pauses as `PAUSED_TOOL_CONTAINMENT` but no longer claims that store was made after the boundary
  was prepared; the store-specific pause now needs a boundary that otherwise verifies against its proof. A test
  also fails when a module renames a `.autocode` store the planning shell must not read (#1250).
- Under `--json`, a setting the run's configuration refuses as the run loads (such as `--single-model` or
  `--spec-first` on a saved run, or `--single-model` with a per-role model flag) printed nothing on stdout.
  It now prints the saved run's status payload with `result`, whose `error.code` is `input_rejected`
  (`{"result": …}` alone before a new run's first checkpoint). The invocation still ends with `autocode: …`
  on stderr and exits 2, and the refused setting is not saved (a new run without `--in-place` may already
  have created its task worktree and branch, which `result.message` names). A native visual run
  (`VISUAL_REVIEW_PROFILE=`) still saves such a refusal as a `PAUSED_INVALID_OUTPUT` pause, printed like any
  other pause with `error` null (#714).
- A native planning launch whose recorded private-read denial leaves out the run store, or a store its pinned
  profile still denies, still pauses as `PAUSED_TOOL_CONTAINMENT` but no longer claims that store was made after
  the boundary was prepared, even when its conformance proof was pinned again to match. The store-specific pause
  now needs a boundary that otherwise verifies against its proof and names only stores its profile does not deny.
  A test also fails when a module renames a `.autocode` store the planning shell must not read (#1250).
- Under `--json`, a tool-containment refusal as the run loads (`--allow-uncontained-tools` on a run with no
  built-in OpenCode stage, or a built-in OpenCode run without it where the kernel tool boundary is unavailable)
  printed nothing on stdout. It now prints like any other refused setting: the saved run's status payload with
  `result`, whose `error.code` is `input_rejected` with the refusal's text (`{"result": …}` alone before a new
  run's first checkpoint). The text, the closing `autocode: …` on stderr, exit code 2 and the saved run are
  unchanged. A native visual run already saved such a refusal as a `PAUSED_INVALID_OUTPUT` pause and still
  does (#714).
- Pull request CI ran the `linux-tool-containment` job, which runs the contained fake OpenCode flow under
  bubblewrap, only when one of four named files changed. A pull request that broke the flow through another
  file it loads, such as the status view (`autocode_run_view.py`) or the containment setup check
  (`autocode_containment_policy.py`), skipped the job, and the break first showed on master. `tools/ci_scope.py`
  now works out the Python files from `tests/test_contained_fake_cli.py` itself. It follows their import
  statements and the scripts they start or copy by file name, so the list stays right as imports change. A
  change to the stage report schemas in `tools/autocode-schemas/` runs the job too. That covers most Python
  files in `tools/` outside the dashboard, so most pull requests that change runtime code now run this job too,
  on its own runner beside the suite job. A missing test module fails CI's job selection instead of skipping
  the job (#709).
- With the Builder's retry policy off (a reviewer routing mode, or `builder_retry` disabled), the status view's
  `escalations` row for a Builder climb read `passed` once the Tester passed a climbed attempt, even after the
  Completion Reviewer sent that work back: nothing charged the review, so the climb's attempts stopped at that PASS,
  leaving the next climbed attempt, its failed check and its cost out while the cost said complete. The correction
  task the review assigns (`REWORK`) now sends that work back as the retry policy's charge does, so the row reads
  `failed_again` with every climbed attempt and its cost (#612).
- A milestone-budget stop before a repair's Builder launched could not be resumed as it advised. The repair's
  recovery packet bound the milestone budget with the run's other settings, so
  `--resume-paused --max-milestone-seconds M` stopped at `PAUSED_STALE_HANDOFF` ("Recovery packet: current source,
  task, settings, contract or scope changed before admission") instead of launching the attempt, such as the
  stronger attempt a known correction chose. The packet now leaves the milestone budget out, as it already left out
  the iteration limit, the active-time budget and the cost cap: those decide whether an attempt may launch, not
  what it acts on. AutoResolver's one-time extension of a default milestone budget changes the same setting and no
  longer counts as a settings change either. A binding saved before this change, on a packet, a repair admission,
  a novelty hold, a retry grant or a retained diagnosis, still holds while the run's settings are unchanged, and
  stops holding when the milestone budget changes. One recorded after the upgrade leaves the budget out, even for a
  packet saved before it: the repair admission written when the Resolver's plan finishes lets that repair launch
  after a milestone budget change (#611).
- The status view's `escalation_outcomes`, the runner's own stamp on each escalation and Builder retry, disagreed
  with the `escalations` row for the Builder's stronger attempt. A run that completed on that attempt's work left it
  and the ordinary retry before it `open` while the row read `passed`: the runner stamped `passed` only where the
  Completion Reviewer or Resolver assigned a next task on a PASS, never at completion. And where a review sent work
  the Tester had passed back and the retry policy made the stronger attempt, that same assignment stamped it
  `passed` before it ran. The runner now also stamps every open one `passed` when the run completes, and at an
  assignment leaves the Builder's own open when the work was sent back after the validation's attempt started (a
  retry-policy charge or a correction task anywhere in the run, which the rows take as a failure after a PASS of the
  escalation's own milestone), or when that attempt's start is not recorded, so a send-back cannot be ruled out
  (`autocode_route_ladder.record_pass`). In `escalation-converts`, `escalation_outcomes` now reads 2 passed and 0
  open, as its row does, and the scenario's oracle checks it. A stamp still ends with the struggle, so a climb a
  later climb replaced reads `passed` there and `failed_again` as a row; `docs/models.md` and `docs/task-run.md`
  say which to read (#612).

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
