# AutoCode inside your coding agent

[← Back to README](../README.md)

Most people already work in Claude Code, Codex or Cursor. AutoCode can run as one
step inside that agent instead of replacing it: the agent starts an AutoCode run
in the repository, tells you where it stands at each stop, passes on your plan
approval or answer, and reports the evidence when the run completes. Two
integrations do this, with the same four operations:

| Operation | Claude Code plugin (`autocode agent-step`) | MCP server (`autocode mcp`) |
| --- | --- | --- |
| Start a run; it works until it needs you or completes | `start "REQUEST" [--workflow KIND] [-- FLAGS]` | `autocode_start` |
| Where the run stands (reads only), waiting up to a limit for its next stop | `status [--full] [--wait SECONDS]` | `autocode_status` |
| Pass on your decision, or continue | `answer`, `approve`, `revise`, `continue`, `follow-up` | `autocode_respond` |
| The completed run's evidence report | `evidence [--markdown]` | `autocode_evidence` |

Both reach a run only through the [task-run interface](task-run.md) (`TaskRun`
and its status view), like any other integration, and use only the Python
standard library: installing AutoCode is all either needs, and neither adds a
dependency.

## What the agent may and may not decide

Your decisions stay yours. Every operation returns the status view's own fields
that say what happens next, unchanged: `status`, `done`, `pause_category`,
`pause_category_label`, `needs`, `next_command`, `workflow` and `stop_reason`,
with the `progress` line, the `explanation`, the `run_dir`, and, while a plan
waits for approval, the `plan` as `autocode --show-goal` prints it. The agent
shows you these at each stop.

- **Plan approval** (`needs.kind` `approve_plan`) takes `needs.token` from the view
  whose plan you approved. Approval authorizes AutoCode to change files, so the
  agent approves only when you say so; a token from an older plan is refused.
- **Plan changes** (`revise_plan`, also at `approve_plan`) take your words for what to
  change; AutoCode drafts a revised plan for a new approval.
- **A question** (`answer`) takes your answer and `needs.resolver_token` from the
  view you answered. One answer per call; the questions left return under a new
  token. When none are left, the run continues on its own.
- **Continue** works only when `needs.kind` is `continue`: the run needs nothing
  from anyone.
- **A follow-up** (`follow_up`) works only once the run has finished: your next
  request is planned and approved like the first, on the same branch.
- **Every other stop** (a human review, a pause, a recovery, a budget, an
  operational request from the Resolver) is refused with the command you run
  yourself: `next_command`.

