# AutoCode as a step inside your coding agent

This directory is the AutoCode plugin for Claude Code. With the `autocode` CLI installed
(`pipx install autocode-supervisor`, then `autocode setup`), add it in Claude Code:

```text
/plugin install autocode --marketplace charlieanna/autocode
```

| File | What it is |
| --- | --- |
| [`skills/autocode/SKILL.md`](skills/autocode/SKILL.md) | The plugin's skill. Without the plugin, copy the `autocode` directory to `.claude/skills/` in your project (or `~/.claude/skills/`). It drives runs with `autocode agent-step`. |
| [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json) | The plugin manifest; the repository's `.claude-plugin/marketplace.json` lists it. |
| [`mcp.json`](mcp.json) | A project `.mcp.json` for the MCP server, `autocode mcp`. Put your own engine and model flags after `--`. For Codex: `codex mcp add autocode -- autocode mcp`. |

Both start a run in the repository, report each stop with its pause category and
the command you would run, pass on only your own plan approval, plan changes or
answer, and report the evidence. Neither needs anything beyond AutoCode itself. See
[AutoCode inside your coding agent](../../docs/agent-step.md).
