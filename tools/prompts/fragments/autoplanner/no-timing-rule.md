NO TIMING CRITERIA: no acceptance criterion, verification method or test may depend on elapsed time or machine
speed ("finishes in under 10 seconds", a timing assertion, a benchmark threshold). It passes on an idle machine and
fails on a loaded one, and the Builder cannot fix that by fixing code. Bound the work instead: state the size of an
enumeration and keep it to a few thousand cases that run in seconds. A plan reviewer raises a blocking concern for one.
Two things are not timing criteria, and a reviewer raises no concern for them. A hang guard: a bound on a wait, join
or subprocess whose only job is to turn a hang or deadlock into a failure instead of a stalled suite, set far above
the work's normal time (tens of seconds for work that takes milliseconds); prefer a proof that needs no clock where one
exists. And an existing test this plan leaves unchanged: the rule covers what this plan adds or changes. Where an
approved criterion does state a speed, keeping its behavior and proving it without the time is a proof correction,
never a question for the person.
