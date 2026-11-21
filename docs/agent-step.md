# AutoCode inside your coding agent

[← Back to README](../README.md)

Most people already work in Claude Code, Codex or Cursor. AutoCode can run as one
step inside that agent instead of replacing it: the agent starts an AutoCode run
in the repository, tells you where it stands at each stop, passes on your plan
approval or answer, and reports the evidence when the run completes. Two
integrations do this, with the same four operations:

| Operation | Claude Code skill (`autocode_agent_step`) | MCP server (`autocode_mcp`) |
| --- | --- | --- |
| Start a run; it works until it needs you or completes | `start "REQUEST" [--workflow KIND] -- FLAGS` | `autocode_start` |
| Where the run stands (reads only), waiting up to a limit for its next stop | `status [--full] [--wait SECONDS]` | `autocode_status` |
| Pass on your decision, or continue | `answer`, `approve`, `continue` | `autocode_respond` |
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
- **A question** (`answer`) takes your answer and `needs.resolver_token` from the
  view you answered. One answer per call; the questions left return under a new
  token. When none are left, the run continues on its own.
- **Continue** works only when `needs.kind` is `continue`: the run needs nothing
  from anyone.
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

## Claude Code skill

Copy [`examples/agent-step/skills/autocode`](../examples/agent-step/skills/autocode/SKILL.md)
to `.claude/skills/autocode` in your project, or to `~/.claude/skills/autocode` for
every project. Claude Code then uses it when you ask for AutoCode. The skill runs
the step with the Python that has AutoCode installed: set `AUTOCODE_PYTHON` to
that interpreter when it is not `python3`.

The steps can also be run by hand. Each prints one JSON summary; a refusal exits 1
with the reason on stderr:

```sh
STEP="python3 -m autocode_cli.autocode_agent_step"
$STEP start "Add a --json flag to the report command" -- --engine claude
$STEP status
AUTOCODE_RESOLVER_TOKEN=TOKEN $STEP answer QUESTION_ID "Use snake_case keys" --token -
AUTOCODE_APPROVE_GOAL_TOKEN=TOKEN $STEP approve -
$STEP continue
$STEP evidence --markdown
```

`--workspace PATH` (default: the current directory) selects the repository and
`--run-dir RUN` the run when the workspace has more than one. A token argument of
`-` is read from the variable the view's `next_command` names, which keeps it out
of the process list.

## MCP server

```sh
python3 -m autocode_cli.autocode_mcp --workspace /path/to/project -- --engine claude
```

The server speaks MCP over standard input and output. It serves one workspace,
fixed when it starts, and everything after `--` is the engine, model and budget
flags for runs it starts: the agent cannot change them. For Claude Code, add it
to the project's `.mcp.json`:

```json
{
  "mcpServers": {
    "autocode": {
      "command": "python3",
      "args": ["-m", "autocode_cli.autocode_mcp", "--workspace", ".", "--", "--engine", "claude"]
    }
  }
}
```

The tools return the summary as JSON text and as `structuredContent`. A refusal is
a tool result with `isError` and the reason, so the agent can read it. Tool
arguments are checked against each tool's schema, and an argument it does not
name, such as `options`, is refused.

## Verify a change you made

To have AutoCode judge a change the agent already made, start a review run that
names the change and what it should do, then read the evidence:

- skill: `$STEP start "Review the uncommitted change in this workspace (git diff HEAD). It should ..." --workflow review -- FLAGS`, then `$STEP evidence --markdown`;
- MCP: `autocode_start` with `{"brief": "Review the uncommitted change ...", "workflow": "review"}`, then `autocode_evidence`.

A review run takes the same path as every task (#3010): Requirements, the Planner and
the Plan Reviewer plan what the review delivers, the person approves that plan, and the
Builder, Tester and Completion Reviewer deliver and check it
([the flow](workflow.md#the-flow)). `--workflow review` labels the run and its report.
