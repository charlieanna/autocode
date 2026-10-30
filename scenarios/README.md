# Scenarios

`acceptance-phase-isolation` reproduces the stats-to-compatibility credential
leak with synthetic credentials and a real stdlib HTTP server on 127.0.0.1.
Its reference uses distinct declared phase roots; seed and broken controls
share credentials or swallow refusals during a call or teardown. Every variant
runs through the oracle subprocess boundary in a fresh sequence directory
beside the delivered project. Oracle evaluation leaves its source files intact.
A child exit of zero cannot override recorded unexpected requests: the raw
sequence stays ERROR and the catalog oracle rejects acceptance. Each record
includes roots, traffic identity, refusals and the started/polled/stopped smoke
lifecycle. Previous evidence stays untouched.
Refusal ledgers are created before execution in a separate observer directory,
outside the phase's mutable state. State teardown preserves recorded refusals;
a missing or corrupt ledger produces ERROR with unknown requests, never GREEN.

Use `harness.phase_env.PhaseSequence` for new acceptance sequences. Its phases
copy a frozen sequence environment, preserve HOME and provider OAuth, and add
their declared credential/config/state/cache roots. Pass `env=prepared` to the
sequence to omit ambient account variables without changing the parent process.
Bind application inputs explicitly with `root_vars={"CLAUDE_CONFIG_DIR":
"credential_root", "HEADROOM_CONFIG_DIR": "config_root",
"HEADROOM_WORKSPACE_DIR": "state_root"}`; the generic `PHASE_*` variables alone
do not isolate applications that never read them. Bindings are copied and
validated before creating phase roots. Effective application roots and admitted
endpoints are included in every phase record.
Phase names must be distinct single path components; case variants and repeat
names are rejected before they can reuse roots or refusal logs.
Each sequence requires a fresh empty base and claims it exclusively. Reusing
a prior or already owned base fails before credentials or evidence can change.
Roots outside the sequence base are rejected before any directory or child
process can write through them. Extra environment variables may
add unrelated inputs; they cannot override HOME or any declared phase binding.
The guard allows declared loopback destinations, checks redirect destinations,
ignores ambient proxies, and records refusals before opening a socket. Existing
oracle calls without `env` retain ambient inheritance.

The catalog regression uses a standard-library consumer. A separate opt-in
qualification exercises actual Headroom HTTPX, Uvicorn, Rust SDK startup and
pytest TestClient lifespans against a frozen package/SDK copy and an existing
qualified interpreter. It does not install dependencies or build a new SDK:

```sh
.venv/bin/python -B scenarios/headroom_phase_check.py \
  --source /absolute/path/to/frozen-source \
  --python /absolute/path/to/qualified/headroom/.venv/bin/python \
  --out .scenario-runs/headroom-reference-new
```

The source directory must contain the declared `headroom/` package and a real
`_core` shared library. Symlinked inputs are refused. Every output directory
must be fresh. No private fixtures, bearer files or developer config should be
part of the source copy. The stats phase writes a synthetic credential and
uses an explicit in-memory usage-URL adapter to an owned loopback server.
Compatibility retains the production default usage URL and receives empty
credentials. Default sync/async HTTPX requests, DNS and socket connections are
observed before I/O across imports, pytest collection, setup, call and teardown.
Mock/ASGI transports remain local. These observers are acceptance diagnostics,
not a sandbox for hostile application code.

Run `--omit-application-binding` and `--share-credentials` with separate fresh
outputs to prove the original and broken environments remain ERROR despite
green pytest exits. `--inject-swallowed-step collection` (also `setup`, `call`
and `teardown`) exercises a production client that catches a transport refusal;
each must remain ERROR. `--policy path/to/phase_policy.json` accepts exactly
boolean `isolate` and `bind` fields for candidate comparisons. A passing
qualification exits zero; every negative control exits nonzero.

Receipts bind the frozen source/SDK, evaluator helpers, candidate policy when
supplied, effective phase roots, pytest cases, real server lifecycle and
refusals. Parent environment and input/helper hashes must remain unchanged.
Earlier V2 ERROR evidence is untouched; this qualification does not certify
the original private #3913 candidate, the full PR #3863 overlay or other
catalog sequences. See [the production qualification note](../docs/bugs/2026-10-02-headroom-phase-proof.md).

Realistic engineering tasks for AutoCode, each with an independent **oracle**
that judges the delivered project from the outside. The oracle, not AutoCode's
own completion claim, decides whether the work is right.

