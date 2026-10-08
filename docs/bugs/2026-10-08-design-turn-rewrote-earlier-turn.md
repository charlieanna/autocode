# A design turn rewrote what the discuss turn wrote, and a build asked about a contradicting criterion (#664)

Found in live runs of `discuss-then-design-then-build` on 2026-10-07 (`claude-tiers`).

## The design turn changed the decision record

In 2 of 63 runs (`s6hy5rmb`, `wqqsz0uk`) the design turn's plan put
`docs/decisions/metadata-cache.json`, the decision record the discuss turn had written, in its
milestone's `affected_paths`, with a criterion on its keys. Its Builder rewrote the record, and the
scenario's oracle failed `design_turn_changed_only_its_report`. Nothing in the runner stopped it: a
Builder may change whatever its task's `affected_paths` name.

**Fix.** A design job proposing a design writes its design. `autocode_workflows.design_rewrites` lists
each file an earlier turn of the conversation wrote (`turns[].previous.wrote`) that the plan would let
the Builder change: named in a milestone's or the initial task's `affected_paths`, or under a planned
folder. `autocode_goal_lifecycle.install_draft` refuses such a draft, so its author gets a report repair,
unless the user's newest message names the file (its path or file name). The design deliverables rule
tells the Planner the same. A build turn, a design review and a single-turn run are not checked.

## The build turn asked about a criterion that contradicted the approved design

In 2 of about 20 runs (`yldlkbq6`, `g43uazf8`) the build turn's approved plan had a criterion whose
expected result the approved design ruled out (C7 expected `metadata('com')` to return another TLD's
value, where the design says a missing entry raises). Changing an approved criterion needs the user, so
the build asked, and the scenario allows no question in that turn.

**Fix.** The approved-design rule, which every planning stage of a build of an approved design gets, now
says to write no criterion, example or test case that contradicts the design, and asks the Plan Reviewer
to check each one against `approved_design` and raise any contradiction as a blocking concern before
approval. This is prompt guidance: whether a reviewer catches every contradiction is up to the model.
