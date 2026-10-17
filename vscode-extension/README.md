# AutoCode for VS Code

Early preview. Install from the Marketplace as `ankurkothari.autocode`.

```sh
code --install-extension ankurkothari.autocode
```

The AutoCode CLI must be installed separately. Provider-backed execution requires
a supported Linux host; this extension does not bundle the CLI or model backends.
Full interactive and live-model qualification is incomplete.

A thin client for AutoCode task runs. It starts the `autocode` CLI, reads the
status view, and takes the action the view names. It imports nothing from
`tools/` and never reads `state.json`.

Chat in the sidebar, give plan feedback, approve a reviewed plan, and continue
with follow-up messages in the same run. Composer and scroll behavior adapt
selected Kilo Code components; see [attribution](https://github.com/charlieanna/autocode/blob/master/vscode-extension/THIRD_PARTY_NOTICES.md).
It never launches a model on its own, cannot approve a plan it has not
displayed, and keeps issued tokens in the child's environment rather than on the
command line.

- Documentation: [`docs/vscode.md`](https://github.com/charlieanna/autocode/blob/master/docs/vscode.md)
- The contract it implements: [`docs/task-run.md`](https://github.com/charlieanna/autocode/blob/master/docs/task-run.md)
- Issue: [#884](https://github.com/charlieanna/autocode/issues/884)

```sh
npm ci
npm run build
npm test
code .          # then F5 for the Extension Development Host
```

`npm test` runs the CLI-facing helper tests plus controller and registered-command
regressions with a substituted VS Code host. It also executes the generated webview
script in jsdom, covering chat history, feedback/follow-ups, stale approval,
polling while typing, model selection, Markdown, and composer/scroll behavior.
These tests launch no providers. They do not qualify the actual VS Code host or a
live model route. Full release qualification remains incomplete.

Current editor and live results: [qualification record](https://github.com/charlieanna/autocode/blob/master/docs/bugs/2026-10-16-vscode-chat-qualification.md).

Create a distributable with `npm run package`. This builds the bundled runtime
and includes its license, icon and third-party notices. Packaging does not publish
the extension or qualify its live workflow.
