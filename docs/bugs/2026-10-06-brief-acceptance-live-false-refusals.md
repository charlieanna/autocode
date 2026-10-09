# Original-brief proof refused correct live deliveries (#452, after #522)

#522 added the original-brief output check and noted that live qualification
was still owed. The first two live runs were both on master `d6aded9`, with
scenario `greenfield-todo-cli` and profile `claude-tiers`. Both delivered
correct code: the scenario oracle passed 10/10. Both ended `HONEST_BLOCKER`, waiting for the user with
`stop_reason` "invalid contract or declarative input". There were two
independent causes. The evidence below comes from ignored local run
directories, so the numbers are copied here.

## A. A correct multi-item list failed the format check

Run `ewqn70hi` (`20261006-201055-…-fe4abfc1`, 343 s). The Plan Reviewer bound the
brief's `` `todo.py list` prints every to-do as `ID TEXT [open|done]` one per
line `` to these steps: `add "buy milk"`, `add "walk dog"`, `complete 1`, `list`.
TEXT was bound to step 0 and ID to step 2, which sealed the item pattern
`1\ buy\ milk\ \[(?:open|done)\]`. The program printed
`1 buy milk [done]\n2 walk dog [open]\n`, exactly what the brief asks for.

The check had only one rule for the whole output: strip one final newline,
then require one line that fullmatches the item pattern. Any listing with
more than one item therefore failed, in the runner (`_RUNNER`) and again in
completion's recheck (`autocode_brief_evidence._observation`). The receipt
(`check-replay/validator-01-666b8a00…/brief-acceptance/a7096f02…/summary.json`)
recorded the runner's reason, "CLI output differs from the original brief
format". The rejected Tester report showed only "mandatory original-brief
invocation failed or timed out". All of #522's tests and the fake Plan
Reviewer set up exactly one item, so none of them caught this.

The fix applies the declared format to every line of a `one per line` listing:

- `autocode_brief_acceptance.line_pattern` derives each line's pattern from the
  sealed manifest. Literal bytes and the finite `[open|done]` alternatives
  stay exact. ID stays opaque. Any other placeholder (TEXT) may only be a
  value that the observation's own steps passed in, up to the listing.
  It is never stored, so manifests, their hashes and `VERSION` are unchanged.
- `output_reason` is the rule completion rechecks with. The contained runner
  repeats it, since the runner cannot import AutoCode, and a test checks
  that both give the same verdict. Output may have one optional final LF or
  CRLF. Every line must fullmatch the line pattern or be the bound item, at
  least one line must be the bound item exactly, and line endings may not be
  mixed: CRLF lines pass with or without a final CRLF, as LF lines do without
  a final LF. The bound item alone therefore still passes whatever its ID looks
  like (an ID argument with a space is not one opaque word). Headers, blank
  lines, extra terminators, trailing spaces, unbracketed or misspelled
  statuses, a changed ID on the bound item and items nobody added all still
  fail. Only the bound item's ID is checked: every other line's ID is opaque,
  so a renumbered (`02`), repeated or literal `ID` there passes, and so does
  the bound item listed a second time with the other status.
- The runner's command now carries the line pattern, so receipts recorded
  under the old rule no longer match `ready()`. A fresh replay is required.
  The CRLF rule above came after #617 and changes the runner's command again,
  so a receipt recorded under #617's runner also needs a fresh replay. Until
  the run replays, completion refuses with "Original-brief output
  observations need fresh, intact runner proof for every declared format."
  That is expected and recoverable; manifests and their hashes are unchanged.
- A failed replay now reports the runner's own reason and the step it
  concerns instead of "failed or timed out": what the listing printed, the
  exit code and stderr tail of a step that exited nonzero, or the step that
  did not finish.

This check still does not prove that every item appears, or appears once. A
listing that leaves out or repeats other to-dos passes if the bound item is
present. Refusing a repeated line would also refuse a correct listing whose
format has no unique field, such as two items added with the same text under
a format without ID. Nor does it prove that an item is gone. It checks format
only, so a listing that still shows a removed or filtered-out to-do passes.
Master before #617 caught that case by accident: it required the whole
output to be the bound item. With `add "buy milk"`, `add "walk dog"`,
`remove 1`, `list` and TEXT bound to "walk dog", a product whose `remove`
does nothing prints `1 buy milk [open]\n2 walk dog [open]\n`. Master
`0591e76` refuses it; this rule passes it, as it must pass any correct
listing of two items. A Plan Reviewer cannot yet ask for an exact listing or
for an item to be absent; that needs a new observation kind (a manifest
`VERSION` change), tracked in #644.
A TEXT line must carry a value that a step passed for TEXT itself, so
a value passed under another placeholder name (say a `rename ID NEW_TEXT`
command) is refused, as it already was for the bound item.

