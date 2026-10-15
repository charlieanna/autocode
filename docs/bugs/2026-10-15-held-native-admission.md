# Cancellation during native admission

The native release barrier keeps the provider target blocked while source and
ownership checks run. Activity observation and hard deadlines remain active
during those checks. A deadline can therefore stop the held process before
admission returns.

Previously, a later admission exception could prevent `wait_for_stage` from
returning its timeout result. The caller recorded the admission or cleanup
error but lost the supervisor's timeout decision. The repair carries that
decision on the original exception and records the existing timeout fields.
Authentication and uncertain cleanup still block continuation.

Explicit abandonment must authenticate the original preparation and stopped
supervisor. A held preparation is not a successful release or report. Closing
it must preserve its failed attempt, partial artifacts, usage uncertainty,
review charges and elapsed time; resuming is a separate operation. An old
origin missing the required original evidence remains blocked.

The runtime digest of an existing native origin remains immutable. A repaired
runtime needs its own qualification; it cannot silently replace a stopped
origin. See [#1279](https://github.com/charlieanna/autocode/issues/1279).

The related alias traversal change reduces repeated temporary sets while
preserving source validation. Its measured component improvement does not
establish the cause or duration of the historical launch failure. See
[#1272](https://github.com/charlieanna/autocode/issues/1272).
