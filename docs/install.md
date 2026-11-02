# Install and run

[← Back to README](../README.md)

## Quickstart

You need macOS or Linux (Windows through WSL), Python 3.11 or newer, Git,
[pipx](https://pipx.pypa.io/), and npm (Node.js) to install the engine.

Linux provides the [kernel provider boundary](providers.md#provider-process-ownership).
macOS can run tasks with portable supervision, which cannot prove that every
detached provider descendant exited. Use supported Linux for the stronger
process-ownership guarantee and for the provider-backed regression checks in
this guide. WSL must meet the same Linux kernel requirements.

If prerequisites are missing, on Ubuntu install `python3-venv`, `pipx` and `git`
with your package manager; on macOS install Python 3.11+, Git and pipx. Node.js/npm
is needed to install Claude Code, Codex, OpenCode or Kilo with npm. API keys go in
OpenCode or Kilo; the built-in
[API-key connection](providers.md#autocode-direct-connections-issue-1493) is retired.

**1. Install AutoCode.**

```sh
pipx install autocode-supervisor
pipx ensurepath
# Open a new terminal so PATH includes pipx's executables.
autocode --version
autocode demo
```

Or use the published [Homebrew tap](https://github.com/charlieanna/homebrew-tap):

```sh
brew install charlieanna/tap/autocode
```

The published version verified on 2026-10-27 is **0.8.2** on both
[PyPI](https://pypi.org/project/autocode-supervisor/0.8.2/) and Homebrew.
These commands install the tagged release, not current master; this guide's
`latest` documentation can describe newer features and fixes. Use the
[advanced checkout/master installation](#advanced-install-from-a-checkout)
when you need those changes. The [release verification record](bugs/2026-10-27-release-installation.md)
distinguishes distribution smoke checks from task/provider qualification.
If pipx's Python is older than 3.11, add `--python python3.11`. The demo is
offline: no sign-in or model spend. Once installed, you do not need to activate
a virtual environment in every shell.

**Connect your accounts once.** Run `autocode setup` right after installing. It
checks Claude Code, Codex, OpenCode and Kilo. When neither OpenCode nor Kilo is
installed it offers to install OpenCode with npm (`--install kilocode` installs Kilo
instead), then offers the sign-in of each installed one that is not signed in, and
says which backend your tasks will use. API keys (Z.ai, MiMo, OpenAI, Anthropic) go in OpenCode or
Kilo. It then asks which model and reasoning level each stage runs, with advice for each, and
every task uses that choice; change it any time with `autocode stages choose`
([Choosing each stage's model](models.md#choosing-each-stages-model)). After that a task needs no flags.

**Already have an engine signed in?** Reuse it. When OpenCode is not installed
and nothing names an engine (no flag, no `default_provider`, no project
`engine`), or OpenCode is installed with no account at all,
a new run takes the first backend this machine has connected: Claude
Code signed in to a subscription, then Codex, then Kilo. It prints the choice (`Engine for this run:
Claude Code. …`), and `autocode doctor` checks that same backend. So with only
Claude Code or only Codex signed in, skip step 2 and go to the project setup in
step 3 with no flags. To choose explicitly, add `--engine codex` or `--engine
claude` to `doctor` and to the task; a flag selects a route for that command
only and never changes your saved defaults or existing runs.

**2. Install the engine and sign in.** The default engine is OpenCode. The terminal
adapter accepts 1.x and 2.x; this quickstart pins **1.18.33**, the version with
historical macOS native-tool qualification. Its default routes use two accounts: the
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

**One login instead of two?** Not on OpenCode's default routes. There are two single-login
routes. Codex with a ChatGPT account: install it, sign in once, and add
`--engine codex` to `autocode doctor` and to every task below.

```sh
npm install -g @openai/codex
codex login
```

Or Claude Code with a Claude subscription (the [built-in claude
provider](providers.md#claude-code-provider)): install Claude Code, sign in once, and add
`--engine claude` to `autocode doctor` and to every task below.

```sh
claude auth login
autocode doctor --engine claude
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
the Codex login instead, and with `--engine claude` the Claude Code login.
For an OpenCode run its `containment` line names the kernel tool boundary the stages get
on this machine (macOS Seatbelt or Linux bubblewrap), or warns, with the reason, that the
run would be refused without one ([native tool containment](execution.md#native-tool-containment)).

**4. Start the first task.**

```sh
autocode "Write hello.py that prints a greeting, with a unittest test for it"
```

On Linux the provider-ownership requirement applies to every task-stage
engine. OpenCode's separate tool policy also needs an explicit choice: see
[native tool containment](execution.md#native-tool-containment) for the explicit
choice to use OpenCode's own permission checks on Linux. That choice concerns
tool access; it does not bypass Linux provider process ownership. The macOS
portable supervision limitation remains as described above.

In a terminal this is a chat. AutoCode may ask a question or two (type the answer, or
an option's number). Once it has understood the request it asks before spending on
planning: `Prepare a reviewed plan?` with what that spends, then `Prepare plan? [y/N]:`;
type `y`. It then shows the plan: what will be built and how each requirement will be
checked.

**5. Approve.** At `Approve this plan? [y/N], or type what to change:` type `y`.
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

## If setup stops

| What you see | What to do |
| --- | --- |
| `autocode: command not found` | Run `pipx ensurepath`, open a new terminal, then `autocode --version`. For a venv install, activate that venv or use its `bin/autocode`. |
| Missing engine or login in `doctor` | Select the engine you actually connected. Use the same `--engine codex` or `--engine claude` for doctor and the task; the unqualified command uses the configured/default route. |
| Missing Git project or HEAD | Run in the project root and make its first commit. Starting in a folder of sibling repositories does not select them as a set. |
| A missing model route | Follow the named route/login fix. An installed CLI alone does not establish access to every selected role's model. |
| Provider session boundary unavailable on Linux | `doctor`'s `session` row names the cause. The CLI automatically drops `CAP_SYS_ADMIN` and `CAP_SYS_PTRACE` from its Linux bounding set and re-executes once before touching run state, so root containers normally need no prefix. A successful drop is reported on stderr and in `doctor`; no other capability is dropped. Set `AUTOCODE_NO_CAPABILITY_DROP=1` to manage this yourself. If the kernel refuses the drop or re-exec, existing stage admission still applies: use an ordinary user or, where permitted, `setpriv --bounding-set=-sys_admin,-sys_ptrace autocode ...`. Other boundary requirements are unchanged; see [provider ownership](providers.md#provider-process-ownership). `doctor` is not a complete kernel-admission test. |
| Another repository is not registered | Set the same `AUTOCODE_HOME` in each terminal and use `autocode registry add /path/to/committed/checkout`; see the [multi-repository walkthrough](multi-repo.md#walkthrough-a-bug-reveals-another-repository). |

## Prerequisites

- **Python 3.11+**
- **Git**
- **OpenCode 1.x or 2.x** connected to ChatGPT and Z.ai (the default engine). Live-checked with OpenCode **1.18.31**. OpenCode 2.x uses `--standalone` and a `provider/model#effort` model id. Strict tool containment and verified visual delivery stay qualified only for OpenCode **1.18.33**.
- **Linux or macOS**, with different provider cleanup guarantees; see [provider process ownership](providers.md#provider-process-ownership). Supported Linux is used for provider-backed qualification. Windows needs WSL with the same Linux kernel requirements. Native Windows support is not planned; [Native Windows](windows.md) records what was audited and what remains.
- Optional: Codex CLI for `--engine codex` (one ChatGPT login) and the Figma path; Claude Code for `--engine claude` (one Claude subscription login). Installation does not change Codex, OpenCode or Claude Code settings.

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

To follow current master without maintaining a development checkout:

```sh
pipx install git+https://github.com/charlieanna/autocode
```

Use `pipx reinstall autocode-supervisor` to refresh an existing pipx Git
installation from its saved source. This is separate from the published PyPI
release; check `autocode --version` and retain the source commit when reporting
behavior. Do not assume two builds with the same package version contain the
same master changes. Choose one active installation rather than relinking a
Homebrew command over an existing pipx/development command.


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

A virtual environment's `bin` directory is on `PATH` only while the environment is
activated (`source .venv/bin/activate`), and only in that shell. In a new shell, typing
`autocode` answers `command not found`: activate again, call `.venv/bin/autocode`, add
that `bin` directory to `PATH` in your shell profile, or use the `pipx` install above,
which puts `autocode` on `PATH` for every shell.

### Sanity check

Confirm the install, then check the machine and your project before the first task:

```sh
autocode --version
autocode doctor --workspace /path/to/project
```

`--version` prints the commit when run from a checkout and `commit unknown` from an
installed package. `doctor` names anything missing (Python, psutil, Git, the default
engine, a committed Git project) with the fix next to it, and exits 1 until the machine
is ready. Its `path` row warns, without failing, when a new shell would not find `autocode`
on `PATH`, and names the directory to add or the `pipx` install. It checks the engine a new run would use: OpenCode, or the provider set as the
default below (with no `--engine` and with `--engine opencode` alike), including that it
offers every role's default model; `--engine codex` checks Codex instead.

### Shell completion

`autocode completion bash|zsh|fish` prints a completion script read from the CLI's own
parsers: the commands, every option of the main command and of each subcommand, with
their choices, a subcommand's own commands (`autocode output read`), and file names after an option that takes a path. Load it in the current shell,
or save it where your shell reads completions:

```sh
source <(autocode completion bash)   # or save it as ~/.local/share/bash-completion/completions/autocode
source <(autocode completion zsh)    # after compinit; or save it as _autocode in a directory on $fpath
autocode completion fish | source    # or save it as ~/.config/fish/completions/autocode.fish
```

`capture`, `registry`, `intervention` and `dashboard` complete only `--help`. A saved
script lists the options of the version that wrote it, so write it again after upgrading.

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

Use the whole workflow through `autocode`, or invoke one unit at its saved boundary with `--unit`:

```sh
autocode "Build a greeting CLI" --workspace /path/to/project --chat

# Or invoke individual units at their saved boundaries:
autocode "Build a greeting CLI" --unit autoplanner --workspace /path/to/project --chat
# After approval, this stops before any Builder starts.
# Each unit continues the project's unfinished run (add --run-dir RUN when it has several):
autocode --unit autocode --workspace /path/to/project --no-chat
autocode --unit autoreview --workspace /path/to/project --no-chat
# If review requests rework, diagnose it without launching a Builder:
autocode --unit autoresolver --workspace /path/to/project --no-chat
# Let Autopilot continue through any remaining build/review cycles:
autocode --workspace /path/to/project --no-chat
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

A Kilo provider needs the Kilo CLI on PATH. On Linux, install the pinned release;
a launch there refuses any other version, because its restricted read-only and
planning stages need that release's kernel shell. macOS has no kernel shell, so its
checkers use Kilo's own shell with edits denied and any Kilo release launches:

```sh
npm install -g @kilocode/cli@7.8.3
kilo auth list
```

A saved run keeps the provider it started with, and `--engine codex` runs still
use Codex. `autocode doctor` then checks the default provider instead of OpenCode.
`AUTOCODE_PROVIDER=codex` does not select the Codex engine: it names a provider config
`codex.toml`, and doctor reports it missing. Use `--engine codex` for Codex.
`autocode dashboard --provider <name>` overrides the default for the dashboard; its
model pickers list that tool's models.

See also: [CLI](cli.md) · [Providers](providers.md) · [Models](models.md)
