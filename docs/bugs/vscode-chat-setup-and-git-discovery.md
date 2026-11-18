# Chat setup must preserve input and report failed discovery

Expanding both workspace and model settings could push the composer below the
VS Code panel. Each section kept its own height, so bounding only the model
section did not bound their combined height. Both sections now share one
shrinking scroll area outside the composer. Rebuilding a checked model row
preserves its keyboard focus; removing its reset button returns focus to the
model control. Moving to another field or chat releases that focus claim.

A separate discovery defect appeared when Git refused a repository during a
live check. Workspace listing treated failed location, HEAD, branch and
worktree queries as absent information. Workspace initialization also treated
a failed location query as permission to create a repository. The view could
therefore describe an inaccessible checkout as a new project.

`autocode_git_location` now owns that distinction for listing and initialization.
Git must report that no repository exists, and no `.git` entry may be present
in the directory or its parents, before initialization is offered. Other query
failures propagate without a placement. Missing refs retain their separate
Git result. The client exposes failed discovery as an expanded, accessible
error and retains an unsent task when start preparation fails.

The regressions cover ownership refusals, unavailable Git, timeouts, broken
linked metadata, empty metadata, bare repositories and failures in all four
placement queries. Existing tests cover valid checkouts, linked worktrees,
uncommitted repositories and folders outside Git. The same checks apply to
every language and model route.

Relevant checks are `tests.test_git_location`, `tests.test_worktree_choice`,
`tests.test_worktrees` and the generated-webview tests in
`vscode-extension/test/editor.test.cjs`.