## B. Every Resolver request on a brief-bearing contract escalated

Run `jb1acns9` (`20261006-201056-…-410957d5`, 294 s). The Builder report cited
no evidence (`evidence_refs: []`). That is an ordinary report rejection
("Evidence references are empty"), and a Builder report repair was queued
with 0 attempts used. Before the repair ran, AutoResolver's runner-owned
admission returned `escalate` with "invalid contract or declarative input".
The run then held and waited for the user.

`autocode_resolver._validate_body` accepted only the original body key sets.
#522 put the runner-owned `brief_acceptance` record into the approved body,
and #524 did the same with `risk_acceptance`. Once a contract carried either
record, every Resolver request on it failed validation: report repair,
blocked validation, diagnosis admission and replan. Run A ended the same way
after its false Tester rejection. The fake `broken/unbracketed-status` control
also reached `HONEST_BLOCKER` through this fallback, not through a designed
refusal.

#571 (merged after `d6aded9`, found by a separate live run; see
`2026-10-06-resolver-rejected-brief-acceptance.md`) made both keys known, so
master no longer escalates this. On top of it, the Resolver now treats both
records as runner-owned (`_RUNNER_OWNED`): allowed in the body, but each must
be an object. They stay out of `ALLOWED_FIELDS`, so a replan that changes one
is a "protected contract change", and a replan that drops or retypes one is an
"invalid candidate body" (after #571 alone, a retyped record was accepted in
an approved body and only refused as a protected change in a replan). A unit
test checks that every optional `autocode_goals.BODY_SCHEMA` field is
accepted, so the next body field cannot repeat this, and a CLI-level control
replays run B's shape end to end.

## Controls

- `tests.test_brief_acceptance` runs the live multi-item observation against
  an independent correct product (PASS) and its unbracketed mutant (FAIL).
  It checks 20 listings through `output_reason`, and runs one of each kind
  through the contained runner to confirm both give the same verdict, and
  keeps a bound item alone valid when its ID has a space.
- `tests.test_brief_evidence` checks a multi-item receipt, four forged
  listings after rehashing, and which step a failed replay names.
- `tests.test_resolver_unit` covers the runner-owned records.
- The fake Plan Reviewer now adds a second item before listing, as the live
  reviewers did. `tests.test_brief_cli` asserts the two-line listing, and its
  new `SCENARIO_FAKE_BUILDER_NO_EVIDENCE` control reproduces run B (it
  escalated on `d6aded9`) and completes through the usual report repair.
- The catalog's `broken/unbracketed-status` solution is still refused. The
  run is `HONEST_BLOCKER`, the oracle fails the same three checks, and the
  brief receipt keeps the format failure. Without the Resolver fallback, the
  Tester's rejected reports go through report repair and then stop as
  invalid output. Sending a failed brief replay to Builder rework instead is
  left for later.

## Still open: a later command's format can be attributed to an earlier one

Found while reviewing this fix; #522's declaration grammar is the same on
master `0591e76`, before #617. A command's clause runs to the next `;` or line break, so a
`prints ... as FORMAT ... one per line` that follows a later command in the
same clause is also attributed to the earlier command. The brief
`` `todo.py add TEXT` adds a to-do and exits 0. `todo.py list` prints every
to-do as `ID TEXT [open|done]`, one per line, and exits 0. `` therefore
declares that `add TEXT` prints the listing too, and a correct product whose
`add` prints nothing is refused, before #617 and after it. A brief that ends
each command with a full stop, a natural style, is enough. When only the
per-line words come later (`` `todo.py add TEXT` prints the new to-do as
`ID TEXT [open|done]` and exits 0, and `todo.py list` prints every to-do, one
per line, and exits 0. ``), `add` is checked as a listing. Master `0591e76`
refused an `add` that printed the whole list, `1 buy milk [open]\n2 walk dog
[open]\n`, because it allowed one line only; the per-line rule passes it.
Ending a clause at the next command changes which declarations such a brief
has, and so the sealed manifests of its runs already under way, so it needs
its own change with a plan for those manifests, tracked in #645. The catalog brief separates its commands with semicolons and is not
affected.
