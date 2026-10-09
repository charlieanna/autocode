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
  --astra-model qwen/qwen-max \
  --terra-model qwen/qwen-coder-plus \
  --sol-model qwen/qwen-max \
  --completion-model qwen/qwen-max \
  "build a web scraper"
```

### Default Models

The Qwen engine uses these default models for each role:

| Role | Default Model | Purpose |
|------|---------------|---------|
| `astra` (plan reviewer) | `qwen/qwen-max` | Strong reasoning for plan review |
| `terra` (builder) | `qwen/qwen-coder-plus` | Code generation specialist |
| `sol` (validator) | `qwen/qwen-max` | Independent validation |
| `completion` (completion owner) | `qwen/qwen-max` | Completion decisions |

### Available Qwen Models

Common Qwen models you can use:

- `qwen/qwen-max` - Most capable, best for complex reasoning
- `qwen/qwen-plus` - Balanced performance and cost
- `qwen/qwen-coder-plus` - Specialized for code generation
- `qwen/qwen-turbo` - Fast and cost-effective

Check available models with:

```bash
qwen models
```

## Testing

Run the test script to verify your setup:

```bash
chmod +x test_qwen_engine.sh
./test_qwen_engine.sh
```

This will:
1. Check Python and Qwen CLI installation
2. Run a dry-run test
3. Verify model configuration
4. Confirm engine registration

## How It Works

The Qwen engine integrates with autocode through:

1. **`tools/autocode_qwen.py`** - Transport module that handles:
   - CLI invocation with proper arguments
   - Output parsing (JSON format)
   - Session management
   - Report extraction and validation

2. **Engine selection** - Use `--engine qwen` to select Qwen as the backend

3. **Role-based models** - Each autocode role (astra, terra, sol, completion) can use a different Qwen model

4. **Approval modes** - The engine uses Qwen's `--approval-mode` flag:
   - `plan` mode for planning/review roles (read-only)
   - `auto` mode for builder role (allows file operations)

## Differences from OpenCode Engine

| Feature | OpenCode Engine | Qwen Engine |
|---------|----------------|-------------|
| CLI command | `opencode run` | `qwen` |
| Model format | `provider/model` | `qwen/model` |
| Directory flag | `--dir <path>` | Uses cwd (no flag) |
| Reasoning effort | `--variant <level>` | Not supported |
| Session resume | `--session <id>` | `--resume <id>` |
| Permissions | Agent-based permissions | `--approval-mode` flag |
| Output format | `--format json` | `--output-format json` |

## Troubleshooting

### "Qwen is not on PATH"

Make sure Qwen CLI is installed and accessible:

```bash
which qwen
qwen --version
```

### "Model must use provider/model format"

Use the correct format: `qwen/model-name`

✅ Correct: `qwen/qwen-max`
❌ Incorrect: `qwen-max` or `openai/qwen-max`

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

1. **Launch** - Spawns Qwen CLI with appropriate flags
2. **Prompt** - Injects schema instructions into the prompt
3. **Execute** - Runs Qwen in the workspace directory
4. **Parse** - Extracts JSON events from output
5. **Validate** - Verifies the final report against the schema
6. **Record** - Saves events and outputs for audit trail

## Contributing

To extend the Qwen engine:

1. Edit `tools/autocode_qwen.py` for transport-level changes
2. Update model defaults in `QWEN_ROLE_MODELS` in `tools/autocode_configure.py`
3. Add new CLI arguments in `tools/autocode_args.py`
4. Test with `./test_qwen_engine.sh`

## Support

For issues related to:
- **Autocode**: Check the main [README.md](README.md)
- **Qwen CLI**: Check [Qwen Code documentation](https://github.com/QwenLM/Qwen-Code)
- **Qwen models**: Check [Qwen model documentation](https://qwen.readthedocs.io/)

## License

Same as Autocode: Apache 2.0
