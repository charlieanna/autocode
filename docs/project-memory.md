# Project memory

A completed task can remember project preferences for the next task in the same
repository. The learned record is `.autocode/project.json`, separate from the
settings a person deliberately chooses in `.autocode/config.toml` or
`[tool.autocode]`.

AutoCode records the observed test framework and a shareable test command,
reusable conventions actually answered by a person, observed model routes, and
the names of pause states that needed a person. Unknown facts remain null.
It writes after the completed checkpoint and evidence; learning cannot approve
a task, change its verified result, or make completion fail.

## Reusing an answer

Requirements can tag its own question IDs as project conventions. Before saving
one, the runner joins the exact question to the issued clarification, the actual
human answer and its source record. It saves the question and answer verbatim,
not a model paraphrase. Proposed defaults, delegated answers, machine decisions
and unasked questions are ineligible. Permission, spending, quota, external side
effect and task-outcome decisions are not reusable authorization.

The next task gives these historical answers to Requirements as project data.
For example, an unchanged preference about test naming need not be asked again.
The new brief and current answers take precedence. Memory is never inserted into
this task's answer ledger, acceptance criteria or human-source citations, and
every required plan and completion approval still belongs to the new task.

## Defaults and configuration

For the test command, precedence is:

1. The current command-line flag.
2. `.autocode/config.toml`.
3. `[tool.autocode]` in `pyproject.toml`.
4. The learned project record.
5. Built-in detection/defaults.

The record has no per-package entry, so its framework and test command describe
the whole repository. A run started with `--package` does not take the learned
test command, and does not save its framework or test command into the record;
it still saves conventions, routes and pause states. The explicit project files
still apply to it. See [Monorepo packages](workflow.md#monorepo-packages).

Framework and route observations provide context and diagnostics. They do not
select a provider, change engines/models, set budgets or grant permission. A
known generated Python suite can use a portable `python3` launcher rather than
storing the previous machine's interpreter path. Custom commands are retained
only when safe to share.

Only a fresh task reads the learned record. A saved task keeps its launch
snapshot and settings, so editing or forgetting the file cannot change a run in
progress. Current-run facts merge under a lock at completion; prior conventions
are preserved unless a new human answer replaces the same question. The loaded
snapshot is never written back as if the person had answered again.

Run `autocode doctor --workspace PATH` to see learned conventions and setting
origins. `--json` includes the validated project-memory record. An unavailable,
invalid or unsupported record is reported and ignored; it does not become a
default or block an otherwise valid task.

## Sharing the record

The [version 1 schema](schemas/project-memory-v1.schema.json) bounds every field.
A small example is:

```json
{
  "version": 1,
  "framework": "unittest",
  "test_command": "python3 -m unittest discover -s tests -t .",
  "conventions": [
    {
      "question": "Which naming convention should future repository tests follow?",
      "answer": "Use descriptive behavior-based unittest test method names."
    }
  ],
  "routes": [],
  "human_pause_states": ["WAITING_FOR_USER"]
}
```

The file holds selected project facts, not transcripts, run output, approval
tokens or evidence. Credential-shaped values, known credential environment
values and machine-specific absolute/private paths are omitted whole; an
omitted answer is never rewritten into a misleading quote. Review this small
record before committing it like other team configuration.

AutoCode's generated `.autocode/.gitignore` allows `project.json` and
`config.toml`. Exact older generated templates are upgraded; custom ignore rules
are preserved. If your custom rules hide the record, use `git add -f
.autocode/project.json` to share it, or leave it ignored.

## Forgetting

```sh
autocode forget --workspace /path/to/project
autocode forget --workspace /path/to/project --json
```

This removes only that project's `project.json`, including when invoked from an
associated task worktree. Explicit configuration, saved runs, evidence and
approval records remain. The command is idempotent and remains operator-only through the unattended wrapper. A later completed task can
learn new project facts; forgetting does not reclassify previous approvals or
answers as new input.
