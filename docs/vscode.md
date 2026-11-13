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
reopen one. Its **Recent** section lists the ten chats used most recently across
every project, newest first, with how long ago each was active; each project's
chats follow, folded, newest first. **Back to Chat** returns to the current conversation and its draft.
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

### Where a chat works

Each chat changes files on a branch of its own, so two chats never edit the same
files. Before you send the first message, **Where this chat works** above the
composer shows where the new chat will work; open it to choose:

- **New worktree and branch** (the default): the chat talks with you in your
  folder, then plans and builds in a worktree of its own under
  `.autocode/worktrees/`, on a new `autocode/…` branch named from the task,
  starting from your last commit. Your folder, your uncommitted edits and other
  chats are not touched; review and merge the branch when it is done.
- **Existing branch**: the same, on a local branch you pick, for example a
  finished chat's branch to carry on. A branch that is checked out anywhere
  (your folder included) or that another chat is about to use is greyed out with
  the reason, so two chats never share a branch.
- **This folder**: the chat changes your folder's files directly, on the branch
  checked out there, as every chat did before. It shows a warning that your own
  edits and other chats in the folder can conflict with it, naming the chats
  working there now, and asks you to confirm when you send.

Once the chat starts, its header always shows where it works (**Own worktree ·
branch autocode/…** or **Your folder · branch main**), and **Show details** lists
the worktree path, the branch and your folder. The choices come from
`autocode worktrees list --json` and the header from the status view's
`worktree` ([Worktrees for clients](task-run.md#worktrees-for-clients)); the
extension passes the chosen place's own flags unchanged. An AutoCode without the
list starts chats in your folder, as before.

In the full flow, new sidebar chats enable joint planning and the core's `--spec-first` flow.
The Requirements agent answers your first message, whatever it is, and talks
with you until it understands what you want done. A greeting or an off-topic
question gets a short, natural reply, as in any chat, and the conversation stays
open; Requirements starts working out what you want once you say what you want done.
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

Models and usage are shown once, in the run header at the top of the chat (below):
each stage's model, level, time, tokens and cost is a row of its stage table, and
**Show details** has the rest. Cache and reasoning are already included in
input/output totals; partial counts end in `+` or say **known**, and unreported
usage stays unknown. These figures describe recorded usage, not an invoice or a
guarantee that the next step fits within the cap.
A report repair counts as the repaired stage's next attempt. When the working
stage is retrying after a failed attempt, its row shows ↻ and the working card
shows a warning with which attempt it is, how long it has run and why the last
one failed. A stage that runs again after succeeding, such as a second
plan-review round, counts its runs and shows no warning. The toolbar
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
appears only when the status view names the pause (`view.pause`). **Stop now**
beside it, when the view names `view.pause.stop_now`, ends the running step at
once instead of letting it finish: nothing more is spent on it, its partial work
and logs are kept, the run pauses ("Stopped by you"), and **Continue** runs that
step again. The run's terminal Stop stays outside the chat.

**Saved code changes** offers only the comparisons and restores named by the
core. While this chat waits for one of its commands to finish, those buttons
are disabled with an explanation. They become available again when the command
finishes; inspecting a run started elsewhere does not disable a named comparison.

**Run details** opens the detailed progress panel. The Runs list reads the local
registry and the open project's chats, and retains unavailable pointers with their
diagnostic. Both surfaces use the core's named commands, including the optional
`view.chat.input` offer. Commands use argument arrays; issued tokens stay in the child
environment.

Use a chat's actions in History (the icons on its row) or right-click it for
**Archive Chat**, **Unarchive Chat** or **Delete Chat**; the entries appear only when
the registry names them. Archived chats stay out of Recent
and the project lists until **Archived Chats** in the Runs title re-reads the list with
them, when an **Archived** section lists them. Delete asks first and names the chat; it permanently removes that chat's transcript,
logs and evidence (the run directory), never the code branch or task worktree, and refuses
a chat that is still running. Archive is reversible; Delete is not.

Above the conversation, a run header stays in view while you scroll, like Kilo
Code's task header. It says where the run is in one sentence, then shows every
stage in one aligned table: Requirements, Planner, Plan Reviewer, Builder, Tester
and Completion Reviewer, plus any other job the run launched (a model preflight,
an Investigator) where it ran. Each row has the stage's mark (✓ ran, ● working,
↻ retrying, › next, – skipped, · still to come), its name with a ×N badge when it
launched more than once and a `+N repairs` badge for its report repairs, which are
not runs of the stage (amber when an attempt failed; hover for the count in
words), its model without the provider prefix and reasoning level, and the time,
tokens and cost of its own attempts (`+` while some are not reported yet). The
working stage's row is highlighted. A **Total** row gives the run's working time,
tokens and accounted spend. Hover a row for its whole route, or why it was
skipped. Where the sidebar is too narrow for a model column, the model sits under
the stage's name (or beside a stage that has not run yet), and below 340 pixels
the tokens column gives way to time and cost. Under the table, a line says what
the working stage is doing now (`reading files`, `running a command`), then where
the chat works, then how the cost (not the time) splits between the Builder, planning and
checking (`Cost: Builder 4% · planning 28% · checking 68%`) and
how many things went wrong. **Show details** unfolds the same
reading as the terminal chat's `/details`, `/why` and `/spend`: where the run
is; each stage's whole route and attempts (`4 attempts, 3 failed`, and why the
last failed), with its report repairs counted apart (`2 report repairs`: a repair only rewrote a
rejected report, so it is not another run of the stage), with what the working stage is doing and its latest message; every recovery and what AutoCode did about it; where the
money went, each job with its share of the cost and its own time, and what the checking produced; **Cost and usage** (the accounted
total and why it may be a floor, the running stage's spend, the price-table and
provider-reported figures, the historical comparison estimate, the cap, tokens by kind, time by kind of work, total
execution time and every attempt with its model, level, outcome and tokens); the
models saved for the next attempts and any route changes; the verification once
the run finishes; the planning path; and which stages ran or were skipped and
why. A stage with no saved route of its own shows the model its attempt launched
on. The choice to keep details open is remembered. The arrow at the end of the
first line folds the whole header to that one line: where the run is, the
working stage's model and level, then the run's total time and cost; the toolbar
keeps the run's cost against its cap and its tokens. Folding is the same for every chat and is remembered
until you unfold it, including when the sidebar is closed and opened again. Everything in the header
comes from the status view (`summary`, `explanation`, `pipeline`, `recoveries`,
`run_metrics`, `cost`, `usage`, `efficiency`, `routes`, `escalations`,
`route_assignments`, `chat.now`, `chat.log`, `verification`, `stage_policy`,
`stage_decisions`); it shows no command or flag. Its one action is **Change this
chat's models** beside **Show details**, shown when the status view offers
`stage_models`: it opens this chat's own Stage models, and is greyed out with the
core's reason (on hover) while a stage is working.

