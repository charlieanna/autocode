# Figma design and implementation

[← Back to README](../README.md)

The Figma workflow uses Codex with the connected Figma plugin. Requirements planning,
plan review, plan finalization, validation, and completion decisions use the review model
tier (Sol); the review and decision steps default to Sol High. Figma editing uses the
Builder model tier (Terra). OpenCode is not needed for this path. The plugin must
be available in Codex CLI sessions; a connection only in another app is insufficient.

```sh
# Design only (both commands are equivalent):
autocode-ui "Design a responsive team operations dashboard"
autocode ui "Design a responsive team operations dashboard"

# Design, review, then hand the accepted Figma result to the implementation runner:
autocode ui "Build a responsive team operations dashboard" --build

# Refine a selected file:
autocode-ui "Refine the project detail layout" --figma-file "https://www.figma.com/design/FILEKEY/Project"

# Implement an existing file, or import a completed design run:
autocode "Build the supplied design" --figma-file "https://www.figma.com/design/FILEKEY/Project"
autocode --ui-run /path/to/project/.autocode-ui/runs/RUN
```

The UI path first runs a complete planning exchange: a Requirements Planner drafts
the brief, an independent Plan Reviewer challenges it, the planner revises it, and
a Plan Finalizer accepts it or requests rework. It then runs Figma Builder →
Design Validator → Completion Owner, with build/review rework. Models remain
configurable separately from these role names. Every attempt has separate prompts,
event logs and structured reports under `.autocode-ui/runs/`. Failed planning or
design reviews cannot produce a build handoff.
By default, planning allows one rework and design allows two. Use
`--max-plan-reworks none --max-reworks none` to continue review loops until accepted
or blocked. Numeric limits remain available; `0` allows no rework. The selected
limits are recorded in the run state.
An accepted handoff records the Figma URL and hashes of the brief and review reports;
modified or incomplete artifacts are rejected when imported. The implementation
roles inspect the live Figma reference again, since the file can change after design.

`--build` starts the normal implementation brief and orchestration in Codex. Its
initial product brief still needs approval. Subsequent visual checks use Figma
comparisons and independent validation by default; they do not require another
human visual approval. Add `--figma-review human` to a new implementation run if
that review is wanted. Explicitly supplied review requirements still take precedence.
The workflow uses the connected Figma editing tools, not an assumed Figma Make API.

The Figma and code paths use the same durable Autocode stage driver. `autocode-ui`
is an installed alias for `autocode ui`, not a separate orchestration product. Each
target supplies its own prompts, report schemas and acceptance rules while sharing
stage selection, checkpoint persistence, retry/skip behavior and terminal-state
handling. With `--build`, the accepted Figma target hands off to the code target.

`autocode ui "Preview the workflow" --dry-run` writes prompts without model calls or
Figma edits and never emits an accepted handoff. If a design stage is interrupted,
inspect its saved reports and Figma file before starting a fresh run with
`--figma-file`; existing run directories are never overwritten.

For an assigned implementation or review task, Figma instructions cover the affected
visual work. Test/parser/harness-only repairs can reuse applicable design evidence;
they do not require a fresh canvas inspection unless they affect presentation or verify
a visual criterion. Final visual acceptance requirements remain in force.

See also: [Models and escalation](models.md) · [Execution and completion](execution.md)
