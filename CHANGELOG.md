# Changelog

All notable changes to AutoCode are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each release lists the pull requests that changed user-visible behaviour.
PR template asks for an entry here; a change without one is incomplete.

## [Unreleased]

### Added

- Core Requirements conversations can be started, reattached, queued, refreshed and handed off through the CLI and TaskRun on one durable unapproved run. Accepted drafts and independent review are visible without the dashboard (#1011).
- Named proof repeats its proving tests three times (and a `test:` test three times
  on the original code); a test whose runs disagree is `unstable`, does not count
  as proof, and stops completion with the next step (#700). The same proof reports
  which changed product hunks no proving test executed, using the project's own
  coverage tool when it has one (`evidence.diff_coverage`, report only) (#701).
- `autocode completion bash|zsh|fish` prints a shell completion script read from the
  CLI's parser: commands, `program`'s commands, and the main command's options with
  their choices and file names (#714).
- Replay saved task runs offline through the public task-run interface, export
  bounded recordings, and refuse missing source or dispatch divergence. Fresh
  checks run normally; explicit recorded-check simulation cannot establish
  current completion (#708).
- Declare a planned, pinned 26-attempt reliability campaign with original task limits and complete outcome accounting; no campaign results are claimed (Refs #634).

- `autocode demo` shows planning, demo approvals, building, refused evidence and
  completion with the bundled offline provider; no account or model spend (#689).

- Add `autocode review-pr URL` (also `autocode-issue review-pr`) to review an exact fetched PR head through public TaskRun, with authenticated findings/evidence and optional fully anchored GitHub COMMENT reviews.

- Verify an externally produced working-tree change, branch or patch with
  `autocode verify`, using caller criteria, independent review and fresh runner
  evidence without planning or building (#690).

- Record first question, approval-ready plan and Builder dispatch times in the
  task status and canonical evidence report; generated reliability tables retain
  the dated samples and recorded latency medians (#727).

- Task, program and component runs now publish a shared evidence report with
  explicit model provenance; TaskRun and issue pull requests use the same
  canonical report (#692).
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

### Removed

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

- Live runs work on macOS again. macOS has no seccomp, so providers there run
  under the portable lifeline and sampled ownership that runner commands use,
  with a one-line notice and no `session_boundary` in their receipt. The fallback
  is macOS only again: a Linux host whose boundary fails (wrong ABI, CAP_SYS_ADMIN,
  namespace mismatch) refuses provider exec, as #1187 intended (#1195).
- Restore the Resolver prompt regression fixture to the shipped role schema, preserving recommendation isolation and expected-failure command guidance (#1215).
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
- Restore master lint after the model-route advisory change by sorting the CLI's
  package and standalone import blocks (#1027).
- A quota-stopped Investigator accepts an explicit model answer, saves its preparation pin, and issues a fresh exact-retry token (#970).
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
