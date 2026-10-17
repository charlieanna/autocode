# VS Code extension

[← Back to README](../README.md)

Chat with AutoCode in the sidebar (issue #884). The extension starts the CLI,
reads `--status` and `registry list --json`, and takes the actions the public
[task-run interface](task-run.md) names. It imports no runner internals and never
reads `state.json`.

## Chat in the sidebar

Open **AutoCode → Chat**, type a request, and press Enter. Shift+Enter adds a
newline. **New chat** starts another conversation; **History** focuses the Runs
list so you can reopen one. The selected conversation and each unsent draft
survive reopening VS Code.

The thread shows your messages, saved answers and completed-turn replies, with
Markdown and code blocks for assistant replies. A fixed composer grows with your
draft. New output follows the bottom until you scroll up or select text;
**Latest messages** returns to the bottom. These composer and scrolling components
are adapted from Kilo Code's open-source UI; the exact revision and attribution
are in [THIRD_PARTY_NOTICES.md](../vscode-extension/THIRD_PARTY_NOTICES.md).

When a plan is ready, its criteria, verification methods, constraints and
permissions appear in the thread. Send **plan feedback and continue** to revise
it. **Approve and build** approves only the displayed plan, then continues the
same run. A stale click is refused and the revised plan must be reviewed.
Answer cards handle questions and model replacement choices.

After completion, sending a **follow-up and continue** reopens the same run for
the next turn. Earlier messages and completed replies remain visible. A new
build plan requires its own approval. While AutoCode works, you can compose a
draft; sending becomes available when the core offers a message action.

**Run details** opens the detailed progress panel. The Runs list reads the local
registry and retains unavailable pointers with their diagnostic. Both surfaces
use the core's named commands, including the optional `view.chat.input` offer.
Commands use argument arrays; issued tokens stay in the child environment.

Starting a new chat asks you to confirm model spend. Buttons that save an input
and then advance explicitly say **and continue**; advancing uses your configured
provider and per-role routes. Person-only recovery decisions still require their
separate confirmation (`autocode.confirmPersonOnly`). Free text cannot bypass an
operational stop or approve a plan.

## Run it from a checkout

```sh
cd vscode-extension
npm ci
npm run build
npm test
code .
```

Press F5 to launch the configured Extension Development Host. The AutoCode view
appears in the Activity Bar. Point `autocode.cliPath` at your `autocode`, or set
`cliPath: python3`, `cliArgs: ["/path/to/autocode/tools/autocode.py"]` for a source
checkout. Provider execution needs a supported host; see [testing.md](testing.md).

PR #1425 remains draft until the actual editor flow and the owner's live-model
qualification both pass. The editor fixture flow has been exercised; the Kilo
token-plan live attempt stopped on a MiMo network failure before building.
See the [qualification record](bugs/2026-10-16-vscode-chat-qualification.md).
The jsdom tests alone establish neither editor-host nor live-provider compatibility.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `autocode.cliPath` | `autocode` | The CLI to start. |
| `autocode.cliArgs` | `[]` | Leading arguments, for a source checkout. |
| `autocode.provider` | `""` | `--provider` when starting or advancing a run. |
| `autocode.extraOptions` | `[]` | Extra engine and model flags on advancing calls. |
| `autocode.pollSeconds` | `5` | Seconds between read-only status updates. |
| `autocode.confirmPersonOnly` | `true` | Ask before acting on a pause the run stopped for a person. |

## Remaining scope

This is an AutoCode chat client with selected Kilo UI adaptations, not the full
Kilo, Cline or ZooCode extension. It displays saved task replies and status
updates; token-by-token model output, file attachments, inline edit/diff
acceptance and editing arbitrary plan files are not implemented.

Plan editing (`--edit-goal`), recovery grants and other actions with unresolved
`GOAL_FILE`, recovery `N` or explanatory placeholders remain explicit gaps.
The client cannot invent those decisions. The separate core Requirements
conversation journal is documented in [task-run.md](task-run.md); this client
uses ordinary task runs and their additive chat projection.
