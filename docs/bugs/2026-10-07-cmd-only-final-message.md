# A shell command returned as the final message was retried as an ordinary bad report (#512)

**Seen:** live planning and build runs (2026-10-05) with the Plan Reviewer and the Tester on one model
served through Codex and the `gocode` provider. Those stages often ended with the arguments of a shell
tool call as their final message, `{"cmd": "ls docs/... && wc -l ..."}`, instead of the stage's JSON
report. The runner rejected it as "$: missing summary", like any report with a missing field. The repair
it queued was a fresh request told to repair that "draft", which had nothing to repair, so the repairs
did the same, and the run paused at `PAUSED_REPEATED_FAILURE` with no hint of the cause. A
provider-side wrapper that unwrapped a JSON report embedded in the command helped only when there was
one; usually the command was all there was.

**Reproduced** offline through the CLI: `SCENARIO_FAKE_CMD_ONLY=<stage>[:<count>]` makes the scripted
model (`scenarios/harness/fake_codex.py`) end that stage this way. Before the fix a once-only command
was "repaired" in a fresh request, and a repeated one paused after three requests with "$: missing
summary".

**Fix** (`autocode_cmd_only_report`, called first in `load_stage_report`):
- A final message whose keys are only `cmd` or `command` (holding a command) and the other arguments
  of a Codex shell call, none of them a field of the stage's schema, is rejected as a known provider
  defect with its own reason and failure class (`CommandOnlyReport`). It is never accepted as a report
  and never run. A command whose text embeds a JSON report is still a command: the runner does not
  unwrap it.
- On a transport that keeps sessions (Codex, OpenCode events), the one same-session correction of
  `autocode_format_correction` resumes the stage's own session and asks for the report alone, with no
  command. It now also applies to planning stages for this defect, and only when the route it launches
  on (engine, provider, model, effort) is the one that started the session: a Plan Reviewer attempt
  that ran on its one-use fallback route gets the full repair instead, never its session resumed on
  another model. It spends no report-repair attempt.
- Otherwise, or when the correction fails, the existing report-only repairs run, told that the rejected
  report is a command with nothing to repair. A stage that keeps doing it stops at the existing bound
  with the defect named in `stop_reason`.

`tests/test_cmd_only_report_cli.py` drives all three routes; `tests/test_cmd_only_report.py` has the rules.

**Not fixed here:** a model that keeps doing it still costs the original request, the correction (on a
transport with sessions), the two repairs and one Investigator call before the pause. Run that role on
another model ([Providers](../providers.md#a-final-message-that-is-only-a-shell-command)). No live rerun
on that route was made for this change.
