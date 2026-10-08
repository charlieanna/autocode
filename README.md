# AutoCode

AutoCode runs your coding agent through planning, approval, implementation and
independent validation. It saves progress and checks evidence before reporting
completion. Start with a request in a committed Git project; approve the plan
before implementation begins.

## Install and start

Requires Python 3.11+, Git, and macOS or Linux (Windows through WSL). Install
from this checkout into a virtual environment:

```sh
python3 -m venv .venv
.venv/bin/pip install --editable .
source .venv/bin/activate
autocode --version
autocode doctor --workspace /path/to/project
autocode "Build a greeting CLI" --workspace /path/to/project
```

OpenCode 1.x is the default engine. To use a logged-in Codex CLI instead, add
`--engine codex`. See [installation](docs/install.md),
[provider setup](docs/providers.md) and [model selection](docs/models.md).
If your account serves only one model, start with `--single-model MODEL` to run
every role on it (per-role reasoning flags still apply).

## Reliability

AutoCode does not report completion without evidence. Live sweeps on the
three-case portfolio (small new application, feature in an existing project,
bug fix) are recorded in [docs/reliability-table.md](docs/reliability-table.md)
and [RELIABILITY.md](RELIABILITY.md). Fake-provider rows are kept separate
and never mixed with these. The table is generated, never edited by hand:

```sh
python tools/reliability_table.py docs/reliability-sweeps.json
```

| Date | Profile | Runs | Passed | False completions |
| --- | --- | ---: | ---: | ---: |
| 2026-10-08 | glm53-mimo | 3 | 2 | 0 |
| 2026-10-08 | glm53-mimo | 1 | 1 | 0 |
| 2026-10-07 | glm53-openai | 3 | 2 | 0 |
| 2026-10-04 | claude-tiers | 9 | 8 | 0 |
| 2026-10-01 | claude-tiers | 2 | 1 | 1 |
| 2026-10-01 | glm53-openai | 3 | 1 | 0 |

When a run stops for input, continue it from the project or its task worktree:

```sh
autocode --status    # what the run needs
autocode resume      # continue it, acknowledging a pause (no --resume-paused needed)
```

AutoCode acts on the only unfinished run there. With several, it lists them and
changes nothing; name one with `--run-dir`, which works from any directory:

```sh
autocode --run-dir /path/to/run --status
autocode --run-dir /path/to/run --chat
```

The [workflow guide](docs/workflow.md) explains questions, plan approval and
review. The [CLI reference](docs/cli.md) covers answers, approvals and recovery.

For browser, Figma or strict test prerequisites, add
`--task-preflight qualification/preflight.json`. The [preflight guide](docs/task-preflight.md)
covers worker permissions, design inputs, named collection and proof setup.
Failed prerequisites pause before model dispatch; readiness never replaces verification.

## Build from a Figma design

This path requires native Codex, ChatGPT login and the connected Figma plugin
available to Codex CLI sessions.

```sh
# Design first, or implement an existing file:
autocode ui "Design a responsive team dashboard"
autocode "Build the supplied design" --workspace /path/to/project --engine codex \
  --figma-file "https://www.figma.com/design/FILEKEY/Project"

# Implement a completed, accepted AutoCode UI run:
autocode --workspace /path/to/project --engine codex \
  --ui-run /path/to/project/.autocode-ui/runs/ACCEPTED-RUN
```

See [Figma design and implementation](docs/figma.md) for design review,
accepted handoffs and visual verification.
Implementation reviews use [current browser capture bundles](docs/visual-captures.md):
`autocode visual-capture --config capture.json` records the rendered implementation
and its inputs before an independent reviewer judges the images.

## Give one component its own design

For an architecture with `components.json`, `dependency_trace.json` and
`contracts/*.schema.json`, add one of these fields to the relevant component:

| Component field | Input |
| --- | --- |
| `"ui_run": "../.autocode-ui/runs/ACCEPTED-RUN"` | A completed, accepted UI run; relative paths resolve from the architecture directory. |
| `"figma_file": "https://www.figma.com/design/FILEKEY/Project"` | An existing Figma reference; the URL alone does not establish an accepted UI run. |

Choose one field per component. Only that component receives the design;
components without either field use their text brief.

```sh
autocode components architecture --workspace /path/to/project \
  --options '--engine codex'
```

Design-bearing builds require native Codex. Plan approval and independent
implementation review still apply. Repeating the command resumes saved work;
a changed accepted handoff is rejected instead of silently changing the design
for that build. See the [complete component example](docs/task-lanes.md#a-component-with-a-figma-design)
for the architecture format, approvals and integration.

## More guides

- [Parallel task lanes and components](docs/task-lanes.md)
- [Large projects and workstreams](docs/program.md)
- [Execution and completion checks](docs/execution.md)
- [Named test proof for Python, Go, Node and Vitest](docs/named-test-proof.md)
- [Exact output, raw fallback and usage measurement](docs/exact-output.md)
- [Dashboard](docs/dashboard.md) and [macOS app](docs/macos-app.md)
- [Testing](docs/testing.md) and [scenario harness](scenarios/README.md)
- [Check a coding tool/model's conformance](docs/provider-conformance.md)
- [Task-run interface for integrations](docs/task-run.md)
- [Experimental OSS Arena and controlled improvement gates](docs/arena.md)


### Role names

The terminal and dashboard use the same job names: Requirements, Planner,
Plan Reviewer, Builder, Tester, Completion Reviewer and Resolver.
A configured model can perform different jobs; the current step names the job
being done. See [roles and reviewer-routing modes](docs/models.md#roles).
