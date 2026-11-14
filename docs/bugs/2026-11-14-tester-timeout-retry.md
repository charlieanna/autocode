# Repeated Tester timeouts offered an inadmissible retry

Disposable VS Code run `20261113-235458-vscode-134bef` paused after two
non-terminal Tester timeouts and an Investigator diagnosis. The operator
changed the Tester from the repeatedly stalled MiMo model to independently
offered DeepSeek V4 Pro, then selected the view's “Retry the failed step once”.
The core rejected it because the failure ledger's separate three-failure
threshold had not been reached. A plain resume escalated operational recovery;
the operator's truthful corrective information returned to the same pause.

A two-timeout hold is now matched to the latest archived event stream, stage,
source and retained failure entry. The same invocation-bound one-attempt retry
used for other repeated failures is accepted only at that exact hold; active
attempts, mismatched evidence and changed source are refused. Saved attempts,
recovery counts, verification gates and limits remain unchanged. The regression
also checks that neither an already consumed grant nor the saved audit record
can authorize a second attempt.
