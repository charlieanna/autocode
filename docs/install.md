# Install and run

[← Back to README](../README.md)

## Ten-minute quickstart

You need macOS or Linux (Windows through WSL), Python 3.11 or newer, Git,
[pipx](https://pipx.pypa.io/), and npm (Node.js) to install the engine.

**1. Install AutoCode.**

```sh
pipx install git+https://github.com/charlieanna/autocode
```

AutoCode is not on PyPI yet. After the first release this step becomes
`pipx install autocode-supervisor`. If pipx's Python is older than 3.11, add
`--python python3.11`.

**2. Install the engine and sign in.** The default engine is OpenCode. The terminal
adapter accepts 1.x and 2.x; this quickstart pins **1.18.33**, the version qualified
for strict tool containment on macOS. Its default routes use two accounts: the
Requirements Gatherer, Planner and Builder run GLM 5.3 on a **Z.ai Coding Plan**,
and the Plan Reviewer, Tester and Completion Reviewer run GPT-6 Sol through
**ChatGPT** ([Models](models.md#default-models)).
So OpenCode needs two logins:

```sh
npm install -g opencode-ai@1.18.33
opencode auth login     # choose OpenAI and sign in with your ChatGPT account
opencode auth login     # choose Z.AI Coding Plan and enter its API key
opencode auth list      # both are listed now
```

**One login instead of two?** Not on OpenCode's default routes. The single-login
route is Codex with a ChatGPT account: install it, sign in once, and add
`--engine codex` to `autocode doctor` and to every task below.

```sh
npm install -g @openai/codex
codex login
```

**3. Make a project and check everything.** A task works in a Git repository with at
least one commit:

```sh
mkdir hello-autocode && cd hello-autocode
git init
git commit --allow-empty -m "Start"
autocode doctor
```

If Git asks who you are, run `git config --global user.name "Your Name"` and
`git config --global user.email you@example.com` once, then commit again. `doctor`
prints ✔ or ✖ for Python, psutil, Git, the engine and the project, with the fix next to
each ✖, and ends with `ready for a first task`. For OpenCode it also reads
`opencode models` (this can take up to a minute) and shows a ✖ `routes` line naming the
login still missing when a role's default model is not offered, as the first task would
refuse to start; `autocode models` shows the full list. With `--engine codex` it checks
the Codex login instead.

**4. Start the first task.**

```sh
autocode "Write hello.py that prints a greeting, with a unittest test for it"
```

For OpenCode, this command requires macOS with `/usr/bin/sandbox-exec` and the
pinned engine version above. On Linux or another OpenCode version, see
[native tool containment](execution.md#native-tool-containment) for the explicit
choice to use OpenCode's own permission checks without kernel containment.

In a terminal this is a chat. AutoCode may ask a question or two (type the answer),
then shows the plan: what will be built and how each requirement will be checked.

**5. Approve.** At `Approve this brief? [y/N], or type planning feedback:` type `y`.
Nothing is built before this; anything else you type goes back to the planners as
feedback. After approval the Builder, Tester and Completion Reviewer run on their own
until the task completes or needs you.

**6. See the result.** The task runs in its own Git worktree, so your checkout is not
touched. When it completes, AutoCode prints what was done and checked, then
`Delivered on branch autocode/<task>-<id> (<commit>)`. Look at it and merge it:

```sh
git log --stat autocode/<task>-<id>
git merge autocode/<task>-<id>
```

If you closed the chat, `autocode status` shows where the run is and `autocode resume`
continues it. [Workflow](workflow.md) explains each stage; [CLI](cli.md) lists every
command.

## Prerequisites

- **Python 3.11+**
- **Git**
- **OpenCode 1.x or 2.x** connected to ChatGPT and Z.ai (the default engine). Live-checked with OpenCode **1.18.31**. OpenCode 2.x uses `--standalone` and a `provider/model#effort` model id. Strict tool containment and verified visual delivery stay qualified only for OpenCode **1.18.33**.
- **macOS or Linux**. Windows needs WSL because the inherited process and lock mechanisms use POSIX APIs.
- Optional: Codex CLI for `--engine codex` (one ChatGPT login) and the Figma path. Installation does not change Codex or OpenCode settings.

Native process supervision uses the `psutil` runtime dependency, which `pipx` and `pip`
install with AutoCode. Works against any committed Git workspace; no IdleCampus files
or services are required.

## Windows through WSL

Run Git, Python and AutoCode inside WSL. Prefer a fresh checkout on the Linux
filesystem, such as `~/src/autocode`, to avoid sharing ownership and environment
state with Windows tools. The repository's `.gitattributes` keeps text checkouts
at LF even when `core.autocrlf=true`; binary assets retain their bytes. This
policy applies to fresh checkouts and does not rewrite an existing dirty tree.
Save any real edits before replacing a checkout with line-ending-only churn;
do not commit that churn to recover it.

A virtual environment created by Windows has `Scripts/`, `Lib/` and `Include/`,
while the Linux commands in the README require `.venv/bin/`. Create the environment
with Linux Python inside WSL. If the shared checkout already has a Windows
`.venv`, keep it separate and create a Linux environment outside the checkout:

```sh
python3 -m venv ~/.venvs/autocode-wsl
~/.venvs/autocode-wsl/bin/python -m pip install --editable .
source ~/.venvs/autocode-wsl/bin/activate
```

If Git reports `detected dubious ownership` for a Windows-mounted checkout,
first verify that you trust that specific directory. You can then allow its
exact path, replacing this example with your checkout's absolute path:

```sh
git config --global --add safe.directory '/mnt/c/Users/YOU/workspace/autocode'
```

Avoid `safe.directory '*'`, which trusts every repository. A checkout on the
Linux filesystem usually avoids this mounted-directory ownership mismatch.

## Advanced: install from a checkout

For working on AutoCode itself, or trying an unreleased change. Run directly from a
checkout (its Python needs `psutil`, as in the project's `.venv`):

```sh
python3 /path/to/autocode/tools/autocode.py "Build a greeting CLI" \
  --workspace /path/to/project --reasoning-effort high
```

Install the checkout once with `pipx`, editable so your changes apply, to invoke it from
any project:

```sh
pipx install --editable /path/to/autocode
```

Or install into your own virtual environment:

```sh
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/autocode "Build a greeting CLI" --workspace /path/to/project
```

### Sanity check

Confirm the install, then check the machine and your project before the first task:

```sh
autocode --version
autocode doctor --workspace /path/to/project
```

`--version` prints the commit when run from a checkout and `commit unknown` from an
installed package. `doctor` names anything missing (Python, psutil, Git, the default
engine, a committed Git project) with the fix next to it, and exits 1 until the machine
is ready. It checks the engine a new run would use: OpenCode, or the provider set as the
default below (with no `--engine` and with `--engine opencode` alike), including that it
offers every role's default model; `--engine codex` checks Codex instead.

### Releasing

Pushing a version tag publishes to PyPI; see [Releasing](releasing.md).

## The normal invocation

From inside any committed Git project:

```sh
autocode "Your rough idea"
```

Or target another committed Git workspace:

```sh
autocode "Your rough idea" --workspace /path/to/project
```

That starts joint requirements planning: a Requirements Gatherer clarifies the
outcome, a Planner drafts a task DAG, and a Plan Reviewer challenges it. You approve
the plan before any Builder starts. See [Workflow](workflow.md).

## Advanced entry points

Use the whole workflow through `autopilot`, or invoke one unit at its saved boundary:

```sh
autopilot "Build a greeting CLI" --workspace /path/to/project --chat

# Or invoke individual units at their saved boundaries:
autoplanner "Build a greeting CLI" --workspace /path/to/project --chat
# After approval, this stops before any Builder starts.
# Each unit continues the project's unfinished run (add --run-dir RUN when it has several):
autocode-build --workspace /path/to/project --no-chat
autoreview --workspace /path/to/project --no-chat
# If review requests rework, diagnose it without launching a Builder:
autoresolver --workspace /path/to/project --no-chat
# Let Autopilot continue through any remaining build/review cycles:
autopilot --workspace /path/to/project --no-chat
```

Each unit command stops successfully before dispatching another unit.
`autocode --unit autoplanner|autocode|autoreview|autoresolver` provides the same
selection; omitting it runs all units.

## Engine and planning flags

New runs use joint Requirements Planner/Plan Reviewer work by default. An approved
three-role OpenCode run can add Planner (GLM) work at a clean execution boundary with
`--joint-planning --resume-paused`. Its approved work and existing sessions remain;
the Planner joins the next brief revision.

Native Codex, including Figma runs, supports `--engine codex --joint-planning`:
requirements gathering, planning, and plan review run in separate read-only Codex
sessions using the existing ChatGPT login. The three routes inherit the saved
planning model unless explicitly selected with `--requirements-model`, `--glm-model`,
and `--plan-reviewer-model` (bare GPT names). Adding joint planning to a saved Codex
run at a clean execution or discovery boundary backs up the checkpoint, retains the
work and existing sessions, and restarts at requirements gathering. The reviewed plan
needs fresh approval before further implementation. Saved runs retain their engine
and limits.

## Default provider

New runs use OpenCode unless you choose otherwise. `--provider <name>` picks the
tool for one run. To change the default for every new run and for the dashboard,
set `AUTOCODE_PROVIDER=kilocode` or add this to `~/.config/autocode/config.toml`:

```toml
default_provider = "kilocode"
```

A saved run keeps the provider it started with, and `--engine codex` runs still
use Codex. `autocode doctor` then checks the default provider instead of OpenCode.
`AUTOCODE_PROVIDER=codex` does not select the Codex engine: it names a provider config
`codex.toml`, and doctor reports it missing. Use `--engine codex` for Codex.
`autocode-dashboard --provider <name>` overrides the default for the dashboard; its
model pickers list that tool's models.

See also: [CLI](cli.md) · [Providers](providers.md) · [Models](models.md)
