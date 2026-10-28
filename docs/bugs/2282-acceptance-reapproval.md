# Fail-first provenance after reapproval

Reapproving an implemented joint task reran Acceptance Writer against its fixed
candidate. The runner treated passing regressions as pre-existing behavior and
rewrote their protected `test:` markers to `guard:`. Independent plan review
correctly refused the rewrite. Native incident bundle manifest:
`1decb9419e925f2fb7aa8e3fbf9e5770427dbf73290cca83301b52400b1e9bae`.

An unchanged case in the same conversation turn can now retain its authenticated
earlier fail-first generation receipt. The historical approval must match the
sealed plan and occur before the current, independently valid approval. Feedback
revokes execution approval without erasing that historical event. An intact test
archive and recorded original failure are required. Citation additions and an
exact binding of the same case-ID test do not change its behavior. Changed
criteria, selectors, assertions, human authority, missing/corrupt archives and
ambiguous approvals do not inherit. Completed-turn inheritance keeps its existing
additional boundary checks, and a new diagnosis reproduction is not inherited.

This preserves only test-generation provenance. Today's tests still execute;
the current plan still needs approval, independent review and fresh runner-owned
fail-before/pass-after proof. No completed or passing proof is synthesized.

The public CLI regression builds through a fake provider, queues a documentation
clarification, explicitly releases its pause, reapproves the plan and requires
retained test markers plus a fresh passing final proof. A fixture documentation
edit gives the second Builder real work without changing the tested behavior.
Offline checks do not establish live TUI completion or automatic PR publication.
