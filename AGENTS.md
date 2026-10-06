# Working on AutoCode

Rules for anyone changing this repository, human or agent. They exist because
the code grew faster than its structure: much of `tools/` sits in import
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
| `tools/dashboard/`, `macos-app/` | Browser dashboard and native macOS host. Frozen: bug fixes only. |
| `docs/` | User documentation. |

**Frozen since 2026-09-26: the browser dashboard (`tools/dashboard/`) and the
macOS app (`macos-app/`).** Bug fixes only, no new features, until the core is
consolidated. Their tests stay in the suite gate. The installed macOS app runs
the dashboard straight from this checkout, so a change that breaks the dashboard
breaks the app. The core must not import the dashboard.

Historical audit records (`audits/`), `learn/` and two top-level result reports
were archived at tag `archive/pre-restructure-2026-09-26`
(`git show archive/pre-restructure-2026-09-26:audits/...`).

## Architecture rules

1. **Keep modules focused.** `autocode.py`, `autocode_goals.py`,
   `autocode_support.py` and `autopilot.py` already carry broad responsibilities.
   New behavior goes in a module with one purpose at the appropriate layer.
   Review responsibilities and dependency direction.
2. **Do not join an import cycle.** 6 modules are in one (listed in
   `tests/test_architecture.py`): the goal lifecycle, workflow, the planning
   unit and the stage context around them. `autocode.py`, `autopilot`, `autocode_goals`
   (the contract; the steps that act on it are in `autocode_goal_lifecycle`)
   and `autocode_support` (the completion gate is `autocode_completion`, the
   stage prompt `autocode_stage_context`) are no longer in one. A new module
   must depend only on lower-level modules, never on `autocode`, `autopilot` or
   anything in a cycle. Pass what you need as arguments instead. The shared
   helpers (clock, hashing, JSON files, locks, snapshots, schemas) are in
   `autocode_util`, which imports nothing from AutoCode. Removing a module from
   the cycles is progress: take it off the list (the test says when).
3. **Target layering**, from the bottom: utilities (files, hashing, locking,
   schemas) → domain (contract, findings, milestones, completion gate; pure
   functions over state) → runtime (processes, providers) → controller
   (`autopilot`, owns the loop) → interfaces (CLI). Lower layers never import
   higher ones.
4. **Run state is an untyped dict with about 140 keys.** Prefer existing keys.
   If you must add one, write it in one place and document what reads it. A
   typed `RunState` is planned.
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

Run everything from the repository root, with the venv interpreter.

```sh
PY=.venv/bin/python   # has psutil; the system python3 does not
$PY -m unittest tests.test_architecture                        # seconds
$PY tools/run_suite.py --changed                               # the tests for what you changed, in parallel
$PY scenarios/run.py run --fake                                # every scenario end to end, under a minute
$PY tools/run_suite.py --scenario-harness                      # harness and catalog, one test per process
$PY tools/run_suite.py                                         # every test, in parallel; what master's CI runs
```

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
change to `tools/`, run `--changed` and the fake scenario runs.

A pull request's CI runs `--changed --all-fast`: every test module except
the slow ones it did not change. `--changed` alone cannot see a CLI-level
test, which imports the harness rather than the module it drives, and that
gap turned master red after #232, #242 and #330. A push to master runs every
test, including the slow ones. A break only a slow module catches can
therefore first show up on master: fix it forward straight away. Making a slow test fast (in-process instead of a CLI
subprocess, a copied Git fixture instead of `git init`, a fake clock instead
of a wait) and taking it off the slow list is progress. Run the full suite yourself only when you change something many
modules share. Every module taken out of the import cycle makes `--changed`
more precise.

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

Tests live in `tools/` for now; do not start a second test directory. For new
end-to-end coverage add a catalog entry with an oracle, a reference solution
and a broken variant (see `scenarios/README.md`). Live-model runs need `--i-authorize-live-model-spend` and are never part of a
routine test run. To run scenarios live on Claude models from a Claude Code cloud session
(setup, batches, restarts, reading results), follow `examples/claude-provider/CLOUD-SESSION.md`.

## Live run before a pull request

Unit tests and fake scenarios cannot show how a real model answers: the first
fix for #195 passed its tests and still looped live, because real GLM
Validators reported PASS where the fake said BLOCKED. So a pull request is opened
only after a live run: AutoCode driven by real models, on the machine where the
change was made, after `--changed` and the fake scenario runs pass. GitHub CI
never runs live models; the pull request carries the evidence.

- **The agent runs it.** The agent making the change also runs the live
  qualification itself, passing `--i-authorize-live-model-spend`: this file is
  the authorization, and asking the user to approve each run is not part of the
  workflow.

- **Any provider.** The rule asks for real models, not a particular tool:
  OpenCode, Kilo Code, or any command-line tool set up as a provider
  (`docs/providers.md`). Use the provider and models you normally work with;
  when the change is in one provider's code, the live run goes through that
  provider. Run a scenario
  (`scenarios/run.py run <scenario> --profile NAME --i-authorize-live-model-spend`;
  `scenarios/harness/profiles.py` has the profiles, such as `glm53-mimo` for
  GLM and MiMo, and `--provider NAME` runs a profile's models through another
  provider) or `autocode` itself with `--provider NAME`.
- **Reach the change.** Pick the scenario or task that runs the changed code;
  a pass that never touches it proves nothing. A path a live run cannot reach
  on demand (a crash, a quota running out) gets a fault-injected or
  fake-provider test instead, and the pull request says so.
- **A failed live run is not automatically the change's fault.** Live runs
  also stop on model variance, quota and provider errors. When the run does
  not pass, run the same scenario on `origin/master`. If master fails the same
  way, open the pull request with both results; if only the change fails, fix
  it first. A run that stops on the provider's own setup before it reaches the
  change (such as `PAUSED_TOOL_CONTAINMENT` on an OpenCode version AutoCode has
  not conformance-tested, #413) is not a result: fix the setup or use another
  provider.
- **Exempt**, with the reason in the pull request: changes to tests, CI or
  docs only; a fix for a red master, which should not wait; bug fixes to the
  frozen dashboard and macOS app.
- **Evidence in the pull request body**, because reviewers cannot open your
  `.scenario-runs/`: the command, the provider, the model for each role, the verdict, the
  duration, the run directory's name, and how you know the changed code ran (a
  stage, an event or a log line). Never paste credentials or whole logs.

A session with no working provider — after trying the setup rules above and
one alternative provider — opens its pull request as a draft that says the live
run is still owed, naming what blocked it; it is marked ready only after a live
run on a local machine.

## Hygiene

- Do not commit run output, logs, `.patch` files or evidence bundles. Scenario
  results go to `.scenario-runs/` (ignored); AutoCode's own state goes to
  `.autocode/` (ignored).
- Write findings worth keeping as a short Markdown note in `docs/bugs/` or in
  the relevant doc, not as a new top-level report file.
- Other AutoCode runs, including self-builds, may be running from this
  checkout. Never edit or delete `.autocode/` contents you did not create.
