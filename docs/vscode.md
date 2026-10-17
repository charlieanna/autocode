# VS Code extension

[← Back to README](../README.md)

Chat with AutoCode in the sidebar (issue #884). The extension starts the CLI,
reads `--status` and `registry list --json`, and takes the actions the public
[task-run interface](task-run.md) names. It imports no runner internals and never
reads `state.json`.

## Chat in the sidebar

Open **AutoCode → Chat**, type a request, and press Enter. Shift+Enter adds a
newline. Chat fills the sidebar, keeping the composer at the bottom. **New chat**
starts another conversation; **History** switches to the Runs list so you can
reopen one. **Back to Chat** returns to the current conversation and its draft.
The selected conversation and each unsent draft survive reopening VS Code.
When the same workspace is open in multiple VS Code windows, each window keeps
its own selected run and drafts, so switching tasks in one window does not
change the conversation shown in another. The Runs list in each window can
still open any task registered for that workspace.

New sidebar chats enable joint planning and the core's `--spec-first` flow.
A bare **hi** receives an immediate goal question without inference or model
probes. Your reply becomes the task. The status shows **Understanding your
request** while the core chooses the workflow (build, fix, review, design or
discuss); the detailed model history keeps the **Job recognizer** stage name.
For a build, the Requirements agent then talks with you before planning.
Answer in the same composer. Requirements reuses
its provider session, retains prior replies and streams assistant text through
the public status view; tool output and reasoning stay outside that stream.
You can keep writing a draft while a reply arrives. The conversation does not
show an empty task dashboard.

Once the requirements are clear, read the bound understanding and choose
**Approve understanding and prepare plan**, or send a correction. This releases
independent plan preparation/review. It does not authorize
implementation. **Approve and build** appears later for the concrete reviewed
execution plan. Terminal users can answer the requirements review with **plan**.

The thread shows your messages, saved answers and completed-turn replies, with
Markdown and code blocks for assistant replies. A fixed composer grows with your
draft. New output follows the bottom until you scroll up or select text;
**Latest messages** returns to the bottom. These composer and scrolling components
are adapted from Kilo Code's open-source UI; the exact revision and attribution
are in [THIRD_PARTY_NOTICES.md](../vscode-extension/THIRD_PARTY_NOTICES.md).

**Models & usage** stays above the composer and starts expanded. **Configured
models** lists every saved role assignment with its full model ID, provider and
reasoning **Variant** (such as medium or high), even before that role runs.
**Models used** groups recorded attempts by job, model, provider and variant,
including retries and earlier models. Several stages or slots can serve the same
job; configured assignments do not imply that all those stages will run.

Collapse the panel to a summary that still shows the current attempt (or **Last
used**), variant, configured-role count, tokens and cost. Your expanded/collapsed
preference survives polling, History navigation
and reopening the chat. Values refresh with status polling and cover the whole
run. Cache and reasoning are already included in input/output totals. Partial
counts are labeled; unreported usage stays unknown.

Provider-reported cost and the core's historical comparison estimates are shown
separately. These estimates are not current API prices or your token-plan bill.
The extension reads the public accounting view and does not calculate its own
prices. A new chat shows configured models once the core creates the run; actual
model history appears as attempts are recorded. Older cores without the new
display metadata still show available model IDs and leave missing variants unknown.

When a plan is ready, the thread lists what it will deliver, marks the items you
check yourself, and shows its constraints and what it may do. Criterion IDs,
verification commands and the plan revision stay in **Run details**. Send
**plan feedback and continue** to revise it. **Approve and build** approves only
the displayed plan, then continues the same run. A stale click is refused and
the revised plan must be reviewed.
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
and then advance explicitly say **and continue**; advancing uses the run’s saved
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

For Kilo, set `autocode.provider` to `kilocode`, connect the selected providers
with `kilo auth`, and put your per-role model routes in `autocode.extraOptions`.
The CLI and Kilo must run on the same supported backend host. A Docker or remote
wrapper must forward the argument array and issued token environment, and expose
the same workspace paths. Point `cliPath`/`cliArgs` at that real CLI or wrapper;
the qualification fixture is only a UI test backend and cannot conduct a model
conversation.

PR #1425 was merged before full live qualification finished. Its draft
Requirements follow-up has passed a real Kilo/token-plan greeting conversation;
full build/test/completion qualification remains outstanding. The earlier full
task attempt stopped on a MiMo network failure before building.
See the [qualification record](bugs/2026-10-16-vscode-chat-qualification.md).
The jsdom tests alone establish neither editor-host nor live-provider compatibility.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `autocode.cliPath` | `autocode` | The CLI to start. |
| `autocode.cliArgs` | `[]` | Leading arguments, for a source checkout. |
| `autocode.provider` | `""` | `--provider` for new chats. |
| `autocode.extraOptions` | `[]` | Extra engine and model flags for new chats. |
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

### Connections

Open **Connections** from the chat toolbar or **AutoCode: Connections** in the
command palette. It lists installed and registered backends in the AutoCode CLI
environment. **Check connection** checks saved credentials without launching a
model. **Sign in / Reconnect** runs the backend's own sign-in in a terminal;
finish there and check again. AutoCode does not ask you to paste credentials into
chat. Credential presence does not establish selected-model access or a passing
live qualification.

**Use for new chats** changes the default backend. Each backend's role/model
options are saved separately, so switching back to Kilo restores its token-plan
routes. Existing chats retain their saved backend, models and budgets. Company
providers such as GoCode appear when registered; their optional connection
commands are described in [Providers](providers.md#connection-setup-and-company-backends).

### No Kilo or OpenCode installed

The **AutoCode · Direct connection** card is always available in Connections.
Choose **Set up AutoCode** to select MiMo, Z.ai, OpenAI or Claude and enter its
API endpoint/key in a terminal, then refresh Connections. **Use for new chats** is enabled after
local setup is complete; it does not mean live model access was verified.
See
[AutoCode direct connections](providers.md#autocode-direct-connections-issue-1493)
for credential storage, protocol support and qualification limits.

Credentials are never entered into the chat or its webview. Existing chats keep
their saved backend. Model overrides, explicit external role transports and
backend-specific containment options are kept with their original backend when
switching connections, so Kilo settings do not become direct API settings.
