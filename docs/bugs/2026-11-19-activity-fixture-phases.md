# Activity fixtures must reach the phase they measure

After the retry-marker correction in #3919, an isolated rerun exposed another
startup assumption in the same real-process support. The stalled-writer case
intended to check retained mid-turn work, but its 0.4-second idle limit could
stop Python before it wrote anything or emitted its first event. With no
changed source, the core correctly chose Builder startup recovery rather than
the Completion Reviewer the test expected.

All five real-provider fixture callers were checked: three measure startup
or startup retry, and two measure behavior after the first event. The shared
fixture now accepts an explicit after-first-event phase for those two cases.
It gives host startup a bounded 30-second fixture allowance, then delegates to
the production monitor's existing idle calculation as soon as a real event is
observed. The enclosing fixture stage is bounded at 60 seconds. Its 0.4-second
inactivity limit and two-second tool limit remain unchanged after the event.
The three startup cases retain the real startup policy without this shim.

The stalled case also asserts that the provider actually wrote the intended
partial source before checking recovery. This prevents another startup stop
from passing as mid-turn coverage. Production limits and recovery code are
unchanged.

On unchanged master, a controlled three-second delay after stdin makes both
mid-turn cases fail in six seconds. The same delay passes both corrected cases
in nine seconds. All 67 activity/recovery and architecture checks pass in 53
seconds, with Ruff lint and formatting. This is test-only evidence; no paid
model run is needed.
