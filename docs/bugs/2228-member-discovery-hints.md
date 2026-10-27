# Model-discovered work remains an investigation hint (#2228)

The retained live history handoff in #2208 quoted a coordinator-generated
conditional as a user requirement: apply tax here unless Q1 assigns it to
checkout. The original user had requested the correct total, without choosing
that topology. `coordinate advance` had concatenated the model's external-work
description into the member's TaskRun user brief, so normal provenance validation
then treated the description as user-authored text.

New member tasks now keep the original user bug verbatim. The discovered work is
stored separately and supplied as `coordinated_member.reported_work` only when the
coordination and change-set records agree on target, workspace and run directory.
The instruction labels it an unconfirmed investigation lead, requiring code and
interface corroboration, with no requirement, decision, scope or approval
authority. It cannot be quoted as a user requirement. Existing saved tasks and
approvals are preserved; missing/legacy metadata supplies no hint.

Both new regressions fail before the fix. The 62 focused architecture/context/
coordination checks and type/lint checks pass. The full supervised Linux public
TUI regression passes in 379.588 seconds, including first-dispatch source-authority
checks, actual three-repository execution, independent joint fail-before/pass-after
proof, linked offline publication, idempotent reopen and stale-proof rejection.
This is deterministic provider evidence; the broader live qualification remains
tracked in #2208.
