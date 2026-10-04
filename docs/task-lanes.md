# Task lanes and multiple tasks

[← Back to README](../README.md)

## Connected task lanes

Use `autocode tasks` (or `autocode-tasks`) when several complete Autocode tasks must
run in order or independently. Tasks inside one lane run sequentially in the same
worktree, so later tasks see earlier source changes. Different lanes use different
worktrees and may run concurrently. Every item invokes the normal UI or code loop;
the task runner does not replace planning, validation or completion gates.

```json
{
  "version": 1,
  "name": "dashboard release",
  "lanes": [
    {
      "id": "application",
      "tasks": [
        {"id": "design", "mode": "ui", "task": "Design the operations dashboard"},
        {"id": "build", "mode": "code", "task": "Build the dashboard", "ui_from": "design"},
        {"id": "polish", "mode": "code", "task": "Polish loading and error states"}
      ]
    },
    {
      "id": "documentation",
      "tasks": [
        {"id": "guide", "mode": "code", "task": "Write the operator guide"}
      ]
    }
  ]
}
```

```sh
autocode tasks flow.json --workspace /path/to/project --max-parallel 2
```

The command saves its checkpoint under `.autocode/task-flows/`. A code task can
pause at its normal plan-approval or intervention boundary; its `run_dir` appears in
the JSON result. Review or resume that ordinary Autocode run, then invoke the same
task-flow command again. Completed UI tasks can feed a later code task through
`ui_from`. `--dry-run` validates and previews the lanes without creating worktrees.

Parallel lanes intentionally remain separate branches. Autocode does not guess how
to merge parallel source changes. Put dependent tasks in one lane, or explicitly
merge completed branches before starting a task that combines them.

## Building components of an architecture in parallel

`autocode components ARCHITECTURE --workspace REPO` takes a design already
committed in `REPO` — `components.json`, `dependency_trace.json` and
`contracts/*.schema.json` — and builds each component as its own AutoCode task
run under `REPO/.autocode-components/<id>`, in parallel with any component it
does not depend on. A component owns only `components/<id>/`; this is checked
against its actual changes at integration, not only requested in its brief.

```sh
autocode components architecture --workspace /path/to/project --joint-planning
```

Without `--auto-approve`, a component that needs a plan approved, a question
answered, or a review accepted stops there; inspect and resume it directly with
`autocode --workspace /path/to/project --run-dir RUN_DIR --status`, using the
`run_dir` this command prints for that component. `--auto-approve` answers
those for every component automatically, including approving the displayed
plan — an explicit person's decision to delegate, not a default choice.

