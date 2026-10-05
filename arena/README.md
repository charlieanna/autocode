# Arena starter projects

The [catalog](catalog.json) contains three real open-source bug cases:

- **Boltons:** `IndexedSet.update` with multiple iterables.
- **Humanize:** `intcomma` with very large positive and negative integers.
- **More Itertools:** custom exceptions in `one` and `only`.

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

See [the Arena guide](../docs/arena.md) for running cases, provider options,
comparison gates, and the limits of public historical exercises.
