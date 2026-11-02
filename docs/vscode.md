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

New sidebar chats start in the run mode shown on the **Run mode** chip beside the
engine chip. AutoCode lists one mode, **Full**: every chat starts with the
Requirements conversation (the **Quick plan** mode that skipped it was removed
on 2026-10-22). The list and each mode's flags come from
`autocode modes list --json`
([Run modes for clients](task-run.md#run-modes-for-clients)); the extension
passes the chosen mode's flags unchanged. A choice is remembered for new
chats in this workspace; one the list no longer names, such as Quick plan,
starts Full. A chat that has started keeps the flow it started with, so the
chip shows only on a new chat. An AutoCode without run modes shows no chip and
starts the full flow.

In the full flow, new sidebar chats enable joint planning and the core's `--spec-first` flow.
The Requirements agent answers your first message, whatever it is, and talks
with you until it understands what you want done. A greeting or an off-topic
question gets a short answer and a question about what to build or change.
Answer in the same composer. Requirements reuses
its provider session, retains prior replies and streams assistant text through
the public status view; tool output and reasoning stay outside that stream.
A reply still being written shows as **AutoCode · writing…** with provisional
text, the same as the terminal chat, and status polls speed up while that
extract is still growing. You can keep writing a draft while a reply arrives.
The conversation does not show an empty task dashboard.

Once the requirements are clear, read the bound understanding and choose
**Approve understanding and prepare plan**, or send a correction. This releases
independent plan preparation/review. Requirements has already named the
workflow (build, fix, review, design or discuss) with that understanding, and
the approval starts it. It does not authorize
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

Models & usage shows **Cost counted toward cap** from the core’s public budget
accounting beside the configured cap. Partial totals say **at least** and name
unpriced stages; unknown amounts stay unknown. The expanded breakdown keeps the
historical comparison estimate separately. These figures describe recorded usage,
not an invoice or a guarantee that the next step fits within the cap.

**Stages** sits above Models & usage once a run has launched one. Each stage
takes a row for how it went (✓ done, ● working, ↻ retrying, ✗ failed), its time,
cost and attempt number, and a second row for its model, reasoning level and
tokens (`+` when some are not reported yet). The newest four show; **N earlier**
lists the rest. A report repair counts as the repaired stage's next attempt. When
the working stage is on its second attempt or later, the working card shows a
warning with how long it has run and why the last attempt failed. The toolbar
shows the run's cost against its cap and its tokens; with two or more chats open
it adds a total for all of them, which leaves out what a restored run carried
over and ends in `+` while any chat has not been read yet. These figures are the
same ones the terminal chat's stage panel reads: `usage.accounting.attempts`,
`cost.breakdown.by_stage`, `cost.spent_usd` and `usage.accounting.tokens`.

A finished run closes with a card: its result, how many requirements were
checked, the files its saved steps and acceptance tests changed, the time its
stages ran and its cost, with **Show changes** (the saved code changes below)
and **Run details**. A new chat opens with what AutoCode does and three example
requests; choosing one only fills the message box. A tab shows ● while its run
waits for you and ✓ once it has finished, and the status bar names the working
stage and the run's cost.

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

While a run works, **Pause** takes the place of **Send**; Send stays beside it
when the run also takes a message. Pause lets the current step finish and save,
then the run pauses with nothing lost, and **Continue** resumes it where it
paused. Until then the button reads **Pausing after this step…**. The button
appears only when the status view names the pause (`view.pause`); a cancelled
mid-step model call is not offered, and the run's terminal Stop stays outside
the chat.

**Saved code changes** offers only the comparisons and restores named by the
core. While this chat waits for one of its commands to finish, those buttons
are disabled with an explanation. They become available again when the command
finishes; inspecting a run started elsewhere does not disable a named comparison.

**Run details** opens the detailed progress panel. The Runs list reads the local
registry and retains unavailable pointers with their diagnostic. Both surfaces
use the core's named commands, including the optional `view.chat.input` offer.
Commands use argument arrays; issued tokens stay in the child environment.

While a step works, the conversation shows the live agent log the status view
names (`view.chat.log`): what each running agent thinks (`∴`), says (`»`) and
does (`$`, `±`, `·`), with its screen name. Like the terminal chat's `/live`,
those entries are display only — a thought is not a report and a command shown
is not a passed check. Where an older core offers only `chat.activity`, the
conversation shows those tool marks instead.

Starting a new chat asks you to confirm model spend. Buttons that save an input
and then advance explicitly say **and continue**; advancing uses the run’s saved
provider and per-role routes. Person-only recovery decisions still require their
separate confirmation (`autocode.confirmPersonOnly`). Free text cannot bypass an
operational stop or approve a plan.

**Change sets.** For a run linked with runs in other repositories (`scope.linked`,
`autocode changeset link`), Run details reads `autocode changeset status --json`
and shows every run of the change set. While the set approval is offered it lists
every run's plan, and **Approve all N plans** asks first, then approves them
together. If a plan changes after the card was shown, nothing is approved. Once
every run is complete and an integration check is due, **Run the set's check**
asks first, then runs it. These are the only change-set actions the extension
takes ([change sets in clients](task-run.md#change-sets-in-clients)).

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

Each status update is a new `autocode` process, which reads the whole workspace once
before answering. On a large project, set `AUTOCODE_SOURCE_HOST=1` in the environment
the editor starts the CLI with, so the updates share one warm snapshot helper; see
[Execution](execution.md#who-approved) just above "Who approved" for what it does and
keeps.

## Remaining scope

This is an AutoCode chat client with selected Kilo UI adaptations, not the full
Kilo, Cline or ZooCode extension. It displays saved task replies and status
updates; token-by-token model output, file attachments, inline edit/diff
acceptance and editing arbitrary plan files are not implemented.

At a total active-time pause, the core can name a finite total-time decision.
Enter the desired total seconds explicitly in the blank field and confirm that
total. It must exceed the saved cap and active time already consumed; chat text
does not supply or authorize it. Other limits, models and plan approval still
apply.

At a planning-review allowance pause, a core-named finite input shows the saved
allowance and calls already used. Enter an explicit total above both and confirm
the exact number of calls. **Set review allowance** takes only the named command
and waits for the next status before offering any separate continuation. Even a
named command containing Resume can save the allowance first. Used calls stay counted, other
limits and models stay saved, and neither action approves a plan. Blank, zero,
fractional or stale choices cannot run.

At an exhausted operational-recovery boundary, **Additional recoveries** is a
separate blank finite input. It shows the existing recovery allowance and counted
spend. Choose from 1 through the spent count and confirm **Grant and continue**.
Choosing 1 replenishes one spent slot; it does not raise the fixed allowance or
bank excess. History stays retained and the existing consecutive-timeout streak
is cleared by that grant. Other limits, routes and plan approval still apply.

Plan editing (`--edit-goal`) and other actions with unresolved
`GOAL_FILE`, untyped recovery `N` or explanatory placeholders remain explicit gaps.
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

### Stage models

Open **AutoCode: Stage Models** from the command palette, or **Stage models…**
in the chat's engine picker, to choose the model and reasoning level each stage
of a new chat runs. Each stage shows what it does and the kind of model and level
that suits it, with one dropdown for the model and one for the level; the level
list follows the chosen model. A model the stage may not run beside the others (a
Builder on the model its Tester runs, for example) is greyed out with the reason.
**Save** keeps the choice for every new chat on that backend, and **Use the
default** forgets it. Chats already started keep their own models. The panel
reads `autocode stages list --json` and takes only the stage's own save or reset
command ([stage choices for clients](task-run.md#stage-choices-for-clients)),
naming the backend when a provider or engine is chosen for new chats.

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
