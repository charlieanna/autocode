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

```sh
PY=.venv/bin/python                               # AutoCode needs psutil from the project virtualenv
$PY scenarios/run.py list                         # the catalog
$PY scenarios/run.py check                        # prove every oracle (no AutoCode, no models)
$PY scenarios/run.py run --fake                   # every scenario through AutoCode with a scripted model (no spend)
$PY scenarios/run.py run feature-stock-refusals --fake --hybrid   # rehearse a hybrid route (no spend; "Hybrid runs")
$PY scenarios/run.py run bugfix-iso-weeks --profile glm53-openai --i-authorize-live-model-spend
$PY scenarios/run.py route --fake                 # which workflow AutoCode recognizes for each prompt in routing.toml
$PY scenarios/run.py compare --fake               # AutoCode vs a plain agent on the same oracles (scripted; no spend)
$PY scenarios/run.py stats                        # per scenario and mode: runs, passes, pass streak, time, model stages
$PY scenarios/run.py build-compare greenfield-greeting-cli greenfield-todo-cli feature-timesheet-by-project parallel-diamond --fake --repeats 2 --jobs 4
$PY tools/run_suite.py --scenario-harness         # the harness's own tests, including all catalog controls, in parallel
```

Results land in `.scenario-runs/<time>-<id>-<mode>/`: `result.json` (verdict,
every oracle check, CLI calls, answers given on the user's behalf, wall time,
stages with a per-stage count and time breakdown, report-repair rounds, model
time and tokens), `steps.jsonl`, the final `state.json`, and the delivered
`project/`, kept for inspection.

