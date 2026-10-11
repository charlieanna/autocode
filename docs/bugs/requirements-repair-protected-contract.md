# Requirements repair needs the approved contract

An Arena follow-up reached Requirements report repair after its draft reworded
approved constraints. The discovery repair handoff contained a null
`protected_contract`, although the revision guard correctly required the
original wording. Repairing the clause named in one error could therefore leave
another protected clause missing.

The report-repair context now supplies the existing protected contract snapshot
for discovery, revision and finalization. It contains every protected behavior,
scope exclusion, constraint, failure case, criterion and permission boundary,
with deep copies. Requirements gathering and execution-report repairs do not
receive that snapshot. The contract revision guard remains unchanged: supplying
context neither approves a revision nor permits dropped tests or public APIs.

Regression coverage checks both test-preservation and public-API constraints,
complete field preservation, copy isolation, stage boundaries and rejection of
either omitted clause. The original Arena failure remains a failed result;
qualification of this repair requires a separate run through the changed path.

A separate hybrid qualification scripted the ordinary greeting workflow, then
omitted only two approved constraints from discovery on a follow-up. The actual
controller supplied the full snapshot to one real-model repair. All six protected
fields were preserved, the implementation stayed unchanged, and the public run
returned to its unapproved-plan boundary. This is repair-path evidence, not an
original Arena verdict.
