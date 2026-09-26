# Task-type scenarios

[← Back to README](../README.md)

The runner must handle more than one kind of engineering request. This catalogue
freezes one scenario per request type, each with an independent oracle that scores
the delivered workspace. The oracle never reads the runner's own reports and never
uses a candidate's green tests as the sole verdict. Runner honesty scenarios (crash, budget, lying
Builder) live separately under [`test-scenarios/`](../test-scenarios/NOTES.md); the
complexity ladder they both serve is the [progressive testing plan](testing-plan.md).

| ID | Request type | What is asked | What the oracle executes |
| --- | --- | --- | --- |
| `BUGFIX-01` | Fix a bug in committed code | Blank or whitespace-only name must exit 2 with usage; existing tests kept; no README or file changes | Five invocations; seeded test names still present; candidate suite passes on the candidate **and fails on the seeded bug** (the regression is real); README byte-identical; no extra files |
| `FEATURE-01` | Add a feature to an existing project | `list --tag TAG` case-insensitive filter in a seeded notes CLI with saved data; old commands and store format unchanged | Delivered `notes.json` must exist and be byte-identical to the seed; filters on mixed-case tags against a scratch copy; no-match, empty tag and missing value behavior; scratch store unchanged after reads; `add` still works; seeded tests kept and suite passes |
| `ARCH-01` | System architecture task | ADR, `architecture/components.json`, per-component contracts, stub packages, and a checker that enforces the declared import boundaries | ADR sections; exact component set, required edges, acyclic, disjoint ownership; contract operations are callable in `services/<id>/api.py`; AST import checks include relative, comma-separated and nested-package imports, excluding docstring examples. The delivered checker must pass the candidate and an allowed-import control, then reject an injected forbidden static import without a traceback or syntax error. Injection preserves docstrings and future imports |
| `PROGRAM-01` | Large requirement: many components, parallel build, combine, deploy descriptors | Catalog, cart, checkout and gateway as separate stdlib HTTP processes, shared contracts, `run_local.py`, an e2e test, and compose descriptors | Checks all four `contracts/<shape>.json` files for object type and required field declarations. Runs delivered `run_local.py` on free ports; independently drives the gateway journey (list, add two items, checkout total, cart cleared, empty-cart 409, order lookup, unknown order 404); sends SIGTERM and checks launcher exit and closed ports within five seconds. Delivered unittest e2e discovery must run at least one test and exit successfully; assertion quality is not checked. Compose lists all four services; descriptors are **never executed** |
| `UI-01` | Design → implementation (Figma stand-in) | Implement a frozen `design/spec.json` as one self-contained `web/index.html` plus a stdlib structural test | Static: ids, labels, `role=status`, viewport meta, DOM order, no external resources, tokens. In Chromium via Playwright: state/disabled maps, transitions, cancelled note, one-row desktop layout, stacked 44px targets at 375px, no horizontal scroll. Without a browser the result is `DEFERRED`, never `PASS` |

The pre-existing `LIVE-01`, `LIVE-02`, `LIVE-05` and `LIVE-06` scenarios stay registered
unchanged. `python3 tools/live_trial.py --list` prints the whole registry.

## Where the pieces are

- `tools/task_scenarios.py`: seeds, briefs, oracles, and for `PROGRAM-01` a ready
  program manifest (`program_manifest`) so the same scenario can run as one run with
  parallel milestone Builders or as [`autocode program`](program.md).
- `tools/scenario_references.py`: a handwritten reference delivery per scenario. They
  are the oracle's positive control and a starting point for a scripted fixture
  provider; they are not model output.
- `tools/test_scenario_oracles.py`: exercises positive and targeted negative controls.
  The references pass (UI defers without a browser); seed-only controls do not pass;
  and targeted breakages fail on the
  expected row (a fix without a regression test, a case-sensitive filter, a filter that
  rewrites or deletes the store, a vacuous architecture checker, relative and comma
  import violations, unrelated checker crashes, a wrong checkout total, an uncleared
  cart even with green no-op candidate tests, missing contracts, empty or miswired
  launchers, failed shutdown, empty or failing e2e suites, a missing service, a wrong
  UI transition, an unstacked mobile layout). Positive controls include alternate
  stdlib JSON serializers.

