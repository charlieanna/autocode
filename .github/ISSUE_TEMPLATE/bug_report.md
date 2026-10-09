---
name: Bug report
about: Something AutoCode did that it should not have, or did not do that it should have
title: "Bug: "
labels: bug
---

<!--
Thanks for reporting. The more of this you can fill in, the more likely it gets
fixed. "It got stuck" is hard to act on; "it got stuck at WAITING_FOR_USER
after --answer, state.json attached" is fixable.
Security problems: do NOT file them here. See SECURITY.md.
-->

## What happened

<!-- One or two sentences. What did you see? -->

## What you expected

## How to reproduce

1. Command you ran (full flags):
   ```sh
   autocode "..." --workspace ... --engine ...
   ```
2. What AutoCode printed or showed in the dashboard:
3. The next command (if any):

## Evidence

<!--
Attach or paste, with credentials and private paths removed:
- the run's `state.json` (under `<workspace>/.autocode/runs/<run>/`)
- the last ~50 lines of terminal output
- `autocode --workspace ... --run-dir ... --status` output
-->

## Environment

- AutoCode commit or version (`git rev-parse --short HEAD` or `pip show autocode-supervisor`):
- OS:
- Engine / provider (`--engine`, `--provider`) and models used:
- Python version:

## Is this an approval, evidence or completion problem?

<!--
If AutoCode built something without approval, called work "done" without
evidence, or closed a finding it should not have, say so here. Those are the
project's core promises and get looked at first.
-->