`run.py stats` reads those results back. One pass can be luck, so it reports
how many times each scenario ran in each mode, how many runs passed, and the
current streak of consecutive passes, with median wall time and model stages.
Fake and live modes are never combined. `test_harness.py` also fails if the
tiny scenarios (`greenfield-greeting-cli`, `bugfix-trivial`) take more model
stages with the scripted model than they do today, so extra steps can't creep in
unnoticed (issue #15).

## Three levels

| Level | Command | What it proves | Cost |
| --- | --- | --- | --- |
| Oracle check | `check` | The oracle rejects the untouched seed, accepts the reference solution, and rejects each plausible-but-wrong variant in `broken/`. | seconds |
| Fake run | `run --fake` | AutoCode's real CLI, planning gates, approval, build, validation and completion work end to end for this kind of task. The scripted model plans from the brief and applies the reference solution. It says nothing about model quality. | seconds per scenario |
| Live run | `run --profile NAME` | How well AutoCode actually does the task with real models. | model spend; requires `--i-authorize-live-model-spend` |

`run --fake --fake-solution broken/<name>` makes the scripted model deliver a
wrong solution. Because its own checks pass, AutoCode completes, and the
harness must report `FALSE_COMPLETE`. That is how the harness itself is tested.

`run --profile NAME --provider OTHER` runs the profile's models and efforts
through another provider (`kilocode`, or any tool set up as in
`docs/providers.md`). The result records the provider, and its mode is
`NAME-via-OTHER`, so `stats` keeps it apart from runs through the profile's own
provider.

`run --profile NAME --hybrid` scripts the stages a scenario's `[hybrid]` route
names and runs every other stage live; its mode is `NAME-hybrid`
([Hybrid runs](#hybrid-runs)).

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `PASS` | AutoCode ended the way the scenario expects (completion, or a stop for the scenarios that expect one) and every oracle check passed. |
| `FALSE_COMPLETE` | AutoCode reported completion but the oracle found failures, or it completed a task it should have stopped on. The worst outcome. |
| `HONEST_BLOCKER` | AutoCode stopped (paused, waiting for a person) without claiming completion, where completion was expected. The oracle summary shows how far the work got. |
| `ERROR` | The harness could not finish (budget used up, no progress, a CLI crash) or the oracle crashed. |
| `SKIPPED` | A required tool is missing, or the scenario does not support the requested mode. |
| `NOT_EXERCISED` | The run ended well but never reached a stage the scenario exists to test (`[run] requires_stages`), or the oracle's [diagnosis](#diagnosis) found that the stage never ran on the failure the scenario plants, so it says nothing about that stage. Not counted as a failure; `run.py stats` shows how often a scenario is exercised. A false completion or error keeps its own verdict. |

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
default and records each answer in `result.json`, with the question's text, why and options. It approves the plan it is
shown and accepts requested human reviews. It never writes AutoCode state and
does not resume paused runs: a pause is reported as `HONEST_BLOCKER`. The one
exception is a scenario's explicit `[fake] answers`: a person's own answer to a
question the driver never answers by default (a quota stop's `route-sol`, for
example). The driver gives that answer, then resumes the pause it leaves once. A
scenario may also answer ordinary clarifying questions this way when the person's own
words are what it tests (`design-alerting-outcomes`): the driver gives the explicit
answer to each question that has one and AutoCode's proposed default to the others. A
gate the driver otherwise leaves to the person is served only when every one of its
questions has an explicit answer.

## Hybrid runs

Some failures a real model rarely makes on cue. In six live `claude-tiers` runs
of `feature-stock-refusals` (2026-10-05) no Builder wrote the vacuous refusal
tests, so AutoResolver never saw the trap (#59). A scenario can declare a route
that scripts the stages producing its failure and leaves the stage under test
live:

```toml
[hybrid]
scripted = ["recognize_workflow", "requirements_gather", "astra_discovery", "astra_challenge", "glm_revise",
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
`output = "report_file"`, as `examples/claude-provider` is; built-in OpenCode and
Codex, and tools with sessions, are `SKIPPED`. Through such a tool the scripted
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
be, and the list of known failures is the distance left.

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
   decided by the first stage of every run (`recognize_workflow`) unless the
   user named it with `--workflow`. Oracles
   check it through `run_checks`, together with which saved stages ran: a
   review must not dispatch a Builder or ask for plan approval; a bug fix must
   not start with requirements gathering; a three-line fix must not get
   plan-review rounds. In fake mode the scripted provider answers this stage
   with keyword rules (`harness/fake_codex.py`, `recognize`), which proves the
   plumbing and nothing about model quality.
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

## Programs

A request too large for one run is a program (issues #22 and #23, `autocode
program`, docs/program.md): one approved plan becomes an agreement, each
milestone a workstream run in its own Git worktree, merged onto one integration
branch. A scenario with `category = "program"` is driven that way by
`harness/program_driver.py`, through the CLI only:

1. `autocode program plan BRIEF --workspace PROJECT FLAGS` starts an ordinary
   planning run (`--unit autoplanner`). The driver answers its questions and
   approves its plan, then stops: the approved plan run is never relaunched.
2. `program derive --run-dir RUN --output program.json --name ID` writes the
   manifest. `[program] revise` is laid over it: the person's own edit before
   approving (tables merge, anything else replaces), such as an interface with
   a producer and consumers, or the journeys' names.
3. `program show` prints the agreement (saved as `agreement.txt`), and `program
   approve --token TOKEN` approves the exact token it displayed.
4. `program run program.json --workspace PROJECT --max-parallel N FLAGS`, again
   and again. Every pass carries the full child flags: they configure only the
   workstream runs that pass starts. After each pass the driver approves a new
   agreement revision (by the token `program show` displays, once), raises a
   `[[program.change]]` request whose `after = "merged:<id>"` has come, decides
   it once the program holds for it, and serves each waiting workstream's gate
   from that run's own status view: plan approval, a question with its proposed
   default, human review, planning budget. A pass whose rows still have a run at
   `RUNNING` (the program's own feedback reached it) is followed by another; a
   pass that changes nothing twice is an `ERROR`.

The driver never relaunches, resumes or follows up a workstream's run (the
program advances each run once per pass), never answers a question only a
person may answer, never authorizes deployment and never retries a failed
workstream: the program is judged where it stopped. Under a live profile it
still approves the agreement and every workstream's plan, as the single-run
driver approves the plans it is shown; `tools/live_trial.py`, by contrast,
stops a live program for a person at the agreement and at every plan, and stops
any program at its first program-level `PAUSED_*` status, without rerunning it
or serving a child's gate.

**Workstream ids.** `[program] revise` and `[[program.change]]` name
workstreams by their `[fake] milestones` ids, which the scripted planner keeps.
A live planner names its milestones itself, so after `program derive` the
driver maps each id the scenario names to the derived workstream whose `owns`
cover every path of that milestone the brief names (the store's producer is
whoever owns `notes/store.py`). A path only the reference solution has, such
as its `notes/cli.py` dispatcher, cannot rule a live plan out. Of several such
workstreams, the one owning the most of the milestone's paths stands for it,
then the most specific owner. The driver renames the interface producers and
consumers, `by` and `after = "merged:<id>"` accordingly (`result.json`
`program.workstream_ids`). When the live plan's split does not line up with
the scenario's (those paths owned by no single workstream, a tie, or two
milestones landing on one workstream), the driver stops before approving
anything and the run is `ERROR`.

```toml
[program]
max_parallel = 2                               # program run --max-parallel (default 2)

[program.revise.shared]                        # merged into the derived manifest before the first approval
interfaces = [{ id = "store", summary = "...", paths = ["notes/store.py"], version = 1, producer = "S", consumers = ["T", "U"] }]

[[program.change]]                             # optional, repeatable
after = "merged:S"                             # raised once workstream S has merged
interface = "store"
by = "T"                                       # the workstream that found the problem
reason = "..."
decide = "accept"                              # publish the interface anew; the driver approves that revision
publish = { version = 2, summary = "..." }     # or decide = "reject" with resolution = "..." (resolve-change --reject)
```

**Where the product lives.** The program merges onto its integration branch,
never the project's own branch, so the oracle judges the integration worktree
(`result.json` `product`); in `check` mode it gets the seed with an overlay, as
always. `oracle.changed_since_seed` compares a worktree with the seed commit,
since `git status` is clean where the product was committed.
`oracle.program_checks(run, scenario)` judges how the program went from the
run record: the program holding, approved and with nothing pending, the
agreement revision whose token the person was last shown, every workstream a
merged run with its own approved plan, the walking skeleton verified first and
nothing else started before that, every merge re-running the checks of all
merged before it (a workstream with a run retired since the last passing
verification, by an accepted change say, takes its checks out of that set until
it merges again), each journey verified by the integration workstream and named
in the program's `final_check` with the name `[program] revise` gives it (else
derive's `J1 Main user journey`), and each scripted change request accepted or
rejected. For an acceptance, the
producer and every consumer are merged under the latest agreement revision;
each of them whose first run started before the change was accepted has a run
retired since then, and no other workstream has a run retired after it. The run
record holds the program's final summary (`program status`), its verifications,
every workstream run (retired ones too) with its stages, the plan run in a
single run's shape (`plan`), the agreement tokens, interfaces and changes, the
scenario's workstream ids mapped to the derived ones, and every model stage of
the plan and workstream runs, which `[run] requires_stages` counts.

**Verdicts.** `COMPLETE` is judged like a completed run (`PASS` or
`FALSE_COMPLETE`); `PAUSED_*` stops (an integration check, the skeleton
unverified, a conflict, ownership, an interface change, inheritance, a journey
unverified) and the program-only stops `WAITING`, `WAITING_AGREEMENT_APPROVAL`,
`WAITING_CHANGE_REQUEST` and `AUTHORIZATION_REQUIRED` are `HONEST_BLOCKER`;
`BLOCKED` (a workstream run failed) and `RUNNING` are `ERROR`. A program that
did not complete names each unmerged workstream's status and its run's status
and progress. A program that passes without raising a scripted change request
(its moment never came) is `NOT_EXERCISED`. Any other verdict stands, with the
request that was never raised added to its summary: a regression that stops the
program before the skeleton merges is still a failure. Single runs are judged
as before (`verdict.judge_program` is used for programs only). `compare`,
`build-compare` and `--hybrid` skip program scenarios.

**The scripted model.** Child runs inherit the fake on `PATH`. The fake reads a
workstream's brief (`PROGRAM WORKSTREAM <id> (<kind>)`, and the ids on its
`Inherited requirements` line) and plans that workstream alone: a code
workstream its own `[fake] milestones` row with no dependencies, under the ids
it inherits; the integration workstream every requirement and journey it
inherits, verified by `[fake] check`, delivering the solution files no milestone
owns. A code workstream whose files already match the solution (a re-check after
an agreement revision) plans a validate-only task, and marks its criteria
`guard:` with a test the workstream already has, as a live Planner did, so the
runner's regression proof runs on an unchanged source. That is the fake's choice, not the
product's: a re-check planned as an implementation of a workstream that
already conforms stalls the program, since its Builder has nothing to change
([docs/bugs/2026-10-06-program-recheck-implement-stall.md](../docs/bugs/2026-10-06-program-recheck-implement-stall.md)).
The integration workstream marks each inherited id `guard:` as its brief asks, naming a
merged workstream's test (or its own journey test) when the solution has one, else the
scenario check, so its proof runs against the integration head and matches guards in files
it leaves alone.
The fake remembers each worktree's workstream beside its configuration, never
in the worktree, for report repairs, whose packets carry no task.
`tests/test_program.py` (`ScenarioFakeBriefTests`) runs the fake on real
`compose_brief` output, so a brief reworded out from under it fails there.
`catalog.load` requires a program scenario's milestone paths to be disjoint,
and its reference and every `broken/<name>/` to be a complete overlay: every
milestone path, and a file no milestone owns (the integration workstream's
delivery). A run that changes nothing stops for want of progress. The program
ignores Python bytecode caches when it checks a workstream's paths. Keep the seed to a
README.md: the first workstream's regression proof treats only a README-only project as
new, so a scaffold (a `.gitignore`, an empty `tests/__init__.py`) stops the walking
skeleton (docs/bugs/2026-10-06-regression-proof-scaffold-base.md).

```sh
$PY scenarios/run.py run program-notes-cli --fake                                   # PASS, about 30 s
$PY scenarios/run.py run program-notes-cli --fake --fake-solution broken/search-shadows-list   # HONEST_BLOCKER
$PY scenarios/run.py run program-notes-cli --fake --fake-solution broken/case-sensitive-search # FALSE_COMPLETE
```

A fake program run takes about 30 s and some 20 CLI calls (plan, derive, show,
approve, about eight `program run` passes, a change request, a revision and a
plan approval per workstream run), roughly four times a typical fake scenario.
A live run spends about one run per workstream run, the plan included.

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
| `bugfix-duplicate-on-timeout` | bugfix | A retry after an uncertain timeout renews a domain twice. Hidden tests inject lost replies before and after processing; removing retries or raising the deadline both fail. Requires a root-cause note and no requirements gathering. |
| `bugfix-stale-prices` | bugfix | Checkout charges stale prices because cache invalidation is left to each write path. The fix belongs at the store's write path (every cache hears every write); either fix route passes, with no requirements gathering. Patching today's callers or dropping the cache both fail hidden tests. |
| `bugfix-cent-drift` | bugfix | Invoice, charge and refunds each round money their own way and drift by a cent. The fix touches every billing module, needs one half-up money rule, and leaves an accounting choice open (tax per line or per invoice), so it must take the planned path: diagnosis, Planner, Plan Reviewer, the user's approval, no requirements gathering. Deriving only the charge from the invoice, or rounding everything with float `round()`, both fail hidden tests. |
| `stuck-planner-citation` | bugfix | The stuck-stage Investigator, with a real model. Every stage is scripted except the Investigator (`--investigator-model openai/gpt-6-sol`, high); the scripted Planner repeatedly cites a nonexistent `.missing` sibling until repairs run out. Passes only if the real Investigator names the cause and its guidance gets the retried Planner through. Requires a real model call; interruptions or report repairs can add calls. Skipped without `--i-authorize-live-model-spend`. |
| `bugfix-trivial` | bugfix | An off-by-one, through the full bug-fix path: diagnosis, plan review and the user's approval, no requirements gathering, no questions. Its proportionality checks (no plan-review rounds, at most five model stages) come back with the short path for small fixes. |
| `bugfix-not-reproducible` | bugfix | The reported bug does not exist in this code. Passes by saying so or asking; a "defensive" change to working code fails. |
| `feature-refund-window` | feature | Built to reach AutoResolver (#59): the seed's `store_date()` helper ignores the store's UTC-8 offset, and the cap is on the running total of partial refunds. A plausible first attempt passes its own tests and fails hidden boundary tests; `diagnosis()` checks, by words, that AutoResolver's diagnosis of source that still fails the hidden tests names a planted defect. Since #294 a clean Validator FAIL can go straight back to the Builder, so runs rarely reach `astra_resolve`; those that never do are `NOT_EXERCISED` (always, with the scripted model). |
| `feature-stock-refusals` | feature | AutoResolver on a natural failure (#59). On the original code `stock.py move` is an unknown subcommand, so argparse exits 2 and never touches the store: refusal tests in the seed's style (exit 2, stderr, unchanged bytes) pass there too, and the runner's regression proof rejects them. No executed check fails, so the Completion Owner's REWORK goes to AutoResolver rather than straight back to the Builder (#294). `check()` judges the product (hidden refusal tests; each refusal rule needs a delivered test that fails on the original code, and an extra test that also passes there is reported, not failed); `diagnosis()` scores the Resolver call ([Diagnosis](#diagnosis)). The scripted run is `CORRECT`; `SCENARIO_FAKE_RESOLVER=misattribute` makes its Resolver blame `stock.py` and is `INCORRECT` while the run still passes; `SCENARIO_FAKE_SCOPE_SLIP=retry` (or `pause`) makes the repair Builder's first attempt also leave a backup of `stock.py` outside its assignment, as a live repair did on 2026-10-06, and the Investigator recommend one more attempt (or leave it to a person), which `tests/test_rejected_repair_retry.py` drives ([2026-10-06-rejected-repair-retry](../docs/bugs/2026-10-06-rejected-repair-retry.md)); `--fake-solution broken/vacuous-refusal-tests` never repairs the tests, so the runner holds at `RESOLVER_PENDING` (`HONEST_BLOCKER`) and the diagnosis is `INCORRECT` on `repair_made_the_tests_discriminate`. Live Builders rarely write the vacuous tests (0 of 6 `claude-tiers` runs, 2026-10-05); `--hybrid` scripts planning and the first Builder so the trap is reached by construction, with AutoResolver live ([Hybrid runs](#hybrid-runs)). The first six hybrid runs (2026-10-05) found real defects in the helpers the seed and every solution shared: a QTY of `²` or of 5000 digits crashed (exit 1), `٣` was read as 3, and a JSON `true` in `stock.json` passed as 1. The seed (whose README contract already required those refusals) and every solution now refuse them, and the hidden tests check them for both commands; those six runs' diagnoses were rightly not bounded test repairs and are not comparable with later runs. |
| `feature-timesheet-by-project` | feature | Adding an option to an existing CLI without changing existing output. |
| `implement-locked-design` | feature | An approved design is a constraint: specified modules and signatures (checked by AST), clock injected, no questions about settled decisions. A single-class "simplification" fails. |
| `implement-design-conflict` | feature | The approved design contradicts a frozen API. The right ending is a stop with the conflict written down and no code changed (`expected = "stop"`). |
| `greenfield-greeting-cli` | greenfield | A small CLI from an empty repository. |
| `greenfield-todo-cli` | greenfield | Durable state and failure cases that must not corrupt data. |
| `port-policy-go` | port | Porting C# to Go against golden vectors. Requires `go`. |
| `parallel-diamond` | parallel | Four milestones where two can be built in parallel. The scripted run plans the same diamond and each Builder applies only its own milestone, so the orchestrator really schedules B and C as one parallel batch. |
| `architecture-two-services` | architecture | A two-component design with contracts and an acyclic dependency graph; no code. |
| `review-planted-defects` | review | A PR with green tests, two planted regressions (timeout reconciliation dropped, `.de` never-retry policy lost) and one nit. Both regressions blocking, nothing else blocking, tree untouched. |
| `review-clean-pr` | review | The same refactor done right. Must approve; a blocking finding is invented. |
| `review-vacuous-tests` | review | The PR's tests pass without exercising the change. The reviewer must deliver a targeted test that fails on the patched code and passes once fixed; the oracle runs both. |
| `design-review-planted` | design | A queue-migration design with three gaps (ordering vs. partition key, no idempotency boundary, no rollback). All three blocking, nothing invented, ordering put to the user as a question. |
| `design-review-sound` | design | The same design with the gaps closed. No blocking concerns. |
| `design-alerting-outcomes` | design | Issue #450: a vague request to monitor an AWS order pipeline's dead-letter queue and alert the team in Slack, in an empty workspace. The person answers with alert rules and no integration preference (`[fake] answers`). Requirements asks about outcomes and constraints (what triggers an alert, which channel and whether an existing integration or restriction binds, what an alert may contain), never which Slack API to use; the Planner recommends mechanisms with their tradeoffs, keeps the uncertain-delivery and deadline promises as blocking questions until the person decides them, and the person approves the exact plan, which authorizes no deployment. `check()` judges `design/alerting.json` without requiring any one AWS architecture: the person's rules kept as theirs, every mechanism choice with two or more options, tradeoffs and a recommendation recorded as `agent_proposed` and stated as a proposed assumption (by the mechanisms it names, not word for word, in a statement that neither denies them nor lists the other options alongside), the channel a parameter rather than a blocker (an `open_blockers` row may name an identity only when a parameter's name declares it), reliability promises as the person decided them, deployment not authorized, nothing but `design/` changed. With a run it also reads the questions the driver answered (their text and options) and the status view's `approved_contract`. The scripted Requirements and Planner follow the outcome-question rules only when those rules are in their prompts (`harness/outcome_questions_provider.py`), so the fake run fails when a rule stops reaching its stage: run against AutoCode from before the rules, it asked "incoming webhook, Slack app with chat:write, or AWS Chatbot?", re-asked after "no preference", and was `FALSE_COMPLETE` on seven checks. The brief never says "do not deploy", so the plan's own line about approval is what the deployment check reads; a question that only asks whether an existing integration or a restriction binds may name mechanisms as examples. The person's answers are `[fake] answers` only: live, the driver answers with the model's own defaults, which need not say "no preference" (a default to a question about mechanisms saying no constraint binds them counts); without one, the re-ask check fails only on a question that puts a mechanism to the person. Two live runs that followed the rules (2026-10-07) are kept in `tests/` and pass all 19 checks; the question about what triggers an alert may say "something goes wrong" or what should page someone instead of "alert". The checks read the model's wording with patterns, so a future live run may still surface wording they misread. Broken variants: `mechanism-as-user-decision`, `detection-as-user-decision`, `unsupported-guarantee`, `delivery-contradicts-answer`, `channel-as-blocker`, `deployment-authorized`, `note-grants-deployment`. |
| `discuss-cache-choice` | discuss | In-process vs. shared cache, decided by facts planted in the repository (four shared-nothing workers against a 60/hour upstream limit). Cites sources, weighs both options, writes no code, asks at most three questions. |
| `investigate-two-caches` | investigate | Explain two caches: scope, TTL and users must match the code; consequence of removing one named; nothing changed. |
| `review-then-fix` | conversation | Review `pr-184.patch`, then "Fix them." in the same run: the PR lands with both regressions fixed and a test that catches each (the oracle swaps back one unfixed file at a time), the advisory finding is left alone, and the fix turn asks no requirements questions. |
| `design-review-with-answers` | conversation | Issue #185: a design review asks which ordering consumers need, and each reply revises the same review in the same run. Before any answer ordering is a question, not a blocker; after "Ordering is per-domain." it is blocking (a transfer moves a domain to another registry, splitting its events across `registry_id` partitions); after "Per-registry is fine." that concern is resolved under the same id. The migration gap stays blocking, the unowned DLQ is advisory, nothing is renumbered or invented, and each turn runs only the Architect and changes only the review. The oracle reads the report's `revisions` trail and each turn's kept report. |
| `discuss-then-design-then-build` | conversation | Issue #185, three jobs in one run: `discuss-cache-choice`, then "Shared it is; design it.", then "Build it.". Each turn changes only its own report or code; the design follows the decision (shared directory, atomic writes) outside its rejected options; the build checks that design first (`check_design`), implements the modules and signatures it names, and passes hidden tests in which separate worker processes share one cache directory. The design and build turns each have their plan approved. |
| `program-notes-cli` | program | Issues #22 and #23 through `autocode program`: plan, derive, show, approve, run. Each workstream is an ordinary build run in its own worktree, linked to the agreement. The walking skeleton S (add and list) is merged and verified first; search (T) and export (U) run in parallel, each merge re-running the cumulative checks; the integration workstream delivers the journey test and verifies the journey `capture-and-find` by name. Once S merges, T raises a change request on the store interface; the person publishes version 2 and approves that revision, so S, T and U lose their approval and are planned, approved and checked again (S's re-check only validates). `broken/search-shadows-list` breaks the skeleton's journey and is undone by the cumulative checks (`PAUSED_INTEGRATION_CHECK`); `broken/case-sensitive-search` completes and only the hidden journey test catches it. See [Programs](#programs). |

Planned next: Figma design → implementation, and multi-service systems started
with `docker compose` and checked end to end.

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

```
catalog/<id>/
  scenario.toml     title, category, optional requires = ["go"], [fake] check = "...",
                    optional [fake] flags = [...] (extra CLI flags), fault = "name" (a scripted
                    mistake in harness/fake_codex.py), live_investigator = true (the scripted run
                    still uses a real Investigator; needs --i-authorize-live-model-spend),
                    answers = { "route-sol" = "gpt-6-luna" } (the person's own answers, by question id;
                    fault "quota_once" stops the Tester on the driver's default Tester model),
                    [run] max_steps, timeout_minutes, expected = "complete"|"stop"|"any", known_failure = "why",
                          requires_stages = ["astra_resolve"] (a model stage the run must reach to count)
                    [hybrid] scripted = ["astra_discovery", ...], first_attempt = ["terra"] (optional: the stages
                          run --hybrid scripts, every call or only the first; see "Hybrid runs")
                    [[turn]] after = "complete", say = "follow-up message" (optional, repeatable; said once
                          the run completes, since a follow-up continues only a finished run)
                    [fake] turn_paths = [["docs/"], ["app/"]] (a conversation: which solution paths each
                          turn delivers, the brief first; one list per turn)
                    [program] max_parallel, revise, [[program.change]] (category "program" only: driven
                          through `autocode program`; see "Programs")
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
  tests/            fixtures for the harness's oracle tests, such as a live run's record and
                    delivery (scenarios/test_harness.py); never copied into the workspace
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
