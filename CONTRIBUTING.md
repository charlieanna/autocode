# Contributing to AutoCode

Thanks for your interest. AutoCode is young (first commit September 2026) and
maintained by one person, so this guide is short and honest about what is and
isn't ready. Read the [README](README.md) first for what the project is and the
[reliability priorities](RELIABILITY.md) for what gets worked on first.

## Ground rules

- **Reliability before features.** A reproducible bug fix with a regression
  test is the most welcome contribution. New features need an identified user
  need and an agreed scope *before* code; open an issue and wait for a reply
  rather than opening a large pull request cold.
- **Keep the README honest.** Nothing moves from "planned" to "implemented"
  without the code, a supported entry point, and test coverage.
- **Never weaken a gate to get green.** Do not skip, disable or quarantine a
  test, relax an approval check, or make missing evidence count as passed.
  A pull request that does this will be closed even if everything else is good.
- **Be kind.** This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## Where code belongs

Read [AGENTS.md](AGENTS.md) for the architecture rules. The target dependency
direction is utilities → domain → runtime → controller → interfaces; lower
layers must not import higher ones. No module is in an import cycle any more,
and [the architecture checks](tests/test_architecture.py) keep it so, but the
layer direction itself is not checked yet, so this table is a guide to
placement rather than a claim that the migration is complete.

| Layer | Responsibility | Existing examples |
| --- | --- | --- |
| Utilities | Files, hashing, locks, clocks and schema helpers without AutoCode imports | [autocode_util.py](tools/autocode_util.py) |
| Domain | Contracts, findings, milestone policy and completion decisions over state | [autocode_goals.py](tools/autocode_goals.py), [autocode_findings.py](tools/autocode_findings.py), [autocode_completion.py](tools/autocode_completion.py) |
| Runtime | Provider selection, subprocess supervision and evidence execution | [autocode_providers.py](tools/autocode_providers.py), [autocode_process.py](tools/autocode_process.py) |
| Controller | Select the saved stage, dispatch a unit, apply its result and persist | [autopilot.py](tools/autopilot.py), [units/](tools/units/) |
| Interfaces | CLI actions and the public task-run/status contract | [autocode_run_actions.py](tools/autocode_run_actions.py), [autocode_taskrun.py](tools/autocode_taskrun.py), [autocode_run_view.py](tools/autocode_run_view.py) |

Keep a new policy helper focused and pass its inputs explicitly. Do not grow
`autocode.py` or `autopilot.py`, or add an import cycle, to implement a local rule.
Cross-task coordination uses [TaskRun](docs/task-run.md), not private runner
calls or another run's `state.json`. Run state remains a dict:
[autocode_run_state.py](tools/autocode_run_state.py) names its keys and readers;
add any necessary new key there with its writer and consumers. Status-view
fields are additive. The dashboard and macOS app remain frozen for bug fixes.

### Stages and jobs

Stage IDs persist in saved runs. Human names come from `SCREEN`, `STAGE_JOB`
and `job_for_stage()` in [autocode_roles.py](tools/autocode_roles.py); use that
catalogue instead of adding a naming table in code. This documentation lists
the executable stages and their current unit owners, including v2 planning.

| Stage ID | Human job | Unit |
| --- | --- | --- |
| `recognize_workflow` | Job recognizer | AutoPlanner |
| `requirements_gather`, `astra_discovery`, `glm_revise`, `requirements` | Requirements | AutoPlanner |
| `plan`, `plan_revise` | Planner | AutoPlanner |
| `astra_plan` | Planner | AutoCode |
| `astra_challenge`, `astra_finalize`, `plan_review`, `plan_finalize` | Plan Reviewer | AutoPlanner |
| `orchestrator` | Orchestrator | AutoCode |
| `terra` | Builder | AutoCode |
| `sol`, `write_acceptance_tests` | Tester | AutoReview |
| `astra_review`, `astra_checkpoint` | Completion Reviewer | AutoReview |
| `collect_design` | Design inventory | AutoReview |
| `review_design` | Designer | AutoReview |
| `check_design` | Design Reviewer | AutoReview |
| `review_change` | Code Reviewer | AutoReview |
| `investigate_bug`, `investigate_stuck` | Investigator | AutoResolver |
| `astra_resolve`, `astra_diagnose` | Resolver | AutoResolver |
| `answer_question` | Analyst | AutoResolver |

