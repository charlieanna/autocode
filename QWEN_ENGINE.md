# Qwen Engine for Autocode

Autocode now supports Qwen Code as a native engine, allowing you to use Qwen models directly without requiring OpenCode.

## Prerequisites

- **Python 3.11+** (for running autocode)
- **Qwen Code CLI** installed and in PATH
- **WSL** (Windows Subsystem for Linux) - required for Windows users
- **Git** for workspace management

## Installation

### 1. Install WSL (Windows only)

Open PowerShell as Administrator and run:

```powershell
wsl --install -d Ubuntu
```

Restart your computer when prompted, then complete the Ubuntu setup.

### 2. Install Python 3.11+ (in WSL)

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv
```

### 3. Install Qwen Code CLI

Follow the installation instructions at [Qwen Code](https://github.com/QwenLM/Qwen-Code).

Verify installation:

```bash
qwen --version
```

### 4. Clone and setup Autocode

```bash
git clone https://github.com/charlieanna/autocode.git
cd autocode
```

## Usage

### Basic Usage

Run autocode with the Qwen engine:

```bash
python3 tools/autocode.py --engine qwen "build a REST API for a todo app"
```

### With Specific Models

You can specify different Qwen models for each role:

```bash
python3 tools/autocode.py \
  --engine qwen \
  --glm-model qwen/qwen3.7-plus \
  --plan-reviewer-model qwen/qwen3.8-max \
  --astra-model qwen/qwen3.8-max \
  --terra-model qwen/qwen3.7-plus \
  --sol-model qwen/qwen3.8-max \
  --completion-model qwen/qwen3.8-max \
  "build a web scraper"
