# Programs: large requirements as parallel workstreams

[← Back to README](../README.md)

One bounded run cannot absorb a requirement that spans many components. A **program**
splits it into workstreams with explicit dependencies and literal ownership, runs each
workstream as an ordinary AutoCode run in its own Git worktree, and merges completed
workstreams onto one integration branch in dependency order. Every workstream still
goes through its own requirements, plan review, exact human approval, Builders,
independent validation and completion gate. The program controller schedules, brands
each child's brief with the shared context, and integrates; it never approves a plan
on your behalf, never merges into your default branch, and never runs a deployment
workstream without explicit authorization.

```text
 Big request
     |
     v
 autocode program plan  ->  ordinary planning units (Requirements, Planner, Plan Reviewer)
     |                      you answer questions and approve the exact displayed plan
     v
 autocode program derive -> program.json  (one workstream per approved milestone + integration)
     |                      you review or edit it
     v
 autocode program run   ->  integration branch  autocode/program-<name>/integration
                             wave 1: workstreams with no dependencies, one worktree each
                             merge --no-ff on completion
                             wave 2: dependents branch from the merged integration head
                             ...
                             integration workstream runs on the integration branch itself
                             deployment workstreams wait for --authorize-deployment
```

## Plan it

```sh
autocode program plan "Build an order system: catalog, cart, checkout, gateway, ..." \
  --workspace /path/to/project
```

This launches the normal planning-only unit (`--unit autoplanner`, read-only, in place)
with a program preamble: milestones must be independently deliverable workstreams
with disjoint `affected_paths`, explicit `depends_on`, contracts first, an early
end-to-end slice, and deployment kept outside the milestones. Answer the questions and
approve the displayed plan exactly as for any run:

```sh
autocode --workspace /path/to/project --run-dir RUN --answer 'Q1=...'
autocode --workspace /path/to/project --run-dir RUN --show-goal
autocode --workspace /path/to/project --run-dir RUN --approve-goal 'r3:<hash>'
```

## Derive the manifest

```sh
autocode program derive --run-dir RUN --output program.json
```

`derive` refuses an unapproved plan. It turns each approved milestone into a
workstream (objective plus its acceptance criteria as the brief, `affected_paths` as
ownership, `depends_on` as dependencies) and appends one `integration` workstream that
depends on every sink milestone and validates the approved end-to-end flow. Shared
constraints, permission boundaries, technical approach and deliverables are copied
into `shared` so every child inherits them. The approved contract's revision and hash
are recorded for provenance.

You can also write a manifest by hand. `tools/task_scenarios.py` carries a complete
example (`PROGRAM_MANIFEST`) for a four-service order system.

### Manifest rules (version 1)

| Field | Rule |
| --- | --- |
| `workstreams[].id` | Letters, digits, `.`, `_`, `-`; unique |
| `kind` | `code`, `ui`, `integration`, `deployment` |
| `owns` | Literal repository-relative paths (no `..`, no `.git`, no `.autocode`). `code`/`ui` must declare at least one. Two workstreams that could run at the same time may not own the same or nested paths; workstreams ordered by a dependency may |
| `depends_on` | Known ids, no self, acyclic |
| integration | At most one; it must (transitively) depend on every non-deployment workstream |
| deployment | Must (transitively) depend on the integration workstream when one exists; nothing except another deployment may depend on it |
| `shared` | Optional lists (`constraints`, `permission_boundaries`, `end_to_end_flow`, `technical_approach`, `deliverables`) and `interfaces` (`id`, `summary`, `paths`) passed into every child brief |

`autocode program run program.json --dry-run` validates and previews without touching Git.

## Run it

```sh
autocode program run program.json --workspace /path/to/project --max-parallel 2
```

Each invocation does one pass:

1. Creates the integration branch and worktree on first use (from the project's
   committed `HEAD`, under `.autocode/worktrees/program-<name>-integration`).
2. Refreshes every launched workstream from its child run's saved `state.json`.
3. Commits and merges (`--no-ff`) every workstream whose run reached `TASK_COMPLETE`.
   Runner metadata under `.autocode/` is never committed.
4. Starts every workstream whose dependencies are all merged, up to `--max-parallel`
   at once, each in a fresh worktree branched from the current integration head.
   Child runs are ordinary `autocode <brief> --in-place --no-chat` runs; `--engine`
   and any unrecognized flags are passed through to them.
5. Prints a JSON summary and exits `0` only when every workstream is merged.

A child run pauses at its own human gates. The summary lists each waiting run
directory; answer or approve there with the normal CLI, then rerun `program run`. A
run you have acted on (its saved status is back to `RUNNING`) is resumed by the
program; a run still at a gate or at any `PAUSED_*` status is left alone.

| Program status | Meaning | Your next action |
| --- | --- | --- |
| `WAITING` | A child run needs a question answered, a plan approved, or an explicit resume | Act in the listed run directory, rerun |
| `PAUSED_MERGE_CONFLICT` | A completed workstream conflicts with the integration branch; the merge was aborted, both branches are intact | Merge it by hand in the integration worktree, commit, rerun (the program adopts the manual merge) |
| `PAUSED_INTEGRATION_DIRTY` | Tracked files in the integration worktree were changed outside a workstream | Commit or restore them, rerun |
| `AUTHORIZATION_REQUIRED` | Everything else is merged; only deployment workstreams remain | Rerun with `--authorize-deployment` after deciding deployment is wanted |
| `BLOCKED` | A child process failed without a saved run | Read `.autocode/programs/<name>/<workstream>/stderr.log`, rerun |
| `COMPLETE` | Every workstream merged on the integration branch | Review the branch and merge it into your default branch yourself |

`autocode program status program.json --workspace ...` prints the same summary without
launching anything. A saved program's manifest is frozen: editing `program.json`
after the first run is refused; start a new program name instead.

## What each child sees

The composed brief (saved under `.autocode/programs/<name>/<workstream>/brief.md`)
contains the program outcome, shared constraints, permission boundaries, technical
approach and end-to-end flow, the shared interfaces, the prerequisite workstreams
already merged on its branch, the workstream's own objective and acceptance criteria,
the exact paths it owns, the paths owned by others, and an instruction not to deploy,
reach external systems or merge. Ownership is advice to the child run's planner; the
child run's own Builder ownership checks and the merge step are the enforcement.

## Boundaries

- No automatic merge into `main`/`master`. The integration branch is yours to review.
- No conflict resolution by the tool. A conflict pauses with both branches preserved.
- No deployment without `--authorize-deployment`, and even then the deployment
  workstream is a normal reviewed run that must not reach external systems unless its
  own approved plan says so.
- The program controller reads only saved child state. An exit code, elapsed time or a
  Builder's report never marks a workstream complete.
- Evidence stays per run: each child run's validation and completion records remain
  in its own run directory; the integration workstream is where the whole flow is
  independently validated on the merged code.

Testing: `python3 -m unittest tools.test_program` covers manifest rules, derivation
from an approved contract, wave order, worktree bases, merges, conflict pause and
manual resolution, the deployment gate, failure blocking, and a real CLI first wave
with the fake Codex provider. The `PROGRAM-01` scenario in [scenarios](scenarios.md)
provides the end-to-end oracle for a live trial (`--mode program`).

See also: [Task lanes](task-lanes.md) · [Execution](execution.md) · [Workflow](workflow.md)
