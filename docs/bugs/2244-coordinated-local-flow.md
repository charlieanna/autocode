# Local member flows cannot wait for the coordinator's later proof

The native GLM-main/OpenAI-review cohort reached implementation and independent
testing. History and Tax each had a passing regression proof, an independently
replayed original test, intact evidence pins and local criterion evidence. Their
approved `end_to_end_flow`, however, also named the coordinator's later actual-code
joint proof. Validators correctly returned `NOT_VERIFIED` for that whole flow.
Members must complete before the coordinator creates the joint task, making
those plans impossible to complete in order. The generic refusal hid the cause.

Retained diagnostic bundle manifests:

- History: `a5e25c6c157a2e5f9ce12fabd5738909d37cbf741f23440786176eb8d96df905`.
- Tax: `4ebf10d0f1996bc374263b369c56d3125f24909f9906019ba9a014591fd6ff3b`.

Authenticated member planning/review context now expressly keeps its
`end_to_end_flow` local and requires a plan revision when it contains a later
coordinator step. Exact whole-set outcomes, preservation obligations and their
traces stay in required behaviors or constraints. This is guidance to produce a
correct plan, not a semantic parser or an exemption from proof. Completion still
rejects an unproven approved flow; its diagnostic now names that flow and the
Validator's explanation when all local criteria passed.

The public TUI fixture now describes actual local member flows separately from
the real-code integration flow, retains the joint obligation in every member,
and checks those approved contracts through public status. Its independent
joint proof, correction, publication and stale-evidence checks remain in place.
Both member-context assertions and the targeted strict-gate diagnostic regression
fail before the fix. The latter was rerun on supported Linux after a first macOS
attempt correctly refused supervised command execution.

The Linux changed selection ran **1,066 tests in 43 modules (413 seconds)**.
All 42 other modules passed. Both full TUI flows reached joint proof and offline
publication, then failed the new local-flow assertion because Tax's fixture
wording omitted the word `local`. That fixture wording is corrected; the affected
four-case TUI module is rerun separately. The first harness retained their text
logs but removed its nested disposable fixture directories at cleanup.

Existing approved native plans are not rewritten or waived. The retained cohort
exited with Ctrl-D at an input gate. It must receive ordinary corrective feedback,
replan and obtain fresh coordinated approval before continuing. Native joint
proof and real fixture PR publication have not yet been established.
