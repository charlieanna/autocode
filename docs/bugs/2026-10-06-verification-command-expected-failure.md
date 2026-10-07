# A criterion that names a command which must fail can never be validated

Fixed on `claude/elegant-planck-ynb2dq`; found by the live run of `program-notes-cli` for #22 and #23 on 2026-10-06
(Claude models, run directory `20261006T034404Z-program-notes-cli-claude-tiers-45uzedfo`).
The cause is not in the program code: any run whose plan words a criterion this way
meets it.

`autocode_verification_plan.obligations` turns every command in backticks in a
criterion's `verification_method` into an `approved_acceptance` obligation, and clean
replay (`autocode_check_replay`) re-runs each one as a `mandatory_approved_execution`
that must exit 0. A usage-error criterion is naturally written the other way round. In
the live run, the skeleton's S4 said that `python3 -m notes add` (no text) and
`python3 -m notes frobnicate` each exit 2 with one usage line. Both commands did exactly
that. The replay then reported them as failed checks, and a report that cites a failing
check cannot pass.

The Tester's own checks all passed in replay: both unittest runs (5 of 5, including
the usage-error test) and its wrapped `sh -c '...; test $? -eq 2'` checks. Three report
repairs got the identical rejection, the Investigator traced it to the obligation, and
the run stopped at `PAUSED_INVALID_OUTPUT`.

## Fix

The criterion did declare the exit status; `autocode_verification_expectations.assertion_commands`
just could not read it and returned the commands bare. The fix reads one more form, narrowly, and
otherwise changes nothing.

**What HEAD read is unchanged.** When the text after the last quoted snippet is entirely the existing
declaration (an expectation verb, an exit phrase, a status or an N/M vector), the result is exactly
as before, including the "one status (0–255) per executable command" error on a count conflict and
the requirement that the quoted snippets are exactly the commands. So
"run `a` and `b` and assert exit 2." (one status, two commands, nothing after it) still raises.

**The one new form.** Only when that declaration does not match, `assertion_commands` tries this,
and returns the commands bare (as before) if anything does not fit. It never raises:

- the requested commands are the last quoted snippets and one run list: the first follows run,
  execute or invoke, and each gap between two commands is exactly "and", "," or ", and". Quoted
  literals before them (the live "after one `add x`") are allowed;
- the text after the last command is the same declaration, then zero or more clauses, each
  introduced by `,`, `;` or "and". A clause carries no backtick, digit, newline, `!`, `?` or
  sentence stop, no spelled-out number, no status word (exit, return, code, status, succeed,
  success, pass, fail and their forms, nonzero), no exception, ordinal, selection or condition
  (otherwise, unless, except, excluding, aside, save, besides, exception, but, only, alone, just,
  first, second, third, last, final, initial, former, latter, preceding, previous, earlier,
  respectively, other, either, or, if, when, whenever, while, until, provided, given, assuming,
  depending, instead, else, alternatively, possibly, skip), in any case, and names output or files
  (stdout, stderr, output, file, content, message, line, usage, error, notes). Trailing stops and
  one final `)` closing a parenthesis the method opened are stripped by hand: an earlier regex for
  them backtracked for seconds on a long run of spaces;
- one status applies to every command of the run list; N statuses need exactly N commands, each
  0–255 and at most three digits.

The live S4 method now gives two `sh -c` wrappers that assert exit 2; the clauses ("empty stdout
and byte-identical notes file content") stay with the Validator, as unparsed prose always did.

The admission refusal tried earlier on this branch (refusing a Planner draft whose prose states a
nonzero exit) is gone: prose cannot tell whether "exits 2" belongs to the requested command or to
the program the command tests ("run `pytest` and confirm the CLI exits 2"). `commands()`,
`approved_commands()`, `launch_commands()` and `obligations()` raise for nothing they did not raise
for before, so an approved contract replays as it did.

**The Planner is told the form.** `EXAMPLE_CRITERIA_RULE` (`tools/units/autoplanner.py`) now says a
command that must fail (a usage error, a refused input) is checked inside the criterion's test, and
that a verification_method naming such a command ends with "and assert exit N" right after the
commands (N/M, one status per command, for several).

**Limits.** One status applies to the whole run list, so a setup command placed in the same list
as the commands that must fail ("run `notes add x` and `notes add` and assert exit 2, usage on
stderr") is asserted to fail too, and its check fails on correct code: the stall again, for that
wording. The Planner is told to give one status per command. Any other phrasing is read as before: its commands stay bare and must exit 0 at replay,
so the live stall remains possible for them ("run `x`; it must exit non-zero", "confirm it exits
with status 2", "assert exit 2, the second returns 0"). The Planner guidance makes the parseable
form the expected one; it does not enforce it.

**Live run 13 met that limit through a repair task.** The Resolver's repair task for the skeleton
added a validation plan step: "run `python3 -m notes add`, `python3 -m notes bogus`, ... Check that
each exits 2". The Resolver had never been given the Planner's rule. Each command was replayed as an
approved-plan check that must exit 0. The rejection said each "was reported as exit 0", though the
Validator never reported them, so two Validator report repairs failed the same way before the
Investigator found the cause. The run then paused at `PAUSED_INVALID_OUTPUT`
(`20261006T232815Z-program-notes-cli-claude-tiers-t3_lgtl4`).

The parser is unchanged, for the reasons above. Two fixes were made:

- **One rule, given to both plan authors.** `autocode_verification_plan.EXPECTED_FAILURE_RULE`
  states the rule, and the Resolver's prompt now carries it next to its validation_plan retests.
- **The rejection names the plan.** For a check the runner added from the approved verification
  methods or the task's validation plan, the replay rejection says the check is required by the plan
  and that the Validator did not report it. It says a Validator report cannot change it, that the
  plan's author (the Resolver, for a repair task) must reword the step, and it quotes the rule. A
  failing check the Validator itself reported keeps the old message.

Tests:

- `tests/test_check_replay.py`
  (`test_a_planned_command_that_must_fail_is_named_as_the_plans_and_the_rule_says_how`) covers both
  messages.
- `tests/test_resolver_runtime.py` (`ResolverPromptTests`) checks the rule in the Resolver's prompt.

Tests in `tests/test_verification_plan.py`: the live S4 method verbatim; the forms that are wrapped;
each rule leaving the commands bare without raising (no run verb, another gap, a literal after the
commands, a status count or value that does not fit, text not introduced by a separator, a clause
naming nothing about output, an unopened `)`, a backtick, a digit, a sentence stop, `!`, `?` or
newline, a status of thousands of digits, prose between the commands and the status, a denied word
in capitals, and every denied word); a long run of spaces read at once; every probe method from this fix's skeptic reviews compared with the
declaration and function at 6cbf805 (the same result, or the commands HEAD returned bare now
wrapped, never a new error); approved contracts over those methods; the Planner sentence; and a
clean replay of the live method through `autocode_check_replay` that passes for a CLI exiting 2
and fails for one exiting 0. Replay of wrapped vectors was already covered by
`test_approved_negative_cli_probes_enforce_the_declared_exit_codes` in `tests/test_check_replay.py`.

## Options considered

- Leave out of the obligations a backticked command the criterion says must fail
  (exit non-zero, print usage), or record the exit status it must have.
- Have the Plan Reviewer refuse a verification method that names a bare command
  expected to fail, and ask for a test or a wrapped check instead.
