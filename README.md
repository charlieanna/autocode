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

When a run stops for input, continue it from the project or its task worktree:

```sh
autocode --status    # what the run needs
autocode resume      # continue it; plain `autocode` does the same
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

## Design an AWS solution

AutoCode can turn a cloud requirement into a reviewed design document. For example:

```sh
autocode "Design AWS DLQ and DynamoDB monitoring with Slack alerts. Design only: no deployment, AWS/Slack API calls or credential access." \
  --workspace /path/to/project --workflow design --joint-planning --no-chat
```

`--workflow design` starts with design review. A request for a new design enters
requirements gathering and planning; an existing design can be reviewed without
building. For a new design, the flow is:

```text
Requirements -> Planner -> Plan Reviewer -> revision/final review -> your approval
-> Builder writes the agreed document -> Tester -> Completion Reviewer
-> Resolver and document rework if needed
```

Provide the alert conditions, timing, destination and organizational constraints;
the Planner should recommend technical mechanisms and distinguish facts from
assumptions. Missing consequential information stops the run for clarification.
Approval authorizes only the deliverables and permissions in that exact plan.
The design workflow is not an AWS sandbox, and live model calls consume provider
quota or incur charges; actual environment discovery and deployment need separately
authorized access.

In the DLQ/DynamoDB/Slack trial, AutoCode produced a design comparing scheduled
DynamoDB checks with Streams-based tracking, recommended non-consuming CloudWatch
DLQ monitoring and a Slack webhook, and documented unresolved environment inputs.
Independent review rejected defects and requested rework. The run ultimately
stopped at `WAITING_FOR_USER`: reliable delivery plus strict no-repeat alerts could
not be established for an ambiguous webhook outcome. It did not report completion
or verify AWS/Slack behavior live.

This is an observed capability, not a claim of hands-off reliability. The trial
also exposed recovery and clarification issues; current recovery regressions are
not green. See the [trial findings and open issues](docs/bugs/2026-10-03-design-clarification-outcomes-vs-mechanisms.md)
for evidence and current status.

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
- [Named test proof for Python, Go and Node](docs/named-test-proof.md)
- [Exact output, raw fallback and usage measurement](docs/exact-output.md)
- [Dashboard](docs/dashboard.md) and [macOS app](docs/macos-app.md)
- [Testing](docs/testing.md) and [scenario harness](scenarios/README.md)
- [Check a coding tool/model's conformance](docs/provider-conformance.md)
- [Task-run interface for integrations](docs/task-run.md)


### Role names

The terminal and dashboard use the same job names: Requirements, Planner,
Plan Reviewer, Builder, Tester, Completion Reviewer and Resolver.
A configured model can perform different jobs; the current step names the job
being done. See [roles and reviewer-routing modes](docs/models.md#roles).