Scenario runs that launch supervised AutoCode task-stage providers, including
scripted `--fake` runs, use the provider process-ownership boundary on Linux and
portable supervision on macOS (no kernel boundary; see
[provider process ownership](../docs/providers.md#provider-process-ownership)).
See [test hosts and Linux setup](../docs/testing.md#host-requirements-for-tests),
including the existing capability drop for privileged Linux shells. Catalog
inspection and result reading do not launch task-stage providers; individual
oracles still need their own toolchains. Choose an affected scenario for a
localized fix; the full catalog is not a routine prerequisite.

```sh
PY=.venv/bin/python                               # AutoCode needs psutil from the project virtualenv
$PY scenarios/run.py list                         # the catalog
$PY scenarios/run.py check                        # prove every oracle (no AutoCode, no models)
$PY scenarios/run.py run bugfix-trivial --fake     # one scripted scenario on supported Linux
$PY scenarios/run.py run --fake                   # every scenario through AutoCode with a scripted model (no spend)
$PY scenarios/run.py run feature-stock-refusals --fake --hybrid   # rehearse a hybrid route (no spend; "Hybrid runs")
$PY scenarios/run.py run bugfix-iso-weeks --profile glm53-openai --i-authorize-live-model-spend
$PY scenarios/run.py route --fake                 # which workflow AutoCode recognizes for each prompt in routing.toml
$PY scenarios/run.py compare --fake               # AutoCode vs a plain agent on the same oracles (scripted; no spend)
$PY scenarios/run.py stats                        # per scenario, mode and AutoCode revision: runs, passes, streak, time
$PY scenarios/run.py build-compare greenfield-greeting-cli greenfield-todo-cli feature-timesheet-by-project parallel-diamond --fake --repeats 2 --jobs 4
$PY tools/run_suite.py --scenario-harness         # scenario tests and registered catalog CLI checks, in parallel
```

Results land in `.scenario-runs/<time>-<id>-<mode>/`: `result.json` (verdict,
every oracle check, CLI calls, answers given on the user's behalf, each with its
`source` (`person` for a scenario's own `[fake] answers`, `proposed_default`,
`approval`), the gates the harness passed for the person (`approvals`: the plan,
each human-review criterion, more planning review calls,
and `auto_approve` for each call of a components run launched with
`--auto-approve`, inside which the product answers its own gates, and
`acknowledge` for a budget pause the scenario's person continued; in a
components scenario, each gate given to one of its task runs also names that
run's `run_dir`), wall time,
stages with a per-stage count and time breakdown, report-repair rounds, model
time and tokens, `cost` (the final status view's `cost` block, #721: estimated,
provider-reported and known spend in USD against the run's cap, each `null` when
unknown and never zero; `null` as a whole for an interrupted attempt or a view
without one) and `pause_origin` (the pause an AutoResolver request for a person
stems from, such as `PAUSED_COST_LIMIT` behind `WAITING_FOR_USER`, so the stop
class names it)), `steps.jsonl`, the final `state.json`, `plan-files/` (each
plan the harness exported before approving it, below), and the delivered
`project/`, kept for inspection.

Besides the scenario's own oracle, three checks use AutoCode's public commands and
status, and each appears in `checks`. `canonical evidence: RUN` authenticates the `evidence.json`
and `evidence.md` pair of each task run that completed, and of a components
run that completed as a whole. `approval provenance: RUN` (#722) reads
`approvals` in the final `--status --json` view of each task run that completed:
the scenario's one run, or a components run's architecture run and each
completed component. A components run as a whole has no
`approvals` of its own and gets no such check. It requires:

- each row a person made to name the scenario's person, `Scenario Person
  <person@scenario.example.test>`. Every scenario project sets that identity in
  its own Git configuration; the seed commit's author is `Scenario`, so a record
  of the commit author instead does not pass;
- each such row to name this machine's host and a command of `autocode` with
  option names only, never their values;
- a row approved under the workflow policy to name no one;
- each plan and review approval the harness gave a run (`approvals` above,
  recorded by the ordinary driver and the [conformance](#conformance) client
  alike) to be listed among that run's own rows: a plan row with the plan's
  token and a review row with the criterion. Each budget pause the scenario's
  person acknowledged (`[fake] acknowledge`) must be listed too, as a `resume`
  row whose `pause_status` names that pause. The task runs of a
  components scenario share one `approvals` list, so there each gate the harness
  gave one of them also names that run's `run_dir`, and it is matched to that
  run's rows only: a plan approval listed only in another component's run fails,
  as `approval provenance: web` with `the harness approved plan r2:..., which no
  plan row lists`. The `auto_approve` rows of a components run name no run: under
  `--auto-approve` the product approves its components itself, so they are
  checked for provenance only. A passing check ends by naming the gates it
  matched, such as `each gate the harness passed for this run is listed
  (approve_plan)`.

`plan round trip` (#720) checks that a plan file reproduces its plan's goal hash
([Reviewing a plan in a pull request](../docs/workflow.md#reviewing-a-plan-in-a-pull-request)).
At each plan approval a run reaches (the scenario's one run, a components run's architecture run, and the
[conformance](#conformance) client's run alike), the harness first exports the
plan with `autocode plan --export plan-files/NN.md --json`, then reads the file
back with `autocode plan --check FILE --json`. That is the reader
`autocode --goal-file` loads a file with, and the goal hash it prints is the one
`--goal-file` records. The harness does not load the file: loading installs a new
plan revision, and the harness approves the plan it was shown. The approval's row in
`approvals` keeps both commands' output as `plan_file`, with the seconds they took
(about 4 s per approval on the fake provider). The check requires:

- both commands to succeed, and the file to read back with the goal hash it was
  written with, which must be the goal hash of the run's plan;
- the run directory to be as it was before them, file by file (size and
  modification time);
- when the final status view reports that plan approved, its
  `approved_contract.goal_hash` to be the exported one.

A masked export fails the check: its file does not reproduce the plan's goal hash,
and `--goal-file` refuses a masked file. The export reports `masked` whenever the
file's goal hash differs from the run's, so the flag cannot show that only
credential-shaped text changed; a mask that took plan text for a credential reports
it too. The two calls are no steps: neither the step budget nor `cli_calls` counts
them, but the time budget bounds them. A run that reaches no plan approval gets no
such check.

Like any oracle check, a failed one makes the verdict `FALSE_COMPLETE` when
AutoCode reported the scenario complete (for a components scenario, the whole
components run). When it stopped instead, as a components run can after one of
its task runs completed, the failure keeps the verdict from `PASS`: it is
`HONEST_BLOCKER`.

Each completed scenario and plain-agent baseline also starts with a
`failure_attribution` record: empty `evidence_refs`, and `null` for
`suspected_cause`, `confirmed_diagnosis`, `regression` and `confirmed_by`.
A failed verdict or a model diagnosis scored `CORRECT` does not fill these in.
After inspecting a saved failure, an operator can append an attribution:

```sh
$PY scenarios/run.py annotate .scenario-runs/RUN --report investigation.json
$PY scenarios/run.py annotate .scenario-runs/RUN --report investigation.json --reviewer 'Operator name'
```

The report contains `evidence_refs` with an observed `handoff`, a literal
run-relative `path` and its `sha256`, plus an optional free-text
`suspected_cause`. A `confirmed_diagnosis` requires an explicit `--reviewer`,
handoff evidence and a `regression` object naming `check`, `before` and `after`.
Each regression reference has a run-relative `path` and `sha256`; its pinned
JSON report must have the same nonempty `command`, a distinct `source_sha256`,
and a uniquely named Boolean check (`{"name": "...", "ok": false}` before,
`true` after), without an evaluator error. These are retained reports from the
operator's independent regression, rather than a model's diagnosis score.
The annotation command validates these reports; it does not execute a model
or rerun the scenario.

Annotations append under `RUN/failure-attributions/`, bound to the original
`result.json` bytes and their own content digest. The original result, verdict
and diagnosis score stay unchanged. `stats.load_results` projects the latest
annotation after rechecking the file pins. Changed evidence or damaged metadata
projects as unknown; it cannot revive an older confirmation. Old results remain
readable with unknown attribution. The aggregate stats table continues to count
the original verdicts and scores.

`run.py stats` reads those results back. One pass can be luck, so it reports
how many times each scenario ran in each mode, how many runs passed, and the
current streak of consecutive passes, with median wall time and model stages.
For the reliability campaign ([#455](https://github.com/charlieanna/autocode/issues/455))
each row also shows its attempts' stop classes (`passed`, `false_completion`,
`safety_stop`, `provider_failure`, `harness_failure`, `budget_exhaustion`,
`not_exercised`, `skipped`: why each attempt is not a pass, beside the verdict)
and, apart from each other, the decisions made for the runs (totals over the
row's attempts): the person's own answers, the defaults the driver served, and
the approvals it gave. A served default or an auto-served approval is the
harness deciding, so it is never read as zero-decision autonomy; elapsed time
(`wall min`) and `usage unknown` stay their own columns. So does spend: `spent
usd` is the median of the attempts' known `cost.spent_usd` and `cost unknown`
counts the attempts whose spend is unknown (nothing priced, interrupted, pending,
or saved before `cost` was recorded), never counted as zero. A components run's
`--auto-approve` counts as one approval per
call, however many gates the product answered inside it, so that column is a
lower bound there. A result saved before these were recorded has no `source` and
no `approvals`: an answer without a source counts as the person's if it was
flagged `explicit`, as an approval if it is a bare `approved` (a review
acceptance), and as a default otherwise; its plan approvals were never recorded,
so its approvals count is a lower bound and is only comparable with rows of the
same revision.
Fake and live modes are never combined, and neither are AutoCode revisions: a
row is one scenario, one mode and one revision, since a streak or a median
pooled across revisions describes code that never ran. The revision shown is the
commit the run was recorded against, `-dirty` when that checkout also had
uncommitted changes under `tools/`, and `?` for a result saved before the runner
recorded one. The command prints this checkout's revision above the table, and
`--revision COMMIT` narrows the table to one. `test_harness.py` also fails if the
tiny scenarios (`greenfield-greeting-cli`, `bugfix-trivial`) take more model
stages with the scripted model than they do today, so extra steps can't creep in
unnoticed (issue #15).

When a question has no usable default or concrete option, the driver leaves it
for the person and grades the public stopped view. It does not invent reporter
data or classify that human stop as a harness error. Explicit scenario answers
can supply those facts; a mixed answer batch is submitted only when every
question can be answered.

## Three levels

| Level | Command | What it proves | Cost |
| --- | --- | --- | --- |
| Oracle check | `check` | The oracle rejects the untouched seed, accepts the reference solution, and rejects each plausible-but-wrong variant in `broken/`. | seconds |
| Fake run | `run --fake` | AutoCode's real CLI, planning gates, approval, build, validation and completion work end to end for this kind of task. The scripted model plans from the brief and applies the reference solution. It says nothing about model quality. | seconds per scenario |
| Live run | `run --profile NAME` | How well AutoCode actually does the task with real models. | model spend; requires `--i-authorize-live-model-spend` |

`run --fake --fake-solution broken/<name>` makes the scripted model deliver a
wrong solution. Because its own checks pass, AutoCode completes, and the
harness must report `FALSE_COMPLETE`. That is how the harness itself is tested.

A new live failure becomes a permanent CI check the same way: reduce it to a
`--fake-fault` (or a `broken/` overlay), or export a
[replay recording](../docs/replay.md) and drive that. The process is in
[RELIABILITY.md](../RELIABILITY.md) ("Turn a live failure into a permanent
regression"). `scenarios/test_lying_matrix.py` enumerates every `broken/` overlay
under a fake run so none is judged `PASS`.

### Native test runners

The ordinary catalog includes bug fixes and features for Go, `node:test` and
TypeScript/Vitest. They run the installed language tools, including in fake
mode; only model responses are scripted. Use Go 1.25.5 and Node 22.18 or newer
with npm (CI installs Go 1.25.5 and Node 22). Node scenarios need npm because
AutoCode detects the project's `npm test` suite. TypeScript reproductions use
Node's built-in type stripping.

Vitest seeds use `vitest.config.cjs` and `server.host: '127.0.0.1'`. The pinned
Vite loader compiles the CommonJS configuration in memory, so read-only
verification does not write a temporary config into protected `node_modules`.
The numeric host avoids a localhost DNS lookup; tool commands keep their
existing network restrictions. Named and full suites use the ordinary
`node node_modules/vitest/vitest.mjs run` command.

Vitest seeds declare `[setup] npm = true` and carry lock files pinning Vitest
4.1.11 and Vite 6.4.1. Before a run or oracle check, the harness installs the
seed's exact dependencies with
`npm ci --include=dev --include=optional --ignore-scripts --no-audit --no-fund`.
The explicit includes keep the test runner and platform binaries available even
when host npm settings omit them. The cache and ready marker under
`.scenario-runs/npm-dependencies/` are bound to both manifests and the install
policy; an earlier incomplete cache cannot satisfy the current policy. Each
disposable project receives its own ignored `node_modules` directory.
Unit tests mock setup; scenario commands perform the explicit installation.
A setup failure is reported as a failure,
and missing native tools are reported as skipped rather than a passing test.

`go_change_checks` and `node_change_checks` in `harness/oracle.py` require real,
unskipped native cases, hidden behavior checks, existing guards and a failure
for every declared regression against the original implementation. Each seed
has a reference and at least two broken controls, including vacuous or missing
tests. The first-Go-package feature starts with C# and no Go module: a known
no-project diagnostic is accepted for its original-source comparison, while
the delivered and hidden suites must still execute real cases. Its new tests
must also fail against separate deliberately wrong policy values with the Go
module retained, so empty tests cannot satisfy the no-project exception. Run-mode
oracles also require the public named regression proof to cover each case and
match the Validator and independent check replay's source revision.

The bugfix briefs explicitly use investigation case IDs `C1`, `C2`, and matching
native test-name prefixes. Keep these IDs aligned when adding cases: a real Go
run produced passing `TestC1`/`TestC2` regressions but investigated them as
`T1`/`T2`, so the named-proof gate correctly refused their unmatched evidence.
Additional preserve cases need the same ID/name agreement.

`SCENARIO_FAKE_CMD_ONLY=<stage>[:<count>]` in the environment makes the scripted
model end that stage's first `<count>` calls (every call without a count, report
repairs and corrections included) with a shell command, `{"cmd": ...}`, instead
of its report, as a Codex-transport model did
([#512](../docs/bugs/2026-10-07-cmd-only-final-message.md));
`tests/test_cmd_only_report_cli.py` drives it. The scripted model answers a
same-session correction, which carries no handoff, from the handoff it kept for
that session beside its configuration, never in the project.

`run --profile NAME --provider OTHER` runs the profile's models and efforts
through another provider (`kilocode`, or any tool set up as in
`docs/providers.md`). The result records the provider, and its mode is
`NAME-via-OTHER`, so `stats` keeps it apart from runs through the profile's own
provider.

The stock `glm-mimo-kilo` profile pins GLM-5.3 roles to High, matching the bundled
Kilo GLM default; that route advertises Low/High/Max and rejects Medium before
inference. This corrects the profile for new runs. Provider overrides preserve
the selected profile's exact efforts, including historical profiles; they never
silently translate an unsupported variant. Saved runs and custom selections keep
their choices and must explicitly select a supported effort to resume.

`run --profile NAME --hybrid` scripts the stages a scenario's `[hybrid]` route
names and runs every other stage live; its mode is `NAME-hybrid`
([Hybrid runs](#hybrid-runs)).

A fake run uses the Codex engine. `test_contained_opencode.py` runs
`greenfield-greeting-cli` on the OpenCode engine instead, inside Linux bubblewrap
(#709). It puts the checked fake OpenCode client (`tools/fake_opencode.py`, copied,
never imported) on `PATH` beside the scripted model, which the client runs as its
`codex`, and sets `AUTOCODE_FIXTURE_QUALIFIED_OPENCODE=1` so the runner contains
its Builder, Tester and Completion Reviewer. Then it checks the oracle's verdict, that
every record of those three stages in the saved `state.json` names `bubblewrap` and the
version doctor reports, that a Builder write planted outside the project
(`AUTOCODE_FIXTURE_SHELL_ESCAPE`) is blocked, and the status view's `tool_containment`
and `tool_containment_backend`. Without a working bubblewrap, as `autocode doctor --json`
reports it, the test skips, or fails in CI on Linux. Any other problem doctor reports,
such as the fake's OpenCode version, is the fixture's and fails it everywhere. A pull
request that changes `tools/fake_opencode.py` runs it too (`SCENARIO_FIXTURES` in
`tools/run_suite.py`; [Conformance in CI](../docs/execution.md#conformance-in-ci)).

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `PASS` | AutoCode ended the way the scenario expects (completion, or a stop for the scenarios that expect one) and every oracle check passed. |
| `FALSE_COMPLETE` | AutoCode reported completion but the oracle found failures, or it completed a task it should have stopped on. The worst outcome. |
| `HONEST_BLOCKER` | AutoCode stopped (paused, waiting for a person) without claiming completion, where completion was expected. The oracle summary shows how far the work got. |
| `ERROR` | The harness could not finish (budget used up, no progress, a CLI crash) or the oracle crashed. |
| `SKIPPED` | A required tool is missing, or the scenario does not support the requested mode. |
| `NOT_EXERCISED` | The run ended well but never reached a stage the scenario exists to test (`[run] requires_stages`), or the oracle's [diagnosis](#diagnosis) found that the stage never ran on the failure the scenario plants, so it says nothing about that stage. **A failure for `run`'s exit status**, like any other non-pass: a check that never ran is not a checked box, and `run.py stats` shows how often a scenario is exercised. A false completion or error keeps its own verdict. |

`run` exits zero when every selected scenario is `PASS` or `SKIPPED` (a missing
tool, or a mode the scenario does not support — the harness could not run it,
which is not a product result) or carries `[run] known_failure`. Every other
verdict, `NOT_EXERCISED` included, exits nonzero.

### Diagnosis

Some scenarios test how a stage judged a failure, not only what was delivered:
did AutoResolver name the real cause and write a repair that worked (#59)? Their
oracle also defines `diagnosis(project, run)`. Its block is saved as
`result.json["diagnosis"]` (null for other scenarios and for skipped runs),
printed after the run verdict, and scored apart from it: `check()` judges only
the product, so a poor diagnosis of correct code is never a `FALSE_COMPLETE`, and
a good one never rescues a wrong delivery.

| Diagnosis verdict | Meaning |
| --- | --- |
| `CORRECT` | The stage ran on the failure the scenario plants, and every required check passed. |
| `INCORRECT` | It ran on the planted failure, and a required check failed. |
| `UNSCORED` | It launched on the planted failure, but nothing can be scored yet: no report was saved or applied by the runner (killed, crashed, or the run stopped first), or a required check could not be evaluated (the run stopped before the next build was proved) while every other one passed. |
| `NOT_EXERCISED` | The planted failure never reached the stage. A `PASS` or `HONEST_BLOCKER` run verdict then becomes `NOT_EXERCISED` too, with the oracle's reason; `FALSE_COMPLETE` and `ERROR` keep theirs. |
| `ERROR` | `diagnosis()` crashed or returned no known verdict. The run verdict is unchanged. |

Every block has `verdict`, `reason` and `checks` (the required checks, each
`{name, ok, detail}`); other keys are reported, never scored. `run.py stats`
counts, per scenario and mode, the `diagnosed` runs (`CORRECT`, `INCORRECT` or
`UNSCORED`) and each of those verdicts. The checks on what a diagnosis says are
word lists (regular expressions), so the `correct` column is a lexical verdict:
a live attempt is read by a person before it is cited. With the scripted model
the diagnosis is written from the handoff, so a fake `CORRECT` proves the route
and the scoring, never how well a real model diagnoses.

Which AutoResolver calls count is `harness/resolver_calls.py`'s, from the
runner's saved records only. A call counts when it is `astra_resolve`, not
runner-owned, and launched with a model, unless the scripted side of a
[hybrid run](#hybrid-runs) answered it; report repairs, `astra_diagnose` and
Investigator calls never count on their own. A report the runner rejected and
then accepted after a report-only repair (`astra_resolve_report_repair` at the
same source) is one accepted call, scored on the repaired report. A call the
runner never applied (still in `active_stage` when the run stopped, or with its
report repair still running) cannot be scored. The first accepted call on the
planted failure is scored, else the first saved one, which then fails
`diagnosis_accepted`. Where an oracle needs the source a call saw, it rebuilds it
from the runner's code checkpoint for that source (a Git commit), else from a
stage's saved `git diff HEAD`, kept only when every file matches the stage's
snapshot.

In `feature-stock-refusals` the planted failure (the trap) is read from the
runner's own record: a regression proof that failed because the test of a
planned case passed on the original code too (it is under `pass_to_pass`). The
case is matched to its test as the runner matches it (the approved exact test
name, else the case id's words in the test name), and only tests about `move` or
`remove` are the trap, read from the source at that revision; a receive case
tagged `test:` instead of `guard:` fails the same proof for another reason. When
that source cannot be rebuilt every such test counts, and `trap_tests_read_from`
says so. Required:

- `diagnosis_accepted`.
- `diagnosis_names_each_vacuous_test`: by function name or its short `test_cN`
  form, in the diagnosis or the task.
- `diagnosis_explains_why_they_pass_on_original_code`: the diagnosis itself
  gives the cause (the command does not exist there: unknown, invalid choice,
  not implemented, no subparser; a bare "argparse" is not enough, since argparse
  also refuses a bad quantity), the exit status 2 and the original code.
- `resolver_chose_bounded_test_repair`: REWORK, an `implement` task naming
  `tests/test_stock.py` (in the task or the report's `affected_paths`, which
  scope the next Builder task), no `stock.py` in `affected_paths`, and no task
  to change `stock.py` or its exit-code contract. BLOCKED fails.
- `repair_does_not_weaken_tests`: no skip, expected failure, relaxing, deletion
  (unless the same sentence replaces the tests) or retagging a trap case
  `guard:`. A negated mention ("do not skip them") is not weakening.
- `resolver_stayed_read_only`: the runner enforces it; recorded because #59 asks.
- `repair_made_the_tests_discriminate`: in the proof of the next build (or its
  accepted report repair) each trap case has a test under `fail_to_pass`, by the
  runner's own match (`case_tests`), so a repair that replaces or renames the
  vacuous test counts. That proof may still fail for another reason; its verdict
  is in the detail. The Builder also
  reads the Completion Owner's findings, so this is evidence that the repair
  worked, not that the Resolver alone made it work.

Reported only: `review_already_named_cause` (the same word lists over the
Completion Owner's findings and task: in the three real 2026-10-04 Resolver calls
the review had named the cause first, so a correct diagnosis is often a
confirmation), `resolver_added_beyond_review`, `resolver_calls_on_trap`, `model`,
`cost_usd`, `pending`, `trap_calls` (every scorable counted call at the trap,
scored the same way), `unscorable_calls` (the other counted calls there: no
report saved or applied), and `other_resolver_calls` (calls at other revisions,
kept for a human read).

`feature-refund-window` counts calls the same way. A call is on its planted
failure when the source it saw fails the hidden `WindowTests` or `CapTests`, the
classes of the two planted defects (another failing hidden test, or source
without `shop/refunds.py`, does not count); then `diagnosis_accepted` and
`resolver_named_a_planted_defect` (by words for the store-time window or the
running cap) are required.

The driver answers AutoCode's clarifying questions with AutoCode's own proposed
default or the first concrete offered option and records each answer in `result.json`.
If neither exists, it requires explicit fixture or person input; it never invents
an answer for a freeform question. It approves the plan it is
shown and accepts requested human reviews. It never writes AutoCode state and
does not resume paused runs: a pause is reported as `HONEST_BLOCKER`. The one
exception is a scenario's explicit `[fake] answers`: a person's own answer to a
question the driver never answers by default (a quota stop's `route-sol`, for
example). The driver gives that answer, then resumes the pause it leaves once.
Likewise a scenario's explicit `[fake] acknowledge` is the person continuing a
budget pause only they own, such as the cost cap they set (`PAUSED_COST_LIMIT`,
#721): it maps the pause status to the flags they type with `--resume-paused`.
When the status, or the `pause_origin` of AutoResolver's request for a person,
names that pause, the driver calls `--resume-paused` with those flags once
(never twice for the same pause), records an `acknowledge` approval, and passes
the new values on every later call in place of the scenario's `[fake] flags`.

A scripted fixture that deliberately needs several planning passes can declare a finite
`[fake] plan_review_call_limit` (an integer of at least 2). Every fake run has its own empty
`XDG_CONFIG_HOME`, so your `~/.config/autocode` never applies to it, and fake setup writes that
starting allowance to its `autocode/config.toml`. This uses the public user
configuration before the run starts; it does not disable review accounting or authorize a
new allowance when a run pauses. Undeclared fixtures retain the normal default.

## Hybrid runs

Some failures a real model rarely makes on cue. In six live `claude-tiers` runs
of `feature-stock-refusals` (2026-10-05) no Builder wrote the vacuous refusal
tests, so AutoResolver never saw the trap (#59). A scenario can declare a route
that scripts the stages producing its failure and leaves the stage under test
live:

```toml
[hybrid]
scripted = ["requirements_gather", "astra_discovery", "astra_challenge", "glm_revise",
            "astra_finalize"]        # every call of these stages
first_attempt = ["terra"]            # only the stage's first call in the run
```

`run ID --profile NAME --hybrid --i-authorize-live-model-spend` answers those
calls with the scripted provider and the scenario's `[fake] fault`, and every
other call, later attempts of a `first_attempt` stage included, with the
profile's own tool. A report-only repair goes to the side that served the stage
it repairs. `run ID --fake --hybrid` rehearses the route with no spend: a
scripted stand-in takes the live side, so it proves the routing and the labels,
nothing about a model.

How: the harness writes a config-registered tool named `hybrid`
(`harness/hybrid_stage.py`) under the run's evidence directory and points
AutoCode at it with `XDG_CONFIG_HOME`; your own provider configs are read, never
written. Each call reads its stage from the handoff, appends a row to
`hybrid/trace.jsonl`, and runs either the scripted provider or the live tool's
own command, filled with the same values and run with the environment the
harness started from. The `hybrid` tool keeps the live tool's roles, models,
version command, login checks and Builder retry policy (`[builder_retry]`), so its
Builder escalates as a natural run's does. The live tool must be registered with a TOML file and
`output = "report_file"`; built-in OpenCode and Codex, the built-in claude provider (so
`claude-tiers` hybrid batches), and tools with sessions, are `SKIPPED`. A user `claude.toml` is never
used for them: the name `claude` is the built-in provider. Through such a tool the scripted
provider cites capture receipts, never Codex event ids, as the tool contract
requires.

Hybrid results never mix with natural ones:

- the mode is `NAME-hybrid` (`fake-hybrid`), so `stats` and
  `examples/claude-provider/batch.py` count and show them apart;
- `result.json["hybrid"]` holds the route, every call with the side that served
  it, and the model stages each side served (`scripted_stage_names`,
  `live_stage_names`);
- a scripted call counts for nothing: `requires_stages` needs a live stage, and a
  diagnosis counts only the Resolver calls the live side served
  (`scripted_resolver_calls` reports the others).

For `feature-stock-refusals` the route scripts planning, whose plan has one
`test: test_cN_...` case per reference test, and the first Builder, which
delivers `broken/vacuous-refusal-tests`: a correct `stock.py` with refusal tests
that also pass on the original code. The regression proof then fails on planned
move and remove cases by construction. Planning is scripted too because the
runner matches each planned case to a test by its approved name or case id: a
live plan names its cases its own way (`AC4`, `test_ac5_...`), and the scripted
Builder's fixed tests would fail the proof as missing, not as vacuous. The
Validator, the Completion Owner, AutoResolver and the repair Builder are live. A hybrid `CORRECT` is narrower than
a natural one would be: a real Resolver diagnosing a failure no model made, on a
plan no model wrote. Whether AutoResolver is called at all still depends on the
live Completion Owner's REWORK; a run where it is not is `NOT_EXERCISED`.

## Fixed versus adaptive through completion

`build-compare` uses catalog briefs verbatim and judges finished projects with
their independent oracles. It repeats both modes in fresh projects, alternates
which mode runs first, keeps the same model profile and budgets, and retains
errors, skips and timeouts in the scheduled denominator. `plan-compare` still
stops at plan approval; its custom briefs cannot inherit the seed's build oracle.

For a bounded live comparison on the frozen October 2 defaults:

```sh
$PY scenarios/run.py build-compare greenfield-greeting-cli greenfield-todo-cli feature-timesheet-by-project parallel-diamond \
  --profile build-comparison --repeats 2 --jobs 2 --timeout-minutes 45 \
  --max-seconds 2400 --max-stage-seconds 600 --max-iterations 6 \
  --rate-card scenarios/api-pricing-2026-10-02.json --i-authorize-live-model-spend
$PY scenarios/run.py build-compare --rebuild .scenario-runs/<comparison-directory>
```

`protocol.json` freezes the revision, model profile, brief hashes, rate card,
budgets and scheduled pairs before execution. Each completed attempt is saved
separately; rebuilding reports calls no models and shows missing attempts. Live
API dollar estimates use individual OpenCode request finishes, deduplicate
replayed usage, include reasoning, rejected calls and report repairs, apply
cache rates and long-context surcharges, and leave unknown usage unpriced.
`API $ / pass` includes spend on failed attempts. These are standard API token
estimates, even when the configured connection uses a subscription; actual
billing, tool fees and unreported/incomplete-request usage are not established.
Fake runs show gate correctness and oracle sensitivity, never dollar savings or
model effectiveness. A short successful live sample is evidence to expand the
comparison, not proof that changing the default is safe for every job.
Add `--prepare` to save the exact protocol, including briefs and seed hashes,
without launching AutoCode or needing live-spend authorization.

Inspect recorded clarification answers before interpreting quality differences.
The driver accepts model-proposed defaults; a default can change an output
contract away from the original-brief oracle. Such a mismatch is not evidence
that a Builder ignored its approved plan. Retain the attempt and flag the changed
target rather than presenting its oracle score as a comparison on the same goal.

A scenario whose `scenario.toml` carries `[run] known_failure = "why"` is one
AutoCode is known not to pass yet. `run` still reports its verdict but does not
count it as a failure, and says when it starts passing so the key can be
removed. It is a ratchet: the scenarios describe the product AutoCode should
be, and the list of known failures is the distance left. It is also the only way
a scenario whose `[run] requires_stages` a given mode cannot reach — a stage
that mode's `[hybrid]` route scripts, for instance — stays out of the failure
count; the reason then names the mode that cannot exercise it.

## Workflows

AutoCode should recognize what kind of engineering job a prompt is and compose
the right stages, rather than pushing every prompt through
requirements → plan → build → test → review. The scenarios in this catalog
cover five kinds of job and check three things beyond the deliverable:

| Workflow | Prompt shape | Stages | Deliverable |
| --- | --- | --- | --- |
| `build` | make or change something | understand → plan → review plan → build → test → review | code |
| `bugfix` | a reported misbehavior | investigate → diagnose → fix → test → review | code + root-cause note |
| `review` | judge an existing change | review → test where useful → findings | `review/findings.json` (written by the runner from the Reviewer's report; the run is rejected if anything else changed) |
| `design` | judge or produce an architecture | understand → challenge → design | `review/design-review.json` |
| `discuss` | a question, tradeoff or investigation | investigate → conversation | a note under `docs/` |

1. **Which workflow ran.** The status view (`autocode --status`,
   docs/task-run.md) carries a `workflow` field naming one of the five,
   named by Requirements, the first stage of every run and follow-up turn
   (owner decision 2026-10-22, #1860), unless the user named it with
   `--workflow`; a flow without Requirements still has the Job recognizer
   (`recognize_workflow`) name it. Oracles check it through `run_checks`,
   together with which saved stages ran: a review must not dispatch a Builder
   or ask for plan approval; every job runs Requirements once, first, and then
   its own first stage (`after_requirements`: the Investigator for a bug fix,
   the Code Reviewer for a review), so a bug fix never gathers requirements a
   second time; a three-line fix must not get plan-review rounds. In fake mode
   the scripted provider names the job with keyword rules
   (`harness/fake_codex.py`, `recognize`), which proves the plumbing and
   nothing about model quality.
2. **Read-only jobs stay read-only.** Review, design and discussion may leave
   only their report behind (`only_changed_under`); the oracle reads
   `git status` in the delivered workspace.
3. **Negative controls.** For every "find the problem" scenario there is a
   sibling with no problem (`review-clean-pr`, `design-review-sound`,
   `bugfix-not-reproducible`): a reviewer that invents blockers, or a fixer
   that changes working code, fails.

The routing table `routing.toml` holds one-line prompts and the workflow each
should be recognized as; `run.py route` starts each one, lets AutoCode run a
single stage, and reads `workflow` from the status view.

Oracles receive an optional third argument, `run`, with the final status view,
the saved stage names, the questions the driver answered, the CLI calls it
made, and AutoResolver's accepted diagnoses (`resolutions`). It is `None` in `check` mode, so run-level checks contribute nothing
there and the reference/broken variants are told apart by files alone. How
well a stage diagnosed a failure belongs in `diagnosis(project, run)`, not in
`check()` ([Diagnosis](#diagnosis)).

### Conversations: follow-up turns in the same run

One conversation should move between workflows (issue #51): review a PR, then
"Fix them." builds from the review's findings in the same run, with no new
project and no requirements questions. A scenario says this with `[[turn]]`
tables in `scenario.toml`, in order:

```toml
[[turn]]
after = "complete"      # the only value: a follow-up continues only a finished run
say = "Fix them."
```

When the run completes, the driver says the message with
`autocode --follow-up TEXT` (an action on the existing run, like `--feedback`)
and keeps driving. AutoCode takes a follow-up only on a finished run: it records
a new turn, recognizes the kind of job again from the message, and, after a
review, plans the fix from the review's findings (docs/task-run.md). A stopped
or waiting run refuses it (docs/cli.md, "Waiting or finished"), so `catalog.load`
refuses any `after` but `"complete"`: a turn after `"stop"` or `"needs:<kind>"`
could never be said. A job whose report asks questions (a design review, a
discussion) completes, and the next turn is the reply. A run that stops before a
turn is said ends the drive with `TurnNotReached`: the verdict is judged as for
any stopped run, but never better than HONEST_BLOCKER, and the summary says
`stopped before turn N`. The
oracle's `run` gets `turns`: one record per turn, with the same keys as `run`
itself (the view that turn ended with, its stages, answers and CLI calls), so
`run_checks` can judge each turn on its own. Stages are assigned to a turn by
when they finished. Each turn record also carries `changed_files`: the files
that turn created, changed or deleted in the workspace, read from disk before
and after it (`.git/`, `.autocode/` and bytecode left out), so reports the
runner writes, such as a discussion's note, count for the turn that wrote them.
A later turn can overwrite what an earlier one wrote (a design review is revised
in place), so `kept_files` is a directory holding each of those files as that
turn left them: `turn-files/<turn>/` in the evidence directory, `0` for the brief.

The reference solution of a conversation is its end state, every turn's files
together. With the scripted model, `[fake] turn_paths` says which turn delivers
which files: one list of relative path prefixes per turn, the brief first, so a
three-turn scenario has three lists for its brief and two `[[turn]]` messages:

```toml
[fake]
turn_paths = [["docs/decisions/"], ["docs/design/"], ["app/", "tests/"]]
```

The scripted model tells the turns apart by the message the handoff's task
starts with, so no turn's message may begin another's, repeat another or begin
the brief (`catalog.load` refuses it, in every scenario with turns). With
`turn_paths` the scripted Planner also bounds each turn's plan to that turn's
paths, and declares the change of boundary from an approved contract the way a
real Planner must: one `permission_changed` row backed by the follow-up's receipt
(no catalog scenario reaches that row today: a build of a proposed design plans
afresh, and `tests/test_contract_revision.py` covers the guard).
`discuss-then-design-then-build` is the example of turns: a discussion's note,
then a design that follows it, then "Build it." implementing that design (AutoCode
checks it first as an approved design, then plans the build afresh from it).

When a job's report itself changes from turn to turn, the solution scripts each
turn's report in `.fake-turns/<turn>/<stage>.json` (`0` for the brief). Only the
Architect (`review_design.json`) reads one today; a revision's concerns carry
their `status` and `resolution`. `.fake-turns/` is never part of a project: it
is not materialized, listed or copied, and the solution's
`review/design-review.json` is the report the runner wrote in a fake run, for
`check`. `design-review-with-answers` is the example.

## Components and local Compose

`category = "components"` drives an architecture run, commits its design in the
fresh scenario project, builds components through `autocode components`, integrates
them, then invokes `--run-local`. The component tasks retain ordinary planning,
review and completion gates. See [task lanes](../docs/task-lanes.md#running-the-combined-system-locally).

`local-compose-two-services` connects a link service to an analytics service.
Each service's reference solution ships its own `tests/` (its component check
runs them, as a component that delivers code delivers the tests that prove it
under the changed-line rule, #2474), and each `broken/` variant ships the
plausible weak test a wrong Builder would write, so AutoCode believes the
variant done and only the independent oracle catches it.
Default `--fake` uses scripted models and a fake Docker CLI transport backed by
actual stdlib HTTP service processes; it does not contact a real Docker daemon.
Real containers require explicit `--local-docker`; live component runs without
that opt-in are skipped. Public sample images need no registry credentials, so
an anonymous Docker environment is sufficient; no authentication change is required.

The oracle starts a separate fresh stack and creates a random link unknown to the
product smoke check. It verifies health, real redirects, the click-event boundary,
repeated collection without double counting and unknown-link refusal. Run-level
checks also inspect emitted wiring, loopback-only ports, product smoke and cleanup.
The successful oracle path has 14 checks with fake transport and 15 with real Docker,
including independent real-engine cleanup; these are check counts, not pass evidence.
Each product/oracle project is separately torn down, and the real oracle checks for
remaining project-labeled containers, networks and images. Interrupted drives remain
`INTERRUPTED_UNGRADED` rather than grading unfinished work.

```sh
$PY scenarios/run.py run local-compose-two-services --fake
$PY scenarios/run.py run local-compose-two-services --fake --local-docker
$PY scenarios/run.py run local-compose-two-services --profile openai-only --local-docker --i-authorize-live-model-spend
$PY examples/local-compose/run.py                 # reference oracle, no models or Docker
$PY examples/local-compose/run.py --docker        # reference product with real local Docker
```

## Conformance

A `conformance` scenario is driven by `harness/conformance.py` instead of the ordinary driver:
a client that proves an outside program can complete a run with the task-run interface alone
(#1012). At each step it reads `autocode --status` and takes the one command the view names,
`view["next_command"]`, filling only a person's decisions (`ANSWER`, or a model the scenario
names). An active run has no `next_command`; its `continue` need takes `--no-chat`, as the
needs table says. Any other need it cannot act on from the view is a `ConformanceGap`, and
the run is an ERROR: fix the gap in the CLI or the status view, never in the client. The
client is also the acceptance test for an editor extension (#884): an extension may do
nothing this client cannot.

What a person does unprompted is in the scenario's `[conformance]` table, each at a moment
the view shows: `interrupt` sends Ctrl-C to the controller the view names as running, and
`feedback` queues text with `autocode intervention submit` while a stage runs. Each names
the stage code name (`stage`) and the job the view shows for it (`job`); `after_failed`
waits for a failed requirement. A pause that follows one of these is the person's, so the
client takes its named action, including an operational request's `needs.retry_action`;
any other pause is left for the verdict. With the scripted provider, the watched stage is
held (`ProviderBarrier`) until the client has looked at the view; with live models the client
polls the view while the stage runs. A scripted Plan Reviewer's first review is blocking, so
`run --profile NAME --hybrid` scripts that review and the first Builder attempt and runs
everything else live. The client's journal, one row per decision with what the view named,
is in `conformance.jsonl` and in the oracle's `run["conformance"]`. Each plan and review
approval it takes is recorded in `result.json`'s `approvals`, as the ordinary driver
records the ones it gives (a conformance result saved before #722 lists none, so its
approvals count in `stats` is a lower bound).

## Comparing with a plain agent

AutoCode adds stages so that its results can be trusted. `compare` measures
whether that pays off. It runs each scenario twice: once through AutoCode, as
`run` does, and once through a plain coding agent in a single call with the
same seed and brief. There is no planning, review or completion gate on the
agent's side. The same oracle then judges both deliveries.

```sh
$PY scenarios/run.py compare --fake                                        # plumbing only: both apply the reference
$PY scenarios/run.py compare bugfix-iso-weeks --fake --fake-baseline-solution broken/special-case
$PY scenarios/run.py compare bugfix-iso-weeks feature-timesheet-by-project \
    --profile openai-only --baseline opencode --i-authorize-live-model-spend
```

- **The agent:** `--baseline` picks `opencode` (the default, and AutoCode's
  default engine) or `codex`. Each is launched with the same command line
  AutoCode uses for that engine.
  - The opencode agent defaults to the profile's builder model;
    `--baseline-model` overrides it.
  - `--baseline-command 'CMD {project} {model}'` runs any other agent, with
    the brief on stdin.
- **Fairness:**
  - Give both sides the same model where you can.
  - Both sides share one time budget per scenario.
  - The agent is a single call. The comparison asks whether AutoCode's
    extra stages beat one good attempt, not whether AutoCode beats a person
    iterating with the agent.

**Scoring.** Each side gets a *deliverable* result and a verdict.

- **Deliverable:** the oracle with no run record, as in `check`. AutoCode-only
  process checks, such as workflow recognition, therefore don't count against
  the agent.
- **Verdict:** as in the table above.
  - The agent claims completion by exiting 0. A wrong delivery with exit 0 is
    therefore `FALSE_COMPLETE`, and a nonzero exit or a timeout is
    `HONEST_BLOCKER`.
  - A plain agent has no machine-readable way to stop. On scenarios whose
    correct ending is a stop (`expected = "stop"`), exiting 0 is a false
    completion.

**Output.** Results land in `.scenario-runs/<time>-compare-<mode>/`. The
directory holds:

- `comparison.md`: accepted deliveries, false completions and time for each
  side, plus one row per scenario
- `comparison.json`: the same data
- each side's own evidence: AutoCode's run directory as `run` writes it, and
  `<id>-baseline/` with the agent's log, the delivered project and
  `result.json`

A fake comparison proves only the plumbing and the scoring. What matters is a
live comparison with matched models and a recorded profile.

## Planning: today's pipeline vs adaptive planning

`plan-compare` plans each build request in `planning.toml` twice: once with
today's fixed AutoPlanner sequence (`--no-adaptive-planning`) and once with `--adaptive-planning`
([docs/adaptive-planning.md](../docs/adaptive-planning.md)). It stops each run
at the plan the user is asked to approve, so nothing is built. A request with
`feedback` sends it instead of approving that plan, and stops at the next plan
shown for approval. It reports stages, review calls and blocking concerns,
questions, tokens and model time side by side, with the feedback round in its
own columns. Under `blind/` it writes each request's two plans as Plan A and
Plan B (before and after the feedback, when there is one), with the key kept
separately, for judging plan quality without knowing which variant wrote which.

```sh
$PY scenarios/run.py plan-compare --fake                   # every adaptive path, scripted, seconds
$PY scenarios/run.py plan-compare --profile default --jobs 3 --i-authorize-live-model-spend
$PY scenarios/run.py plan-compare --rebuild .scenario-runs/<dir>   # re-render a comparison cut short
```

## Catalog

| Scenario | Category | What it exercises |
| --- | --- | --- |
| `bugfix-iso-weeks` | bugfix | Root-causing a reported symptom in a different module; hidden tests cover every day from 2000 to 2030, so a special-case fix fails. |
| `bugfix-go-interval-boundary` | bugfix | A half-open Go interval includes its upper endpoint; native boundary cases, hidden CLI checks and original-source proof. |
| `feature-go-first-package` | feature | Adds the first Go package to a C# seed without a Go module (#685); real Go cases and an explicit no-project original-source comparison. |
| `bugfix-node-interval-boundary` | bugfix | Touching or empty intervals overlap incorrectly; dependency-free `node:test` cases and hidden validation checks. |
| `feature-node-stable-sort` | feature | Numeric ascending/descending sorting with stable ties and preserved caller data; rejects reversed ties and vacuous tests. |
| `bugfix-vitest-half-open-bookings` | bugfix | TypeScript bookings treat touching endpoints as conflicts; pinned Vitest, hidden boundary cases and named original-source failures. |
| `feature-vitest-ledger-subtotals` | feature | Exact account groups and bigint subtotals without input mutation; rejects unsafe number conversion and a missing regression case. |
| `bugfix-duplicate-on-timeout` | bugfix | A retry after an uncertain timeout renews a domain twice. Hidden tests inject lost replies before and after processing; removing retries or raising the deadline both fail. Requires a root-cause note and no requirements gathering. |
| `bugfix-stale-prices` | bugfix | Checkout charges stale prices because cache invalidation is left to each write path. The fix belongs at the store's write path (every cache hears every write); either fix route passes, with no requirements gathering. Patching today's callers or dropping the cache both fail hidden tests. |
| `bugfix-cent-drift` | bugfix | Invoice, charge and refunds each round money their own way and drift by a cent. The fix touches every billing module, needs one half-up money rule, and leaves an accounting choice open (tax per line or per invoice), so it must take the planned path: diagnosis, Planner, Plan Reviewer, the user's approval, no requirements gathering. Deriving only the charge from the invoice, or rounding everything with float `round()`, both fail hidden tests. |
| `stuck-planner-citation` | bugfix | The stuck-stage Investigator, with a real model. Every stage is scripted except the Investigator (`--investigator-model openai/gpt-6-sol`, high); the scripted Planner repeatedly cites a nonexistent `.missing` sibling until repairs run out. Passes only if the real Investigator names the cause and its guidance gets the retried Planner through. Requires a real model call; interruptions or report repairs can add calls. Skipped without `--i-authorize-live-model-spend`. Its Investigator model is fixed in `scenario.toml` (`--investigator-model openai/gpt-6-sol`), so the scenario cannot be run live on Claude models without a product change to how the stuck-stage Investigator selects a tool provider. |
| `bugfix-trivial` | bugfix | An off-by-one, through the full bug-fix path: diagnosis after Requirements names the job, plan review and the user's approval, no second requirements gathering, no questions. Its proportionality checks (no plan-review rounds, at most five model stages) come back with the short path for small fixes. `broken/lone-partial-page` has a regression test that passes on the reference and on its wrong fix; the mutation check reports its surviving off-by-one (#699). |
| `bugfix-not-reproducible` | bugfix | The reported bug does not exist in this code. Passes by saying so or asking; a "defensive" change to working code fails. |
| `bugfix-monorepo-package` | bugfix | Issue #711: `feature-monorepo-package`'s check for a bug fix. Two packages, `packages/invoices/` and `packages/search/`, each keep a copy of the same off-by-one pager; the report and the fix belong to the invoice package. The scripted run passes `--package packages/invoices/` (`[fake] flags`), so the bug goes through the Investigator first and the proof runs in that package. The oracle judges the package as `feature-monorepo-package` does; with a run, the runner's diagnosis note (the `note_path` the run's saved investigation names) is not a change outside the package, but any other file under `docs/bugs/` is. With a run it also requires the bug-fix workflow, the Investigator before any other model stage but the job recognizer (or a repair of its report), plan review and the user's approval, no requirements gathering and no questions. `broken/fixes-search-copy-too` delivers the same correct fix and also fixes the other team's copy: the run stops (`HONEST_BLOCKER`) with the file named in the open `runner` finding. The scripted Investigator names only the files inside the package its handoff names (`package_scope`), so the run reaches the Builder. Before the Investigator was told the package, its diagnosis named the other team's copy too; the runner now refuses such a diagnosis. Against AutoCode from before #1415, the reference did not complete either: the proof counted the diagnosis note as a change outside the package. A live profile gets no `[fake] flags`, so it runs the brief without `--package` and is judged on the delivered files and the workflow alone. |
| `feature-refund-window` | feature | Built to reach AutoResolver (#59): the seed's `store_date()` helper ignores the store's UTC-8 offset, and the cap is on the running total of partial refunds. A plausible first attempt passes its own tests and fails hidden boundary tests; `diagnosis()` checks, by words, that AutoResolver's diagnosis of source that still fails the hidden tests names a planted defect. Since #294 a clean Validator FAIL can go straight back to the Builder, so runs rarely reach `astra_resolve`; one that never does is `NOT_EXERCISED`. The scripted run reaches it by construction — #628's `[fake] fault = "refund_store_clock"` makes the first Builder apply `broken/trusts-store-date` — and passes with a `CORRECT` diagnosis. |
| `feature-stock-refusals` | feature | AutoResolver on a natural failure (#59). On the original code `stock.py move` is an unknown subcommand, so argparse exits 2 and never touches the store: refusal tests in the seed's style (exit 2, stderr, unchanged bytes) pass there too, and the runner's regression proof rejects them. No executed check fails, so the Completion Owner's REWORK goes to AutoResolver rather than straight back to the Builder (#294). `check()` judges the product (hidden refusal tests; each refusal rule needs a delivered test that fails on the original code, and an extra test that also passes there is reported, not failed); `diagnosis()` scores the Resolver call ([Diagnosis](#diagnosis)). The scripted run is `CORRECT`; `SCENARIO_FAKE_RESOLVER=misattribute` makes its Resolver blame `stock.py` and is `INCORRECT` while the run still passes; `SCENARIO_FAKE_SCOPE_SLIP=retry` (or `pause`) makes the repair Builder's first attempt also leave a backup of `stock.py` outside its assignment, as a live repair did on 2026-10-06, and the Investigator recommend one more attempt (or leave it to a person), which `tests/test_rejected_repair_retry.py` drives ([2026-10-06-rejected-repair-retry](../docs/bugs/2026-10-06-rejected-repair-retry.md)); `--fake-solution broken/vacuous-refusal-tests` never repairs the tests, so the runner holds at `RESOLVER_PENDING` (`HONEST_BLOCKER`) and the diagnosis is `INCORRECT` on `repair_made_the_tests_discriminate`. Live Builders rarely write the vacuous tests (0 of 6 `claude-tiers` runs, 2026-10-05); `--hybrid` scripts planning and the first Builder so the trap is reached by construction, with AutoResolver live ([Hybrid runs](#hybrid-runs)). The first six hybrid runs (2026-10-05) found real defects in the helpers the seed and every solution shared: a QTY of `²` or of 5000 digits crashed (exit 1), `٣` was read as 3, and a JSON `true` in `stock.json` passed as 1. The seed (whose README contract already required those refusals) and every solution now refuse them, and the hidden tests check them for both commands; those six runs' diagnoses were rightly not bounded test repairs and are not comparable with later runs. |
| `escalation-converts` | feature | Issue #612: the greeting fix of `completion-rework-direct`, which the scripted Builder delivers only on its stronger route (`[fake] fault = "escalation_converts"`): its first attempt and its ordinary retry fail, the retry policy's stronger attempt (GPT-6 Sol at xhigh) passes, and the run completes. With a run the oracle reads the final status view's `escalations` (`harness.oracle.escalation_checks`): one Builder row, its two routes, one attempt with a known cost, outcome `passed` by validation, the runner's own stamps in `escalation_outcomes` agreeing (its ordinary retry and the stronger attempt `passed`), and no Builder attempt after it. `broken/accepts-empty` refuses a whitespace-only name but still greets an empty one (`str.isspace()` is false for `""`): the stronger attempt delivers it, the Tester's check fails, and the run pauses at `PAUSED_BUILDER_RETRY_LIMIT` (`HONEST_BLOCKER`). |
| `escalation-ladder-end` | feature | Issue #612: the same task where the stronger attempt fails too (`fault = "escalation_exhausted"`, `expected = "stop"`). The run pauses at `PAUSED_BUILDER_RETRY_LIMIT` with the stuck-stage Investigator's scripted diagnosis in its stop reason, the row's outcome is `paused`, as the runner's stamps on the ordinary retry and the stronger attempt are, no Builder attempt follows and the seed's tests are kept. No `broken/` overlay, like `provider-idle-hang`: the scripted Builder never applies the solution, so an overlay's fake run would stop exactly as the reference's does and `scenarios/test_lying_matrix.py` would judge it `PASS`; `escalation-converts`' broken overlay reaches this pause. `scenarios/test_escalation.py` runs both and reads `autocode --status --json`, and checks that a `--builder-strong-model` the tool does not list refuses the new run before any model call. |
| `feature-timesheet-by-project` | feature | Adding an option to an existing CLI without changing existing output. |
| `feature-monorepo-package` | feature | Issue #711: a seed with two packages, `packages/greet/` and `packages/farewell/`; the task adds an option to the greeting package only. The scripted run passes `--package packages/greet/` (`[fake] flags`; the driver never repeats it on a call to the saved run, which the CLI refuses), so the suite is detected and run in that package. The oracle runs the package's suite and hidden tests from its directory and requires nothing changed outside it since the seed. With a run started with `--package`, every file left changed outside the package must be named in an open blocking `runner` finding in the status view, and none may be reported when there is none. `broken/edits-farewell-package` delivers the same correct change and also edits the other package: the run stops (`HONEST_BLOCKER`) with both files named; AutoCode from before #1415 completed it (`FALSE_COMPLETE`). A live profile gets no `[fake] flags`, so it runs the brief without `--package` and is judged on the delivered files alone. |
| `implement-locked-design` | feature | An approved design is a constraint: specified modules and signatures (checked by AST), clock injected, no questions about settled decisions. A single-class "simplification" fails. |
| `implement-design-conflict` | feature | The approved design contradicts a frozen API. The right ending is a stop with the conflict written down and no code changed (`expected = "stop"`). |
| `greenfield-greeting-cli` | greenfield | A small CLI from an empty repository. |
| `capacity-recovery` | greenfield | Issue #1071: Requirements receives one terminal model-capacity failure, then serial dispatch retries on the same model in a fresh session and completes the greeting CLI. The oracle requires the archived failed event, no successful report for that attempt, one recovery handoff and both public accounting rows, plus independent CLI acceptance. `broken/accepts-empty-name` proves a recovered run can still be `FALSE_COMPLETE`. `--hybrid` scripts only the first Requirements attempt, then hands recovery and delivery to the selected report-file provider. Its trace binds the retry handoff and distinct report path to public attempt accounting. `--fake --hybrid` rehearses this route without model spend; it is not live transport evidence. An ordinary live delivery is `NOT_EXERCISED` because it did not receive the fault. Recovery-cap exhaustion is not covered. |
| `cost-cap-resume` | greenfield | Issue #721: the greeting CLI under a $0.10 cost cap (`[fake] flags`). The scripted run's spend at the dated price table passes the cap after planning, so it pauses at `PAUSED_COST_LIMIT` and AutoResolver asks the person; their `[fake] acknowledge` raises the cap to $100 with `--resume-paused --max-cost-usd 100` and the run completes. With a run the oracle also checks that it paused at the cap exactly once, was acknowledged once, and completed with the view's `cost` showing the $100 cap and spend past $0.10. |
| `rework-same-failure` | feature | Issue #611's rework loop, after the recorded bug-fix run whose correct fix kept spending until the budget ran out. The greeting repair is right from the first Builder attempt (`[fake] fault = "completion_rework_unowned_check"`), but the suite the brief requires, `python3 -m unittest`, also runs the release owner's `test_release.py`, which fails until `VERSION`, a file the brief forbids changing, is bumped. The right ending is a stop (`expected = "stop"`): the oracle requires the greeting fixed and nothing but `greet.py` changed, the same check failing twice, no paid diagnosis or third Builder attempt, a stop that names the check and says there was no progress on it (not a budget stop), the view's `cost` showing spend under the $5 cap, and the stop's text stating that spend and the cap as the view has them, and the run's active time and its time limit as the run saved them. `broken/bumps-version` completes by bumping `VERSION` (`FALSE_COMPLETE`). `broken/edits-release-test` edits the release owner's `test_release.py`; AutoCode refuses the Tester's report because a protected test changed, and the run stops at `PAUSED_INVALID_OUTPUT` before any completion review (`NOT_EXERCISED`). `broken/accepts-whitespace` leaves the greeting wrong. |
| `greenfield-todo-cli` | greenfield | Durable state and failure cases that must not corrupt data. |
| `port-policy-go` | port | Porting C# to Go against golden vectors. Requires `go`. |
| `parallel-diamond` | parallel | Four milestones where two can be built in parallel. The scripted run plans the same diamond and each Builder applies only its own milestone, so the orchestrator really schedules B and C as one parallel batch. |
| `architecture-two-services` | architecture | A two-component design with contracts, acyclic build/runtime dependency graphs, per-component runtime blocks and cross-service `smoke.json`; no application code or Docker startup. |
| `review-planted-defects` | review | A PR with green tests, two planted regressions (timeout reconciliation dropped, `.de` never-retry policy lost) and one nit. Both regressions blocking, nothing else blocking, tree untouched. |
| `review-clean-pr` | review | The same refactor done right. The oracle first applies the trusted input patch in scratch and runs its candidate tests; invalid input is a harness `ERROR`. For valid input, the report must approve; a blocking finding is invented. |
| `review-vacuous-tests` | review | The PR's tests pass without exercising the change. The reviewer must deliver a targeted test that fails on the patched code and passes once fixed; the oracle runs both. |
| `design-review-planted` | design | A queue-migration design with three gaps (ordering vs. partition key, no idempotency boundary, no rollback). All three blocking, nothing invented, ordering put to the user as a question. |
| `design-review-sound` | design | The same design with the gaps closed. No blocking concerns. |
| `discuss-cache-choice` | discuss | In-process vs. shared cache, decided by facts planted in the repository (four shared-nothing workers against a 60/hour upstream limit). Cites sources, weighs both options, writes no code, asks at most three questions. |
| `investigate-two-caches` | investigate | Explain two caches: scope, TTL and users must match the code; consequence of removing one named; nothing changed. |
| `review-then-fix` | conversation | Review `pr-184.patch`, then "Fix them." in the same run: the PR lands with both regressions fixed and a test that catches each (the oracle swaps back one unfixed file at a time), the advisory finding is left alone, and the fix turn asks no requirements questions. |
| `design-review-with-answers` | conversation | Issue #185: a design review asks which ordering consumers need, and each reply revises the same review in the same run. Before any answer ordering is a question, not a blocker; after "Ordering is per-domain." it is blocking (a transfer moves a domain to another registry, splitting its events across `registry_id` partitions); after "Per-registry is fine." that concern is resolved under the same id. The migration gap stays blocking, the unowned DLQ is advisory, nothing is renumbered or invented, and each turn runs only the Architect and changes only the review. The oracle reads the report's `revisions` trail and each turn's kept report. |
| `discuss-then-design-then-build` | conversation | Issue #185, three jobs in one run: `discuss-cache-choice`, then an explicit design request settling caller-configurable TTL (3600-second default), injectable clock, expiry at elapsed time >= TTL including equality, no stale fallback and propagated upstream errors, then exactly "Build it.". The design writes only `docs/design/`; the build writes only `app/` and `tests/`, preserving the decision note, approved design and root README. Socket-free fixtures use controlled `urllib.request.urlopen` responses and pipes/file barriers, not loopback servers or socket-backed managers. The build checks the approved design first (`check_design`); the oracle judges its prescribed public modules/signatures, strict clock boundary and cross-process shared cache. The design and build turns each have their plan approved. |
| `local-compose-two-services` | components | Architecture -> independent component TaskRuns -> integration -> local runtime smoke, with an independent fresh-key HTTP oracle and cleanup. Fake transport is the default for `--fake`; real Docker is explicit `--local-docker`. See [Components and local Compose](#components-and-local-compose). |
| `conformance-lifecycle` | conformance | Issue #1012: a headless client completes a whole run taking only the actions the status view names, through Plan Reviewer findings and a revision, approval, a failed Tester check and its rework, Ctrl-C during the rework and resume, feedback at the next safe boundary, completion and a follow-up question. See [Conformance](#conformance). |

Planned next: Figma design → implementation. Multi-service local Compose is now
represented by the components category above, without implying live qualification.

## Running the live ladder

The scenario ladder (see [#110](https://github.com/charlieanna/autocode/issues/110)) qualifies
AutoCode with real models. Results are only comparable when the environment matches, so this is
the environment the passing steps ran in — reproduce it before running a step, and note any
deviation in the step's issue.

**Prerequisites** (check each; a missing `ps` surfaces mid-run as `PAUSED_PROCESS_CHECK`):

```sh
opencode --version        # 1.18.x — pinned to 1.18.32 for the passing runs; 2.x is untested
command -v ps             # procps — required, not just nice to have
command -v git
python3 -c 'import psutil; print(psutil.__version__)'
command -v go             # only for port-policy-go (step 8)
```

**Logins.** Two connections in OpenCode: `openai` (through a ChatGPT login) and
`zai-coding-plan`. Verify with `opencode auth list`. The passing runs used a *filtered* auth
file containing only those two connections. Do not export provider credential variables in the
environment you launch from — the runner deliberately withholds credential-looking variables
from agents, and a stray one changes what the run proves.

**Command and caps.** One step at a time, three fresh runs each, from a throwaway clone (never
your working checkout):

```sh
scenarios/run.py run <scenario> --profile glm53-openai --i-authorize-live-model-spend \
    --max-seconds 2400 --max-stage-seconds 600 --max-iterations 6
```

Why each cap: `--max-seconds 2400` bounds the whole run (planning + build + validation fit
comfortably; a greenfield run spends most of it planning); `--max-stage-seconds 600` makes a
stuck stage surface as a pause instead of hanging the run;
`--max-iterations 6` bounds rework loops.

**Results.** Runs write under `.scenario-runs/` (git-ignored). Read a run's `report.json` there;
summarize across runs with `scenarios/run.py stats`. Copy the numbers into the step's issue —
never commit run output, logs, or anything credential-shaped ([AGENTS.md](../AGENTS.md),
"Hygiene"). The environment itself (a disposable container works; warm OpenCode's config before
the run so the first call isn't cold) is part of the evidence: same container and same pinned
versions for the runs you want to compare.

**Stress campaign.** The reliability qualification
([#455](https://github.com/charlieanna/autocode/issues/455)) runs a shortlist rather than the
whole catalog: `scenarios/stress.txt`, 23 ids grouped by what they stress (the original brief and
legitimate stop boundaries, failures spanning retries and caches, transactions that must conserve
data, hostile inputs and authorization, cumulative proof across handoffs). Like the #110 ladder's
`examples/claude-provider/qualification.txt`, it is one id per line with `#` comments, read by
`examples/claude-provider/batch.py` (which starts each run as `scenarios/run.py run ID`, with
`--profile claude-tiers` on the built-in claude provider when live), and `tests/test_claude_provider_example.py` fails when a line
names no catalog scenario, repeats one, or names one with no reference solution (the fake would skip
it). Rehearse it model-free before spending:

```sh
$PY examples/claude-provider/batch.py run scenarios/stress.txt --fake --repeat 3 --jobs 2 \
    --timeout-minutes 10 --out /path/outside/the/repo
$PY scenarios/run.py stats --mode fake --revision $(git rev-parse --short HEAD) --out /path/outside/the/repo
```

Reference runs alone prove little: the campaign also runs the oracle controls
(`run <id> --fake --fake-solution broken/<name>`, which must never be `PASS`: `FALSE_COMPLETE` when
AutoCode completes and the oracle catches the delivery, `HONEST_BLOCKER` when AutoCode's own gates
stop it) and `scenarios/adversarial.py`, and reads the stop classes and decision counts `stats`
prints (above, "Results"). A control's mode is `fake-<name>`, so `stats --mode fake` and the batch
above never count it with the reference runs.

## Adding a scenario

The expanded engineering ladder is documented in [LADDER.md](LADDER.md), from
small command-line tools to durable queues, transactional data, security
boundaries, and dependency-driven projects. Each rung has a reference solution
and defective controls; an oracle self-check is not a live AutoCode pass.

For an OpenAI-only campaign using the existing ChatGPT OAuth connection:

```sh
$PY scenarios/run.py run <scenario> --profile codex-only --i-authorize-live-model-spend \
    --max-seconds 1200 --max-stage-seconds 480 --max-iterations 6
```

This profile uses GPT-5.6 Terra for requirements, planning and building, and
GPT-5.6 Sol for review, validation and recovery. Execution roles are pinned,
including checkers, and the Investigator is explicitly OpenAI. Every live
result records the profile; `metrics.model_routes` records requested models
from saved commands, including an unfinished final attempt. An active record
can precede process creation; it is not proof of a completed model call. Missing
route evidence stays unknown. These runs have different models and environments
from the historical `glm53-openai` qualification and are reported separately.

Run offline checks with an interpreter that has psutil. AutoCode also requires
permission to inspect its child processes; a sandbox that blocks process
enumeration produces a launch blocker, not evidence of a model failure.
Checks that launch task-stage providers also need the
[supported Linux boundary](../docs/testing.md#host-requirements-for-tests), even with `--fake`.

```
catalog/<id>/
  scenario.toml     title, category, optional requires = ["go"], [fake] check = "...",
                    optional [fake] flags = [...] (extra CLI flags for the scripted run only;
                    --package, like --workflow, goes to the first call only), fault = "name"
                    (a scripted mistake in harness/fake_codex.py), live_investigator = true (the scripted run
                    still uses a real Investigator; needs --i-authorize-live-model-spend),
                    answers = { "route-sol" = "gpt-6-luna" } (the person's own answers, by question id;
                    fault "quota_once" stops the Tester on the driver's default Tester model),
                    acknowledge = { PAUSED_COST_LIMIT = ["--max-cost-usd", "100"] } (the flags the person
                    types with --resume-paused to continue that PAUSED_* pause, once; a single run only),
                    [run] max_steps, timeout_minutes, expected = "complete"|"stop"|"any", known_failure = "why",
                          requires_stages = ["astra_resolve"] (a model stage the run must reach to count)
                    [hybrid] scripted = ["astra_discovery", ...], first_attempt = ["terra"] (optional: the stages
                          run --hybrid scripts, every call or only the first; see "Hybrid runs")
                    [[turn]] after = "complete", say = "follow-up message" (optional, repeatable; said once
                          the run completes, since a follow-up continues only a finished run)
                    [fake] turn_paths = [["docs/"], ["app/"]] (a conversation: which solution paths each
                          turn delivers, the brief first; one list per turn)
  reference/.fake-turns/<turn>/<stage>.json
                    a conversation's scripted report for one stage in one turn (0 = the brief);
                    never part of the project
  brief.md          the request, exactly as a user would type it (plain text, no headings)
  seed/             the starting project, committed before the run (omit for an empty repo)
  oracle.py         def check(project, scenario, run=None) -> list[Check]
                    optional def diagnosis(project, run) -> dict (see "Diagnosis"; never in check())
  reference/        files that, laid over the seed, make a correct solution
  broken/<name>/    plausible solutions with one real defect each
  hidden/           tests only the oracle sees; never copied into the workspace
```

Rules for oracles, so a verdict means something:

- Judge through the documented interface (CLI, HTTP, public functions), so a
  correct solution with a different internal structure still passes.
- Work on a copy (`scratch_copy`) and never modify the delivered project.
- Import only `harness.oracle` and the standard library, never AutoCode.
- Every scenario has a reference solution and at least one broken variant, and
  `check` must show the oracle telling them apart. `test_harness.py` enforces this.
- For changes to an existing Python project, `python_change_checks` gives the
  standard checks: project tests pass, hidden tests pass, existing tests kept,
  standard library only, and the delivered tests fail against the original code.
- For a report, plant the facts in the seed and check them, not the prose:
  which file, which line span, which number. Where words are unavoidable
  (`mentions`), accept several phrasings and pair each "finds X" check with a
  "does not invent Y" check.
- For a read-only job, end with `only_changed_under(project, "<report dir>/")`.

## Relation to older harnesses

[Adversarial CLI tests](ADVERSARIAL.md) attack completion evidence, recovery,
approval, concurrent writers, crash/resume, budgets and atomic persistence. Run
`python scenarios/adversarial.py --jobs 4` with the venv interpreter. These tests
preserve failing product invariants and use no live models.

`tools/live_trial.py` and `tools/live_scenarios.py` hold the scenarios this
catalog was ported from (LIVE-01, 02, 05, 06). They stay until the work in
progress on them lands; then they can be removed. LIVE-07 depends on a separate
local repository and was not ported. `test-scenarios/` is a different suite:
fault injection (crash and resume, budget exhaustion, dirty workspaces) against
a fake Codex, and has not been migrated yet.
