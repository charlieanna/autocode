# Changelog

All notable changes to AutoCode are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each release lists the pull requests that changed user-visible behaviour.
PR template asks for an entry here; a change without one is incomplete.

## [Unreleased]

### Added

- Task, program and component runs now publish a shared evidence report with
  explicit model provenance; TaskRun and issue pull requests use the same
  canonical report (#692).
- **KiloCode Alibaba Token Plan route**: the bundled `kilocode` provider now declares an
  `[[auth.routes]]` entry for `alibaba-token-plan/`, so a run using those models verifies the login
  is present in `api` mode before it launches, as it already does for `openai/`. That route serves
  Qwen (`qwen3.8-max`, `qwen3.7-plus`, `qwen3.6-flash`) on the operator's own plan, and DeepSeek,
  GLM, Kimi and MiniMax on the same login, so a verifier can come from a different vendor than the
  producer it checks.

- Compact `autocode --help` with complete `--help-all`, unified issue/arena/
  dashboard/unattended commands, and a guard requiring public-flag documentation.
  Compatibility console scripts remain internal aliases. Paused runs show one
  of four cause categories and the current scoped recovery command, preserving
  saved state names and all approval gates (#691).

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

- OpenCode request intervals now pair starts and finishes within the same
  message; ambiguous timing stays unknown while known usage remains available.
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
- Scope diagnosed bug regression cases to approved milestone test bindings, retain accepted cases, and require every case at final completion.
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
