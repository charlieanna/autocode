# A roster timeout is an internal failure, not a report or a person decision

Before admitting the next stage of a saved run, AutoCode checks its selected
models. When the plain Kilo roster command exceeded both existing listing bounds,
it raised an untyped error. The outer setup handler classified that error as
invalid model output and offered a person a Resume action. No stage request had
started and no report had been returned.

The exact roster producer now raises a typed prelaunch fact. Saved-run admission
records it in the existing runner-owned diagnostics and history, keeps the work,
approvals and policy, and blocks further stages. The public view explains the
internal hold without a person question or action. Ordinary errors and model
reports cannot acquire this authority by matching its message.

The listing uses synchronous subprocess calls. A timeout establishes that their
direct child was collected; it does not authenticate cleanup of arbitrary
descendants. The outcome therefore remains Blocked, with that uncertainty in its
evidence. It is neither completion nor a claim of full process quiescence. This
change adds no retry, timeout, route fallback, cache authority or client action.
