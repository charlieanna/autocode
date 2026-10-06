# AutoCode Arena (experimental)

Arena runs pinned repository issues through the existing public TaskRun interface,
scores the candidate with evaluator-owned checks, and retains a SQLite attempt
ledger. It never opens an issue or PR, pushes a patch, edits AutoCode itself, or
promotes a version. The `autocode-issue` workflow remains the operator's route for
an individual issue and eventual PR submission.

Install the checkout with `pip install -e .`. Commands can also be invoked as
`python tools/autocode_arena.py`. The default store is `.autocode/arena`, ignored
by Git; `--arena PATH` before the subcommand selects another evaluator directory.
Use one operator at a time: concurrent catalog writers are not supported.

## Start with the included projects

The checkout includes six pinned upstream bug cases in [`arena/catalog.json`](../arena/catalog.json).
Each has a problem description and a self-contained oracle under `arena/cases/`.
The catalog pins the full baseline and reference commit IDs; preparation fetches
those commits, verifies that the baseline fails and the reference passes, and
only then adds the case to the local Arena.

| Case ID | Project and upstream report | Work to solve | Starter split |
| --- | --- | --- | --- |
| `boltons-indexedset-update` | [Boltons #474](https://github.com/mahmoud/boltons/pull/474) | Update an ordered set from several iterables while retaining order and tuple-valued members | development |
| `humanize-intcomma-large` | [Humanize #392](https://github.com/python-humanize/humanize/pull/392) | Format positive and negative integers beyond floating-point range without losing digits | regression |
| `more-itertools-custom-exceptions` | [More Itertools #1279](https://github.com/more-itertools/more-itertools/pull/1279) | Preserve custom exceptions that evaluate to false and avoid formatting items when a custom exception is given | holdout |

From the checkout root, using its installed venv:

```sh
.venv/bin/python arena/prepare.py --list
.venv/bin/python arena/prepare.py --arena .autocode/arena
.venv/bin/python tools/autocode_arena.py --arena .autocode/arena cases
```

Preparation requires GitHub network access but makes no model calls. It creates
the Arena, so do not run `init` first. Use a fresh destination; preparation refuses
to overwrite existing evaluator data. Add `--case boltons-indexedset-update` to
prepare just one project. A failed preparation is retained for inspection; choose
a new destination when retrying. Source repositories are in `sources/`, fixed
reference trees in `references/`, and admitted cases in `catalog.json` and `cases/`
under that destination. Candidate attempts get their own frozen broken source
tree without upstream history or remotes.

Run the development case with an authenticated native Codex CLI. The checkout's
venv needs `pytest` for this project's test command; install it before starting
an attempt. Keep the runner source and its dependencies fixed until the attempt
finishes, because changes invalidate the recorded execution identity.

```sh
export PATH="$PWD/.venv/bin:$PATH"
.venv/bin/python tools/autocode_arena.py --arena .autocode/arena \
  run boltons-indexedset-update --cohort baseline-v1 \
  --i-authorize-live-model-spend --approve-benchmark-plans \
  --timeout 3600 --option=--engine=codex \
  "--option=--test-command=$PWD/.venv/bin/python -m pytest tests/test_setutils.py -q"
.venv/bin/python tools/autocode_arena.py --arena .autocode/arena report
```

This makes real model calls and approves the displayed benchmark plan through
Arena's exact plan token. The one-hour deadline applies to each AutoCode CLI
invocation, including a review and repair cycle. To use another configured
provider, replace `--option=--engine=codex` with `--option=--provider=NAME` and
add the model options that provider needs. The test command above is specific
to Boltons; choose the corresponding project's test command for other cases.

These cases are public historical exercises. Their descriptions are explicitly
marked evaluator-authored, problem-only adaptations of upstream PR reports, not
authenticated pre-fix issue snapshots. They omit solution text and comments.
The checked-in source links and reference revisions remain evaluator material;
this host-level setup cannot prevent a model from finding a public historical fix.
The starter holdout is public and is **not a secret or contamination-free benchmark**.
Use fresh private holdouts before drawing general improvement conclusions.

The starter oracles import the candidate source directly and use only Python's
standard library. Humanize's generated version metadata is supplied in memory for source
archive imports; its formatting implementation still comes from the candidate.
These targeted checks do not replace each project's full upstream test suite.
The catalog/preparation tool is distributed with the checkout, not the wheel.

## Hard cases

The harder group exercises interacting algorithms and lifecycle boundaries in
larger projects. The listed module counts describe the upstream reference fixes;
AutoCode may produce a different correct implementation.

| Case ID | Upstream report | Behavior | Production modules in reference fix | Split |
| --- | --- | --- | --- | --- |
| `sympy-matrix-derivatives` | [SymPy #25150](https://github.com/sympy/sympy/pull/25150) and linked reports | Matrix gradients and Hessians, rectangular coordinates, noncommuting powers and tensor axes | 11 | development |
| `django-filteredrelation-join-lifecycle` | [Django #33766](https://code.djangoproject.com/ticket/33766), [PR #16786](https://github.com/django/django/pull/16786) | Conditions that spawn joins, filtered aliases, cloning and nested subqueries | 3 | regression |
| `pytest-stop-fixture-teardown` | [pytest #11706](https://github.com/pytest-dev/pytest/issues/11706), [PR #11721](https://github.com/pytest-dev/pytest/pull/11721) | Teardown and plugin reports after max-failure and stepwise stops | 2 | holdout |

These are public historical exercises. The holdout split controls which evidence
an improvement proposal exposes; it does not make a public fix private. Fresh
private tasks are needed for stronger generalization claims.

Prepare only these cases in a fresh destination:

```sh
.venv/bin/python arena/prepare.py --arena .autocode/arena-hard-v1 --oracle-timeout 120 \
  --case sympy-matrix-derivatives \
  --case django-filteredrelation-join-lifecycle \
  --case pytest-stop-fixture-teardown
```

Install runtime and test dependencies before freezing a cohort. The oracles need
`mpmath` for SymPy and `asgiref`/`sqlparse` for Django. The tested upstream scopes
also use pytest, numpy, hypothesis, attrs, py, pexpect and xmlschema. Keep every
installed version fixed through all attempts. Disable third-party pytest plugin
autoload with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; explicit setup plugins still load.

Use the checkout's absolute venv interpreter in `--option=--test-command=...`.
These bounded upstream scopes cover the affected subsystems; they are not the
entire SymPy, Django or pytest test suites:

| Case | Arguments after `python -m pytest` | Per-process setup |
| --- | --- | --- |
| SymPy | `-q -p no:cacheprovider sympy/core/tests/test_diff.py sympy/core/tests/test_function.py sympy/core/tests/test_args.py sympy/matrices/expressions/tests sympy/tensor/array/tests/test_array_derivatives.py sympy/tensor/array/expressions/tests` | `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` |
| Django | `-q -o 'python_files=test*.py' -p no:cacheprovider tests/filtered_relation tests/queries` | `PYTHONPATH="$PWD/arena/support/django" PYTEST_PLUGINS=arena_django_bootstrap` plus plugin autoload disabled |
| pytest | `-o minversion= testing/test_runner.py testing/test_session.py testing/test_stepwise.py -q` | `PYTHONPATH="$PWD/arena/support/pytest"` plus plugin autoload disabled |

The Django setup plugin uses candidate-local source, its existing test apps and
two in-memory SQLite databases. Other database engines are outside this scope.
The pytest `sitecustomize` helper prefers candidate source and supplies generated
version metadata only when absent; nested pytester processes inherit that source.
Neither helper installs or substitutes a runtime implementation. Apply each
helper only to its own case and keep its files frozen through the attempt. The
environment is inherited by AutoCode's collected, derived and replayed checks.

For example, the Django case can be launched with:

```sh
export PATH="$PWD/.venv/bin:$PATH"
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH="$PWD/arena/support/django" PYTEST_PLUGINS=arena_django_bootstrap \
.venv/bin/python tools/autocode_arena.py --arena .autocode/arena-hard-v1 \
  run django-filteredrelation-join-lifecycle --cohort hard-baseline-v1 \
  --i-authorize-live-model-spend --approve-benchmark-plans \
  --timeout 3600 --oracle-timeout 120 --option=--engine=codex \
  --option=--max-iterations=3 \
  "--option=--test-command=$PWD/.venv/bin/python -m pytest -q -o 'python_files=test*.py' -p no:cacheprovider tests/filtered_relation tests/queries"
```

Use an oracle deadline of 120 seconds for the harder cohort; pytest's oracle
starts several isolated child suites. Benchmark approval still covers only the
exact plan token, never questions, human reviews, recovery or quota overrides.

## Declare a case

Choose an issue you have permission to evaluate, a local clone, and the **full
commit SHA before the fix**. Supply a historical issue JSON snapshot for historical
cases; fetching today's discussion may leak the fix. Arena does not infer the
correct historical commit, scrub spoilers, or discover issues automatically.

Write a self-contained, trusted Python oracle, outside the candidate repository.
It receives the candidate directory as its first argument and prints one JSON
object with a nonempty `checks` list:

```python
import json
import subprocess
import sys
from pathlib import Path

workspace = Path(sys.argv[1])
result = subprocess.run(
    [sys.executable, "-B", str(workspace / "greet.py"), "World"],
    capture_output=True, text=True, timeout=5,
) if (workspace / "greet.py").is_file() else None
ok = bool(result and result.returncode == 0 and result.stdout == "Hello, World\n")
print(json.dumps({"checks": [{"name": "greeting", "ok": ok}]}))
```

The oracle must exit 0 when it successfully evaluates either a passing or failing
candidate. A crash, timeout, malformed result, duplicate/missing required check,
or content mutation yields an evaluation error, never a passing result. Avoid
writing into the candidate; use temporary directories and `python -B` for imports.
The oracle is pinned as a single script. Its imported dependencies, services and
environment are **not** transitively pinned; prefer self-contained offline checks.

Provide a separate known-good reference workspace. Ingestion actually runs the
oracle against the frozen failing baseline and the reference. Both controls must
behave as expected before the case enters the catalog:

```sh
autocode-arena init
autocode-arena ingest OSS-0001 \
  --repository /path/to/local/repo \
  --base FULL_COMMIT_SHA \
  --issue owner/repo#123 --issue-file /path/to/historical-issue.json \
  --oracle /path/to/oracle.py --reference /path/to/known-good-workspace \
  --check greeting --split development
```

Without `--issue-file`, ingestion uses the existing GitHub client to read the issue
and its latest comments; `GH_TOKEN` / `GITHUB_TOKEN` work as in `autocode-issue`.
The supplied snapshot is an evaluator declaration, not independently authenticated
history. A control failure leaves its case directory for inspection; remove that
new failed directory before retrying the same ID. Do not change a cataloged case;
declare a new case instead.

## Attempt and score

```sh
autocode-arena run OSS-0001 --cohort baseline-v1 \
  --runner /path/to/autocode-v1 \
  --i-authorize-live-model-spend \
  --option=--provider --option=YOUR_CONFIGURED_PROVIDER
autocode-arena report --cohort baseline-v1
```

Each attempt gets a fresh repository with only the frozen source tree and one
local base commit. It has no upstream remotes, later commits or copied hooks.
Issue and oracle snapshots remain outside it. The Arena layer never reads
TaskRun's private `state.json`; status and approvals go through its public API.

By default the attempt stops at the operator's plan gate. For automated benchmark
runs, explicitly add `--approve-benchmark-plans`; Arena saves the displayed plan
and approves its exact current token. This is **benchmark automation**, not human
acceptance. Questions, review criteria, recovery and quota overrides are never
automatically answered. `--timeout` bounds each TaskRun CLI invocation, not the
entire experiment; `--oracle-timeout` bounds each independent evaluation.

The attempt directory retains status, checks, content identity, the candidate
patch (including new files), and result JSON. SQLite retains the authoritative
final observation. Stopped attempts are final snapshots; manually continuing the
underlying TaskRun does not rewrite their score. Use a fresh attempt for comparison.
Interrupted attempts remain `RUNNING` in the ledger and block the comparison gate.

| Outcome | Meaning |
| --- | --- |
| PASS | Runner claims completion and all independent checks pass |
| FALSE_COMPLETE | Runner claims completion but an independent check fails |
| STOPPED | Runner pauses or requests input; not proof of a correct stop |
| FAIL | Runner does not complete and is not in a recognized input/pause state |
| ERROR | Execution or evaluation could not establish a verdict |

Exit codes: PASS 0, STOPPED 2, other attempt outcomes 1; invalid requests 2.
Reports show both false completions per attempt and per completion, with explicit
denominators. Raw public usage is retained; dollar cost stays unknown rather than
equating subscription tokens with an API invoice. There is no inferred human time,
maintainer acceptance, or independently measured test coverage.

`--fixture` uses the existing greeting-only scripted Codex provider, refuses other
engines/providers, and needs no live-spend authorization. It tests plumbing; fixture
results cannot qualify an AutoCode version. It does not solve arbitrary OSS issues.

## Controlled improvement

```sh
autocode-arena propose --cohort baseline-v1
# Investigate the development failures, implement a candidate in another checkout.
# Run every declared case freshly under baseline-v1 and candidate-v2, then:
autocode-arena compare --baseline baseline-v1 --candidate candidate-v2
```

`propose` saves development failures, failed check names, and investigation areas.
Root cause remains `UNDETERMINED`: an oracle failure alone cannot establish whether
the bug was in requirements, planning, implementation or validation. It deliberately
does not disclose holdout failures in an improvement proposal or automatically
generate a self-modification. The operator can give this evidence to AutoCode's
ordinary planning/build workflow and review the proposed change.

Declare distinct `development`, `regression` and `holdout` cases before comparing.
The gate requires exactly one baseline and one candidate attempt for every catalog
case, a single source version per cohort, identical case/configuration/approval
policy, live evidence, no incomplete evaluations, no candidate false completion,
no previously passing case lost, and at least one solve improvement on holdout.
Repeated attempts cannot be cherry-picked inside a cohort. A successful gate emits
`CANDIDATE_FOR_HUMAN_REVIEW`, never promotion. This small-sample rule is an engineering
gate, not statistical proof of generalized improvement. Repeated use of a holdout
contaminates it; rotate fresh holdouts and keep them out of improvement workspaces.

## Trust and validation limits

These are trusted-code experiments, not a containment boundary for hostile OSS.
Candidate code, build tools and evaluator code can execute on the same host, with
its permissions. Process deadlines and the existing birth-identity supervisor help
with ordinary failures; a separate directory does not prevent an agent from reading
or altering evaluator files. Use a separate isolated machine/container with no
credentials for unfamiliar code. Catalog, snapshots and SQLite are operator-owned:
hashes detect accidental edits but are not signatures against a hostile writer.

The tests exercise CLI parsing, positive/negative oracle controls, fresh frozen
checkouts, TaskRun approval boundaries using a scripted interface, persistent
outcomes and comparison rejection cases. They substitute oracle transport and the
TaskRun boundary; they do not establish live-provider performance. The shared
supervisor and TaskRun have their own integration suites. A live Arena attempt that
reaches the changed path is still required before marking the PR ready.
