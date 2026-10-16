# AutoCode for VS Code

A thin client for AutoCode task runs. It starts the `autocode` CLI, reads the
status view, and takes the action the view names. It imports nothing from
`tools/` and never reads `state.json`.

Start a run, follow its progress, and answer what it asks without leaving the
editor. It never launches a model on its own, cannot approve a plan it has not
displayed, and keeps issued tokens in the child's environment rather than on the
command line.

- Documentation: [`docs/vscode.md`](../docs/vscode.md)
- The contract it implements: [`docs/task-run.md`](../docs/task-run.md)
- Issue: [#884](https://github.com/charlieanna/autocode/issues/884)

```sh
npm ci
npm run build
npm test
code .          # then F5 for the Extension Development Host
```

`src/core/` holds the CLI-facing logic and imports no VS Code API, so `npm test`
runs it in-process. `src/` above it is the editor layer: the runs tree, the detail
panel, and the commands.