## Running a scenario

Offline oracle controls (no provider, no model spend):

```sh
python3 -m unittest tools.test_scenario_oracles
```

Score a workspace you delivered by any route (a manual run, a dashboard run, a
program's integration worktree). The report lands in the evidence bundle:

```sh
python3 tools/live_trial.py BUGFIX-01 --score-only /path/to/delivered/workspace
```

Drive a scenario through the real CLI. The driver seeds the workspace, launches
`autocode`, serves the explicit human gates (`--answer`, `--approve-goal`) from the
saved state, and scores the result with the oracle. The runner's own completion claim
is never the verdict:

```sh
# Harness check only: the scripted fixture provider implements LIVE-01.
python3 tools/live_trial.py LIVE-01 --profile fixture --workspace /tmp/trial

# Authorized live trial of a task type.
python3 tools/live_trial.py FEATURE-01 --profile glm53-mimo --workspace /tmp/trial \
  --i-authorize-live-model-spend

# The large requirement as a program: each workstream is its own reviewed run.
python3 tools/live_trial.py PROGRAM-01 --mode program --profile glm53-mimo \
  --workspace /tmp/trial --i-authorize-live-model-spend
```

Exit codes and verdicts follow [testing](testing.md#live-trial-results):
`PASS` (0), `HONEST_BLOCKER` (2, a recorded pause rather than delivered work),
`FALSE_COMPLETE`/`FAIL`/`ERROR` (1). `--score-only` exits 2 for `DEFERRED`.

`--i-authorize-live-model-spend` never authorizes deployment. The driver's separate
`--authorize-deployment` flag is required to schedule deployment workstreams;
`PROGRAM-01` generates descriptors as ordinary code and does not need that flag.

## Limits, stated plainly

- Every task-type baseline is `NOT_RUN`. The controls cover references and selected
  broken variants, not arbitrary implementations or all task requirements. No live
  model has been scored on these baselines yet. Record each live result
  in [VALIDATION.md](../VALIDATION.md) with profile, revision and bundle path.
- One case per task type is a capability example, not a general pass rate. Comparing
  `PROGRAM-01` in run and program modes reuses the task and scorer, but program mode
  additionally supplies a handwritten decomposition and has separate child budgets
  and approvals. It is a workflow comparison, not an isolated measurement of parallelism.
- The scripted fixture provider implements only the greeting scenario, so `--profile
  fixture` exercises the harness for `LIVE-01` only. Other scenarios need a live profile
  or `--score-only` on a delivered workspace.
- `UI-01` is a stand-in for the Figma route. The real design workflow
  (`autocode ui ... --build`, [Figma](figma.md)) needs the Codex Figma plugin and has no
  offline oracle. `UI-01` tests the implementation half against a frozen reference; it
  does not test Figma inspection or design generation.
- `ARCH-01` examines static Python imports, not dynamic imports or the semantic
  quality of architecture decisions. Its paired checker controls rule out the
  demonstrated syntax/import-crash false positives, not every possible unrelated
  failure or an adversarial checker tailored to the controls.
- `PROGRAM-01` checks contract field declarations, not complete JSON Schema type
  semantics. Delivered e2e discovery checks successful, nonempty execution, not
  assertion quality or candidate-test coverage. Independent HTTP checks decide the
  sampled behavior even when the candidate tests are green no-ops.
- `PROGRAM-01` checks compose service names, not image builds, networking or deployability.
  Its `deploy` workstream is `kind: code` because it writes descriptors only; it still
  forbids running Docker or reaching external systems. Integration depends on that
  descriptor workstream so final verification includes its files. Nothing is deployed.
- These oracles execute candidate Python locally; they are not a security sandbox.
  Subprocess deadlines and process-group cleanup bound ordinary hangs and clean up
  ordinary descendants, but do not contain hostile code or children that detach into
  a different process group. Launcher shutdown checks cover SIGTERM and listener
  closure, not every Ctrl-C race or possible non-listening background worker.
- The driver serves default answers to open questions and approves the displayed plan
  token. That is test automation, not a person's judgement; do not count a driven run
  as human acceptance where a criterion requires human review.

See also: [Program workstreams](program.md) · [Testing plan](testing-plan.md) · [Testing](testing.md)
