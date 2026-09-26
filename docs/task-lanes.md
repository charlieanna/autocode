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

Resume with the printed run path and either the original project or task workspace:

```sh
autocode --workspace /path/to/project \
  --run-dir /path/to/project/.autocode/worktrees/TASK/.autocode/runs/RUN
```

Existing runs retain their original checkout. `--in-place` explicitly starts a new
task in the selected checkout and retains its single-writer lock. Worktrees and
branches remain available after a task ends; inspect and commit their changes, then
merge the branch when ready. Autocode does not automatically merge or delete them.

See also: [Figma design](figma.md) · [Workflow](workflow.md)