`--integrate TARGET` combines every finished component's changes into `TARGET`
(created fresh from the repository's HEAD if it does not exist yet), left
uncommitted for review, the same way a single AutoCode task leaves its own
work. An existing `TARGET` must be the top directory of a worktree of the same
repository; anything else is refused before a file is written. Extra flags for the underlying task runs (models, reasoning effort,
provider) go after `--options`, shell-quoted.

### A component with a Figma design

Design the screen before starting the component build. In `components.json`,
give that component a `ui_run` pointing to a completed, accepted AutoCode UI
run. Relative paths resolve from the architecture directory, not from the
component worktree:

```json
[
  {
    "id": "web",
    "description": "Team dashboard using the accepted screen design",
    "requirements": ["R1"],
    "depends_on": ["api"],
    "publishes_contracts": [],
    "consumes_contracts": ["team"],
    "ui_run": "../.autocode-ui/runs/ACCEPTED-RUN"
  },
  {
    "id": "api",
    "description": "Team API",
    "requirements": ["R2"],
    "depends_on": [],
    "publishes_contracts": ["team"],
    "consumes_contracts": []
  }
]
```

The UI run may live outside the repository. Its accepted handoff and artifacts
are validated before any component starts. The component receives its ownership
and contract brief plus the accepted UI brief through the ordinary `--ui-run`
build path. It still owns only `components/web/`, and plan approval and independent
implementation review remain required.

Alternatively, use `"figma_file": "https://www.figma.com/design/FILEKEY/Project"`
to supply an existing Figma reference. That URL alone does not establish an
accepted design run. Choose one field per component. Components without either
field keep their ordinary text brief.

Figma implementation requires the existing native Codex Figma workflow, ChatGPT
login and the connected Figma plugin; use `--engine codex` for this build.
Any other explicit `--engine` is rejected before components start. See
[Figma design and implementation](figma.md) for setup and visual verification.
Implementation and review inspect the live file because it can change remotely.
This command consumes an existing design; it does not create a design run.

Progress is saved in `.autocode-components/manifest.json` as each component
starts and stops. Running the same command again continues the build:
finished components are left alone, and a component that stopped for input
picks up from where it stopped, in the same worktree and run. You can answer or
approve that component's own run directly first, or pass `--auto-approve` the
second time. If `components.json`, a contract or an accepted UI handoff changed
since the saved build, the command refuses to resume, because the saved components
were built against the old contracts or designs; remove `.autocode-components/`
to rebuild from scratch. A worktree the manifest does not record is refused too,
rather than guessed at. Rerunning with the same `--integrate TARGET` is safe:
a component whose exact result `TARGET` already holds (the same content and
file mode at every path it changed) is left alone and listed in the summary's
`integration.already_applied`, and a component that finished since is added.
A component is applied only to a `TARGET` that holds none of its result yet,
so a rerun never applies a change twice. Otherwise integration stops at that
component and names the paths that differ. If `TARGET` was edited there or is
checked out at another commit, the message says to integrate into a new
target. If HEAD itself changed those paths since the component was built (for
example, an earlier integration was committed and then edited), a new target
would differ the same way, so the message says to rebuild the component:
remove its worktree `.autocode-components/<id>` and run the command again.

When the work is one requirement that must be split, built in parallel and
combined, use a [program](program.md) instead of lanes: workstreams declare
dependencies and ownership, dependents branch from the merged results of their
prerequisites, and completed workstreams are merged onto one integration branch
with conflicts paused for you.

## Multiple tasks in one project

New implementation tasks automatically get separate Git worktrees and branches,
so two terminal commands or dashboard conversations can use the same project:

```sh
autocode "Add billing history" --workspace /path/to/project
autocode "Fix search navigation" --workspace /path/to/project
```

Each task starts from the project's **committed HEAD**, on an `autocode/<task>-<id>`
branch under `.autocode/worktrees/`. The original checkout and its uncommitted
changes are retained. Commit changes first if new tasks should include them.
Dependencies and ignored environment files are not copied into the new worktree.
The command prints the task workspace and branch; the dashboard discovers its run
through the registry. Each worktree has its own runner lock, checkpoints, and code.
Two writers still cannot operate on the same worktree.

AutoCode's own directories (`.autocode/`, `.autocode-components/`, `.autocode-ui/`)
each hold a `.gitignore` containing `*`, so run state, logs and nested task worktrees
never appear in `git status` or get staged by `git add -A` in your checkout or in a
task worktree. Your own `.gitignore` is not touched, and a `.gitignore` you already
put in one of these directories is left as it is.

### When a task finishes

When a run in a task worktree completes, AutoCode commits the delivered source to the
task's branch (`autocode/<task>-<id>`, author `AutoCode <autocode@localhost>`) and
prints the branch and commit. Only the branch moves: the worktree is detached at the
commit it started from, with the delivered changes still in its files, so the
completion evidence (pinned to the worktree's HEAD) stays current and you can still
inspect or run the result there. Later work in the same worktree, such as a rework
after feedback, is committed on top at its next completion. Runs started with
`--in-place`, program workstreams and components are never committed this way.

Review and merge the branch like any other, for example `git merge autocode/<task>-<id>`
from your checkout. Then remove finished worktrees:

```sh
autocode clean-worktrees --workspace /path/to/project         # list what would be removed
autocode clean-worktrees --workspace /path/to/project --yes   # remove it
```

A worktree is removed only when every run in it is `TASK_COMPLETE`, its source is
exactly what its branch holds, and no runner holds its lock. Its `.autocode/`
records (run state, logs, evidence) are copied to `.autocode/archive/<worktree>/`
first; the branch is kept. Anything else is listed with the reason it is kept.
Worktrees recorded by an `autocode program` are left to that command. The
dashboard and registry show a removed worktree's runs as `workspace_missing`.

Resume from the project while the task is its only unfinished run, or from inside the
task worktree, or name the run from anywhere:

```sh
cd /path/to/project/.autocode/worktrees/TASK && autocode resume
autocode --run-dir /path/to/project/.autocode/worktrees/TASK/.autocode/runs/RUN
```

Existing runs retain their original checkout. `--in-place` explicitly starts a new
task in the selected checkout. Only one run's agents work in a checkout at a time:
a second run started there (or resumed there) while another run's agents are working
prints which run holds the checkout and exits with status 2, changing nothing. Run
the same command again once the other run stops, or start the task without
`--in-place` so it gets its own worktree. Answering, approving or giving feedback to
a waiting run launches no agent and is not blocked. AutoCode never merges branches;
see [When a task finishes](#when-a-task-finishes) for committing and removing worktrees.

See also: [Figma design](figma.md) · [Workflow](workflow.md)
