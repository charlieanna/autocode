# Arena project catalog

The [catalog](catalog.json) contains eight pinned open-source bug cases: three
starter Python cases and five hard cases across Python, TypeScript and Go.

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

Each directory in `cases/` contains a problem-only brief and an independent Python
oracle. Full baseline and fixed reference commits are pinned in the catalog.

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

By default preparation admits all eight cases. Use `--case` to select a subset.
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
