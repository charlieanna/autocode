# First-plan coverage and later revision (#2113)

Paging TUI run `20261028-211621-chat-154a01` on master `b1e1fb3ab` spent
413.900 active seconds in four planning/review calls, including 178.927 seconds
in revision. Bundle manifest:
`c27a115df40e368c516d47088af44895bd2667b1fc603a02097544272998c079`.

The initial draft promised exact integer arithmetic, retained page bounds and
nonpositive-size rejection. Its proposed assertions covered six small inputs,
partial-page success and size zero. The reviewer correctly requested large-domain
and partial-page classes plus independent bounds, empty-input and negative-size
guards. The revision added AC6/AC7. This was substantive review, not a wording
repair or full-contract retranscription.

The missing checks were not lost in the handoff: confirmed Requirements and the
Investigator's invariant/fix plan reached the first Planner. Its own draft repeats
them. Numeric-domain and input-class guidance were also present; prompt compression
was disabled. Missing input or a disabled policy therefore does not explain this
incident. One erroneous huge-integer probe also cost a further model turn, but the
65.138-second interval includes other work and is not all a removable error cost.

The candidate aligns the existing author instructions: a criterion can specify a
concrete case or a bounded family with explicit inputs and exact expectations
derived independently from the rule. Preservation promises need actual named
assertions; a suite command cannot stand in for assertions absent from that suite.
Restore and preservation proofs stay distinct, existing tests stay intact, and
review still checks the resulting coverage independently. No new output field,
controller, model route or default stage-selection policy is added.

This is a testable model-guidance hypothesis, not a demonstrated sole cause of the
retained revision. The initial draft already combined several examples despite
the singular wording. Deterministic tests can verify effective prompt delivery
and correct family/guard proof behavior; only a live trial can show whether this
model follows the guidance and avoids a later coverage revision. Do not report a
speedup or close #2113 on prompt-string assertions alone.
