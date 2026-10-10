# Human requirements in bug planning

A reproduced bug can go directly from Investigator to Planner, carrying the
original task and a separate diagnosis. The planning prompt previously called
that model-written diagnosis “the requirements.” Its general policy already
required preserving the human task, so the two instructions assigned conflicting
authority.

The diagnosis now supplies technical evidence. The task and saved human answers
and events define the requested outcome. The plan must preserve applicable human
obligations omitted by the diagnosis and raise a blocking concern when the
diagnosis proposes conflicting behavior. The Plan Reviewer checks the comparison
independently. Existing regression proof, exact invariants, restore and preserve
test distinctions, and plan approval continue to apply.

For example, a diagnosis of duplicate renewals may describe a retry correction
without mentioning the user's requirement to retain callback behavior. Planning
must carry that requirement into the correction. A proposed callback removal
must be reviewed as a conflict with the request.

The prompt tests inspect the rendered instructions and separate task/diagnosis
handoff at all four planning stages. They establish the prompt contract and task
retention; they do not prove model compliance. This finding does not establish
the cause of an Arena failure.

Live qualification exposed another handoff gap: an approved technical approach
listed restoration tests while omitting two retained preservation cases. Planning
now reconciles every retained case ID with implementation and validation steps,
keeps existing tests intact, and requires explicit named assertions for each guard.
The earlier blocked run remains a non-pass. A fresh real-model greeting bug-fix
scenario reached completion and passed all ten independent checks after this
addition; its Planner and Plan Reviewer prompts contained the revised policy.
This does not qualify an original Arena challenge.
