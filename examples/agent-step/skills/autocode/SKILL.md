---
name: autocode
description: Hand a coding task, or a change you already made, to AutoCode in this repository and report what it needs and what it verified. Use when the person asks to run something with AutoCode, to have AutoCode plan and build a change, or to verify a change with AutoCode's independent review. AutoCode plans, builds and checks; the person approves its plan and answers its questions.
---

# AutoCode

AutoCode runs a coding task in this repository through planning, the person's
plan approval, building and independent checks, and records the evidence. Your
part: start it when the person asks, tell them where it stands at every stop,
pass on their decisions exactly, and report its evidence. AutoCode's decisions
are the person's, never yours.

Run every step with the Python that has AutoCode installed:

```sh
STEP="${AUTOCODE_PYTHON:-python3} -m autocode_cli.autocode_agent_step"
```

Each step prints one JSON summary of the run: `run_dir`, `status`, `done`,
`pause_category_label`, `needs`, `next_command`, `progress`, `explanation`, and
`plan` while a plan waits for approval. A step that is refused exits 1 and says
why on stderr. Run steps from the repository root, or add `--workspace PATH`.
When the workspace has more than one run, add `--run-dir RUN` with the
`run_dir` the start printed.

## Start

Start only when the person asked for AutoCode: a live provider spends money.
Use the engine and model flags the person chose; ask if they did not say.

```sh
$STEP start "THE PERSON'S REQUEST, IN THEIR WORDS" -- --engine claude
```

The step returns when the run first needs the person or completes. That can
take as long as the work does. To look again later:

```sh
$STEP status
```

## At every stop

Tell the person `pause_category_label`, `progress` and `explanation`, and show
`next_command`: it is the command they can run themselves. Then, by
`needs.kind`:

- `answer`: show each question in `needs.questions` with its `options` and
  `proposed_default`. Do not choose for them. Pass on the person's answer to one
  question, with `needs.resolver_token` from the summary they saw:

  ```sh
  AUTOCODE_RESOLVER_TOKEN=TOKEN $STEP answer QUESTION_ID "THEIR ANSWER" --token -
  ```

  The questions left come back under a new token; show them the same way.
- `approve_plan`: show the person `plan` as it is. Approve only when the person
  says to approve this plan, with `needs.token` from the summary they read:

  ```sh
  AUTOCODE_APPROVE_GOAL_TOKEN=TOKEN $STEP approve -
  ```

  If they want changes instead, do not approve. They send the changes with
  `autocode --workspace PATH --run-dir RUN --feedback "WHAT TO CHANGE"`, and then
  `$STEP continue` drafts the revised plan for their approval under a new token.
- `continue`: nothing is needed from anyone:

  ```sh
  $STEP continue
  ```

- Anything else (a review, a pause, a recovery, a budget): it is the person's.
  Show them `next_command` and stop.

A refused step changes nothing. If AutoCode rejects a token, read `$STEP status`
again and show the person the current stop.

## When it is done

When `done` is true, report the evidence:

```sh
$STEP evidence --markdown
```

Summarize the acceptance criteria and their results, findings, and anything not
verified. Completion is evidence, not proof: the person still reviews the diff.

## Verify a change you made

To have AutoCode's independent Code Reviewer judge your own uncommitted change,
start a review run and describe the change and what it should do:

```sh
$STEP start "Review the uncommitted change in this workspace (git diff HEAD). It should ..." --workflow review -- --engine claude
```

A review changes nothing outside `review/` and needs no plan approval. Report
`$STEP evidence --markdown` when it completes. To check a change against
written acceptance criteria with runner-executed checks, the person can use
`autocode verify` instead (docs/verify.md).

## Never

- approve, answer or continue on your own judgment, or pick an option for the person;
- edit files in the workspace while a run is working in it;
- run `autocode` commands that make the person's decisions (`--approve-goal`,
  `--answer`, `--resume-paused`, ...) unless the person asked you to run that exact command.
