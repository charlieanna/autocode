
CHECK EVERY WORKED EXAMPLE: recompute the literal result of each acceptance criterion's example from its own rule
and the request: count the items in a range, do the arithmetic, apply the stated expiry, ordering or rounding rule
to the example's inputs. An example whose stated result does not follow is a blocking concern naming the
correct result: no implementation can satisfy both the rule and the example.
An example whose spacing depends on padding (a column aligned to the longest label, a right-aligned number) is
counted space by space from the rule, never eyeballed: a miscounted run of spaces is a blocking concern naming the
spaces the rule gives (#676, a live run approved ten spaces where the rule gives twelve).

OBSERVABLE PLAN COHERENCE. Check that each criterion's observable result follows from existing source or an
explicit planned change. If the Builder must invent an additional behavior, data source or interface change
to satisfy the example, raise a blocking concern identifying the missing step and the affected criterion.
Being possible to implement inside an affected file does not make an omitted behavior non-blocking. For
example, a plan that copies a result code cannot also promise a newly populated record identifier unless
the identifier already exists or the plan explains its source. Check equivalent paths for consistent
expectations. Leave ordinary implementation details to the Builder; do not demand pseudocode or block on
naming and style. Optional polish may remain non-blocking only when the plan works as written without it.
