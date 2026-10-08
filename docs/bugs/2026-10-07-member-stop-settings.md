# Settings at a parallel Builder member's stop (#542, #543)

At a parallel member's quota or content-filter stop, resuming with another role's
model flag withdrew its request. Rebuilding the request from the saved stop reason
dropped the member payload and its `route-terra` question, but copied advice telling
the person to answer that missing question. A generic Builder failure repeated the
old resolver advice too.

Combining a settings flag with `--retry-builder` kept the request pending, then made
its binding stale. The state writer treated it as a legacy decision from the last
stage record. That record was a copied worker attempt, so the parent's task guard
failed with `Role result belongs to another implementation task`.

The fix restores a withdrawn operational request's cause before publishing it again.
It restores its member payload only for that same cause while the persisted worker
result still names the same attempt at the current batch's stop. Old route advice is
therefore never carried into a request that
does not ask the model question. A pending receipt at a parallel checkpoint also
prevents legacy reconciliation from using a worker's record as a parent decision.

Republishing a receipt retains its first issuance time. Recovery orders receipts by
their latest issuance, withdrawal or response, so toggling settings back and forth
cannot select an older withdrawal or revive an answered request.

The retry and setup paths share member validation. Setup checks rejected retries
before saving accompanying settings; rejected retries save nothing. A refused member
whose request was answered with information or left paused still collects its stop
and asks its model question again, as #541 requires.

A parallel member's route lives in its child run. Parent settings beside a retry
cannot change it: Builder model, provider and reasoning effort changes with a member
retry are refused. A stopped member's `route-terra` answer changes the child's route.
Other settings may accompany an accepted retry and apply to the continuing run.

The member stop paths require quota, refusal or failure injection to reach reliably.
CLI tests cover those causes and check the advised model answer, sibling retention,
unchanged saved state on rejection, and successful completion after an accepted retry.
