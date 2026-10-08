# Stock refusal scenarios must expect failure classification

This note covers the four stock-refusal harness assertions in
[#670](https://github.com/charlieanna/autocode/issues/670).

Master's failure routing now classifies an unknown failure before allowing the
Builder's bounded retry. The scripted stock fixture supplies an Investigator
report for the failed refusal-test proof. Four older assertions still expected
the Resolver to proceed directly to the Builder.

PR #681's combined checkout `28d8ca4117e863285a3964eb768e670a22694ed3`
passed 4,789 tests across 326 modules, then failed those four assertions in the
305-test scenario harness. An unchanged control on its master parent
`317eb853e6d35804d5a61cb7bd80493518233a97` reproduced all four assertion
failures. The control used the same source and fixtures on macOS/Python 3.12;
the original CI ran on Ubuntu/Python 3.11. The original failures are retained.

Separately retained public reference and local hybrid diagnostics both completed
with PASS and a CORRECT diagnosis from one Resolver call. Each ran exactly one
Investigator after the Resolver and before the repair Builder. Its accepted
report classified an execution failure and recommended retry; its probe under
native read only containment exited zero, and the Investigator changed no project files.
The hybrid tool served `live-validator`, `live-completion`, `live-resolver`,
`live-reviewer`, `live-builder`, `live-validator`, and `live-completion` in that
order. These are scripted stand-in names; no real models were called.

The assertions require that exact routing, the `investigate`, `retry` decision
sequence, and the Investigator's reviewer model. Existing proof binding,
diagnosis scoring, one-Resolver-call limits, provider environment checks, and
the broken variant's honest stop remain required. This correction changes tests
and documentation. AGENTS.md exempts those changes from live qualification;
the diagnostics establish the fixture behavior.
