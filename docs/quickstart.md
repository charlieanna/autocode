# Quickstart

[← Back to README](../README.md)

From nothing to a first reviewed change in about ten minutes. Each step links to the
full guide when you need more.

You need macOS or Linux (Windows through WSL), Python 3.11 or newer, Git,
[pipx](https://pipx.pypa.io/) and Node.js with npm. One coding-agent account is enough:
a Claude subscription, a ChatGPT login, or an API key you put in OpenCode or Kilo.

## 1. Install the CLI

```sh
pipx install autocode-supervisor
pipx ensurepath          # then open a new terminal
autocode --version
autocode demo            # offline tour: no sign-in, no model spend
```

Or `brew install charlieanna/tap/autocode`. Details and other hosts:
[Install and run](install.md).

## 2. Connect an account and choose models

```sh
autocode setup
```

Setup finds Claude Code, Codex, OpenCode and Kilo, offers to install OpenCode or Kilo when
you have neither, signs in what is not signed in yet, and asks which model and reasoning
level each stage runs. Every task uses that choice; change it later with
`autocode stages choose`. If Claude Code or Codex is already signed in, setup reuses it.
See [Providers](providers.md) and [Models](models.md).

## 3. Check a project

AutoCode works in a Git repository. Try it on a fresh one first:

```sh
mkdir hello-autocode && cd hello-autocode
git init
autocode doctor          # ends with "ready for a first task"
```

Each ✖ line names its fix.

## 4. Pick where you talk to AutoCode

Use whichever you already work in. Each one shows you AutoCode's questions and plan, and
nothing is built before you approve the plan.

- **Terminal:** `autocode "your request"` opens a chat in the terminal.
- **Claude Code** (terminal or desktop app): add the plugin once with
  `/plugin install autocode --marketplace charlieanna/autocode`, then ask
  *"Use AutoCode to …"*. Claude shows you each question and the plan and passes on only
  your answers, plan changes and approval. From Claude Code or Codex the change is left
  in your folder as uncommitted edits, as the agent's own edits would be.
- **Codex:** add the MCP server once with `codex mcp add autocode -- autocode mcp`, then
  ask Codex to use the autocode tools.
- **VS Code:** the extension below.

More: [AutoCode inside your coding agent](agent-step.md).

### VS Code extension

Install **AutoCode Task Chat** from the Marketplace, or from a terminal:

```sh
code --install-extension ankurkothari.autocode-task-chat
```

Open the project folder in VS Code and click the AutoCode icon in the Activity Bar. The
extension runs the `autocode` on your `PATH`; if it lives somewhere else, set
`autocode.cliPath`. Prefer the terminal? Everything below works the same with
`autocode "your request"`. More: [VS Code extension](vscode.md).

## 5. Ask for a change

Type a request in the chat, for example:

> Write hello.py that prints a greeting, with a unittest test for it.

Requirements talks with you until it understands what you want, then shows its
understanding. Choose **Approve understanding and prepare plan**. Nothing is changed yet.

## 6. Approve the plan

The Planner drafts a plan and the Plan Reviewer checks it. The chat shows what will be
delivered and how each item will be checked. Send feedback to revise it, or choose
**Approve and build**. Nothing is built before this.

## 7. Get the result

The Builder writes the change, then the Tester and the Completion Reviewer check it
independently. AutoCode reports done only with evidence, such as a test that fails without the
change and passes with it. By default each chat works on its own branch in a worktree, so
your folder is untouched. **Show changes** shows the whole diff; merge the
`autocode/…` branch when you are happy with it.

## If something stops

A run that needs you shows ● on its tab and says what it needs. Most stops are
answered in the chat. [Understanding a stop](cli.md#understanding-a-stop) explains
each one, and `autocode doctor` re-checks the host.

Next: [Workflow](workflow.md) explains each stage, and
[Completion evidence](execution.md#completion-gate) explains what "done" proves.
