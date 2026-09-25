# Providers and adapters

[← Back to README](../README.md)

OpenCode is the default engine. Codex is available with `--engine codex`. Any other
tool registers with one TOML file.

## OpenCode adapter

The Requirements Gatherer, Planner and default Builder use the connections already configured in OpenCode. `--engine opencode`
is accepted but is optional for new runs:

```sh
python3 tools/autocode.py "Your rough idea" --workspace /path/to/project
```

Override the three planning roles independently with `--requirements-model`,
`--glm-model`, and `--plan-reviewer-model` (plus their reasoning-effort flags).
Override execution roles with `--astra-model`, `--terra-model`,
`--sol-model`, or `--completion-model` using a provider/model ID from `opencode models`, including
`openai/…` with an OpenCode ChatGPT OAuth connection. Saved runs retain their original role
engines and sessions; no existing run is migrated by a dashboard selection.
OpenCode reasoning variants can be selected in the browser or with the existing
role-specific reasoning-effort flags. New joint runs start with separate GLM requirements
and planner sessions, Sol High for the Plan Reviewer, Terra Medium for the Builder, Sol High for
the Validator, and a separate Sol Medium session for the Completion Owner. The four
execution roles then follow the automatic ladders documented in [Models](models.md). Provider
credentials remain with OpenCode: Autocode does
not read its auth file or change your global configuration.

The same approval, task, independent-evidence and completion gates apply. The adapter
uses OpenCode's [non-interactive JSON event interface](https://opencode.ai/docs/cli/#run),
validates the final report against the stage schema, and verifies command evidence
against actual completed bash events. Raw events, session IDs and stage-local
permission overrides are saved alongside the checkpoint. Token limits include cache
reads/writes and reasoning tokens. Malformed, truncated or uncertain results pause;
the runner does not automatically replay the provider request.

OpenCode has a different isolation boundary: Requirements Planner sessions and other
read-only OpenCode roles have edit tools denied and their workspace snapshots
checked, but OpenCode tool permissions are **not an OS sandbox**. Shell commands
and configured external tools retain OpenCode's native permission policy. Autocode
does not enable `--auto` or override user-level permission rules with blanket allows.
A denied required operation is reported back as a blocker.
See OpenCode's [permission documentation](https://opencode.ai/docs/permissions/).

Resuming preserves the saved engine, models and separate role sessions. Start a new
run when switching between Codex and OpenCode; their session IDs cannot be reused
across engines. A response with an unexpected session ID pauses the run.
OpenCode version or configuration drift pauses the saved run, including changes to
custom config-directory files, agent definitions and local plugin/tool definitions.
This adapter was live-checked with OpenCode **1.18.31**; OpenCode 2.x is not supported.

## Codex provider overrides

Pass `--engine codex` to start a Codex-engine run. Roles can use different
**Responses-compatible Codex providers** in one run, e.g. planning on the ChatGPT
subscription while the Validator audits and the Builder codes via another compatible provider:

```sh
python3 tools/autocode.py "Build a greeting CLI" --workspace /path/to/project \
  --engine codex \
  --astra-model gpt-6-astra \
  --sol-model audit-model --sol-provider other_provider \
  --terra-model implementation-model --terra-provider other_provider --reasoning-effort high
```

Each role can also have its own reasoning effort. For example, use the Astra route at
extra-high (`xhigh`) for read-only discovery and planning:

```sh
python3 tools/autocode.py "Build a greeting CLI" --workspace /path/to/project \
  --engine codex --astra-model gpt-6-astra --astra-reasoning-effort xhigh
```

Role-specific effort overrides the shared `--reasoning-effort` value. All selected
models and providers are saved in the run checkpoint, so resumed runs retain this
assignment.

An interactive terminal starts in chat mode by default. The Requirements Gatherer presents a few material
questions at a time; type replies directly or `/default` to accept a proposed default.
When the build brief is displayed, type feedback to revise it, `y` to approve that exact
revision, or `/pause` to save and exit. After approval the implementation, validation
and review loop runs automatically within the configured limits. During questions,
`/feedback TEXT` sends a broader correction to the Requirements Gatherer.

Use `--chat` to select this mode explicitly, or `--no-chat` for one command per turn.
Non-interactive invocations default to the command-per-turn interface.

`--<role>-provider` is saved per role like models and is passed to Codex as a
`model_provider` override; roles without a provider keep the local Codex login
(ChatGPT auth). The named provider must implement the OpenAI **Responses** API.
For the connected Z.ai Coding Plan, use the OpenCode engine above. Changing the global
provider/auth in local Codex config still pauses a saved Codex-engine run.

## Add a tool

OpenCode is built in. Any other tool registers with one TOML file, not a Python
package. `--provider <name>` loads `~/.config/autocode/providers/<name>.toml` and,
if that file is absent, the bundled example at `tools/providers/configs/<name>.toml`.
Configs inside a project are not loaded.

```toml
name = "gocode"
command = ["sh", "-c", "eval \"$(gocode env --shell bash)\" && exec codex exec -C \"$1\" --sandbox \"$2\" --model \"$3\" -c model_reasoning_effort=\"$4\" --output-schema \"$5\" -o \"$6\" -", "gocode", "{workspace}", "{sandbox}", "{model}", "{effort}", "{schema}", "{report}"]
prompt = "stdin"                      # or "file" (uses {prompt_file})
models_command = ["gocode", "models"] # optional; or a static list: models = [...]
version_command = ["gocode", "--version"]

[roles]
astra = { model = "gpt-5.6-sol", effort = "high" }
terra = { model = "gpt-5.6-terra", effort = "medium" }
sol = { model = "gpt-5.6-sol", effort = "high" }
completion = { model = "gpt-5.6-sol", effort = "medium" }
glm = { model = "gpt-5.6-sol", effort = "medium" }
plan_reviewer = { model = "gpt-5.6-sol", effort = "high" }
```

