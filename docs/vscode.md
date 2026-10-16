# VS Code extension

[← Back to README](../README.md)

A thin client for AutoCode task runs (issue #884, roadmap #1006). It is a client
of the [task-run interface](task-run.md) and nothing else: it starts the
`autocode` CLI, reads `--status`, and takes the action the status view names. It
imports no module from `tools/`, never reads `state.json`, and does not parse the
CLI's prose to decide what to do.

## What it does today

- **Start Run…** picks a workspace, takes the task in your words, names a fresh
  run directory with `--new-run-dir`, and launches
  `autocode "BRIEF" --in-place --no-chat` as a background process. Its own output
  goes to the *AutoCode* channel.
- **Runs** lists the local run registry (`autocode registry list --json`),
  grouped by workspace, and keeps a stale pointer visible with its reason instead
  of dropping it.
- The **run panel** shows what the view says: `progress.line`, the milestone
  tasks, each acceptance criterion's state, the open findings, cost so far
  (unknown cost is shown as unknown, never as zero), and what the run is waiting
  for.
- **Take the named action** runs `view.next_command`. Because that command is
  formatted by AutoCode from `view.needs`, one generic path covers approval,
  answers, review acceptance, resumes, retries and job retries — the same set the
  headless conformance client (#1012) exercises.

## What it will not do

- It never launches a model by itself. Starting a run and continuing one each
  ask you to confirm, because both spend your allowance.
- It cannot approve a plan it has not displayed. `displayed_plan` appears in the
  view only while the last displayed token matches the current sealed contract,
  and the approval is bound to that token: a plan revised while the panel sat
  open is refused, and you are asked to review the new one.
- An answer to a `blocker` or an `operational_exhaustion` request, a `resume`, a
  `retry_job` and a `recover_source` stop are the run asking a person. The client
  asks again before taking them (`autocode.confirmPersonOnly`).
- Issued tokens never go on a command line — `/proc/<pid>/cmdline` is
  world-readable. They travel in the child's environment, the way the CLI
  formats them with the `-` selector.

## Run it from a checkout

```sh
cd vscode-extension
npm ci
npm run build
npm test          # the pure CLI-facing logic, in-process
code .
```

In the debug window (`F5`) the AutoCode view appears in the Activity Bar. Point
`autocode.cliPath` at your `autocode`, or set `autocode.cliArgs` to run it from a
source checkout, e.g. `cliPath: python3`,
`cliArgs: ["/path/to/autocode/tools/autocode.py"]`.

`autocode.provider` is the route your models bill to. Check it before a live run:
AutoCode refuses to spend without it landing where you meant.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `autocode.cliPath` | `autocode` | The CLI to start. |
| `autocode.cliArgs` | `[]` | Leading arguments, for a source checkout. |
| `autocode.provider` | `""` | `--provider` when starting or advancing a run. |
| `autocode.extraOptions` | `[]` | Extra engine and model flags on advancing calls. |
| `autocode.pollSeconds` | `5` | Seconds between `--status` reads. A read never rewrites the checkpoint or launches a provider. |
| `autocode.confirmPersonOnly` | `true` | Ask before acting on a pause the run stopped for a person. |

## Not yet

Follow-ups, the conversation surface, plan editing (`--edit-goal`), recovery
grants, checkpoint compare/restore and dependency binding all need decisions the
view leaves open — a `GOAL_FILE` or a recovery `N` is not something a client may
invent. The panel names them as a gap and points you at the command instead.
