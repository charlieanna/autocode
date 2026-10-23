# Proof corrections causing wording questions (#1966)

The live TUI run's planning bundle has manifest
`3202df86d5af35da49a6fb795719a155a4c68d519f92c0ebf7a5e9012c03b363`.
The Reviewer correctly identified a multi-test selector and a Python suite command
mistakenly prefixed `test:`. Correcting one proof caused GLM to also paraphrase the
criterion and triggered an owner wording question before implementation-plan approval.

The existing criteria delta required full rows for such changes. A token-bound
`proofs` array now replaces only `verification_method`; criterion text, sources,
human-review setting and every other field come from the saved row. Unknown,
duplicate, stale, conflicting or extra-field operations fail. Old pinned repair
schemas receive no instruction to emit the new field. Saved contracts still use
the existing canonical criterion array.

All ordinary revision guards still run. A valid named test or guard cannot become
an ordinary command. Only a literal recognized Python suite incorrectly prefixed
`test:` may lose exactly that prefix in a model-authored, unapproved, unprotected
draft; its command must remain identical. Approved or user-provided proofs remain
protected. This does not infer English equivalence, approve a plan, change limits,
merge verification roles or suppress an existing human decision.

Deterministic coverage checks exact retained fields, pinned schemas and authority
failures, and drives the public CLI to normal plan approval without a wording
question. The live trial remains retained for post-merge continuation. Two earlier
repairs in that trial arose from an operator-added workflow instruction promoted
into a product requirement; this fix does not claim to resolve that classification
question or establish zero-repair live performance.