```

If your account serves only one model, `--single-model qwen/<id>` assigns it to
every role and permits same-model verification; model-family independence is then
intentionally unavailable, as it is for any single-model run.

### Default Models

`DEFAULT_MODELS` in `tools/autocode_qwen.py` owns these, and
`QWEN_ROLE_MODELS` in `tools/autocode_configure.py` reads them:

| Role | Default Model | Purpose |
|------|---------------|---------|
| `glm` (planner) | `qwen/qwen3.7-plus` | Requirements and planning |
| `plan_reviewer` | `qwen/qwen3.8-max` | Independent plan review |
| `astra` (investigator/resolver) | `qwen/qwen3.8-max` | Strong reasoning |
| `terra` (builder) | `qwen/qwen3.7-plus` | Implementation |
| `sol` (validator) | `qwen/qwen3.8-max` | Independent validation |
| `completion` (completion owner) | `qwen/qwen3.8-max` | Completion decisions |

The Planner and the Plan Reviewer must be **different** models. One model for
both pauses the run with `PAUSED_CROSS_MODEL` before any agent is launched, as it
does for every other engine.

### Available Qwen Models

A model id is whatever your own Qwen Code install serves: the ids under
`modelProviders` in `~/.qwen/settings.json`. Qwen Code has no model-listing
command, so AutoCode cannot check availability for this engine — it validates the
`qwen/` addressing only, and an id your account does not serve fails when the CLI
is launched. The defaults above are ModelStudio Token Plan ids; a DashScope
account serves names such as `qwen-max` and `qwen-coder-plus` instead, so override
the role models to match your own registry.

Write them as `qwen/<id>`: the `qwen/` prefix is AutoCode's transport namespace
and is stripped before the CLI sees it, so `qwen/qwen3.8-max` launches
`qwen --model qwen3.8-max`.

## Testing

Run the test script to verify your setup:

```bash
chmod +x test_qwen_engine.sh
./test_qwen_engine.sh
```

This will:
1. Check Python and Qwen CLI installation
2. Run a dry-run test
3. Verify model configuration, including that no verifier shares its producer's model
4. Confirm engine registration
5. Run the transport and run-configuration unit tests

The unit tests alone cover the transport without any model spend:

```bash
python3 tools/test_qwen.py                 # event adaptation, reports, launch
python3 -m unittest tests.test_qwen_engine # run configuration and role routing
```

## How It Works

The Qwen engine integrates with autocode through:

1. **`tools/autocode_qwen.py`** - Transport module that handles:
   - CLI invocation with proper arguments
   - Event adaptation from Qwen's `stream-json` output
   - Session management
   - Report extraction from the schema-validated structured output

2. **Engine selection** - Use `--engine qwen` to select Qwen as the backend

3. **Role-based models** - Each autocode role (`glm`, `plan_reviewer`, `astra`,
   `terra`, `sol`, `completion`) can use a different Qwen model

4. **Structured reports** - Every stage is launched with `--json-schema` pointing
   at that stage's report schema. Qwen registers a `structured_output` tool and
   ends the session on the first call that validates, and the runner reads the
   report from the terminal result event's `structured_result`. Nothing is scraped
   out of the model's prose; a stage that returns no structured output fails.

5. **Events and command evidence** - `--output-format stream-json` prints one JSON
   object per line into the stage's event log, which the idle watchdog reads while
   the stage runs. Qwen's events are Anthropic-style: an `assistant` message
   carries `tool_use` blocks and a later `user` message carries the matching
   `tool_result`. `normalized_events` turns a completed `run_shell_command` result
   into an `item.completed` command event, so a Validator check can cite it. A
   successful result is the command's bare stdout, so its exit code is 0; a failed
   one is a diagnostic envelope ending in `Exit Code: N`; a call the approval
   policy refused has no exit code and becomes non-citable `tool_output` instead of
   invented evidence. Token counts come from the terminal result event: Qwen's
   `input_tokens` already include cache reads and its `output_tokens` already
   include reasoning, so neither is added again. Qwen reports no cost, so a stage's
   `cost_usd` stays unknown in the usage ledger.

6. **Approval modes** - The engine uses Qwen's `--approval-mode` flag:
   - `plan` for read-only stages. It exposes no shell and no edit tool, so such a
     stage cannot change the workspace; it still exposes `structured_output`.
   - `yolo` for the Builder and the judging stages, which must run their own
     checks and write receipts under `.autocode/`. `auto` is not used: its
     classifier refused a second authorized command as out of scope in a live
     probe, and a refusal arrives as a failed tool result rather than as the
     denial AutoCode reports as a blocker. Containment for these stages is the
     runner's before/after workspace snapshot, not the approval mode.

## Differences from OpenCode Engine

| Feature | OpenCode Engine | Qwen Engine |
|---------|----------------|-------------|
| CLI command | `opencode run` | `qwen` |
| Model format | `provider/model` | `qwen/model` |
| Directory flag | `--dir <path>` | Uses cwd (no flag) |
| Reasoning effort | `--variant <level>` | Not supported; recorded, never passed |
| Session resume | `--session <id>` | `--resume <id>` |
| Permissions | Agent-based permissions | `--approval-mode plan` / `yolo` |
| Output format | `--format json` | `--output-format stream-json` |
| Report | Final assistant text | `--json-schema` structured output |
| Capture receipts | Not used (events mode) | Not used (events mode) |
| Cost reporting | From provider events | Not reported by Qwen |

## Troubleshooting

### "Qwen is not on PATH"

Make sure Qwen CLI is installed and accessible:

```bash
which qwen
qwen --version
```

### "Model must use provider/model format"

Use the correct format: `qwen/model-name`

✅ Correct: `qwen/qwen3.8-max`
❌ Incorrect: `qwen3.8-max` or `openai/qwen3.8-max`

### "Qwen returned no structured report"

The stage ended without a `structured_output` call that validated against the
schema. Read the saved event log named in the pause: the terminal result event
shows whether Qwen errored, ran out of wall time, or simply answered in prose.

### A stage pauses with `PAUSED_TRANSPORT_CHANGED`

`qwen --version`, or the resolved `qwen` executable, differs from the one the run
was created with. A saved run keeps its transport identity; start a new run after
upgrading Qwen Code.

### "No module named 'autocode_qwen'"

Make sure you're running from the autocode directory and all files are present:

```bash
ls tools/autocode_qwen.py
```

### Permission errors on Windows

Remember to run autocode from WSL, not Windows command prompt. Autocode requires POSIX APIs that are only available in WSL on Windows.

## Examples

### Simple API

```bash
python3 tools/autocode.py --engine qwen "build a simple REST API with endpoints for users"
```

### With Workspace

```bash
python3 tools/autocode.py \
  --engine qwen \
  --workspace /path/to/project \
  "add authentication to the existing API"
```

### With Iteration Limit

```bash
python3 tools/autocode.py \
  --engine qwen \
  --max-iterations 10 \
  "refactor the database layer"
```

### Resume a Session

```bash
python3 tools/autocode.py \
  --engine qwen \
  --run-dir /path/to/previous/run \
  --resume-paused
