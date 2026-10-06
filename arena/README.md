# Arena project catalog

The [catalog](catalog.json) contains six pinned open-source bug cases.

## Starter cases

- **Boltons:** `IndexedSet.update` with multiple iterables.
- **Humanize:** `intcomma` with very large positive and negative integers.
- **More Itertools:** custom exceptions in `one` and `only`.

## Hard public historical cases

- **SymPy:** matrix gradients, Hessians, element coordinates, ordered powers and tensor axes; the upstream fix spans 11 production modules.
- **Django:** filtered joins whose conditions refer to other joins, alias reuse and query cloning; the fix spans three production modules.
- **pytest:** fixture cleanup and plugin error reporting after early stops; the fix crosses session state and the execution protocol.

Difficulty describes the interacting behaviors in these selected issues. These
cases are public historical exercises; their fixes are discoverable. They do not
constitute private or contamination-free holdouts.

Each directory in `cases/` contains a problem-only brief and an independent Python
oracle. Full baseline and fixed reference commits are pinned in the catalog.

From the AutoCode checkout root:

```sh
.venv/bin/python arena/prepare.py --list
.venv/bin/python arena/prepare.py --arena .autocode/arena
.venv/bin/python tools/autocode_arena.py --arena .autocode/arena cases
```

Preparation fetches the projects and verifies both failing and passing controls.
It needs a fresh destination and makes no model calls. Downloaded repositories,
reference trees, and the validated catalog stay under the chosen Arena directory.
By default it prepares all six cases. To prepare only the harder group:

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
