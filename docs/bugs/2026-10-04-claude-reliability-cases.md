# The three RELIABILITY.md live cases on Claude models (2026-10-04)

Run 2026-10-04 from a clean worktree of master `e8366ad` (after #371–#373), profile
`claude-tiers` (`examples/claude-provider`: Sonnet for requirements, planning and validation; Opus
for plan review, completion review, investigation and AutoResolver; Haiku as the Builder), three
runs of each case, 90-minute budgets. Evidence was kept outside the checkout and not committed.

| Case | Scenario | Runs | Verdicts | Oracle | Wall time | Cost |
| --- | --- | --- | --- | --- | --- | --- |
| Small new application | greenfield-todo-cli | 3 | 2 PASS, 1 HONEST_BLOCKER | 10/10 each | 277–774 s | $12.36 |
| Feature in an existing project | feature-timesheet-by-project | 3 | 3 PASS | 6/6 each | 254–356 s | $7.24 |
| Bug fix | bugfix-iso-weeks | 3 | 3 PASS | 5/5 each | 297–312 s | $7.24 |

8 of 9 runs passed, $26.84 in all. No FALSE_COMPLETE: every deliverable was correct, including the
blocked run's. Two findings, both in prompts rather than in the code the models wrote.

## Finding 1: the Validator was told to cite event IDs that its provider refuses

The Validator's shared instructions (`autocode_support.py`) said to list every check "with
evidence_ref 'event:' and exit_code null", and only afterwards "for capture receipts follow the
execution engine's evidence instructions". The Claude provider writes its report to a file
(`output_mode = report_file`), and for such a provider the runner accepts only capture receipts:
an `event:` reference is rejected with "Report-file providers require capture receipts". So the
generic line contradicted the provider's rule, and the generic line came first.

Eight of the nine runs had at least one Validator report sent back for this. A repair usually
fixed it at the cost of one more Validator call. In the blocked greenfield run, the Validator kept
citing event IDs, AutoResolver's attempts ran out, and the run stopped for a person with correct
code in the workspace.

**Fixed:** the line now says to cite each check's receipt path when the engine's evidence
instructions ask for capture receipts, and to use `event:` only otherwise. A test in
`tests/test_receipts.py` requires every instruction that mentions `evidence_ref 'event:'` to name
the receipt alternative in the same sentence.

## Finding 2: every bug-fix draft first dropped a literal from the bug report

The bug report quotes the wrong output `2024-W54` in backticks. Since #371 the runner sends back a
planner draft that drops a literal the user wrote in backticks
(`tools/autocode_brief_literals.py`). All three bug-fix drafts left it out (it is the output the
fix must stop producing, so it belongs in a failure case or a regression criterion), and each
took one planner repair, about $0.20 each. The check worked as designed; the planner just did not
know about it in advance.

**Fixed:** the drafting stages' prompt now names the literals the runner will check, with the
hint that a wrong output quoted in a bug report belongs in a failure case or regression criterion
(`brief_literals.rule`, added by `units/autoplanner.py`; tests in `tests/test_brief_literals.py`).

## Verification (to-do case, three runs, on the fix)

Same profile and budgets, the fix's commit `b33ced2`, $12.07 in all:

| Run | Verdict | Oracle | Wall time | Cost | Validator reports sent back |
| --- | --- | --- | --- | --- | --- |
| 1 | PASS | 10/10 | 429 s | $2.88 | none |
| 2 | HONEST_BLOCKER | 10/10 | 747 s | $4.69 | 2 |
| 3 | PASS | 10/10 | 658 s | $4.50 | 2 |

No report was sent back for an event ID; it had happened in 8 of 9 runs before the fix. The four
reports that were sent back had other causes, and the stop has a new one. All three are still open:

1. **A note stuck on a plan check makes it fail every replay (the stop).** After the first review,
   AutoResolver wrote the task's validation plan entry as `python3 -m unittest test_todo -v (all 10
   pass)`. `autocode_verification_plan.commands` treats plain text as a command unless it reads as
   prose, and a trailing parenthesis is not in its prose patterns, so the check replay ran the whole
   line in `/bin/sh` and got `Syntax error: "(" unexpected` (exit 2) on every Validator report. The
   Investigator named the cause and stopped the run, since no Validator report could pass. This is
   the same class of error as the earlier `passes;` and `from repo root:` sentences that the prose
   patterns already catch.
2. **Probe scripts under `.autocode/` cited as checks.** The Validator is told to keep scratch
   files under `.autocode/` (`VALIDATOR_NOTE`), but the check replay runs in a clean copy of the
   source without `.autocode/`, so a cited check such as `sh .autocode/probe.sh` fails there. It
   happened once in each of runs 2 and 3; each repair recovered. `RUN_FILES_HINT` already explains
   the clean copy for checks that read run files; the instruction to keep scratch files there says
   nothing about cited checks.
3. **A multi-line `python3 -c` check whose command text differs from its receipt.** The check's
   `command_text` was not copied verbatim from the receipt (the shell quoting differs). Once, in
   run 2; the repair recovered.
