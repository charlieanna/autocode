# A pinned reliability campaign declaration

[`arena/campaign.json`](../arena/campaign.json) is the **PLANNED** first
protocol slice of [#634](https://github.com/charlieanna/autocode/issues/634).
It declares 13 tasks and two repetitions each: 26 scheduled rows. It contains
no run results, and no campaign has been executed for it. It is a measurement
specification, not a command accepted by the current Arena or scenario CLI.
[`tools/reliability_ledger.py`](../tools/reliability_ledger.py) checks a
published attempt ledger against it and reports the portfolio
([below](#attempt-ledger-and-portfolio-report)), and
[`tools/reliability_import.py`](../tools/reliability_import.py) fills ledger
rows from scenario and Arena result records
([below](#importing-native-results)). Automatic execution and fresh holdout
rotation remain separate work in #634.

The tasks include the three hard Python Arena cases; the existing bug-fix,
feature and greenfield scenarios; the transactional import, ledger, durable
queue, outbox and event-replay ladder; interrupted verification reuse; and a
review followed by a fix in the same run. Verification reuse is explicitly
**fixture** evidence because its interruption comes from the scripted fault.
Other tasks declare live execution. Their results must be reported separately.

## What is pinned

The source revision fixes the complete AutoCode and evaluator inputs. The
manifest additionally records the selected profile, policy file hashes, full
upstream base/reference commits and checks, and each task's entire definition
tree, including briefs, seed, reference, broken controls and hidden tests.
The `build-comparison` profile is copied from the existing profile definition;
its existing role keys, models, efforts and extra options are preserved.

A definition hash is SHA-256 of compact ASCII JSON containing sorted rows
`[repository_relative_path, git_mode, sha256_of_blob_bytes]`. Symlinks use
their Git blob bytes and `120000` mode; they are not followed. `files` counts
the rows. Tests read the local Git objects at the declared source revision,
so later catalogue/profile changes do not rewrite this protocol's inputs.
The manifest can be tested without importing an oracle, preparing projects,
fetching upstream repositories or launching a provider:

```sh
.venv/bin/python -B -m unittest -v tests.test_reliability_campaign
```

The declared Python, psutil, native provider and package versions are future
admission requirements, not evidence that those environments were qualified
for all tasks. Before any attempt, capture and freeze its installed project
and test dependencies and the actual runtime identities. Verify the original
failing and passing controls, preserve all setup failures, and obtain the
normal live-spend/approval authorization. The current Arena option whitelist
does not consume this profile declaration; a future executor must prove its
actual configuration matches, rather than assume a profile name applied it.

## Limits and attempt accounting

Scenario `max_steps` and harness wall time come from their original catalogue
definitions, including the 90-minute ISO-week case and five-minute interrupted
verification case. There is no blanket timeout override. Arena keeps the
documented 3,600-second **per CLI invocation** timeout, 120-second oracle bound
and three-iteration cap. These are not a 3,600-second total campaign ceiling;
this declaration adds no total campaign ceiling or budget refill.

The report compares each attempt's spent budget with these limits. An attempt
that spent more than a declared limit did not run under the pinned budget: it
blocks a comparison, and a `PASS` that did is not counted as a verified
delivery. A harness that stops an attempt at its limit can record slightly
more than the limit; the report still shows it as over, since a ledger row
cannot tell that from a larger budget.

Freeze the manifest hash before admission. Every schedule ID must appear
exactly once in the published attempt ledger, including failures, errors,
skips, unexercised or interrupted work and unattempted `DEFERRED` rows. Keep
the native status/verdict, actual models/efforts, independent oracle evidence,
elapsed time, interventions and spent budget. Unknown usage or cost stays
`null`, never zero. Publish failed comparisons beside successes; do not replace
a failed repetition with a successful retry. A new attempt needs a new declared
schedule, with the original evidence retained.

AutoCode saying complete alone is not delivery proof. Preserve the existing
Arena and scenario verdicts: an Arena `STOPPED` result does not by itself prove
that stopping was appropriate. Fixture passes do not qualify a live version;
incomplete attempts block comparison and no result automatically promotes code.
The Arena holdout split consists of **public historical** cases, not private
or contamination-free holdouts. This finite protocol makes no statistical
reliability or model-effectiveness claim.

## Attempt ledger and portfolio report

The ledger is the published record of a campaign: one JSON object naming the
declaration's `id` and the SHA-256 of its exact bytes, and one row per
scheduled attempt. Start from the template, which records every scheduled
attempt as `DEFERRED` (not attempted), and replace a row as its attempt ends:

```sh
.venv/bin/python tools/reliability_ledger.py template arena/campaign.json > ledger.json
.venv/bin/python tools/reliability_ledger.py report arena/campaign.json ledger.json
.venv/bin/python tools/reliability_ledger.py report arena/campaign.json ledger.json --json
```

The report command reads files only. It runs no AutoCode, oracle or model, never
rescores a native outcome and never promotes a version. It refuses a ledger,
with exit status 2, when:

- a scheduled attempt is missing, appears twice or an unscheduled one is added
  (a retry needs a new declared schedule; the original row stays);
- the ledger names another declaration or its bytes changed after admission;
- a row lacks a declared attempt field or adds an undeclared one;
- a fixture row is relabeled live, or a native outcome is not one the task's
  harness produces;
- a native `PASS` contradicts its own status (a stop on a task that expects
  completion) or carries a failing oracle check;
- a non-`PASS` row has no reason, or a `DEFERRED` row carries a status, oracle
  result, elapsed time, usage, cost or spent budget (an attempt that spent
  anything ran, and keeps its native outcome).

It also refuses a declaration that weakens the acceptance rules it applies or
lists a native outcome that has no portfolio outcome below.

Each row holds the declared fields. `source_revision` is the full commit that
ran, or null when the record does not establish one (a checkout with
uncommitted changes, or an Arena runner that is not the declared revision); a
null revision is reported as a deviation. `oracle_checks` is a list of `{name, ok}`
or null when ungraded, `interventions_decision_or_repair` a list of
`{kind, note}` with kind `decision` (a legitimate answer or approval) or
`repair` (a person fixed the work), and `elapsed_seconds`, `estimated_usd` and
each `spent_budget` value a nonnegative number or null when unknown.
`spent_budget` names its amounts after the task's declared `limits`
(`harness_minutes` and `max_steps` for a scenario; `cli_invocation_seconds`,
`oracle_seconds` and `max_iterations` for Arena). Each declared limit is
reported as within, over or unknown: a spend equal to the limit is within it,
and a missing or null spend is unknown, never within.
`runtime_identity` names the pinned runtime versions and a `dependencies`
freeze record; `actual_models_and_efforts` has the profile's `provider`,
`models` and `effort` shape. An imported Arena row also keeps the runner
options as forwarded (`runner_options`) and `option_deviations`, a list of
strings naming each option that differs from the declared configuration
([below](#importing-native-results)). A live row that lists any is reported
as a deviation; a value that is not a list of strings is refused.

The report puts every attempt in exactly one portfolio outcome:

| Portfolio outcome | Native outcome and evidence |
| --- | --- |
| Verified delivery | `PASS` at a completion status, every oracle check passing (for Arena, every declared upstream check present), evidence references, the declared revision, runtime and live profile, and no repair |
| Verified delivery after a human repair | The same, with at least one `repair` intervention |
| Appropriate stop | `PASS` at a stop on a task that expects a stop; none of this campaign's tasks does |
| Native PASS without complete evidence | A `PASS` missing checks or evidence, recorded on another or no revision, runtime or profile, or over a declared limit |
| False completion | `FALSE_COMPLETE` |
| Stopped (appropriateness not established) | Arena `STOPPED` or scenario `HONEST_BLOCKER` |
| Ended without completing or stopping | Arena `FAIL` |
| Error | `ERROR` |
| Skipped or never reached | `SKIPPED`, `NOT_EXERCISED` |
| Ungraded | `RUNNING`, `INTERRUPTED_UNGRADED`, `PENDING_UNGRADED` |
| Not attempted | `DEFERRED` |

Live and fixture attempts are counted in separate sections and never pooled.
Each section also reports decisions and repairs, cost across every attempt
including failures (an unpriced attempt keeps the total unknown, beside the
known subtotal), cost per verified delivery without repair, and elapsed time.
Each section counts the attempts within every declared limit, over at least
one, and with a spend unknown. A comparison is reported as blocked while any
attempt has no judgement, differs from the declared revision, runtime or
profile, or spent more than a declared limit. Evidence references are the
evaluator's declarations: the report checks their presence, not their
authenticity.

## Importing native results

`tools/reliability_import.py` fills a ledger's `DEFERRED` rows from the
records the harnesses already write. The evaluator names the scheduled attempt
each record belongs to; nothing is matched by guessing:

```sh
.venv/bin/python tools/reliability_import.py arena/campaign.json ledger.json \
  --scenario scenario/bugfix-iso-weeks/01 .scenario-runs/RUN/result.json \
  --arena arena/sympy-matrix-derivatives/01 .autocode/arena-hard-v1 ATTEMPT_ID \
  --runtime-identity runtime.json
```

`--scenario` takes a scenario run's `result.json` (or its evidence directory).
`--arena` takes an Arena store directory and an attempt ID, and reads that
attempt from the store's `results.sqlite3`, opened read-only. Both repeat. The
ledger is updated in place, and the command prints each imported attempt's
native outcome and portfolio outcome with any gaps, deviations or overruns.

The native verdict, status and oracle checks are copied as recorded, never
rescored. What a record does not establish stays null:

- A scenario run records its commit and whether `tools/` held uncommitted
  changes; with changes, the revision is null. Arena records a content
  identity of its runner's `tools/`, not a commit: the revision is the
  declared one only when that identity equals the declared revision's, read
  from local Git objects, and null otherwise.
- A scenario run's cost is the status view's spent amount only when that view
  says it is complete; a floor is not the attempt's cost. Arena does not price
  attempts, so its rows' cost stays unknown.
- The scenario harness measures its wall-clock drive time (`harness_minutes`)
  and its recorded CLI calls (`max_steps`). Arena measures none of its declared
  limits per CLI invocation, so they stay unknown.
- A live scenario run records its resolved profile. Arena records the runner
  options it forwarded. The import reads the provider, and the model and effort
  AutoCode gives each role, from them in AutoCode's order: `--single-model`
  names every model, a role's own effort flag overrides `--reasoning-effort`
  (which the scenario harness writes as the builder's effort), and
  `--resolver-model` and `--resolver-reasoning-effort` override the resolver's
  `--astra-*` flags. It also compares the options as a whole with the declared
  configuration: the scenario harness's flags for the profile (`--provider`,
  `--joint-planning`, the profile's extra options and the role flags) and the
  task's `--max-iterations` cap. Each option that is missing, added or set to
  another value is recorded in `option_deviations` and reported as a
  deviation, so such an attempt's `PASS` is not a verified delivery.
  `--test-command` is not compared, since the declaration leaves each case's
  test command to the evaluator. A scripted (`--fake`) or hybrid run is
  fixture evidence and cannot fill a live row.
- Neither record holds the runtime versions or the dependency freeze.
  `--runtime-identity` adds the evaluator's admission record (a JSON object with
  the pinned runtime keys and `dependencies`) to every row the call imports;
  without it, those rows report the runtime as not recorded.

Each answer and gate the harness gave on the person's behalf, and Arena's
benchmark plan approval, is recorded as a `decision`, its note naming the
answer's source or the gate. Neither harness records a repair: if a person
repaired an attempt's work, add a `repair` intervention to its row by hand.

The import refuses, with exit status 2 and the ledger unchanged, when a row
already holds an attempt (a retry needs a new declared schedule), a record
would fill two rows, the record is of another task or kind, an Arena case in
the store pins another base commit or other checks than the declared task, or
the resulting ledger fails any check of the report.
