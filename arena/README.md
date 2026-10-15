# Arena project catalog

The [catalog](catalog.json) contains eight original pinned open-source bug cases: three
starter Python cases and five hard cases across Python, TypeScript and Go.
It also declares a separate corrected TypeScript successor. Original case
definitions and results remain independent of that successor.

## Starter cases

- **Boltons:** `IndexedSet.update` with multiple iterables.
- **Humanize:** `intcomma` with very large positive and negative integers.
- **More Itertools:** custom exceptions in `one` and `only`.

## Hard public historical cases

- **SymPy:** matrix gradients, Hessians, element coordinates, ordered powers and tensor axes; the upstream fix spans 11 production modules.
- **Django:** filtered joins whose conditions refer to other joins, alias reuse and query cloning; the fix spans three production modules.
- **pytest:** fixture cleanup and plugin error reporting after early stops; the fix crosses session state and the execution protocol.
- **TypeScript:** correlated union tags and payloads after destructuring, with assignment and error-preservation boundaries.
- **Go:** HTTP/2 canceled stream capacity, health acknowledgments, strict admission, healthy reuse and dead connection replacement.

Difficulty describes the interacting behaviors in these selected issues. These
cases are public historical exercises; their fixes are discoverable. They do not
constitute private or contamination-free holdouts.

Each case has a problem-only brief and an independent Python oracle. Full baseline
and fixed reference commits are pinned in the catalog. A `fixture_case` declaration
reuses the named case's oracle and preservation adapter while keeping the
successor's own brief. It does not change the original case's inputs or results.
The corrected successor also carries its approved maintainer instructions in
`preparation_note`, so normal preparation retains the same task scope.

Install the prerequisites in the [Arena guide](../docs/arena.md#hard-cases)
before preparing all projects, including the external
[TypeScript build bundle](../docs/arena.md#typescript-setup-and-native-tests) and
[Go module cache](../docs/arena.md#go-setup-and-native-tests).

From the AutoCode checkout root:

```sh
.venv/bin/python arena/prepare.py --list
.venv/bin/python arena/prepare.py --arena .autocode/arena --oracle-timeout 400
.venv/bin/python tools/autocode_arena.py --arena .autocode/arena cases
```

Preparation fetches the projects and verifies both failing and passing controls.
It needs a fresh destination and makes no model calls. Downloaded repositories,
reference trees, and the validated catalog stay under the chosen Arena directory:

| Path under the chosen Arena directory | Contents |
| --- | --- |
| `sources/<case-id>/` | Downloaded upstream repository |
| `references/<case-id>/` | Pinned fixed reference tree |
| `cases/<case-id>/` | Problem description, oracle and failing control |
| `attempts/<attempt-id>/workspace/` | Candidate workspace created for a run |

By default preparation admits all nine declarations, including the separate
TypeScript successor. Use `--case` to select a subset.
For just the three hard Python projects:

```sh
.venv/bin/python arena/prepare.py --arena .autocode/arena-hard-v1 --oracle-timeout 120 \
  --case sympy-matrix-derivatives \
  --case django-filteredrelation-join-lifecycle \
  --case pytest-stop-fixture-teardown
```

Install the projects' dependencies before preparing or running them. Hard-case
oracles require `mpmath`, `asgiref` and `sqlparse`; upstream suites also require
pytest and their test dependencies. The [Arena guide](../docs/arena.md#hard-cases)
lists the bounded test scopes and source-local setup helpers.

See [the Arena guide](../docs/arena.md) for running cases, provider options,
comparison gates, and the limits of public historical exercises.

## Corrected TypeScript declaration

`typescript-dependent-destructuring-baselines-v2` retains the original upstream
commits, oracle, adapter and all 15 behavioral checks. It permits necessary updates
to only `controlFlowAliasing.errors.txt` and `controlFlowAliasing.types` under
`tests/baselines/reference/`, preserving every test definition, original adapter
guard and unrelated expectation. The original case requires existing upstream
expectations to remain intact, which conflicts with the requested narrowing in an
existing destructured-parameter test. This successor resolves that task scope
conflict explicitly; a successor pass is never an original-case pass.

Prepare it in a fresh directory after the TypeScript setup in the Arena guide:

```sh
.venv/bin/python arena/prepare.py --arena .autocode/arena-typescript-baselines-v2 \
  --case typescript-dependent-destructuring-baselines-v2 --oracle-timeout 120
```

No qualification result is asserted by this declaration. Retain original attempts
and report successor attempts under their separate case ID and cohort.

## Planned reliability protocol

[`campaign.json`](campaign.json) pins 13 existing tasks and 26 scheduled attempts
for the first bounded slice of #634. It is PLANNED and contains no results or
execution command. See [the protocol](../docs/reliability-campaign.md) for its
original limits, complete attempt accounting and public-holdout restrictions,
and for `tools/reliability_ledger.py`, which checks a published attempt ledger
against it and reports the portfolio.
