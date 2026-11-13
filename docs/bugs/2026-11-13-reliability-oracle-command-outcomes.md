# Reliability oracles must observe command completion

The four required cases in `RELIABILITY.md` accepted `returncode != 0` as
evidence of a requested error or of delivered tests failing on original code.
The oracle helper returns -1 for a timeout and 126/127 for launch errors;
POSIX signals also produce negative return codes. None proves that the command
completed with the expected error. TODO's completion list and both stable-ID
lists also checked their output without requiring exit 0, and stable-ID setup
discarded the add commands' results. Valid output or a store written before a
timeout could pass those checks (#3423).

`harness.oracle.run` retains its `CompletedProcess` fields and return codes.
Its additive `observed_normal_exit` annotation is true only when the child
actually returns a nonnegative status, and false for signals, timeouts and
launch exceptions. The affected error checks require a positive status with
that observation; an absent observation supplies no evidence. Real child exits
126 and 127 remain valid failures, distinguished from failed launches without
interpreting stderr. The TODO success checks require exit 0 as their brief does.
An abnormal original-code check says it did not complete normally, rather than
claiming that delivered tests failed on the original code.

Deterministic counterexamples preserve the reference commands' output and
store bytes while injecting only the relevant process outcome. They cover
timeouts, signals, missing and non-executable interpreters, normal positive
exits, and valid partial list output. The Docutils counterexample uses a tiny
local project with the real catalog oracle; its reference and broken controls
are checked separately against the pinned upstream seed.

This repairs benchmark evidence. It neither changes Core execution nor
qualifies a live run, and the previously frozen live oracle remains unchanged.
