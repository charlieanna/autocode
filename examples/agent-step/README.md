# AutoCode as a step inside your coding agent

| File | What it is |
| --- | --- |
| [`skills/autocode/SKILL.md`](skills/autocode/SKILL.md) | A Claude Code skill. Copy the `autocode` directory to `.claude/skills/` in your project (or `~/.claude/skills/`). It drives runs with `python3 -m autocode_cli.autocode_agent_step`. |
| [`mcp.json`](mcp.json) | A project `.mcp.json` for the MCP server, `python3 -m autocode_cli.autocode_mcp`. Put your own engine and model flags after `--`. |

Both start a run in the repository, report each stop with its pause category and
the command you would run, pass on only your own plan approval or answer, and
report the evidence. Neither needs anything beyond AutoCode itself. See
[AutoCode inside your coding agent](../../docs/agent-step.md).
