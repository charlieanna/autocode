# Fresh regression proof after reapproval (#2297)

A recovered native TUI joint run retained two failed proofs for unchanged candidate source. The person then approved corrected preservation wording and an exact real-composition test binding. Completion Review counted only source revision when limiting failure retries, so it suppressed fresh proof and supplied obsolete failures to Reviewer and Resolver.

Completion Review now checks the approved contract and due case identities before reusing proof. The runner records a digest of the full execution context in its proof history and counts failures only in that context. A new context receives fresh execution and its own single bounded retry. Legacy proof without context is rerun. Old receipts and history remain intact; this grants no passing result, approval or completion credit.

Four deterministic regressions fail before this fix and pass afterward: reapproval after repeated failure, changed cases after PASS, retry isolation across contracts, and legacy context migration. Existing same-context retry, reuse and changed-source tests remain. Linux public bug-fix workflow coverage exercises the real runner and mandatory baseline-fail/candidate-pass gate. Live TUI qualification remains pending until native joint proof, independent review and automatic publication finish.
