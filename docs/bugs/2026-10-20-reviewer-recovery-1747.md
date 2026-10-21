# Reviewer recovery repeated protocol discovery (#1747)

The retained TODO run on `5ee343561d25bca6933c2a52dd76cdfd5c6435a0`
returned Requirements, then spent two 900-second MiMo V2.6 Flash Medium stages
without a review. Its bundle manifest is
`e91973ded07f542d41b3f4c29475f52fb3fdfc8332b5830731a3b3274d619494`.
No code was delivered. Partial usage and prompt size alone do not establish the
cause of a provider timeout.

The completed tool records do establish repeated searches for AutoCode's
`brief_observations`, `bindings`, and `observe_step` protocol under private run
files. Three deterministic defects made this avoidable work possible:

- The observation instruction named fields without explaining their indexing,
  generated IDs, expectation semantics, or a valid example.
- Planning received execution-receipt instructions and a live event-log locator,
  although its report did not require executed verification.
- Recovery told the model to reuse evidence while trimming prior reports and
  leaving exploratory tools available.

The repair supplies a compiler-checked observation example and separate planning
output instructions. An already-authorized ordinary Kilo review recovery receives
complete prior planning reports and no native tools. Missing evidence remains a
concern; the existing report validation, independent review, plan approval,
routes, consumed calls, and limits still apply. Other transports retain their
existing tool enforcement; the no-tools claim is specific to ordinary Kilo.

Three new regressions fail on the original runtime. A real Kilo 7.8.3 loopback
fixture observes read tools in ordinary planning, no tools in recovery, and a
denied direct read attempt. These are deterministic protocol and enforcement
results. Live time to plan and delivery must be measured separately after merge;
this note does not claim the timeout or total task latency is already qualified.
