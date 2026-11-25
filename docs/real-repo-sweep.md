# Real-repository benchmark sweep

A recurring live measurement of AutoCode on pinned tasks from real open-source repositories, on the Claude
models. Results go to [the reliability table](reliability-table.md) as `claude-tiers` live rows; this page
says what is measured, how often, and how to run it.

## What is pinned

The sets are fixed in
[`examples/claude-provider/real_repo_sweep.py`](../examples/claude-provider/real_repo_sweep.py), so every
sweep measures the same tasks at the same upstream commits:

| Set | Cadence | Repeats | Cases |
| --- | --- | ---: | --- |
| `starter` | weekly | 3 | Docutils `:number-lines: 0` (scenario `bugfix-realrepo-docutils-number-lines`, 191,000 lines), Boltons #474, Humanize #392, More Itertools #1279 (Arena starter cases) |
| `hard` | monthly | 2 | SymPy #25150, Django #33766, pytest #11706, TypeScript #35283 (the corrected `-baselines-v2` case), Go #59690 (Arena hard cases) |
| `kinds` | monthly | 1 | One feature and one refactor in each of Python, JavaScript, Go, Rust and Java (Arena `kinds` cases, below) |

The `starter` and `hard` sets are bug fixes in Python, TypeScript and Go. The `kinds` set measures the
other jobs a person gives AutoCode, a new feature and a change that keeps behavior, in the languages
people bring most, each at the size of one upstream pull request or commit:

| Language | Feature | Refactor |
| --- | --- | --- |
| Python | More Itertools: `subfactorial()` to count derangements (#1166) | Humanize: `scientific()`'s superscript digits in a module constant (#313) |
| JavaScript | js-yaml: a `maxDepth` limit on nesting (commit 4700baf) | Commander: `node:util`'s `stripVTControlCharacters` in place of its own colour stripping (#2486) |
| Go | cast: float strings such as `"10.9"` to integer types (#261) | golang-lru: the expirable LRU moved out of `simplelru` into its own package (commit 1154eab) |
| Rust | semver: compare by SemVer precedence, ignoring build metadata (#305) | httparse: no duplicated letter branches in chunk-size parsing (#219) |
| Java | Commons CLI: count how often an option appears (#396) | Commons CLI: one helper for `CommandLine`'s repeated "first value or null" (commit bbc7001) |

A feature case's oracle checks the new behavior; a refactor case's oracle checks that behavior is unchanged
on inputs pinned from the base commit and that the duplication the request names is gone.

The cadence and size are the owner's choice (2026-11-24). The upstream pins, oracles and test commands are in
[`arena/catalog.json`](../arena/catalog.json), [Arena](arena.md) and the scenario's `scenario.toml`.

Every case runs on the `claude-tiers` route (`scenarios/harness/profiles.py`): Sonnet for Requirements, the
Planner and the Tester; Opus for the Plan Reviewer, Resolver, Completion Reviewer and Investigator; Haiku
for the Builder; medium reasoning. Fable is never used. Plans are approved by their exact token (benchmark
automation, not a person's review). There is no per-run cost cap, as for all test runs
([AGENTS.md](../AGENTS.md#fake-first-verification-and-optional-live-runs)); the sweep's size is the
spending bound.

## What is reported

For each sweep: runs, passes, **false completions** (AutoCode reported done and the independent oracle
disagrees), median minutes, and each run's reported cost. A run that stops (`STOPPED`, `HONEST_BLOCKER`)
is not a pass; its note says why. A stop that asks a person about AutoCode's own machinery is a defect
and gets fixed ([AGENTS.md](../AGENTS.md#what-a-person-is-asked)).

Cost is the API-equivalent `cost_usd` Claude Code reports for each stage, summed by the status view's
`cost.spent_usd`; on a subscription login it is not an invoice. The pilot (2026-11-23) cost $12.11 for
four runs; a weekly `starter` sweep is about $36, a monthly `hard` sweep an estimated $50 to $80.

These are public historical bugs, so a model may have seen their fixes: the numbers track AutoCode over
time on fixed tasks, not a contamination-free benchmark score.

## Run it

From a Claude Code cloud session, after the setup in
[CLOUD-SESSION.md](../examples/claude-provider/CLOUD-SESSION.md#1-set-up-every-new-session) plus `pytest`
in the venv:

```sh
setpriv --bounding-set=-sys_admin,-sys_ptrace .venv/bin/python examples/claude-provider/real_repo_sweep.py \
  run starter --out /tmp/sweep --repeat 3 --i-authorize-live-model-spend
.venv/bin/python examples/claude-provider/real_repo_sweep.py summary /tmp/sweep --master "$(git rev-parse --short HEAD)"
```

`run` prepares a fresh Arena store for each Arena case under `--out/arenas/` (it needs GitHub access), runs one case at a time, and
writes one record per attempt to `--out/records/`; the same command continues after a restart. For the
`hard` set, install the dependencies [Arena](arena.md#hard-cases) lists first (the Python test
dependencies, the TypeScript build bundle with `ARENA_TYPESCRIPT_*` set, Go 1.25.5 with its frozen module
cache). For the `kinds` set: Go, a Rust toolchain with Cargo, a JDK with Maven and Node 22; the js-yaml
case's own test dependency is installed once under `--out/support` from
[`arena/support/js-yaml`](../arena/support/js-yaml/package-lock.json). `summary` prints the row to add at the top of `docs/reliability-sweeps.json`; then regenerate the
table and the README copy (`python tools/reliability_table.py docs/reliability-sweeps.json`).
