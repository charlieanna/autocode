
APPROVED DESIGN: approved_design in the handoff data is a design the user has already approved, checked
against this repository with no conflicts. It is a constraint, not a suggestion: plan exactly what it
specifies (module and file layout, names, signatures, rules, rejected alternatives). Do not redesign it,
do not revisit its rejected alternatives, and do not ask the user about decisions it already makes; ask
only about something it genuinely leaves open. Trace each of its constraints to a milestone.
Write no criterion, example or test case whose expected result contradicts it: a value, an error, an order
or a call count the design rules out. The Plan Reviewer checks each one against approved_design and raises
any that contradicts it as a blocking concern before approval: once the plan is approved, changing such a
criterion needs the user, so the build would have to stop and ask.
