# Planner report declared engineering proposals as contract changes

The authentic single-composer Figma live qualification stopped before Builder.
A complete GLM revision corrected unapproved named test proofs and added a
literal-text check, but declared items such as `AC1 verification_method`,
`AC8 (new criterion added)`, `technical_approach`, `M1` and `initial_task`.
The contract guard treated every declaration as a protected change requiring
user permission. A subsequent report-only model repair exhausted its output
limit. No UI or independent visual acceptance resulted.

`autocode_contract_delta` recognizes only exact, agent-proposed declarations
of already-permitted engineering changes. Proof aliases must qualify under
the existing draft proof policy; additions must actually be new; proposal
fields and existing milestone IDs must actually change. Exact protected
identities win over aliases. The guard still checks every protected body
field, saved approval and user proof history. It never invents user authority,
rewrites original report history, accepts a truncated report, or approves a plan.
Current master already canonicalizes wrapped references to existing protected
items; this repair preserves that behavior and addresses the remaining
new-criterion/proposal declarations. Saved contract declarations retain those
canonical identities while the original report remains unchanged.
Unknown or unauthorized declarations identify the offending item in the error.
The Planner prompt specifies exact item IDs and an empty delta for these
engineering corrections.

Regression tests cover the observed complete-report shape and reject behavior
weakening, changed permissions, dropped requirements, approved/user-set proofs,
historical approval, removal of test markers, forged feedback, unknown aliases
and collisions with protected text. Planning application retains the previous
contract and original declarations, then returns to independent plan review.
The saved rejected live report also passes unchanged against the corrected guard.

This repairs a planning/report classification blocker. It does not establish
pixel fidelity, live qualification success, or resolution of the broader Figma
visual-evidence and efficiency backlog. Those require separate actual receipts.
