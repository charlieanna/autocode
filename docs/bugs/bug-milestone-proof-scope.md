# Diagnosis cases at milestone checkpoints

A real multi-milestone bug-fix Arena attempt finished its first milestone and
independently replayed its checks, but the regression proof demanded named cases
assigned to unfinished milestones. Review routed repeated repair work against
the first milestone and eventually reached the no-progress pause. That original
attempt remains a failed result.

Diagnosis cases can now use the approved criteria's explicit `test:` and
`guard:` names to establish milestone ownership. A checkpoint proves its current
and previously accepted cases. The last milestone and final completion still
require all original diagnosis cases, including preservation guards. Final
completion authenticates that coverage from the executed proof receipt; a
passing first-milestone receipt cannot satisfy the whole-goal gate. The model
handoff exposes its case scope and the refusal identifies remaining cases.

Incomplete mappings, shared names, or conflicting restore/preserve marks retain
the full case requirement. No names are inferred from similar English text.
Legacy plans therefore retain their existing behavior. Planner instructions ask
for complete bindings using the existing verification-method field; no new run
state key or schema field is introduced.

Public proof-function tests exercise an existing broken subtraction API, an
unfinished multiplication fix, and an addition preservation guard. The first
checkpoint can pass; the unfinished final proof fails; supplying all fixes and
the guard makes the full proof pass. A real Codex qualification on commit
`9d040484` completed three ordered arithmetic milestones in 855 seconds. Public
checkpoint observations showed T1/T4 passing with later functions still broken,
then T1/T2/T4 passing with the final function still broken. Final completion and
a fresh delivery check passed all four tests. This is supplemental qualification;
it does not count as a completed original Arena project.
