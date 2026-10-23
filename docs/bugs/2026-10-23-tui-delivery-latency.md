# Why the TUI repair did not reach implementation

Run: `20261023-004044-chat-afb1d2`, isolated project
`autocode-tui-responsive-1903`, Codex and GLM through Kilo. The owner asked Codex
on October 23 to implement the TUI fix directly and audit the failed delivery.
The paid run remains paused; no implementation plan was approved and Builder
never ran. The preserved redacted bundle manifest SHA256 is
`f8e6a4e41d8c5c78b61cd5db8cbc241c9e90bbb93ba4244146b5f9ac301f2f82`.

## Measured evidence

The saved attempts total 9,917.527 provider-active seconds over 37 attempts.
This is summed attempt time, not elapsed engineering time or a task-completion
benchmark. The public accounting estimate was $4.953749, with ten stages still
unpriced; it is not a complete provider bill.

After the approved switch to GLM Low, another 1,891.58 active seconds elapsed
without implementation. The individual reports were:

| Stage | Seconds | Result |
| --- | ---: | --- |
| Requirements revision | 347.926 | Rejected stale retained-criterion reference |
| Repair | 181.468 | Accepted |
| Final plan review, Codex Medium | 98.176 | Substantive concerns |
| Discovery | 309.527 | Missing milestone coverage for two criteria |
| Repair | 133.888 | Accepted |
| Plan review, Codex Medium | 91.829 | Remaining contradictions and proof gaps |
| Requirements revision | 484.689 | Rejected retained reference again |
| Repair | 199.971 | Rejected; next request exceeded the prompt limit |

The remainder was access preflights. A previous GLM Medium revision ran 602.664s
without a report. Medium was not supported by that configured GLM model: Kilo
ignored the label and the provider used its default. PR #1941 now rejects this
before inference instead of silently changing effective effort. Low makes these
observed calls finish within the stage allowance; it does not establish fast
or dependable end-to-end delivery.

## Causes and corrections

1. **The wrong effective reasoning setting.** #1941 fixes validation against the
   configured provider's actual model variants. Saved choices are preserved;
   unsupported effort does not silently become a different depth.
2. **Avoidable report repair work.** The repair prompt lacked the current exact
   retained-criterion inventory, yet required its identities. It also repeated
   the original and latest full drafts. #1947 supplies the inventory and a
   lossless inline delta for the older draft, preserving both reports and the
   protected contract. Its tool-free repair does not depend on file access.
3. **Real planning defects.** Reviewers correctly rejected a constructor-breaking
   old-code test, wall-clock race oracles, a child-count assertion presented as
   queue occupancy proof, contradictory child-lifetime requirements, attached
   Ctrl-C treated as viewer exit, and circular milestone acceptance. Bypassing
   review would conceal these defects, not solve delivery latency.
4. **Engineering guidance became more mandatory source work.** Three operator
   answers about test mechanics entered the requirement-source inventory. Later
   reviews demanded their source bindings. This is observed contract expansion
   overhead, not evidence that the owner requested new product behavior. Next
   design work should distinguish an implementation decision from an additional
   business requirement, preserving approved outcomes and change receipts.
5. **The interface could animate while ignoring input.** Attached `watch()` slept
   instead of calling the TUI keyboard wait hook. Current/other status reads were
   synchronous and unbounded. The direct Codex repair for #1880 and #1882 D1
   restores input, bounded public-status polling, honest unavailable states and
   ownership-specific pause handling. It does not implement active-run switching
   or qualify SSH/disconnect lifecycle behavior.
6. **Repeated operator interruptions.** Some prompts came from real plan approval;
   others came from explicit trial budget, route, recovery or client-action
   restrictions. They were operator approvals, not all native product questions.
   Missing named controls and inferable engineering questions still made recovery
   harder. Do not count these as business ambiguities or reset usage to hide them.
7. **Startup and process overhead.** Model-free Kilo 7.8.3 probes in an empty home
   took 1.968s (`version`), 7.990s (`debug config`) and 9.778s (`debug agent`).
   These explain only part of a measured 76.46s handoff gap. The remaining gap is
   unresolved; prompt size alone is also unproven because a larger Codex prompt
   succeeded. Cache only evidence whose configuration and validity are known.
8. **Engineering and CI time was separate.** Two earlier #1947 pushes missed the
   architecture size ratchet and an existing prompt-byte ordering regression.
   Those avoidable CI rounds were Codex engineering overhead, not provider-stage
   runtime. Final focused checks and exact-head required CI passed before merge.

## What should happen next

Finish and qualify the existing TUI controls first. Use the existing #1867
cost-aware-stage-selection discussion to design evidence reuse and stage
admission: explicit dependencies and invalidation, no skipping independent
verification merely because a producer reports success. Reuse accepted evidence
through recovery instead of repeatedly regenerating complete plans. Keep the
remaining overview/switching work under #1882 D2 and durable lifecycle under
#1881; this repair must not close those outcomes prematurely.

## Comparison limits

This trial used Kilo as AutoCode's execution backend. It demonstrates AutoCode's
additional planning, review and report-repair overhead, and the benefit of its
independent review catching real faults. It does not measure a standalone Kilo
attempt on the same task. No speed ratio or lower-cost claim against Kilo is
supported. Direct Codex implementation is now authorized because this AutoCode
chat did not deliver code. Terminal input latency is measured separately from
model task-completion time in the regression evidence.
