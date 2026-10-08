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

The checkout includes eight pinned upstream bug cases in [`arena/catalog.json`](../arena/catalog.json):
three starter Python cases and five hard cases across Python, TypeScript and Go.
Each has a problem description and a self-contained oracle under `arena/cases/`.
The catalog pins the full baseline and reference commit IDs; preparation fetches
those commits, verifies that the baseline fails and the reference passes, and
only then adds the case to the local Arena. TypeScript also receives the disclosed
test-only adapter described below; its upstream pins remain in the catalog.

| Case ID | Project and upstream report | Work to solve | Starter split |
| --- | --- | --- | --- |
| `boltons-indexedset-update` | [Boltons #474](https://github.com/mahmoud/boltons/pull/474) | Update an ordered set from several iterables while retaining order and tuple-valued members | development |
| `humanize-intcomma-large` | [Humanize #392](https://github.com/python-humanize/humanize/pull/392) | Format large positive and negative integers, including ordinary integer subclasses, without losing digits | regression |
| `more-itertools-custom-exceptions` | [More Itertools #1279](https://github.com/more-itertools/more-itertools/pull/1279) | Preserve custom exceptions that evaluate to false and avoid formatting items when a custom exception is given | holdout |

From the checkout root, using its installed venv:

```sh
.venv/bin/python arena/prepare.py --list
.venv/bin/python arena/prepare.py --arena .autocode/arena \
  --case boltons-indexedset-update --case humanize-intcomma-large \
  --case more-itertools-custom-exceptions
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

Omitting `--case` prepares all eight projects. Install their dependencies first,
including the external TypeScript build bundle and Go module cache below, and use
`--oracle-timeout 400` when including Go. Preparation does not install those
dependencies or freeze their contents for you.

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
marked evaluator-authored, problem-only adaptations of upstream issue and PR
reports, not authenticated pre-fix issue snapshots. They omit solution text and comments.
The checked-in source links and reference revisions remain evaluator material;
this host-level setup cannot prevent a model from finding a public historical fix.
The starter holdout is public and is **not a secret or contamination-free benchmark**.
Use fresh private holdouts before drawing general improvement conclusions.

The starter oracles import the candidate source directly and use only Python's
standard library. Humanize's generated version metadata is supplied in memory for source
archive imports; its formatting implementation still comes from the candidate.
These targeted checks do not replace each project's full upstream test suite.
The catalog/preparation tool is distributed with the checkout, not the wheel.

For Humanize's native number tests, supply generated version metadata through
the case-specific setup plugin:

```sh
PYTHONPATH="$PWD/arena/support/humanize" PYTEST_PLUGINS=arena_humanize_bootstrap \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q \
  -p no:cacheprovider tests/test_number.py
```

Put those environment settings in the attempt environment or its configured
test command. The helper supplies metadata only; number formatting comes from
the candidate. The integer-subclass check was added after an earlier four-check
candidate missed that boundary. Prepare a fresh store and cohort for the current
five checks; earlier results retain their original score.

## Hard cases

The harder group exercises interacting algorithms and lifecycle boundaries in
larger projects. The listed module counts describe the upstream reference fixes;
AutoCode may produce a different correct implementation.

| Case ID | Upstream report | Behavior | Production modules in reference fix | Split |
| --- | --- | --- | --- | --- |
| `sympy-matrix-derivatives` | [SymPy #25150](https://github.com/sympy/sympy/pull/25150) and linked reports | Matrix gradients and Hessians, rectangular coordinates, noncommuting powers and tensor axes | 11 | development |
| `django-filteredrelation-join-lifecycle` | [Django #33766](https://code.djangoproject.com/ticket/33766), [PR #16786](https://github.com/django/django/pull/16786) | Conditions that spawn joins, filtered aliases, cloning and nested subqueries | 3 | regression |
| `pytest-stop-fixture-teardown` | [pytest #11706](https://github.com/pytest-dev/pytest/issues/11706), [PR #11721](https://github.com/pytest-dev/pytest/pull/11721) and [PR #12048](https://github.com/pytest-dev/pytest/pull/12048) | Teardown and plugin reports after early stops, including multiple failing callbacks on one fixture | 3 | holdout |
| `typescript-dependent-destructuring` | [TypeScript #35283](https://github.com/microsoft/TypeScript/issues/35283), [PR #46266](https://github.com/microsoft/TypeScript/pull/46266) and linked reports | Correlated union tags and payloads after destructuring, with assignment and error-preservation boundaries | 2 | development |
| `go-http2-hung-reset-health` | [Go #59690](https://github.com/golang/go/issues/59690) | Canceled stream capacity, health acknowledgments, strict admission, healthy reuse and dead connection replacement | 1 | regression |

The pytest case combines early-stop routing with same-fixture exception aggregation.
Its reference is pinned to the [early-stop reapplication](https://github.com/pytest-dev/pytest/pull/12279),
after both upstream fixes; the early-stop change was temporarily reverted between them. Three additional oracle checks
cover same-fixture callbacks under maxfail, stepwise and setup failure, including
callback counts and every plugin-visible exception. Earlier 9-check passes do not
establish this stronger 12-check result: prepare a fresh Arena and cohort; never
rescore or overwrite an existing attempt. The three-module count covers the two
relevant fixes, not all intervening upstream changes.

These are public historical exercises. The holdout split controls which evidence
an improvement proposal exposes; it does not make a public fix private. Fresh
private tasks are needed for stronger generalization claims.

Prepare the three Python hard cases in a fresh destination:

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

Use an oracle deadline of 120 seconds for these Python cases; pytest's oracle
starts several isolated child suites. Benchmark approval still covers only the
exact plan token, never questions, human reviews, recovery or quota overrides.

### TypeScript setup and native tests

The TypeScript controls used Node v25.9.0. Select and freeze the Node executable
before preparation, and keep it unchanged through an attempt. Create the external
build bundle from the checked-in lockfile:

```sh
arena_ts_deps="$PWD/.scenario-runs/arena-dependencies/typescript"
mkdir -p "$arena_ts_deps"
cp arena/support/typescript/package.json arena/support/typescript/package-lock.json "$arena_ts_deps/"
(cd "$arena_ts_deps" && npm ci --ignore-scripts --no-audit --no-fund)
export ARENA_TYPESCRIPT_DEPS="$arena_ts_deps"
export ARENA_TYPESCRIPT_HELPER="$PWD/arena/support/typescript/arena_typescript_compiler.cjs"
export ARENA_NODE="$(command -v node)"
.venv/bin/python arena/prepare.py --arena .autocode/arena-typescript-v1 \
  --case typescript-dependent-destructuring --oracle-timeout 120
```

The lockfile pins TypeScript 4.5.2, `@types/node` 14.18.2 and
`@types/microsoft__typescript-etw` 0.1.1. Record the installed bundle's content
identity, the helper and the Node runtime before freezing the attempt. The pinned
TypeScript package bootstraps the build: both the helper and oracle compile the
candidate's own source in a temporary directory and load that fresh compiler.
Generated candidate build files are not reused. The diagnostic metadata generator
and table also come from the candidate.

The pinned upstream tree lacks the selected native test file. Preparation adds
only `tests/cases/unittests/arenaDependentDestructuring.test.cjs`, containing three
preservation guards that pass on the broken baseline. It commits those new tests
as an adapted baseline and adds the identical adapter to the reference tree.
Upstream runtime source and existing tests are preserved. `arena/catalog.json`
retains the upstream baseline/reference pins; the prepared case's
`preparation.json` records `upstream_base_commit`, `adapted_base_commit`,
`upstream_reference_commit` and per-file hashes under `test_overlay_sha256`.

Use this direct native command in `--option=--test-command=...`, with the same
environment inherited by AutoCode and its regression replay:

```sh
node --test tests/cases/unittests/arenaDependentDestructuring.test.cjs
```

The task must extend that file with its own named regressions and retain the
three guards. Passing only the initial preservation tests does not establish a
fix. The independent oracle checks 15 diagnostic behaviors, including cases
that must continue to report errors. This is a focused compiler scope using an
in-memory declaration library; it does not run the full historic Gulp/Mocha,
conformance, language-service, emit or platform suites. Its external oracle
deadline is 120 seconds.

### Go setup and native tests

The HTTP/2 case uses Go 1.25.5 with `GOTOOLCHAIN=local`. Its independent oracle
uses `testing/synctest` in an external evaluator module; the candidate's upstream
`go.mod` is preserved. Select the frozen Go executable through `PATH` (the local
controls used `/opt/homebrew/bin/go`). Before going offline, run `go mod download`
from a separate checkout of the catalog's baseline into an external `GOMODCACHE`.
The pinned dependencies are `golang.org/x/crypto` v0.28.0, `x/sys` v0.26.0,
`x/term` v0.25.0 and `x/text` v0.19.0.

Freeze the toolchain and module-cache contents before preparation. Give each
attempt separate writable build and temporary directories outside its source;
set `GOCACHE`, `GOPATH`, `GOTMPDIR` and `TMPDIR` to those directories. Then inherit
the following offline settings through preparation and every native replay:

```sh
export GOTOOLCHAIN=local GOMODCACHE=/absolute/frozen/go-module-cache
export GOPROXY=off GOSUMDB=off GOWORK=off GOFLAGS=-mod=readonly
export CGO_ENABLED=0 GODEBUG=asynctimerchan=0
.venv/bin/python arena/prepare.py --arena .autocode/arena-go-v1 \
  --case go-http2-hung-reset-health --oracle-timeout 400
```

Use the following literal native command in `--option=--test-command=...`, with
`PATH` selecting the frozen Go executable:

```sh
go test ./http2 ./http2/hpack -count=1 -timeout=120s -parallel=2
```

The task must add its own native Go regression tests. The evaluator-owned
oracle's six checks use a scripted `net.Pipe` wire peer, public connection state and actual
HTTP/2 frames. They distinguish strict waiting and pool reservations, health ACKs
from peer PINGs, repeated healthy cancellation/reuse, dead connections exhausted
at several concurrency limits, and single-use preservation. It builds and tests
only a disposable copy. A passing child must have actually run and passed its
requested named test; skipped or missing results and real process timeouts are
evaluation errors. Retained controls record two additional local source-integrity
checks separately; these do not add scored behaviors.

Use `--oracle-timeout 400` during both preparation and attempts: compilation is
bounded at 180 seconds and each probe at 20 seconds in Go and 25 seconds by its
supervisor. The native command covers HTTP/2 and HPACK; it does not establish the
full x/net suite, TLS interoperability, real TCP/firewall failure handling or
performance across platforms.

“Hard” describes the selected interacting behaviors, not a measured ranking of
model difficulty. Positive/negative oracle controls and native setup checks
establish case validity; they do not establish live AutoCode success, general
reliability or improvement across languages. Freeze external dependencies and
retain fresh per-attempt evidence before making those claims.

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

## Reliability table

Dated sweep rows live in `docs/reliability-sweeps.json`. The published table is
generated, never edited by hand (#694):

```sh
python tools/reliability_table.py docs/reliability-sweeps.json > docs/reliability-table.md
python tools/reliability_table.py docs/reliability-sweeps.json --readme   # short README copy
```

Fake-provider and live-model rows are separate tables and are never mixed. Each
row names the master commit the sweep ran on. Add a row after a sweep with the
date, profile, commit, mode, run/pass/false-completion counts and a note that
points at the run's bug file or evidence directory. The generator refuses a row
whose counts do not add up or whose mode is neither `live` nor `fake`.