```

## Architecture

The Qwen engine follows the same architecture as OpenCode:

1. **Launch** - Spawns the Qwen CLI with the stage's model, schema and approval mode
2. **Prompt** - Pipes the stage prompt, with the output and evidence contract injected, to stdin
3. **Execute** - Runs Qwen in the workspace directory
4. **Adapt** - Reads the `stream-json` event log and normalizes it to command evidence, usage and a terminal turn
5. **Validate** - Verifies the structured report against the stage schema, and each cited check against an executed event
6. **Record** - Saves events and outputs for audit trail

## What the live runs showed (2026-10-09, Qwen Code 0.25.0, macOS)

A `bugfix` run on a scratch project — a half-up rounding bug with two failing
tests — reached `TASK_COMPLETE`, with every stage on Qwen and every model stage
exiting 0: the Job Recognizer and the Builder on `qwen3.7-plus`, and the
Investigator, Planner, Plan Reviewer, Validator and Completion Reviewer on
`qwen3.8-max`.

- The delivered fix was `(cents + 5) // 10` with four new regression tests; all
  eight tests pass, and both the runner's regression proof and its clean-copy
  check replay ran.
- The Validator returned `PASS` with checks citing real Qwen tool-call ids
  (`event:call_…`) and carrying the exit codes their results proved, so the
  runner's independent evidence check bound them rather than rejecting them.
- The planning and review stages ran in `plan` mode and used read tools only.
- Token counts were accounted per stage (one Investigator reported 251k input,
  219k of it cached, 12.8k output). Cost stayed unknown, as Qwen reports none.

Two operational findings, from an earlier attempt at the same task:

- A Builder report was rejected once for citing `event:toolu_01…`, an
  Anthropic-style id Qwen never issues. AutoCode's bounded report-only repair
  recovered it, and the completing run needed no repair.
- Launch the run with the interpreter of the checkout you are running. A run
  started by another checkout's virtualenv spawns its check-replay preparation
  worker with that interpreter against this tree's worker script, and the
  mismatched ownership receipts pause the replay with
  `PAUSED_VERIFICATION_UNCERTAIN`. That is a two-checkout mistake, not a Qwen one.

One run of one scenario shows the workflow can get through on these models, not
that it reliably does. The plan was approved by a person; nothing else was.

## Known limits

Qualified against **Qwen Code 0.25.0** on macOS. Not covered by this engine:

- **Verified visual delivery** stays qualified only for the native OpenCode route;
  a run that requests it pauses with `PAUSED_VISUAL_EVIDENCE`.
- **Kernel tool containment** (`AUTOCODE_TOOL_CONTAINMENT`) is an OpenCode route.
  A `yolo` Builder runs its shell on the machine that runs the task, so use a
  disposable workspace, as with any uncontained engine.
- **Native read-only snapshots** are OpenCode's. A read-only Qwen stage is
  prevented by `plan` mode and checked afterwards by the runner's before/after
  workspace snapshot.
- **Capture receipts** are not offered: the runner sets a capture context only for
  `report_file` providers, so checks cite executed events instead.
- **Reasoning effort** is recorded per role but never reaches the CLI; Qwen Code
  has no effort flag.
- **Cost** is not reported, so the usage ledger shows tokens without a price.
- Your own Qwen Code customizations (hooks, extensions, MCP servers, context
  files) load inside every stage. The Claude example avoids this with
  `--setting-sources project`; Qwen Code's equivalents are `--bare` and
  `--safe-mode`, and neither is passed here.
- `autopilot` has no `--engine` flag, so its pre-stage drift check still compares
  Codex's settings. Resume a Qwen run with `autocode`, not `autopilot`.

Run the provider conformance probe before relying on a new Qwen Code version or a
new model id; a pass applies to that recorded combination only.

## Contributing

To extend the Qwen engine:

1. Edit `tools/autocode_qwen.py` for transport-level changes, including the role
   defaults in its `DEFAULT_MODELS`
2. Add new CLI arguments in `tools/autocode_args.py`
3. Test with `python3 tools/test_qwen.py` and
   `python3 -m unittest tests.test_qwen_engine`, then `./test_qwen_engine.sh`

## Support

For issues related to:
- **Autocode**: Check the main [README.md](README.md)
- **Qwen CLI**: Check [Qwen Code documentation](https://github.com/QwenLM/Qwen-Code)
- **Qwen models**: Check [Qwen model documentation](https://qwen.readthedocs.io/)

## License

Same as Autocode: Apache 2.0
