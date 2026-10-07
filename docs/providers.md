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
and planner sessions, GPT-6 Sol High for the Plan Reviewer, GLM Medium for the Builder, GPT-6
Sol High for the Validator, and a separate GPT-6 Sol Medium session for the Completion Owner.
Escalation then follows the ladders and Builder retry policy documented in
[Models](models.md). Provider
credentials remain with OpenCode: Autocode does
not read its auth file or change your global configuration.

The same approval, task, independent-evidence and completion gates apply. The adapter
uses OpenCode's [non-interactive JSON event interface](https://opencode.ai/docs/cli/#run),
validates the final report against the stage schema, and verifies command evidence
against actual completed bash events; the runner then re-runs a passing Validator's
checks itself in a clean copy ([Execution](execution.md#the-runner-re-runs-the-validators-checks)). Raw events, session IDs and stage-local
permission overrides are saved alongside the checkpoint. Token limits include cache
reads/writes and reasoning tokens. Malformed or uncertain results pause; the runner
does not automatically replay the provider request. The one exception is a Validator or
Completion Owner response cut off by the output-token limit: when the process exited
cleanly with the source unchanged, the runner archives the attempt and queues a bounded
report-only repair from the saved partial response and check evidence rather than
replaying the provider request.

OpenCode has a different isolation boundary: Requirements Planner sessions and other
read-only OpenCode roles have edit tools denied and their workspace snapshots
checked, but OpenCode tool permissions are **not an OS sandbox**. Shell commands
and configured external tools retain OpenCode's native permission policy. Autocode
also rejects a read-only report when native OpenCode step snapshots record a
workspace change during the attempt, even if the final files were restored. This
check applies when loading saved reports and when reusing review evidence for
report repair. Before a native OpenCode launch, the runner adds local Git
exclusions for `.autocode/`, `.autocode-ui/`, `__pycache__/` and `*.pyc`, matching
its existing source-snapshot boundary and preserving other exclusion rules.
This prevents runtime evidence writes from looking like source drift. The check
cannot detect writes outside the workspace, ignored files, or
transient changes between snapshots, and is unavailable when the transport omits
snapshots. Use an OS sandbox when filesystem prevention is required. Autocode
does not enable `--auto` or override user-level permission rules with blanket allows.
A denied required operation is reported back as a blocker.
See OpenCode's [permission documentation](https://opencode.ai/docs/permissions/).

### Output cap

OpenCode stops a model's response at the smaller of the model's listed output limit
and `OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX`, and reasoning counts against the same cap.
Unset, the variable means 32000 tokens, which a planning report (full contract,
requirement trace and responses) can exceed: the stage stops with finish reason
`length` and truncated JSON. AutoCode therefore launches OpenCode with
`OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX=64000` unless you set a positive whole number
yourself, which wins:

```sh
OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX=100000 autocode "..."
```

It does not default to the model's whole listed limit (131072 for GLM 5.3): OpenCode
starts compacting a session when its context reaches the window minus this cap, and some
APIs refuse a request whose input plus maximum output exceeds the window. Each stage
record keeps the cap its process got under `output_token_cap` (`tokens`, and `set_by`:
`operator`, `autocode`, or `opencode` when the variable did not reach it). A length stop's
pause names that cap and how many tokens the last response used. To continue after one,
set a larger cap, set the attempt aside with `--abandon-stage`, and `--resume-paused`.
The rules are in `tools/autocode_output_cap.py`; configured command providers are not
affected.

### Environment variables agents see

Every provider process, and every test command the runner executes itself, gets
the runner's environment minus variables that look like credentials: a name word
such as `TOKEN`, `SECRET`, `PASSWORD`, `KEY` or `AUTH` (`GITHUB_TOKEN`,
`AWS_SECRET_ACCESS_KEY`, `SSH_AUTH_SOCK`, `OPENAI_API_KEY`), or a URL value with
an embedded password. Proxy variables and AutoCode's own `AUTOCODE_*` settings are
kept, and so are token counts: a `TOKEN` word followed by `MAX`, `LIMIT`, `COUNT` or
`BUDGET`, or after `MAX`, holding a whole number
(`OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX=100000`). Each stage record lists the withheld
names (never values) under `withheld_env`. The rules are in `tools/autocode_agent_env.py`.

Providers sign in from their own stored logins (OpenCode, Codex and Kilo auth
files), so the default routes need none of these. If
your provider or your project's tests genuinely need one, name it:

```sh
AUTOCODE_PASS_ENV=ZHIPU_API_KEY,DATABASE_URL autocode "..."
```

Withholding variables is not a sandbox: an agent with shell access can still read
files such as `~/.aws/credentials` or `~/.config/gh/hosts.yml`.

Resuming preserves the saved engine, models and separate role sessions. Start a new
run when switching between Codex and OpenCode; their session IDs cannot be reused
across engines. A response with an unexpected session ID pauses the run.
OpenCode version or configuration drift pauses the saved run, including changes to
custom config-directory files, agent definitions and local plugin/tool definitions.
This adapter was live-checked with OpenCode **1.18.31**. OpenCode 2.x is accepted: the stage runs in the workspace directory with `--standalone`, and reasoning effort is `provider/model#effort` rather than `--variant`. A saved run records the OpenCode version, so resume refuses a silent major-version change. Strict tool containment and verified visual delivery stay qualified only for OpenCode **1.18.33**.

## Check provider compatibility

Before relying on a new tool/model combination, run the shared
[provider conformance probe](provider-conformance.md). It checks the same workspace,
JSON report, command evidence, usage and session rules through Codex, OpenCode and
KiloCode. Passing applies to that recorded tool/model/configuration combination.

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

### A final message that is only a shell command

Some models served through a Codex transport sometimes end a stage with the
arguments of a shell tool call instead of the stage's JSON report
([#512](https://github.com/charlieanna/autocode/issues/512)):

```json
{"cmd": "ls docs/ && wc -l docs/*.md"}
```

AutoCode treats this as a known provider defect, never as a report, and never
runs the command. A final message counts as only a command when its keys are
`cmd` or `command` (holding a command) and, at most, the other arguments of a
Codex shell call (`workdir`, `timeout_ms`, `yield_time_ms`, `max_output_tokens`,
`shell`, `login`, `with_escalated_permissions`, `justification`), and none of
them is a field of the stage's schema. A command whose text embeds a JSON report
is still a command: AutoCode does not unwrap it, though a wrapper in your
provider command may before AutoCode reads the report. The stage is rejected
with `Provider final message is a shell command ({"cmd": ...}) instead of the
JSON report; ...` and then:

1. On a transport that keeps sessions (`--engine codex`, OpenCode, or a tool with
   `output = "opencode_events"` and `resume`), the runner resumes the stage's own
   session once and asks for the report alone, with no command. This correction
   spends no report-repair attempt. It applies to every stage, planning stages
   included, but only when it would run on the route (engine, provider, model,
   effort) that started the session: a Plan Reviewer attempt that ran on its
   one-use fallback route goes straight to step 2.
2. Otherwise, or when that correction fails, the usual report-only repairs run
   (`report_repair.max_attempts`, two by default), told that the rejected report
   is a command with nothing to repair.
3. A stage that keeps doing it stops there, usually at `PAUSED_REPEATED_FAILURE`,
   with that reason in `stop_reason`. As for any stuck stage, the Investigator
   looks at it first.

**Roles affected.** Every stage's final message is checked, so every role is
covered. The defect was seen (2026-10-05) on the roles one profile ran through
Codex with the `gocode` provider: the Plan Reviewer (`astra_challenge`,
`astra_finalize`) and the Tester (`sol`). Its Builder and Requirements ran
through another tool and did not show it. No role is qualified on a route that
shows the defect: if a role keeps stopping this way, run it on another model,
and check a new tool/model pair with the
[conformance probe](provider-conformance.md) first.

## Add a tool

[`examples/claude-provider`](../examples/claude-provider/README.md) is a worked example: it registers the
`claude` CLI so a run can use Haiku, Sonnet and Opus with no OpenCode or Codex install.

OpenCode is built in. Any other tool registers with one TOML file, not a Python
package. `--provider <name>` loads `~/.config/autocode/providers/<name>.toml` and,
if that file is absent, the bundled example at `tools/providers/configs/<name>.toml`.
Configs inside a project are not loaded.

```toml
name = "kilocode"
command = ["kilo", "run", "--dir", "{workspace}", "--model", "{model}", "--variant", "{effort}", "--format", "json"]
prompt = "stdin"                        # or "file" (uses {prompt_file})
output = "opencode_events"              # or "report_file"; see below
resume = ["--session", "{session}"]     # optional; enables saved sessions
models_command = ["kilo", "models"]     # optional; or a static list: models = [...]
version_command = ["kilo", "--version"]

[roles]
astra = { model = "openai/gpt-5.6-sol", effort = "high" }
terra = { model = "openai/gpt-5.6-terra", effort = "medium" }
sol = { model = "openai/gpt-5.6-sol", effort = "high" }
completion = { model = "openai/gpt-5.6-sol", effort = "medium" }
glm = { model = "zai-coding-plan/glm-5.3", effort = "medium" }
plan_reviewer = { model = "openai/gpt-6-sol", effort = "high" }
```

`[roles]` must keep each verifier on a different model family from what it checks
(Planner/Plan Reviewer, Builder/Validator, Builder/Completion Owner); a run whose
roles break that pauses with `PAUSED_CROSS_MODEL` before any agent is launched.

Placeholders are `{model}`, `{effort}`, `{workspace}`, `{report}`, `{schema}`,
`{prompt_file}`, `{run_dir}`, `{role}`, and `{sandbox}`. The opt-in Codex
artifact adapter below adds the standalone `{sandbox_args}` argv splice. `{sandbox}` is
`read-only` for planning, `workspace-write` for the builder, and
`workspace-write` for the judging stages (Validator, Completion Owner,
milestone checkpoint): their contract requires writing runner-owned
operational files under `.autocode/` (capture receipts, reports), which
`read-only` forbids. Their source contract stays enforced by the runner's
after-stage workspace snapshot, not by the sandbox.
`{effort}` reaches the tool only if the command uses it.
Use `{{` and `}}` for literal braces in a command argument, such as
`${{VAR}}` or `{{"key":1}}`; single braces are reserved for placeholders.
Changing the config file or the tool version pauses a saved run.

### Output modes

`output` says how the result comes back:

- `output = "report_file"` (the default): the tool writes exactly one JSON object
  to `{report}`, as `codex exec -o` does. Every stage starts fresh. Command
  evidence is a `capture_command` receipt file, not an `event:` id. These tools
  need supported usage events for token accounting; otherwise those counts
  remain unknown in the usage ledger.
- `output = "opencode_events"`: the tool prints OpenCode-format JSON events, as
  `opencode run --format json` and `kilo run --format json` do. Autocode reads
  the final report, token usage and command exit codes from those events, and
  resumes each role's session with the required `resume` template, for example
  `resume = ["--session", "{session}"]`.

Every structured handoff for these output modes includes an executable
`capture_command`, including standalone review, design, discuss and investigation
jobs. Models append `--output .autocode/evidence/<unique-name>.json -- <command>`
to that value. It uses the public `autocode.py capture` entry point; the
`autocode_capture_command.py` implementation module is not a standalone command.
An existing handoff command and its explicit output mode are preserved; the
supplied default inherits the runner's output-mode environment. This supplies
tool context without granting additional filesystem permissions.

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

A stage log holds the tool's stdout and stderr. When a tool exits with an error,
AutoCode reads the provider's reason from its `error` and `turn.failed` JSON
events and from the plain-text error lines (`ERROR: …`, `Error: …`) that end the
log, as `codex exec` without `--json` prints them. So
`ERROR: exceeded retry limit, last status: 429 Too Many Requests` stops the stage
as a rate limit, and a used-up plan or a busy model as a quota or capacity stop
([Models](models.md#when-a-roles-quota-runs-out)). Tool output earlier in the log
is never read as the provider's error, even when it mentions 429.

### Codex commands that write capture receipts

A registered `report_file` command that passes `--sandbox read-only` to Codex
cannot create AutoCode's required capture receipts. For that command, opt in to
`sandbox_adapter = "codex_artifacts"` and replace the legacy sandbox flag and
`{sandbox}` value with the standalone `{sandbox_args}` argument:

```toml
name = "codex_receipts"
sandbox_adapter = "codex_artifacts"
command = ["codex", "exec", "--ephemeral", "{sandbox_args}", "-C", "{workspace}", "--model", "{model}", "-c", "model_reasoning_effort=\"{effort}\"", "-c", "approval_policy=\"never\"", "-c", "forced_login_method=\"chatgpt\"", "--output-schema", "{schema}", "-o", "{report}", "--json", "-"]
prompt = "stdin"
output = "report_file"
version_command = ["codex", "--version"]

[roles]
astra = { model = "gpt-6-sol", effort = "medium" }
terra = { model = "gpt-6-luna", effort = "medium" }
sol = { model = "gpt-6-sol", effort = "medium" }
completion = { model = "gpt-6-sol", effort = "medium" }
glm = { model = "gpt-6-luna", effort = "medium" }
plan_reviewer = { model = "gpt-6-sol", effort = "medium" }

[auth]
command = ["codex", "login", "status"]
forbid_env = ["OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"]
[[auth.routes]]
models = "gpt-"
pattern = "(?m)^Logged in using (ChatGPT)$"
expect = "ChatGPT"
```

Save this as `~/.config/autocode/providers/codex_receipts.toml`, then select it
with `autocode "Your task" --provider codex_receipts`. Choose models available
to your login. Requirements inherits the configured Planner (`glm`) model and
effort; `--requirements-model` and `--requirements-reasoning-effort` still
provide explicit overrides.

This adapter requires **Codex CLI 0.160.0 or later**, checked before a model
request. For planning and review it selects a named permission profile derived
from `:read-only`, adding writes only to the workspace's `.autocode/evidence/`,
`.autocode/output/`, and the exact assigned stage JSON report. The Builder keeps
`workspace-write`. Codex's `-o` option saves the final JSON response; the model
still runs actual capture commands to produce independently checked receipts.
No permission answer automatically changes this profile.

**Opting in passes `--ignore-user-config` to each Codex invocation.** Global files
are untouched and the stored login is reused, but user-level configuration,
plugins, MCP servers and provider defaults are omitted. Supply any required,
compatible options explicitly in the command. Managed requirements still apply.
Do not combine the adapter with legacy sandbox flags, additional writable roots,
configuration profiles or permission-policy overrides. The adapter rejects those
combinations instead of silently overriding a policy.

Operational paths must be canonical and unaliased. Pre-existing symlinks,
hard-linked files and special files in the writable operational directories are
rejected before launch; AutoCode does not delete or rewrite them. A stage report
must be a JSON file under the run's `iterations/` directory. Existing source
snapshot, receipt identity, hash, regression-proof and completion checks still
apply. Language tools that need additional caches must use an allowed operational
path or a separately configured provider; this adapter does not grant arbitrary
cache directories.

Existing provider configs and `--engine codex` keep their behavior. Changing a
saved run's provider config triggers its existing transport-change pause; inspect
and accept that change through the public recovery command. Permission-profile
behavior has been exercised on macOS with Codex 0.160.0; other platform/version
combinations need their own conformance check.

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
