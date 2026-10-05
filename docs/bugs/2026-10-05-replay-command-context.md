# Replay identity belongs to the whole command

Clean replay cached its within-invocation execution context by the first command
word and collection eligibility. A multiline Python `-c` check and a plain CLI
check can share both values while selecting different interpreter identities:
the multiline check uses the conservative runtime fallback, while the plain
check binds its explicit interpreter. Copied virtualenv `python` and `python3`
aliases expose the difference even when their executable bytes match. The plain
check then incorrectly rejects successful output as a verification-context change.

The context key now includes the entire command. Identical commands retain their
existing reuse behavior, and every execution still refreshes its identity afterward.
`ScratchReplayTests.test_multiline_and_plain_commands_have_separate_runtime_contexts`
reproduces the failure with a copied virtualenv, actual Git scratch worktrees and
Python subprocesses. The existing visual TaskRun test supplies a public completion
control and a failing visual criterion control.
