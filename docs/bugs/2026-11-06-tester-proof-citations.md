# Initial Tester citation contract

A live Tester returned PASS for both members of an integrated batch, but cited
individual mutation logs in its per-line reasons. Those logs existed and were
readable; the admission contract accepts the enclosing authenticated runner proof
for these observations. The initial prompt only said to cite the runner proof,
while the repair handoff explained the narrower citation rule explicitly.

The initial changed-code instruction now names regression_proof.path and states
that raw mutation logs and coverage files may be inspected but are not accepted
references in changed_code_exceptions. Existing evidence and judgment gates are
unchanged. A regression checks the original handoff, with the existing CLI
evidence-gate tests retained as the mapped consumer.

A later read-only retry regenerated the proof while its Completion Reviewer task
still named the previous proof directory. The Tester inspected both proofs but
cited the older one, triggering another report repair. The shared Tester and
Completion Reviewer instruction now identifies the current handoff's proof as
the citation authority and tells next-task authors to refer to that proof
symbolically. Requested checks and source-bound admission remain unchanged.
The retry guidance regression fails without this instruction.
