---
name: autocode
description: Hand a coding task, or a change you already made, to AutoCode in this repository and report what it needs and what it verified. Use when the person asks to run something with AutoCode, to have AutoCode plan and build a change, or to verify a change with AutoCode's independent review. AutoCode plans, builds and checks; the person approves its plan and answers its questions.
---

# AutoCode

AutoCode runs a coding task in this repository through planning, the person's
plan approval, building and independent checks, and records the evidence. Your
part: start it when the person asks, show them clearly where it stands at every
stop, pass on their decisions exactly, and report its evidence. AutoCode's
decisions are the person's, never yours.

Every step is one command:

```sh
STEP="autocode agent-step"
```

If `autocode` is not found, tell the person to install it once
(`pipx install autocode-supervisor`, then `autocode setup`) and stop. If
`autocode agent-step` is refused with "unrecognized arguments", their AutoCode is
older than this skill: ask them to update it (`pipx upgrade autocode-supervisor`,
or `brew upgrade autocode`) and stop. If
`AUTOCODE_PYTHON` is set, use `STEP="$AUTOCODE_PYTHON -m autocode_cli.autocode_agent_step"`
instead.

Each step prints one JSON summary of the run: `run_dir`, `working`, `status`,
`done`, `pause_category_label`, `needs`, `next_command`, `progress`,
`explanation`, and `plan` while a plan waits for approval. A step that is
refused exits 1 and says why on stderr. Run steps from the repository root, or
add `--workspace PATH`. When the workspace has more than one run, add
`--run-dir RUN` with the `run_dir` the start printed.

## Start

Start only when the person asked for AutoCode: it uses their model account.
Pass their request in their own words. Add no flags: AutoCode uses the account
and models the person chose in `autocode setup`. Add engine or model flags after
`--` only when the person names them (for example `-- --engine codex`).

```sh
$STEP start "THE PERSON'S REQUEST, IN THEIR WORDS"
```

If the start is refused, show the person the reason and suggest `autocode doctor`,
which names the fix for each problem.

The step returns at once: the run works in the background, and `working` is
true until it stops. Tell the person in one line that AutoCode has started and
is gathering requirements. Then wait for the stop with a limit shorter than your
own command time limit, and repeat while `working` is still true:

```sh
$STEP status --wait 50
```

While you wait, report `progress` only when it changes (for example
"Planner working"), in one short line. Every advancing step (`approve`,
`revise`, `answer`, `continue`, `follow-up`) also returns at once and takes the
same `--wait SECONDS`. While `working` is true there is nothing to decide yet,
and a step that would advance the run is refused.

## At every stop

A stop is a summary with `working` false. Say in plain words what AutoCode needs
(`pause_category_label`, `explanation`). Then, by `needs.kind`:

- `answer`: show each question in `needs.questions` as a short numbered list of
  its `options`, marking the `proposed_default` as recommended, and say they can
  also answer in their own words. Do not choose for them. Pass on the person's
  answer to one question, with `needs.resolver_token` from the summary they saw:

  ```sh
  AUTOCODE_RESOLVER_TOKEN=TOKEN $STEP answer QUESTION_ID "THEIR ANSWER" --token -
  ```

  The questions left come back under a new token; show them the same way.
- `approve_plan`: show the person `plan` as it is (it is already written for
  them), then ask: approve it, or say what to change. Approve only when the
  person says to approve this plan, with `needs.token` from the summary they read:

  ```sh
  AUTOCODE_APPROVE_GOAL_TOKEN=TOKEN $STEP approve -
  ```

  If they want changes instead, pass on their words; AutoCode drafts a revised
  plan and shows it for approval under a new token:

  ```sh
  $STEP revise "WHAT THEY WANT CHANGED, IN THEIR WORDS"
  ```

- `continue`: nothing is needed from anyone:

  ```sh
  $STEP continue
  ```

- Anything else (a review, a pause, a recovery, a budget): it is the person's.
  Explain it in plain words, show them `next_command` as the command they can
  run in a terminal, and stop.

A refused step changes nothing. If AutoCode rejects a token, read `$STEP status`
again and show the person the current stop.

## When it is done

When `done` is true, report the evidence:

```sh
$STEP evidence --markdown
```

Summarize in a few lines: what was built, each acceptance criterion and how it
was checked, any findings, and anything not verified. The change is left in this
workspace as uncommitted edits (`git status` shows them; `.autocode/` holds the
run's records and is not part of the change). Tell the person to read the diff
before committing: the evidence is not proof.

If the person then asks for another change to the same work, send it as a
follow-up; it is planned and approved like the first request, on the same branch:

```sh
$STEP follow-up "THEIR NEXT REQUEST"
```

## Verify a change you made

To have AutoCode judge your own uncommitted change, start a review run and
describe the change and what it should do:

```sh
$STEP start "Review the uncommitted change in this workspace (git diff HEAD). It should ..." --workflow review
```

A review takes the same path as every task: its plan needs the person's approval
like any other. Report `$STEP evidence --markdown` when it completes.

## Never

- approve, revise, answer, continue or follow up on your own judgment, or pick an
  option for the person;
- edit files in the workspace while a run is working in it;
- run `autocode` commands that make the person's decisions (`--approve-goal`,
  `--answer`, `--resume-paused`, ...) unless the person asked you to run that exact command.
