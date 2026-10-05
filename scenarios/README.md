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

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `PASS` | AutoCode ended the way the scenario expects (completion, or a stop for the scenarios that expect one) and every oracle check passed. |
| `FALSE_COMPLETE` | AutoCode reported completion but the oracle found failures, or it completed a task it should have stopped on. The worst outcome. |
| `HONEST_BLOCKER` | AutoCode stopped (paused, waiting for a person) without claiming completion, where completion was expected. The oracle summary shows how far the work got. |
| `ERROR` | The harness could not finish (budget used up, no progress, a CLI crash) or the oracle crashed. |
| `SKIPPED` | A required tool is missing, or the scenario does not support the requested mode. |
| `NOT_EXERCISED` | The run ended well but never reached a stage the scenario exists to test (`[run] requires_stages`), so it says nothing about that stage. Not counted as a failure; `run.py stats` shows how often a scenario is exercised. A false completion or error keeps its own verdict. |

The driver answers AutoCode's clarifying questions with AutoCode's own proposed
default and records each answer in `result.json`. It approves the plan it is
shown and accepts requested human reviews. It never writes AutoCode state and
does not resume paused runs: a pause is reported as `HONEST_BLOCKER`. The one
exception is a scenario's explicit `[fake] answers`: a person's own answer to a
question the driver never answers by default (a quota stop's `route-sol`, for
example). The driver gives that answer, then resumes the pause it leaves once.

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
there and the reference/broken variants are told apart by files alone.

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
the brief (`catalog.load` refuses it). `discuss-then-design-then-build` is the
example: a discussion's note, then a design that follows it, then "Build it."
implementing that design (AutoCode checks it first as an approved design).

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
| `feature-refund-window` | feature | Built to reach AutoResolver (#59): the seed's `store_date()` helper ignores the store's UTC-8 offset, and the cap is on the running total of partial refunds. A plausible first attempt passes its own tests and fails hidden boundary tests; the oracle checks that AutoResolver's diagnosis names a planted defect. Runs that never reach `astra_resolve` are `NOT_EXERCISED` (always, with the scripted model). |
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
| `discuss-cache-choice` | discuss | In-process vs. shared cache, decided by facts planted in the repository (four shared-nothing workers against a 60/hour upstream limit). Cites sources, weighs both options, writes no code, asks at most three questions. |
| `investigate-two-caches` | investigate | Explain two caches: scope, TTL and users must match the code; consequence of removing one named; nothing changed. |
| `review-then-fix` | conversation | Review `pr-184.patch`, then "Fix them." in the same run: the PR lands with both regressions fixed and a test that catches each (the oracle swaps back one unfixed file at a time), the advisory finding is left alone, and the fix turn asks no requirements questions. |
| `discuss-then-design-then-build` | conversation | Issue #185, three jobs in one run: `discuss-cache-choice`, then "Shared it is; design it.", then "Build it.". Each turn changes only its own report or code; the design follows the decision (shared directory, atomic writes) outside its rejected options; the build checks that design first (`check_design`), implements the modules and signatures it names, and passes hidden tests in which separate worker processes share one cache directory. The design and build turns each have their plan approved. |

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
                    [[turn]] after = "complete", say = "follow-up message" (optional, repeatable; said once
                          the run completes, since a follow-up continues only a finished run)
                    [fake] turn_paths = [["docs/"], ["app/"]] (a conversation: which solution paths each
                          turn delivers, the brief first; one list per turn)
  brief.md          the request, exactly as a user would type it (plain text, no headings)
  seed/             the starting project, committed before the run (omit for an empty repo)
  oracle.py         def check(project, scenario, run=None) -> list[Check]
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