Under `glm_first_v1` reviewer routing, `astra_checkpoint` does the **Tester**
job; the name describes its work, not the model running it. `_report_repair`
uses its original stage's job name. Provider/model routes are separate from
these names, and `orchestrator` does not call a model.

## Set up a development checkout

Requirements: Python 3.11+, Git, macOS or Linux (Windows needs WSL).
These are checkout requirements. Supervised task-stage providers, including
fake CLI and scenario providers, require the supported Linux host described in
[Host requirements for tests](docs/testing.md#host-requirements-for-tests).
macOS can still be used for editing, static checks, pure logic and mocked tests
that do not launch those providers.

```sh
git clone https://github.com/charlieanna/autocode.git
cd autocode
python3 -m venv .venv
.venv/bin/pip install -e .          # installs the one runtime dependency, psutil
.venv/bin/autocode --help
```

Running AutoCode against a real model needs a configured provider (OpenCode,
Codex or a command-tool adapter); see [Install](docs/install.md) and
[Providers](docs/providers.md). Routine fake-provider tests need no model
account: they use temporary Git repositories and make no model requests.
Tests that launch supervised task-stage providers still need Linux; having a
fake executable on `PATH` does not bypass host admission.

## Run the tests

Run checks from the repository root with `.venv/bin/python`, which has the
runtime dependencies. For a localized bug, run the regression that exposes it
and the affected public-interface or direct-consumer checks. Use fake providers
or fault injection; a fake returning PASS without exercising the defect is not
evidence. Add the architecture check when imports or module boundaries change.
Documentation-only edits need link/source checks, not behavior-test reruns.

On macOS, a running Docker Desktop can provide supported Linux execution for
exactly one fake provider-backed unittest selector or named fake scenario; see
[One focused Linux fake run from macOS](docs/testing.md#one-focused-linux-fake-run-from-macos).
It does not weaken macOS host admission and does not accept live-provider flags.

Use the same pinned lint tools as CI for Python changes (select affected paths
while iterating):

```sh
.venv/bin/python -m pip install -r requirements-lint.txt
.venv/bin/ruff check tools/ tests/ scenarios/
.venv/bin/ruff format --check tools/ tests/ scenarios/
.venv/bin/mypy tools/
```

`requirements-lint.txt` is the shared version source. Upgrade its pins deliberately
in a pull request and review any new findings before merging.

These are available checks, not a checklist for every fix:

```sh
.venv/bin/python -m unittest tests.test_goals.GoalTests.test_permission_response_keeps_the_approved_goal_and_evidence
.venv/bin/python -m unittest tests.test_architecture
.venv/bin/python tools/run_suite.py --changed
.venv/bin/python tools/run_suite.py                    # full Python suite
.venv/bin/python tools/run_suite.py --scenario-harness # harness and catalog tests
```

Broaden only for a demonstrated affected consumer, a shared-contract change,
a failure or an explicit repository-wide request. Say why before a broad run.
Repeat a passing check only after a relevant behavior or test change; reuse
current-revision CI evidence for broad checks. Full-suite duration varies with
subprocess timeouts, toolchains and host load; do not plan around a fixed
7–8-minute estimate. Live-model runs are optional, risk-specific evidence,
not a prerequisite for an otherwise verified PR; see [AGENTS.md](AGENTS.md#fake-first-verification-and-optional-live-runs).

The suite runner discovers `tests/test_*.py`, applies the documented exclusions
in `tests/suite_exclusions.json`, and fails on a failed test or stale exclusion.
[CI](.github/workflows/tests.yml) runs `--changed --all-fast` on PRs, retaining
changed slow modules and all fast modules; master runs the full Python suite.
The scenario harness and dashboard gates run separately. The main suite runs
on Ubuntu, with a separate native macOS tool-containment job.
The macOS job does not establish support for supervised task-stage providers.

For dashboard bug fixes, select the affected checks (need localhost sockets;
browser tests also need their pinned browser tooling). The available Python
gate is:

```sh
.venv/bin/python -m unittest discover -s tools/dashboard/tests -p 'test_*.py'
```

Check the selected test's environment requirements. Native containment and
opt-in [provider conformance](docs/provider-conformance.md) checks require the
appropriate platform and pinned tools; ordinary fake-provider tests do not
need a model account, but provider-backed CLI paths need supported Linux.
Use a Linux checkout for the focused regression, or identify what the exact
revision's CI actually exercised; keep locally unrun checks visible.
Keep run output in ignored directories and preserve
failed attempts. Report exact commands, results and any environment limits.

`scripts/install-hooks.sh` is an optional local convenience that runs the full
suite before every push; it is not the default focused-check policy or a
replacement for CI.

## Make a change

1. **Open or find an issue first** for anything bigger than a typo. Say what
   you plan to do; the maintainer will confirm scope or redirect you.
   Check existing work and follow the [issue-ownership rules](AGENTS.md#taking-an-issue)
   before changing code; multiple sessions share the same GitHub account.
2. **Branch from `master`.** Any name is fine.
3. **Write the test first** when fixing a bug. The test should fail before your
   fix and pass after. Fixture tests with fake providers are the standard;
   see the existing `tests/test_*.py` modules for the patterns and
   `tools/fake_codex.py` / `tools/fake_opencode.py` for the fake providers.
4. **Keep the change small.** One fix or one feature per pull request. A
   250-line diff gets reviewed this week; a 2,500-line diff may not get
   reviewed at all.
5. **Match the surrounding code.** The codebase is plain Python 3.11 with no
   framework and few dependencies; keep it that way. Match the comment
   density and naming of the file you are in.
6. **Update the docs in the same pull request** when behaviour, flags or
   defaults change (`README.md`, `docs/*.md`).
7. **Run the focused checks** for the changed behavior and direct consumers,
   then push and report pending CI accurately. Broader checks need the concrete
   reason described above.

### Worked example: wiring a workflow-owned stage

Use the existing `review_change` stage as the example when an agreed change
needs a workflow-owned job. It reviews an existing change and produces
`review/findings.json`; it does not plan or build the requested product. A new
job or workflow still needs scope agreement under the ground rules above.

1. **Give the job one module.** [autocode_review_job.py](tools/autocode_review_job.py)
   defines `STAGE`, `SCHEMA`, `packet()`/`prompt()`, `apply()`, `owns()` and
   `render()`. Its prompt is the packaged resource
   [review-change.md](tools/prompts/review-change.md). It receives the test-runner
   callback rather than importing the CLI/controller. Review's report uses
   its job schema, not the build roles' `autocode_role_schema` extensions.
2. **Register dispatch and completion.** Import the module and add it to `JOBS`
   and `UNIT` in [autocode_jobs.py](tools/autocode_jobs.py). `review_change`
   belongs to `autoreview`; the registry lets the runner/controller recognize
   the job and select its completion renderer. Registration alone does not
   prepare the model request or choose the workflow's entry stage.
3. **Wire the owning unit.** [units/autoreview.py](tools/units/autoreview.py)
   includes the job in its local `JOBS`, prepares the request with `job_request()`
   and applies it through `apply_job()`. Review uses the existing `sol` route
   and its own scratch-proof callback from the unit. If an agreed job needs a
   separate route, keep preparation and `route_for()` in
   [units/autoplanner.py](tools/units/autoplanner.py) consistent; do not silently
   borrow a different model, effort or saved session.
4. **Choose a supported entry.** [autocode_workflows.py](tools/autocode_workflows.py)
   maps the existing `review` workflow to `REVIEW_STAGE` in `FIRST_STAGE`.
   Recognition or the existing `--workflow review` override reaches it.
   Adding a workflow requires its own agreed scope and recognition/schema
   changes; adding a job does not imply a new CLI subcommand.
5. **Declare names and write boundaries.** Add the stage's human job in
   [autocode_roles.py](tools/autocode_roles.py) and a unique artifact stem in
   [autocode_artifacts.py](tools/autocode_artifacts.py). Review's
   [stage-access rules](tools/autocode_stage_access.py) allow only `review/`
   repository artifacts and put scratch work under `.autocode/scratch/`.
   Its `apply()` rejects stray writes and requires a test for each testable
   blocking finding to fail independently on the reviewed change. Native
   snapshot allowances in [autocode_readonly_events.py](tools/autocode_readonly_events.py)
   also retain source protection; a write-enabled request does not authorize
   arbitrary source changes.
6. **Keep the saved/public contracts explicit.** Review stores its result in
   the existing `review` key in [autocode_run_state.py](tools/autocode_run_state.py).
   New keys need named writers/readers, and new status-view fields belong in
   [autocode_run_view.py](tools/autocode_run_view.py). Preserve old stage IDs,
   tokens, approvals and evidence; do not add a new direct status writer past
   the architecture ratchet.
7. **Exercise the behavior with focused fakes.** [test_review_job.py](tests/test_review_job.py)
   covers routing, schema/request preparation, allowed writes, completion and
   rejection of invented blocking findings. [test_workflows.py](tests/test_workflows.py),
   [test_stage_access.py](tests/test_stage_access.py), [test_roles.py](tests/test_roles.py)
   and [test_artifacts.py](tests/test_artifacts.py) show the registration guards.
   For changed user behavior, add a public CLI/TaskRun regression or a catalog
   scenario with its oracle and broken control; see [scenarios/README.md](scenarios/README.md).
   Run only the affected controls plus architecture checks for changed boundaries.

### Saved-run compatibility

People have half-finished tasks in `.autocode/runs/*/state.json` when they
upgrade. A change to the state format, stage IDs, approval tokens or evidence
records must either read the old shape or refuse it with a clear message.
Silently reinterpreting a saved run, or silently treating it as approved, is a
bug. Call out any state-format change explicitly in the pull request.

## Pull requests

Use the pull request template. A good pull request says:

- what changed and why (link the issue);
- how you tested it (the exact commands);
- whether any documentation, state format or default changed.

The maintainer reviews pull requests roughly weekly. Review comments are
requests, not verdicts; push back if you disagree, with reasons.

## Contributions written with AI tools

AI-assisted contributions are welcome; much of this repository was written
that way. The rules:

- **You are responsible for the code.** Read every line before you submit it.
  "The model wrote it" is not an answer to a review question.
- **Disclose it** with a `Co-Authored-By:` trailer on the commit naming the
  tool, as the existing history does.
- **Tests and docs still apply.** Generated code without tests, or with tests
  that don't exercise the behaviour, will be sent back.
- **Don't paste transcripts.** Pull request descriptions and issue comments
  should be written for a human reader.

## Reporting bugs and proposing features

Use the issue templates. A bug report with a saved `state.json` (secrets
removed), the exact command, and what you expected is fixable; "it got stuck"
is not. Existing write-ups live in [`docs/bugs/`](docs/bugs/) and are a good
model.

Security problems: **do not** open a public issue; see [SECURITY.md](SECURITY.md).

## Licence

AutoCode is licensed under the Apache License, Version 2.0 (see `LICENSE`).
By contributing you agree that your contribution is licensed under the same
terms, as described in section 5 of that licence. There is no separate
contributor agreement to sign.