A refused call changes nothing. Engine, model and budget flags are set when a run
starts (the skill's `start -- FLAGS`, the MCP server's own command line); later
calls use the run's saved settings. Each call is recorded in the run's
[activity log](execution.md#activity-log) with the caller `agent-step` or `mcp`.

No call waits for the work (#3984). A start, an approval, an answer or a continue
saves what it was given, hands the work to the run's next stop to a background
process, and returns at once with `working: true`. While `working` is true the
other fields describe a run still at work: there is nothing to decide yet. Read
the status again, with a wait (`--wait SECONDS`, MCP `wait_seconds`) shorter than
the agent's own tool time limit, until `working` is false, then act on that stop.
Every advancing call takes the same wait, so a short task can still finish in one
call. While a run works, a call that would advance it again, its evidence, and a
new start in the same workspace are refused, never queued. The background process
outlives the agent's call and the MCP server; if it ends without recording how,
`background.state` is `lost` and the run's own status says what follows.

The background work's records and output are in `.autocode/agent-step/` in the
workspace (`RUN.log` holds the process's output).

Over MCP, a call that waits and carries a `progressToken` in its request's `_meta`
gets `notifications/progress` every few seconds while the run still works: its
`message` is the run's progress line (for example `Builder working · 1 of 3 tasks
done`) and `progress` counts up from 1, with no `total`, since how long the work
takes is unknown. A client that resets its tool timeout on progress can then wait
longer than that timeout in one call; any client can show the line to the person.
A call without a token gets no notifications.

## Claude Code plugin

Install the `autocode` CLI once (`pipx install autocode-supervisor`, then
`autocode setup`; see the [quickstart](quickstart.md)), then add the plugin in
Claude Code, in the terminal or the desktop app:

```text
/plugin install autocode --marketplace charlieanna/autocode
```

or from a shell:

```sh
claude plugin marketplace add charlieanna/autocode
claude plugin install autocode@autocode
```

The plugin adds the AutoCode skill
([`examples/agent-step/skills/autocode`](../examples/agent-step/skills/autocode/SKILL.md)).
Ask for AutoCode in your own words in a Git repository, for example *"Use AutoCode to
add a --json flag to the report command"*; Claude starts the run, shows you its
questions and plan, and passes on only your answers and approval. Without the plugin,
copy that directory to `.claude/skills/autocode` in a project or `~/.claude/skills/autocode`.

The skill runs `autocode agent-step`, so it needs only `autocode` on `PATH`. It adds no
engine or model flags: the run uses the account and models `autocode setup` saved. The
steps can also be run by hand. Each prints one JSON summary; a refusal exits 1 with
the reason on stderr:

```sh
STEP="autocode agent-step"
$STEP start "Add a --json flag to the report command"
$STEP status --wait 50
AUTOCODE_RESOLVER_TOKEN=TOKEN $STEP answer QUESTION_ID "Use snake_case keys" --token -
$STEP revise "Keep the old text output as the default"
AUTOCODE_APPROVE_GOAL_TOKEN=TOKEN $STEP approve -
$STEP continue
$STEP evidence --markdown
$STEP follow-up "Also document the flag in the README"
```

`--workspace PATH` (default: the current directory) selects the repository and
`--run-dir RUN` the run when the workspace has more than one. A token argument of
`-` is read from the variable the view's `next_command` names, which keeps it out
of the process list. Engine and model flags for a new run follow `--` on `start`
(`$STEP start "..." -- --engine codex`). `python -m autocode_cli.autocode_agent_step`
runs the same command line.

A run started this way works in the repository itself, not in a worktree of its own: the
change is left as uncommitted edits for you to review and commit.

`revise` passes the person's requested changes to a plan that waits for approval;
AutoCode drafts a revised plan, which comes back for approval under a new token.
`follow-up` asks a finished run for the next change: it goes back through
Requirements and the plan on the same branch, like the first request.

## MCP server

```sh
autocode mcp --workspace /path/to/project -- --engine claude
```

The server speaks MCP over standard input and output. It serves one workspace,
fixed when it starts, and everything after `--` is the engine, model and budget
flags for runs it starts: the agent cannot change them. With no flags, runs use the
account and models `autocode setup` saved.

For Codex, add it once:

```sh
codex mcp add autocode -- autocode mcp
```

For Claude Code, add it to the project's `.mcp.json`:

```json
{
  "mcpServers": {
    "autocode": {
      "command": "autocode",
      "args": ["mcp", "--workspace", "."]
    }
  }
}
```

The tools return the summary as JSON text and as `structuredContent`. A refusal is
a tool result with `isError` and the reason, so the agent can read it. Tool
arguments are checked against each tool's schema, and an argument it does not
name, such as `options`, is refused. `autocode_respond` takes the same decisions as
the steps above: `approve_plan`, `revise_plan`, `answer`, `continue` and `follow_up`.

### Every project's runs

The server has a fifth tool, `autocode_runs`, so you can ask the agent "what is AutoCode
running?" from any project. It returns what `autocode runs --all --json` prints: `counts`
of every run by bucket (`needs you`, `working`, `settled`, `unreadable`) and one row per
run with its `workspace`, `title`, `status`, `pause_category_label`, `phase`, `updated_at`
and `next_command`. `show` selects the rows: `active` (the default: runs that need you or
are still working), `needs` (only runs that need you) or `all` (finished runs too).

It reads only. The other four tools act on the server's own workspace, so a run of another
project is yours to act on there, with the `next_command` its row names. With the skill,
the agent runs `autocode runs --all --active --json` for the same rows.

## Verify a change you made

To have AutoCode judge a change the agent already made, start a review run that
names the change and what it should do, then read the evidence:

- skill: `$STEP start "Review the uncommitted change in this workspace (git diff HEAD). It should ..." --workflow review -- FLAGS`, then `$STEP evidence --markdown`;
- MCP: `autocode_start` with `{"brief": "Review the uncommitted change ...", "workflow": "review"}`, then `autocode_evidence`.

A review run takes the same path as every task (#3010): Requirements, the Planner and
the Plan Reviewer plan what the review delivers, the person approves that plan, and the
Builder, Tester and Completion Reviewer deliver and check it
([the flow](workflow.md#the-flow)). `--workflow review` labels the run and its report.
