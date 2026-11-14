# VS Code live run: Models control at an uncertain cleanup pause

During disposable model-backed run `20261113-235458-vscode-134bef`, a regression
proof paused because its command cleanup could not be confirmed under process-table
churn. VS Code disabled the Models control but said a stage was working and that
the choice would return after the stage. The status was already
`PAUSED_VERIFICATION_UNCERTAIN`, with a runner check retained for reconciliation.

The public stage-model view now explains that the prior check has not been
confirmed stopped, and VS Code labels the control unavailable. Both preserve the
existing restriction; neither authorizes a model change or automates cleanup.
Regression tests exercise the view reason, refusal and rendered extension chip.
The pause asking the person to inspect AutoCode's cleanup remains a separate
recovery defect; this display fix does not claim to solve it.