Placeholders are `{model}`, `{effort}`, `{workspace}`, `{report}`, `{schema}`,
`{prompt_file}`, `{run_dir}`, `{role}`, and `{sandbox}`. `{sandbox}` is
`read-only` for planning and review and `workspace-write` for the builder.
`{effort}` reaches the tool only if the command uses it.
Use `{{` and `}}` for literal braces in a command argument, such as
`${{VAR}}` or `{{"key":1}}`; single braces are reserved for placeholders.
Changing the config file or the tool version pauses a saved run.

### Output modes

`output` says how the result comes back:

- `output = "report_file"` (the default): the tool writes exactly one JSON object
  to `{report}`, as `codex exec -o` does. Every stage starts fresh. Command
  evidence is a `capture_command` receipt file, not an `event:` id. These tools
  report no token usage, so `--max-reported-tokens` pauses with
  `PAUSED_USAGE_UNKNOWN`.
- `output = "opencode_events"`: the tool prints OpenCode-format JSON events, as
  `opencode run --format json` and `kilo run --format json` do. Autocode reads
  the final report, token usage and command exit codes from those events, and
  resumes each role's session with the required `resume` template, for example
  `resume = ["--session", "{session}"]`.

The runner can fill an omitted check exit code from a unique executed event or
the cited, verified capture receipt before checking the unchanged report schema.
It never replaces a supplied exit code, guesses from output text, or resolves
ambiguous executions. The original report is retained as `.reported.json`, and
the stage records the derived metadata. Nonzero exits remain failures.
For `report_file` tools, capture commands must inherit `AUTOCODE_CAPTURE_CONTEXT`
from the tool process: the capture helper records it automatically and the
validator checks it against the saved attempt. Receipts from another attempt,
missing capture context, or changed output hashes are rejected. Event providers
continue to require their independent tool-event attestation.

### Auth checks

A tool can declare how its subscription login is checked. Without an `[auth]`
table Autocode does not inspect the tool's login. With one, before an OpenAI
role starts, Autocode runs `command`, strips terminal color codes, and requires
every match of `pattern` to equal `expect`. A mismatch, a failed listing, or a
variable in `forbid_env` pauses the run with `PAUSED_BILLING_ROUTE` before any
model is called:

```toml
[auth]
command = ["kilo", "auth", "list"]
forbid_env = ["OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"]

[[auth.routes]]
models = "openai/"
pattern = "^\\s*[●•]\\s+OpenAI\\s+(\\S+)\\s*$"
expect = "oauth"
```

A tool that reports a subscription usage limit or runs out of pay-as-you-go
credit pauses the run with `PAUSED_BUDGET`.

### KiloCode

`tools/providers/configs/kilocode.toml` is bundled. It runs `kilo run` with the
same subscription routes as the OpenCode defaults: `openai/...` models use
Kilo's ChatGPT OAuth connection and `zai-coding-plan/...` uses its Z.AI Coding
Plan connection. Connect both with `kilo auth` first. The one difference from
OpenCode is the plan reviewer, which is GPT-5.6 Sol here because Kilo has no
Cursor ACP route:

```toml
name = "kilocode"
command = ["kilo", "run", "--dir", "{workspace}", "--model", "{model}", "--variant", "{effort}", "--format", "json"]
prompt = "stdin"
output = "opencode_events"
resume = ["--session", "{session}"]
models_command = ["kilo", "models"]
version_command = ["kilo", "--version"]

[roles]
astra = { model = "openai/gpt-5.6-sol", effort = "high" }
terra = { model = "openai/gpt-5.6-terra", effort = "medium" }
sol = { model = "openai/gpt-5.6-sol", effort = "high" }
completion = { model = "openai/gpt-5.6-sol", effort = "medium" }
glm = { model = "zai-coding-plan/glm-5.3", effort = "medium" }
plan_reviewer = { model = "openai/gpt-5.6-sol", effort = "high" }

[auth]
command = ["kilo", "auth", "list"]
forbid_env = ["OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"]

[[auth.routes]]
models = "openai/"
pattern = "^\\s*[●•]\\s+OpenAI\\s+(\\S+)\\s*$"
expect = "oauth"
```

Copy it to `~/.config/autocode/providers/kilocode.toml` to change models or
reasoning levels; any ID from `kilo models` works. `kilo/...` IDs bill the Kilo
Gateway pay-as-you-go account instead of a subscription. The `[auth]` table
above runs `kilo auth list` before an `openai/` role and pauses with
`PAUSED_BILLING_ROUTE` unless that login is `oauth`, and also when
`OPENAI_API_KEY`, `CODEX_API_KEY`, or `OPENAI_BASE_URL` is set. Kilo has no
sandbox flag, so a read-only stage that edits files is caught afterwards by the
workspace snapshot check and pauses.

### Default provider

New runs use OpenCode unless you choose otherwise. `--provider <name>` picks the
tool for one run. To change the default for every new run and for the dashboard,
set `AUTOCODE_PROVIDER=kilocode` or add this to `~/.config/autocode/config.toml`:

```toml
default_provider = "kilocode"
```

A saved run keeps the provider it started with, and `--engine codex` runs still
use Codex. `autocode-dashboard --provider <name>` overrides the default for the
dashboard; its model pickers list that tool's models.

See also: [Models](models.md) · [Install](install.md) · [CLI](cli.md)