While a step works, the conversation shows the live agent log the status view
names (`view.chat.log`): what each running agent thinks (`∴`), says (`»`) and
does (`$`, `±`, `·`); each mark names itself on hover. Each agent's screen name
heads its run of steps once, followed by what it is doing now from `view.chat.now`
("Requirements · searching the code"), and the run's headline is not repeated
above it. A run of thoughts folds to one "Thought" line ("Thinking…" while one
still arrives) that a click unfolds. Tool calls are in plain words ("Searched tools
for “context_packet”"), and a run of them with the same `summary` is one counted
line ("Searched code ×4") that a click unfolds to each call. Like the terminal chat's `/live`,
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

## Update from master

Once Marketplace publishing is set up ([VS Code extension](releasing.md#vs-code-extension)),
every master commit that changes the extension is published to the Marketplace, and VS Code's
auto-update installs it like any other extension. An extension installed from a `.vsix` with the
same name is replaced by the next Marketplace version.

To install the newest extension from master yourself, for example before it is published, run
this from any AutoCode checkout:

```sh
scripts/update-vscode-extension.sh
```

It fetches master from `origin`, builds it in a temporary Git worktree (so your
checkout, its branch and uncommitted changes stay as they are), packages the
extension and installs it with `code --install-extension --force`. Then reload the
VS Code window (**Developer: Reload Window**). `--local` builds the checkout you are in
instead, as it stands, and `--remote NAME` fetches from another remote. It needs Node.js
(`npm`) and the `code` command; on macOS, when `code` is not on `PATH`, it uses the one
inside Visual Studio Code.app, and `CODE=cursor` or `CODE=code-insiders` picks another
editor.

The extension starts whichever `autocode` `autocode.cliPath` names. When that CLI is an
editable install of the same checkout (`pipx install --editable`, see
[install from a checkout](install.md#advanced-install-from-a-checkout)), `git pull` on
master updates it too; a separate release install keeps its own version.

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

Each status update is a new `autocode` process, which would otherwise read the whole
workspace once before answering: 13–27 s on a 12.5k-file project, past the 30 s status
bound, so the update fails instead of showing the run. The extension starts every CLI
call with `AUTOCODE_SOURCE_HOST=1`, so the updates share one warm snapshot helper
instead. Setting `AUTOCODE_SOURCE_HOST=0` in the environment the editor starts in turns
it off; see [Execution](execution.md#who-approved) just above "Who approved" for what
the helper does and keeps.

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
spend. It is also offered when the held timeout pause is represented by an
unpublished Resolver exhaustion proposal (`RESOLVER_PENDING`). Choose from 1
through the spent count and confirm **Grant and continue**.
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

A new chat shows **This chat's models** above the composer before you send
anything: each stage with the model and level it will run in its dropdowns, and
beside them where that comes from: the default, your Stage models choice, or this
chat's own (hover for the whole route). Change a stage's model or level there
and only that chat runs it; Stage models and every other new chat are left alone.
AutoCode checks the pick against its list, which takes a few seconds on OpenCode or
Kilo: meanwhile the row keeps your pick and says **Checking…**, and a refused pick
goes back to what the stage runs with the reason. A list read that has not answered
after a minute fails and says so instead of leaving the box greyed out.
The same independence rules apply, so a model the stage's checker runs is greyed
out with the reason, and **Use Stage models** drops this chat's pick. When the chat
starts, AutoCode checks the picks again and the run starts with them; once it has
started they are listed in the run header's stage table like any started chat's models
([Changing a new chat's models](models.md#changing-a-new-chats-models)).

The list is shown at once. Listing the models a backend offers takes a few
seconds on OpenCode or Kilo, so the extension keeps the latest list for each
folder and backend in the workspace and shows it as soon as a chat opens, then
reads it again in the background when it is more than a minute old, after a
restart, or after a Stage models save; the fresh list replaces it in every new
chat that has no picks of its own. Only a folder and backend never listed before
shows "Reading the stages…" first. A chat with picks of its own reads its own
list, and every pick is checked again where the chat starts, so a list shown from
the cache never decides what a run uses.

The **Models** chip under the message box does the same from the chat box: choose
a stage, then one of the models AutoCode offers it, then a level. On a new chat it
changes that chat's pick (the chip counts the stages changed); on a started chat it
changes that chat's own stage from its next launch, through the list the chat's
status names. While a stage is working it is greyed out as "Models · after this
stage", with the core's reason on hover, and it comes back between stages; on a new
chat it reads "Models · loading…" while the backend's models are read and
"Models · starting…" once the first message starts the chat. A model
the stage may not run beside the others is not offered, and AutoCode checks the
pick again either way. An AutoCode without per-chat models shows no chip.

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
