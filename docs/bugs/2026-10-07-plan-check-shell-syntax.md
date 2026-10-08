# A planned check that the replay's shell cannot parse

Found by a live run of `discuss-then-design-then-build` (`djtwgjcl`) on 2026-10-07.

A Completion Reviewer's (or Resolver's) `next_task` had this `validation_plan` step:

````
python3 -c "import re;t=open('docs/design/metadata-cache.md').read();l=t.lower();assert '```python' not in t and ..."
````

The replay runs a planned command as `/bin/sh -c COMMAND` (`autocode_command_supervision.run`, through
`autocode_verify.run_command`). Inside double quotes a backtick starts a command substitution, so the shell
stopped every time with `/bin/sh: 1: Syntax error: EOF in backquote substitution`. Every Validator report
that cited the step was refused, with "A Validator report cannot change it: the plan's author must reword
that step", and the run paused at `PAUSED_INVALID_OUTPUT`. The command could not have run anywhere.

It was not the only one. Across the plans recorded by about 70 live runs that day (2,356 distinct plan
rows, 369 commands that `commands()` extracts), 5 commands could not be parsed. All of them were backticks
inside double quotes, in `python3 -c "..."` or inside an `sh -c '... "```" ...'` script. No command that
`/bin/sh` can parse was refused.

## Fix

`autocode_verification_plan.refuse_new_plan` now refuses both problems in one repair: a row that names
`git status` ([bug note](2026-10-07-plan-names-git-status.md)) and a command that `/bin/sh` cannot parse.
It is called at the same points: `autocode_goal_lifecycle.install_draft`, `autopilot._apply_result` for a
Completion Reviewer's or Resolver's CONTINUE or REWORK `next_task`, and `autocode_progressive_state` for a
new progressive proposal or slice revision. The plan's author gets its report back for repair. The
refusal names every row it refuses, with the shell's own error line.

- Only the commands the replay would run are parsed: what `commands()` extracts from a criterion's
  `verification_method`, a task's `validation_plan` step or a progressive check's method. Prose is never
  parsed. A task's `requirements` are read by the Validator and not replayed, so they are checked only for
  `git status`.
- The command is read by `/bin/sh -n -c COMMAND`, the replay's shell and invocation with `-n`, which runs
  nothing. It runs with a 5-second limit and a minimal environment. When the planned command is
  `sh -c SCRIPT`, which is also how the runner wraps a command that must exit non-zero, the script is
  parsed too, because that inner `sh` reads it when the check runs.
- If the shell cannot be started or does not answer in time, nothing is refused. The replay still
  reports a command it cannot run.
- `SHELL_SYNTAX_RULE` is told to the Planner and Plan Reviewer, the Completion Reviewer (and the final
  audit under reviewer routing) and the Resolver, next to `GIT_STATUS_RULE`: never put a backtick inside
  double quotes, and put code that needs a backtick or both kinds of quotes in a file in the repository.

## Limits

As with `git status`, a contract approved before this rule, an assigned task and a saved progressive plan are
not checked again. `djtwgjcl` itself still holds the step. Only shell syntax is checked: a command that
parses but fails when it runs is not refused, for example Python code inside `-c` that does not compile or a
file that does not exist. A `bash -c` script is not parsed. The Validator's own checks are not parsed here;
the replay already refuses a failing check with a report repair.
