# Task-type scenarios

[← Back to README](../README.md)

The runner must handle more than one kind of engineering request. This catalogue
freezes one scenario per request type, each with an independent oracle that scores
the delivered workspace. The oracle never reads the runner's own reports and never
trusts a candidate's green tests. Runner honesty scenarios (crash, budget, lying
Builder) live separately under [`test-scenarios/`](../test-scenarios/NOTES.md); the
complexity ladder they both serve is the [progressive testing plan](testing-plan.md).

| ID | Request type | What is asked | What the oracle executes |
| --- | --- | --- | --- |
| `BUGFIX-01` | Fix a bug in committed code | Blank or whitespace-only name must exit 2 with usage; existing tests kept; no README or file changes | Five invocations; seeded test names still present; candidate suite passes on the candidate **and fails on the seeded bug** (the regression is real); README byte-identical; no extra files |
| `FEATURE-01` | Add a feature to an existing project | `list --tag TAG` case-insensitive filter in a seeded notes CLI with saved data; old commands and store format unchanged | Filters on mixed-case tags against a scratch copy of the seeded store; no-match, empty tag and missing value behavior; store byte-identical after reads; `add` still works; seeded tests kept and suite passes |
| `ARCH-01` | System architecture task | ADR, `architecture/components.json`, per-component contracts, stub packages, and a checker that enforces the declared import boundaries | ADR sections; exact component set, required edges, acyclic, disjoint ownership; every contract operation is a callable in `services/<id>/api.py`; static import scan finds no undeclared dependency; the candidate's `check.py` passes on the candidate and **fails when a violation is injected** |
| `PROGRAM-01` | Large requirement: many components, parallel build, combine, deploy descriptors | Catalog, cart, checkout and gateway as separate stdlib HTTP processes, shared contracts, `run_local.py`, an e2e test, and compose descriptors | Starts all four processes on free ports and drives the order journey through the gateway (list, add two items, checkout total, cart cleared, empty-cart 409, order lookup, unknown order 404); compose file lists the four services. Deployment descriptors are checked statically and **never executed** |
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
- `tools/test_scenario_oracles.py`: proves each oracle before it may score a live run.
  The reference passes; the seed alone fails; and targeted breakages fail on the
  expected row (a fix without a regression test, a case-sensitive filter, a filter that
  rewrites the store, a vacuous architecture checker, an undeclared import, a wrong
  checkout total, an uncleared cart, a missing service, a wrong UI transition, an
  unstacked mobile layout).

## Running a scenario

Offline oracle proof (no provider, no model spend):

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

## Limits, stated plainly

- Every task-type baseline is `NOT_RUN`. The oracles are proven against references and
  broken variants; no live model has been scored on them yet. Record each live result
  in [VALIDATION.md](../VALIDATION.md) with profile, revision and bundle path.
- The scripted fixture provider implements only the greeting scenario, so `--profile
  fixture` proves the harness for `LIVE-01` only. Other scenarios need a live profile
  or `--score-only` on a delivered workspace.
- `UI-01` is a stand-in for the Figma route. The real design workflow
  (`autocode ui ... --build`, [Figma](figma.md)) needs the Codex Figma plugin and has no
  offline oracle. `UI-01` tests the implementation half against a frozen reference; it
  does not test Figma inspection or design generation.
- `PROGRAM-01` checks the running services and the shape of the deployment
  descriptors. Nothing is deployed, and no container runtime is invoked.
- The driver serves default answers to open questions and approves the displayed plan
  token. That is test automation, not a person's judgement; do not count a driven run
  as human acceptance where a criterion requires human review.

See also: [Program workstreams](program.md) · [Testing plan](testing-plan.md) · [Testing](testing.md)
