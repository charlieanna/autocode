# The three RELIABILITY.md live cases on the default profile (2026-10-07)

Run 2026-10-07 from a clean detached worktree of master `4e7e6895` (after #648 and #650), profile
`glm53-openai` through OpenCode 1.18.33: GLM-5.3 on the Z.AI coding plan for Requirements, the Planner,
the Tester and the Completion Reviewer; GPT-6 Sol on OpenCode's ChatGPT login for the Plan Reviewer,
the Builder and the bug Investigator; GPT-6 Astra for the Resolver. These are the routes `autocode
doctor` reports as ready on this machine, so this is what a new user gets. One run of each case,
90-minute budgets, the three started within five seconds of each other. Both plans report zero cost,
so the dollar figures are the harness's estimates from token counts. Evidence stayed in the worktree's
`.scenario-runs/` and was not committed.

| Case | Scenario | Verdict at the first stop | Oracle | Wall time | Model stages | Est. cost |
| --- | --- | --- | --- | --- | --- | --- |
| Bug fix | bugfix-iso-weeks | PASS (`TASK_COMPLETE`) | 5/5 | 1,067 s | 7, no repair, no rework | $0.30 |
| Small new application | greenfield-todo-cli | PASS (`TASK_COMPLETE`) | 10/10 | 2,111 s | 12, one planning report repair | $0.80 |
| Feature in an existing project | feature-timesheet-by-project | HONEST_BLOCKER (`WAITING_FOR_USER`) | 5/6 | 1,375 s | 9, one completion-review report repair | $0.37 |

All three deliveries were correct. No run completed falsely. No Validator report was sent back in any
run (the 2026-10-04 Claude sweep had one sent back in 8 of 9 runs before its fix). The feature case
stopped on a real contradiction in its own approved contract and finished after a person corrected it
(below): 2,321 s more, oracle 6/6, the project's 23 tests passing, contract revision 6.

## The feature case: an honest stop on a miscounted example, then no named way forward

The brief gives a layout rule and no sample output: "Rows keep today's layout: labels left-aligned to
the longest label and hours right-aligned in the same `0.00h` column." The Requirements stage derived
byte-exact worked examples from it, and AC1 and AC3 spell the by-project `total` row with ten spaces
(`total          15.50h`). With 13-character labels the rule, and the renderer the brief says must not
change, give twelve. The Plan Reviewer approved revision 3 with the rule and the wrong example in one
sentence. Every later stage was honest: the Builder implemented the rule and wrote the tests from the
literals, the runner's regression proof failed on exactly those two tests, the Completion Reviewer
escalated, and the Resolver read `timesheet/report.py`, proved the contradiction and asked one question
with a recommended option: approve a revision correcting only the two strings. Filed as #676; the only
check on a derived example is the Plan Reviewer, and `autocode_brief_literals` cannot help because the
literal is not in the brief.

Acting as the person who agrees with that recommendation:

1. `--status` showed `needs.kind` `answer`, `resolver_scope` `blocker`, `action` null. The view's own
   contract documents `answer` as `--answer QUESTION_ID=TEXT --resolver-token TOKEN`.
2. That command was rejected: "Use --resolver-response for this operational request; it is not a
   requirements answer".
3. `--resolver-response provide_information` with the approval as the message was accepted. The run
   went to `PAUSED_RESOLVER` ("AutoResolver received the human response; no execution, approval or
   additional allowance was authorized"), `needs` became `resume` with no `action`, and
   `information_review` stayed null.
4. `--resume-paused` held: "AutoResolver retained the human guidance. No new execution allowance or
   changed cause was established; the run remains paused without repeating the same request." This is
   the open item in `2026-10-06-operational-information-reevaluation.md`: a `blocker` answer is never
   re-evaluated.
5. The path that worked was found in the code, not in anything the run said: `--edit-goal body.json`
   with AC1 and AC3 corrected installed draft r4; the Plan Reviewer ran; `AWAITING_GOAL_APPROVAL` at r6;
   `--approve-goal`; the Builder corrected the tests and the README sample; `TASK_COMPLETE`.

Filed as #675. The Resolver did its job and the product then lost the person's decision. Two of the
three interventions here were repairs to AutoCode's guidance (steps 2 and 4); only the decision itself
(step 5) was a product decision a person has to make.

## Still unconfirmed live

The 2026-10-04 fix that leaves a plan check with a parenthesized note to the Validator
(`verification_plan.NOTE`) was not exercised: none of the three runs wrote such a check. The two
findings listed as open there (a probe script under `.autocode/` cited as a check, a multi-line
`python3 -c` check restated with different quoting) did not recur; the common prompt now says never to
cite a path under `.autocode/` as a check, and the Validator instructions say to copy `command_text`
from the receipt.

## What this says

On today's default profile the three cases deliver correct code, and two of three finish with no
person. The third needed a person for a decision that is legitimately a person's, and then needed that
person to read the source to find the command that applies the decision. Closing #675 removes the
second part; #676 would have removed the stop. One run per case is a sample, not a rate.
