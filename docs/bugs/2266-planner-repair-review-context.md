# Current plan-review concerns in report repair

A coordinated joint task reached a new review, but its revision report omitted
responses. The repair request included an empty execution findings ledger and
no current Plan Reviewer report. Repeated repairs concluded that no responses
were required; validation correctly continued to reject the missing concerns.

Revision report repair now carries the current saved Plan Reviewer entry once,
with its exact concerns and source identity. It explicitly distinguishes this
review from the execution findings ledger. Superseded contracts and historical
reviews stay out of this added payload. Response validation, protected
requirements, saved contracts, routes, budgets and execution receipts are
unchanged.

The assembled-request regression uses a second review with two concern IDs and
an empty execution ledger, rejects historical concerns and checks that planning
records are unchanged. An offline public CLI regression deliberately drops the
revision responses, then repairs them using only the supplied current review.
It must reach plan approval without another discovery, another review, an
Investigator or an unapproved Builder. This deterministic evidence does not
establish live-model convergence or completed multi-repository publication.
