# AutoCode

AutoCode runs your coding agent through planning, approval, implementation and
independent validation. It saves progress and checks evidence before reporting
completion. Start with a request in a committed Git project; approve the plan
before implementation begins.

## Documentation

[Read the documentation site](https://charlieanna.github.io/autocode/latest/).
New here? The [quickstart](docs/quickstart.md) goes from install to a first
reviewed change in about ten minutes. Then read these five guides: [install AutoCode](docs/install.md),
[run your first task](docs/workflow.md),
[understand a pause](docs/cli.md#understanding-a-stop),
[read completion evidence](docs/execution.md#completion-gate), and
[review the plan in a pull request](docs/issues.md#reviewing-the-plan-in-a-pull-request).
The site has search, deeper guides and a version selector; `latest` follows master.

## Install and start

Requires Python 3.11+, Git and [pipx](https://pipx.pypa.io/). Linux provides
kernel-enforced provider process ownership. macOS uses portable supervision
with reduced cleanup guarantees; Windows uses WSL. See the
[host and installation guide](docs/install.md) before the first task.

Install once, then use AutoCode from any project:

```sh
pipx install autocode-supervisor
pipx ensurepath
# Open a new terminal after ensurepath.
autocode --version
autocode demo
```

Or install the published release with Homebrew:

```sh
brew install charlieanna/tap/autocode
```

The demo uses no account or model spend. It shows rejected evidence, a
completion refusal and a verified result. PyPI and Homebrew currently publish
**0.8.2**; these install a tagged release, while this documentation follows master.
For fixes merged after that tag, use the [master or development-checkout
installation](docs/install.md#advanced-install-from-a-checkout). See the
[release verification record](docs/bugs/2026-10-27-release-installation.md) for
what was checked and the limits of that evidence.

Run `autocode setup` once: it checks Claude Code, Codex, OpenCode and Kilo,
installs OpenCode if you have no hub yet, offers each one's own sign-in, and says
which one your tasks will use. Then
choose an engine you have connected, or pass none. For example, with a logged-in Codex CLI:

```sh
cd /path/to/your/committed-git-project
autocode doctor --engine codex
autocode "Fix the incorrect result when submitting an order" --engine codex
```

Answer the questions, read the plan and its exclusions, then approve it. AutoCode
builds and checks the change, and stops when it needs your decision. Reopen the
same project with `autocode resume`; use `autocode --status` to see what it needs.

For Claude Code use `--engine claude`. Without an explicit choice, the existing
configured/default route applies: OpenCode when it is installed; otherwise the
first connected of Claude Code, Codex and Kilo, which the run names as
`Engine for this run: …`. API keys go in OpenCode (`opencode auth login`) or Kilo
(`kilo auth login`); AutoCode's own direct API-key connection is retired. The [installation guide](docs/install.md) covers
sign-in, a new Git project, route checks and common setup errors.

## Stage models and dynamic reasoning effort

Choose each stage's model and reasoning ceiling once per backend (`claude`,
`codex`, `opencode`, `kilocode`); AutoCode keeps the choices in
`~/.config/autocode/stages.toml` and checks cross-model verifier independence so
producers never grade their own work (`autocode stages choose` or
`autocode stages list`).

Inside those saved ceilings, AutoCode dynamically scales reasoning effort per
stage (`low → medium → high → xhigh → max`) across only the variants each model
offers:

- **Builder 3-rung per-task cascade:** on a bounded leaf task (`1..4`
  non-sensitive files, including parallel milestone workers), Attempt 1 steps
  one offered level below your saved ceiling (`high → low` on `glm-5.3`,
  `high → medium` on `codex` and `claude`); Attempt 2 after Tester feedback
  restores your saved ceiling; Attempt 3+ or hard tasks (`6+` files or
  `sensitive-paths`) climb one level above (`max` or `xhigh`).
- **Review, planning and recovery sizing:** Tester and Completion Reviewer step
  down one level on small, clear tasks; Planner and Plan Reviewer step up one
  level on hard plans; JSON report-format repairs run at `low`; and struggling
  stages climb their own model's offered levels before switching routes or
  pausing. Explicit CLI `--<role>-reasoning-effort` flags always stay exact.

Recommended stage profiles (`autocode stages set`):

```sh
# 1. Cross-vendor subscription hybrid (Kilo / OpenCode — $0 marginal spend):
#    GLM-5.3 produces fast; OpenAI GPT-6-sol / GPT-6-astra verify independently.
autocode stages set --backend kilocode \
  --stage requirements zai-coding-plan/glm-5.3 low \
  --stage planner zai-coding-plan/glm-5.3 high \
  --stage plan_reviewer openai/gpt-6-sol high \
  --stage builder zai-coding-plan/glm-5.3 high \
  --stage tester openai/gpt-6-sol high \
  --stage completion openai/gpt-6-sol high \
  --stage task_planner openai/gpt-6-astra high

# 2. Pure native Codex (--engine codex — $0 ChatGPT subscription):
autocode stages set --backend codex \
  --stage requirements gpt-5.6-sol low \
  --stage planner gpt-5.6-sol medium \
  --stage plan_reviewer gpt-6-astra high \
  --stage builder gpt-5.6-terra high \
  --stage tester gpt-5.6-sol high \
  --stage completion gpt-6-astra high \
  --stage task_planner gpt-6-astra high

# 3. Role-tiered Claude Code (--engine claude — fastest wall-clock, reserve for high-stakes tasks):
#    Keep Sonnet/Opus on planning and review; use Haiku only on Builder.
autocode stages set --backend claude \
  --stage requirements claude-sonnet-5-5 low \
  --stage planner claude-sonnet-5-5 medium \
  --stage plan_reviewer claude-opus-5-5 medium \
  --stage builder claude-haiku-4-5-20251001 high \
  --stage tester claude-sonnet-5-5 high \
  --stage completion claude-opus-5-5 high \
  --stage task_planner claude-opus-5-5 high
```

See [choosing each stage's model](docs/models.md#choosing-each-stages-model) and
[default models and escalation](docs/models.md#default-models) for details.

## A bug that turns out to span repositories

On a supported Linux host, open terminal chat. This flow needs current master,
not the published **0.8.2** release, so use the [master or development-checkout
installation](docs/install.md#advanced-install-from-a-checkout) first:

```sh
cd /path/to/your/project
autocode chat
```

Use `/connect` to check your coding backend, and `/repos /path/to/repository`
once for each local checkout AutoCode may use, then describe the bug. Grounded discoveries select unique
catalog matches automatically and can lead to further repositories. AutoCode
prepares isolated worktrees, shows the coordinated plans for approval, fixes the
repositories, generates a joint regression and publishes linked PRs after
independent verification. Missing access or ambiguous matches produce a question.

The joint regression must fail on the combined original code and pass on the
exact candidate trees. Separate green repository tests cannot finish this flow.
PRs are never merged automatically. See [TUI setup](docs/multi-repo.md#automatic-bug-fixes-from-terminal-chat)
and [verification limits](docs/multi-repo.md#verification-and-current-limits).

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
| 2026-11-24 | claude-tiers | 8 | 8 | 0 |
| 2026-11-23 | claude-tiers | 1 | 1 | 0 |
| 2026-11-23 | claude-tiers | 4 | 3 | 0 |
| 2026-10-08 | glm53-mimo | 3 | 2 | 0 |
| 2026-10-08 | glm53-mimo | 1 | 1 | 0 |
| 2026-10-07 | glm53-openai | 3 | 2 | 0 |
| 2026-10-04 | claude-tiers | 9 | 8 | 0 |
| 2026-10-01 | glm53-openai | 3 | 1 | 1 |
| 2026-10-01 | glm53-openai | 3 | 1 | 0 |
| 2026-10-10 | native-glm53-latency | 1 | 0 | 0 |

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
- [Channel bridge: requirements from GitHub issues, questions and approvals in comments](docs/channels.md)
- [Execution and completion checks](docs/execution.md)
- [Named test proof for Python, Go, Node and Vitest](docs/named-test-proof.md)
- [Exact output, raw fallback and usage measurement](docs/exact-output.md)
- [Testing](docs/testing.md) and [scenario harness](scenarios/README.md)
- [Check a coding tool/model's conformance](docs/provider-conformance.md)
- [Task-run interface for integrations](docs/task-run.md)
- [VS Code extension](docs/vscode.md)
- [AutoCode inside Claude Code and other agents: skill and MCP server](docs/agent-step.md)
- [Experimental OSS Arena and controlled improvement gates](docs/arena.md)


### Role names

The terminal and VS Code use the same job names: Requirements, Planner,
Plan Reviewer, Builder, Tester, Completion Reviewer and Resolver.
A configured model can perform different jobs; the current step names the job
being done. See [roles and reviewer-routing modes](docs/models.md#roles).
