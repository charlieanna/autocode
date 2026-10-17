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

`npm test` runs the CLI-facing helper tests plus controller and registered-command
regressions with a substituted VS Code host. It also executes the generated webview
script in jsdom, covering plan approval, polling while typing, and model selection.
These tests launch no providers. They do not qualify the actual VS Code host or a
live model route; both remain required before this draft is ready.
